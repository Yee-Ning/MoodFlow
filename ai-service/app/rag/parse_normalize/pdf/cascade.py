"""Quality-gated PDF parser cascade with bounded backend attempts."""

from __future__ import annotations

import time

from app.rag.parse_normalize.base import BaseParser, ParserError
from app.rag.parse_normalize.cache_identity import component_identity, identity_hash
from app.rag.parse_normalize.models import (
    DetectedFileType,
    ParseInput,
    ParseReport,
    ParseStatus,
    ParserAttempt,
    ParserAttemptStatus,
    ParserExecution,
    ParserResult,
)

from .adapters import PDFAdapterOutput, PDFParserAdapter
from .inspector import PDFInspectionError, PDFInspector
from .models import PDFBackend, PDFParserMode
from .quality import PDFParseQualityEvaluator, PDFQualityAssessment
from .router import PDFParserRouter


class PdfCascadeParser(BaseParser):
    parser_name = "pdf-cascade"
    parser_version = "1.1.0"
    supported_file_types = frozenset({DetectedFileType.PDF})

    def __init__(
        self,
        *,
        adapters: dict[PDFBackend, PDFParserAdapter],
        inspector: PDFInspector | None = None,
        router: PDFParserRouter | None = None,
        quality_evaluator: PDFParseQualityEvaluator | None = None,
    ) -> None:
        missing = set(PDFBackend) - set(adapters)
        if missing:
            raise ValueError(
                f"missing PDF adapters: {', '.join(sorted(item.value for item in missing))}"
            )
        self.adapters = dict(adapters)
        self.inspector = inspector or PDFInspector()
        self.router = router or PDFParserRouter()
        self.quality_evaluator = quality_evaluator or PDFParseQualityEvaluator()

    def parse(self, parse_input: ParseInput) -> ParserResult:
        execution = self.execute(parse_input)
        if execution.result is None:
            raise ParserError("pdf_cascade_failed")
        return execution.result

    def execute(
        self, parse_input: ParseInput, parser_mode: str = "auto"
    ) -> ParserExecution:
        started = time.perf_counter()
        try:
            mode = PDFParserMode(parser_mode)
        except ValueError as exc:
            raise ParserError("unsupported_pdf_parser_mode") from exc
        try:
            inspection = self.inspector.inspect(parse_input.raw_bytes)
        except PDFInspectionError as exc:
            return self._inspection_failure(parse_input, exc, started)

        backend = self.router.initial_parser(inspection.profile, mode)
        primary = self._attempt(
            self.adapters[backend],
            parse_input,
            inspection,
            parser_error_count=0,
            fallback_reason=None,
        )
        attempts = [primary.attempt]
        if mode is not PDFParserMode.AUTO:
            if primary.output is None:
                return self._failed(
                    parse_input,
                    primary,
                    attempts,
                    started,
                    parser_error_count=1,
                )
            return self._finalize(
                parse_input,
                primary,
                attempts,
                started,
                fallback_used=False,
            )
        if self._passes(primary.assessment):
            return self._finalize(
                parse_input,
                primary,
                attempts,
                started,
                fallback_used=False,
            )

        fallback_reason = (
            f"{primary.output.parser_name}_quality_below_pass_threshold"
            if primary.output is not None
            else f"{primary.attempt.parser}_error"
        )
        error_count = int(primary.output is None)
        fallback = self._attempt(
            self.adapters[PDFBackend.DOCLING],
            parse_input,
            inspection,
            parser_error_count=error_count,
            fallback_reason=fallback_reason,
        )
        attempts.append(fallback.attempt)
        if fallback.output is not None:
            return self._finalize(
                parse_input,
                fallback,
                attempts,
                started,
                fallback_used=True,
            )
        if primary.output is not None:
            return self._finalize(
                parse_input,
                primary,
                attempts,
                started,
                fallback_used=False,
                parser_error_count=error_count + 1,
            )
        if (
            inspection.native_text_available
            and backend is not PDFBackend.PYMUPDF4LLM
        ):
            recovery_reason = (
                "optional_pdf_backends_unavailable_native_text_recovery"
            )
            recovery = self._attempt(
                self.adapters[PDFBackend.PYMUPDF4LLM],
                parse_input,
                inspection,
                parser_error_count=error_count + 1,
                fallback_reason=recovery_reason,
            )
            attempts.append(recovery.attempt)
            if recovery.output is not None:
                return self._finalize(
                    parse_input,
                    recovery,
                    attempts,
                    started,
                    fallback_used=True,
                    parser_error_count=error_count + 1,
                    force_manual_review=True,
                    extra_warnings=(
                        "complex_layout_native_text_recovery_requires_review",
                    ),
                )
        return self._failed(
            parse_input,
            fallback,
            attempts,
            started,
            parser_error_count=error_count + 1,
        )

    def supports_parser_mode(self, parser_mode: str) -> bool:
        try:
            PDFParserMode(parser_mode)
        except ValueError:
            return False
        return True

    def fingerprint(self, parser_mode: str = "auto") -> str:
        identity = {
            "backends": {key.value: adapter.cache_identity() for key, adapter in self.adapters.items()},
            "inspector": component_identity(self.inspector),
            "quality": component_identity(self.quality_evaluator),
            "router": component_identity(self.router),
        }
        return f"{self.parser_name}:{self.parser_version}:{parser_mode}:{identity_hash(identity)}"

    def _attempt(
        self,
        adapter: PDFParserAdapter,
        parse_input: ParseInput,
        inspection,
        *,
        parser_error_count: int,
        fallback_reason: str | None,
    ) -> "_AttemptResult":
        started = time.perf_counter()
        try:
            output = adapter.parse(parse_input, inspection)
            assessment = self.quality_evaluator.evaluate(
                output.result,
                inspection,
                parser_error_count=parser_error_count,
            )
            attempt = ParserAttempt(
                parser=output.parser_name,
                parser_version=output.parser_version,
                status=ParserAttemptStatus.SUCCESS,
                quality_score=assessment.quality_score,
                elapsed_ms=self._elapsed_ms(started),
                fallback_reason=fallback_reason,
                ocr_used=output.ocr_used,
            )
            return _AttemptResult(output=output, assessment=assessment, attempt=attempt)
        except Exception as exc:
            error = self._error_code(exc)
            unavailable = error.endswith("_backend_unavailable")
            attempt = ParserAttempt(
                parser=adapter.parser_name,
                parser_version=adapter.parser_version,
                status=(
                    ParserAttemptStatus.UNAVAILABLE
                    if unavailable
                    else ParserAttemptStatus.ERROR
                ),
                error=error,
                elapsed_ms=self._elapsed_ms(started),
                fallback_reason=fallback_reason,
                ocr_used=False,
            )
            return _AttemptResult(output=None, assessment=None, attempt=attempt)

    def _finalize(
        self,
        parse_input: ParseInput,
        attempted: "_AttemptResult",
        attempts: list[ParserAttempt],
        started: float,
        *,
        fallback_used: bool,
        parser_error_count: int | None = None,
        force_manual_review: bool = False,
        extra_warnings: tuple[str, ...] = (),
    ) -> ParserExecution:
        assert attempted.output is not None
        assert attempted.assessment is not None
        assessment = attempted.assessment
        if parser_error_count is not None:
            assessment = self.quality_evaluator.evaluate(
                attempted.output.result,
                self.inspector.inspect(parse_input.raw_bytes),
                parser_error_count=parser_error_count,
            )
        if assessment.metrics.empty_content or (
            assessment.quality_score
            < self.quality_evaluator.config.quality_fallback_threshold
        ):
            status = ParseStatus.FAILED
            result = None
            artifacts = []
        elif force_manual_review:
            status = ParseStatus.MANUAL_REVIEW
            result = attempted.output.result
            artifacts = attempted.output.artifacts
        elif assessment.quality_score < self.quality_evaluator.config.quality_pass_threshold:
            status = ParseStatus.MANUAL_REVIEW
            result = attempted.output.result
            artifacts = attempted.output.artifacts
        elif fallback_used:
            status = ParseStatus.FALLBACK_USED
            result = attempted.output.result
            artifacts = attempted.output.artifacts
        elif assessment.warnings:
            status = ParseStatus.WARNING
            result = attempted.output.result
            artifacts = attempted.output.artifacts
        else:
            status = ParseStatus.PASS
            result = attempted.output.result
            artifacts = attempted.output.artifacts
        report = ParseReport(
            source_id=parse_input.snapshot.source_id,
            parser_name=attempted.output.parser_name,
            selected_parser=attempted.output.parser_name,
            parser_attempts=attempts,
            quality_score=assessment.quality_score,
            status=status,
            warnings=list(dict.fromkeys([*assessment.warnings, *extra_warnings])),
            metrics=assessment.metrics,
            elapsed_ms=self._elapsed_ms(started),
        )
        return ParserExecution(
            result=result,
            selected_parser=attempted.output.parser_name,
            selected_parser_version=attempted.output.parser_version,
            report=report,
            artifacts=artifacts,
        )

    def _failed(
        self,
        parse_input: ParseInput,
        attempted: "_AttemptResult",
        attempts: list[ParserAttempt],
        started: float,
        *,
        parser_error_count: int,
    ) -> ParserExecution:
        inspection = self.inspector.inspect(parse_input.raw_bytes)
        assessment = self.quality_evaluator.empty_assessment(
            inspection, parser_error_count=parser_error_count
        )
        selected = attempted.attempt.parser
        version = attempted.attempt.parser_version
        report = ParseReport(
            source_id=parse_input.snapshot.source_id,
            parser_name=selected,
            selected_parser=selected,
            parser_attempts=attempts,
            quality_score=assessment.quality_score,
            status=ParseStatus.FAILED,
            warnings=[*assessment.warnings, "all_pdf_parsers_failed"],
            metrics=assessment.metrics,
            elapsed_ms=self._elapsed_ms(started),
        )
        return ParserExecution(
            result=None,
            selected_parser=selected,
            selected_parser_version=version,
            report=report,
        )

    def _inspection_failure(
        self, parse_input: ParseInput, exc: Exception, started: float
    ) -> ParserExecution:
        metrics = self.quality_evaluator.empty_assessment(
            # Inspection failed, so a one-page inert profile is not fabricated;
            # the shared failure helper cannot be used without a real profile.
            self._minimal_inspection(),
            parser_error_count=1,
        ).metrics
        report = ParseReport(
            source_id=parse_input.snapshot.source_id,
            parser_name=self.parser_name,
            selected_parser=self.parser_name,
            parser_attempts=[],
            quality_score=0,
            status=ParseStatus.FAILED,
            warnings=[self._error_code(exc)],
            metrics=metrics,
            elapsed_ms=self._elapsed_ms(started),
        )
        return ParserExecution(
            result=None,
            selected_parser=self.parser_name,
            selected_parser_version=self.parser_version,
            report=report,
        )

    @staticmethod
    def _minimal_inspection():
        from .models import PDFInspection, PDFProfile

        return PDFInspection(
            profile=PDFProfile.UNKNOWN,
            page_count=1,
            native_text_available=False,
            extracted_text_chars=0,
            average_chars_per_page=0,
            empty_text_page_ratio=1,
            image_count=0,
            suspected_scan=False,
            suspected_multi_column=False,
            table_hint=False,
            pages=[],
        )

    def _passes(self, assessment: PDFQualityAssessment | None) -> bool:
        return bool(
            assessment is not None
            and not assessment.metrics.empty_content
            and assessment.quality_score
            >= self.quality_evaluator.config.quality_pass_threshold
        )

    @staticmethod
    def _error_code(exc: Exception) -> str:
        value = str(exc).splitlines()[0].strip()
        return (value or type(exc).__name__)[:500]

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return max(0.0, (time.perf_counter() - started) * 1_000)


class _AttemptResult:
    def __init__(
        self,
        *,
        output: PDFAdapterOutput | None,
        assessment: PDFQualityAssessment | None,
        attempt: ParserAttempt,
    ) -> None:
        self.output = output
        self.assessment = assessment
        self.attempt = attempt

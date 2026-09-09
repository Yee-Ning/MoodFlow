"""Orchestration for parser routing, quality evaluation, and persistence."""

from __future__ import annotations

import hashlib
import time
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.rag.source_ingestion.manifest import load_candidates
from app.rag.source_ingestion.models import CandidateSource, ReviewStatus

from .base import ParseNormalizeError
from .detection import FileTypeDetector
from .manifest import RawSnapshotLoader, RawSnapshotNotFoundError
from .models import (
    NORMALIZATION_VERSION,
    NormalizedDocument,
    ParseInput,
    ParseStatus,
)
from .normalizer import MarkdownNormalizer
from .parsers import HtmlParser, JsonParser, MarkdownParser, TxtParser
from .quality import ParseQualityEvaluator
from .registry import ParserRegistry
from .router import (
    ParserRouter,
    UnsupportedCurrentStageError,
    UnsupportedFileTypeError,
)
from .writer import AtomicNormalizedWriter


class OutcomeStatus(StrEnum):
    PARSED = "parsed"
    WARNING = "warning"
    FALLBACK_USED = "fallback_used"
    MANUAL_REVIEW = "manual_review"
    SKIPPED = "skipped"
    FAILED = "failed"
    UNSUPPORTED_FOR_CURRENT_STAGE = "unsupported_for_current_stage"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    MISSING_RAW_SNAPSHOT = "missing_raw_snapshot"


class ParseOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    status: OutcomeStatus
    parser_name: str | None = None
    quality_score: float | None = Field(default=None, ge=0, le=1)
    output_directory: str | None = None
    detail: str | None = None


class ParseNormalizeService:
    def __init__(
        self,
        *,
        project_root: Path,
        raw_manifest_path: Path,
        candidate_manifest_path: Path | None,
        loader: RawSnapshotLoader,
        router: ParserRouter,
        detector: FileTypeDetector,
        normalizer: MarkdownNormalizer,
        quality_evaluator: ParseQualityEvaluator,
        writer: AtomicNormalizedWriter,
    ) -> None:
        self.project_root = project_root.resolve()
        self.raw_manifest_path = raw_manifest_path
        self.candidate_manifest_path = candidate_manifest_path
        self.loader = loader
        self.router = router
        self.detector = detector
        self.normalizer = normalizer
        self.quality_evaluator = quality_evaluator
        self.writer = writer

    def parse_source(
        self,
        source_id: str,
        *,
        force: bool = False,
        manifest_path: Path | None = None,
        parser_mode: str = "auto",
    ) -> ParseOutcome:
        started = time.perf_counter()
        selected_manifest = manifest_path or self.raw_manifest_path
        try:
            loaded = self.loader.load_latest(selected_manifest, source_id)
        except RawSnapshotNotFoundError:
            return ParseOutcome(
                source_id=source_id,
                status=OutcomeStatus.MISSING_RAW_SNAPSHOT,
                detail="raw_snapshot_not_found",
            )
        except ParseNormalizeError as exc:
            return self._failed_outcome(
                source_id, "unselected", self._error_code(exc), started
            )

        try:
            parser = self.router.select(loaded.record)
        except UnsupportedCurrentStageError:
            return self._unsupported_outcome(
                source_id,
                OutcomeStatus.UNSUPPORTED_FOR_CURRENT_STAGE,
                "unsupported_for_current_stage",
                started,
            )
        except UnsupportedFileTypeError:
            return self._unsupported_outcome(
                source_id,
                OutcomeStatus.UNSUPPORTED_FILE_TYPE,
                "unsupported_file_type",
                started,
            )

        if not parser.supports_parser_mode(parser_mode):
            return self._failed_outcome(
                source_id,
                parser.parser_name,
                "parser_override_only_supported_for_pdf",
                started,
            )
        parse_fingerprint = parser.fingerprint(parser_mode)
        if not force and self.writer.fingerprint_matches(
            source_id,
            raw_content_hash=loaded.record.content_hash,
            parse_fingerprint=parse_fingerprint,
            normalization_version=self.normalizer.version,
        ):
            return ParseOutcome(
                source_id=source_id,
                status=OutcomeStatus.SKIPPED,
                parser_name=parser.parser_name,
                output_directory=self._relative_output(source_id),
                detail="matching_parse_fingerprint",
            )

        candidate = self._candidate(source_id)
        try:
            execution = parser.execute(
                ParseInput(
                    snapshot=loaded.record,
                    raw_bytes=loaded.raw_bytes,
                    candidate=candidate,
                )
            )
            parsed = execution.result
            report = execution.report
            if parsed is None:
                if report is None:
                    raise ParseNormalizeError("parser_returned_no_result")
                output = self.writer.write_failure(source_id, report)
                return ParseOutcome(
                    source_id=source_id,
                    status=OutcomeStatus.FAILED,
                    parser_name=execution.selected_parser,
                    quality_score=report.quality_score,
                    output_directory=output.relative_to(self.project_root).as_posix(),
                    detail="parse_quality_failed",
                )
            markdown = self.normalizer.render(parsed.blocks)
            elapsed_ms = self._elapsed_ms(started)
            if report is None:
                report = self.quality_evaluator.evaluate(
                    source_id, execution.selected_parser, parsed.blocks, elapsed_ms
                )
            if report.status is ParseStatus.FAILED:
                output = self.writer.write_failure(source_id, report)
                return ParseOutcome(
                    source_id=source_id,
                    status=OutcomeStatus.FAILED,
                    parser_name=execution.selected_parser,
                    quality_score=report.quality_score,
                    output_directory=output.relative_to(self.project_root).as_posix(),
                    detail="parse_quality_failed",
                )
            document = self._document(
                loaded.record,
                execution.selected_parser,
                execution.selected_parser_version,
                parsed,
                markdown,
                report.quality_score,
                candidate,
                parse_fingerprint,
            )
            output = self.writer.write(
                document, report, artifacts=execution.artifacts
            )
            outcome_status = {
                ParseStatus.WARNING: OutcomeStatus.WARNING,
                ParseStatus.FALLBACK_USED: OutcomeStatus.FALLBACK_USED,
                ParseStatus.MANUAL_REVIEW: OutcomeStatus.MANUAL_REVIEW,
            }.get(report.status, OutcomeStatus.PARSED)
            return ParseOutcome(
                source_id=source_id,
                status=outcome_status,
                parser_name=execution.selected_parser,
                quality_score=report.quality_score,
                output_directory=output.relative_to(self.project_root).as_posix(),
            )
        except Exception as exc:
            return self._failed_outcome(
                source_id, parser.parser_name, self._error_code(exc), started
            )

    def parse_manifest(
        self,
        manifest_path: Path,
        *,
        force: bool = False,
        parser_mode: str = "auto",
    ) -> list[ParseOutcome]:
        try:
            source_ids = list(self.loader.latest_records(manifest_path))
        except ParseNormalizeError as exc:
            return [
                ParseOutcome(
                    source_id="manifest",
                    status=OutcomeStatus.FAILED,
                    detail=self._error_code(exc),
                )
            ]
        outcomes: list[ParseOutcome] = []
        for source_id in source_ids:
            try:
                outcomes.append(
                    self.parse_source(
                        source_id,
                        force=force,
                        manifest_path=manifest_path,
                        parser_mode=parser_mode,
                    )
                )
            except Exception as exc:
                outcomes.append(
                    ParseOutcome(
                        source_id=source_id,
                        status=OutcomeStatus.FAILED,
                        detail=self._error_code(exc),
                    )
                )
        return outcomes

    def parse_all_approved(
        self, *, force: bool = False, parser_mode: str = "auto"
    ) -> list[ParseOutcome]:
        if self.candidate_manifest_path is None:
            return []
        return [
            self.parse_source(
                candidate.source_id, force=force, parser_mode=parser_mode
            )
            for candidate in load_candidates(self.candidate_manifest_path)
            if candidate.status is ReviewStatus.APPROVED
        ]

    def _candidate(self, source_id: str) -> CandidateSource | None:
        if self.candidate_manifest_path is None or not self.candidate_manifest_path.exists():
            return None
        return next(
            (
                candidate
                for candidate in load_candidates(self.candidate_manifest_path)
                if candidate.source_id == source_id
            ),
            None,
        )

    def _document(
        self,
        record,
        parser_name,
        parser_version,
        parsed,
        markdown,
        quality_score,
        candidate,
        parse_fingerprint,
    ) -> NormalizedDocument:
        fingerprint = "|".join(
            (
                record.source_id,
                record.content_hash,
                parser_name,
                parser_version,
                NORMALIZATION_VERSION,
            )
        )
        document_id = (
            f"doc-{record.source_id}-"
            f"{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:20]}"
        )
        metadata = {
            "parser_metadata": parsed.metadata,
            "source_metadata": candidate.model_dump(mode="json") if candidate else {},
            "parse_fingerprint": parse_fingerprint,
        }
        return NormalizedDocument(
            document_id=document_id,
            source_id=record.source_id,
            title=candidate.title if candidate else parsed.title,
            author=(candidate.author if candidate and candidate.author else parsed.author),
            published_at=(
                candidate.published_at
                if candidate and candidate.published_at
                else parsed.published_at
            ),
            language=parsed.language,
            original_file_type=self.detector.detect(record),
            raw_path=record.raw_path,
            parser_name=parser_name,
            parser_version=parser_version,
            normalization_version=NORMALIZATION_VERSION,
            raw_content_hash=record.content_hash,
            markdown=markdown,
            blocks=parsed.blocks,
            tables=parsed.tables,
            images=parsed.images,
            metadata=metadata,
            parse_quality_score=quality_score,
            corpus_version=record.corpus_version,
        )

    def _failed_outcome(
        self, source_id: str, parser_name: str, warning: str, started: float
    ) -> ParseOutcome:
        report = self.quality_evaluator.failure(
            source_id, parser_name, warning, self._elapsed_ms(started)
        )
        output = self.writer.write_failure(source_id, report)
        return ParseOutcome(
            source_id=source_id,
            status=OutcomeStatus.FAILED,
            parser_name=parser_name,
            quality_score=0,
            output_directory=output.relative_to(self.project_root).as_posix(),
            detail=warning,
        )

    def _unsupported_outcome(
        self,
        source_id: str,
        status: OutcomeStatus,
        warning: str,
        started: float,
    ) -> ParseOutcome:
        report = self.quality_evaluator.failure(
            source_id, "unselected", warning, self._elapsed_ms(started)
        )
        output = self.writer.write_failure(source_id, report)
        return ParseOutcome(
            source_id=source_id,
            status=status,
            parser_name=None,
            quality_score=0,
            output_directory=output.relative_to(self.project_root).as_posix(),
            detail=warning,
        )

    def _relative_output(self, source_id: str) -> str:
        return self.writer.output_directory(source_id).relative_to(
            self.project_root
        ).as_posix()

    @staticmethod
    def _error_code(exc: Exception) -> str:
        value = str(exc).splitlines()[0].strip()
        return value[:200] if value else type(exc).__name__

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return max(0.0, (time.perf_counter() - started) * 1_000)


def create_default_service(
    project_root: Path,
    *,
    raw_manifest_path: Path | None = None,
    candidate_manifest_path: Path | None = None,
) -> ParseNormalizeService:
    root = project_root.resolve()
    detector = FileTypeDetector()
    registry = ParserRegistry(
        [HtmlParser(), MarkdownParser(), TxtParser(), JsonParser()]
    )
    return ParseNormalizeService(
        project_root=root,
        raw_manifest_path=raw_manifest_path
        or root / "data" / "raw" / "sources_manifest.jsonl",
        candidate_manifest_path=candidate_manifest_path
        if candidate_manifest_path is not None
        else root
        / "data"
        / "source_ingestion"
        / "jay_chou_corpus_v1"
        / "sources_candidates.json",
        loader=RawSnapshotLoader(root),
        router=ParserRouter(detector, registry),
        detector=detector,
        normalizer=MarkdownNormalizer(),
        quality_evaluator=ParseQualityEvaluator(),
        writer=AtomicNormalizedWriter(root),
    )

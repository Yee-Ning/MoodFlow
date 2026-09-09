import unittest

from app.rag.parse_normalize.base import ParserError
from app.rag.parse_normalize.models import (
    BlockType,
    DocumentBlock,
    ParseStatus,
    ParserResult,
)
from app.rag.parse_normalize.pdf.adapters import PDFAdapterOutput, PDFParserAdapter
from app.rag.parse_normalize.pdf.cascade import PdfCascadeParser
from app.rag.parse_normalize.pdf.inspector import PDFInspector
from app.rag.parse_normalize.pdf.models import PDFBackend
from app.rag.parse_normalize.pdf.quality import (
    PDFParseQualityConfig,
    PDFParseQualityEvaluator,
)
from tests.test_pdf_optional_adapters import FIXTURE_ROOT, parse_input_for


def result_with(
    text: str, *, duplicate: bool = False, pages: tuple[int, ...] = (1,)
) -> ParserResult:
    values = [
        (f"{text} Page {page}." if len(pages) > 1 else text, page)
        for page in pages
    ]
    if duplicate:
        values *= 2
    return ParserResult(
        blocks=[
            DocumentBlock(
                block_id=f"quality-b{index:06d}",
                block_type=BlockType.PARAGRAPH,
                text=value,
                order_index=index,
                metadata={"page_number": page},
            )
            for index, (value, page) in enumerate(values)
        ]
    )


class StubAdapter(PDFParserAdapter):
    def __init__(self, name: str, output: ParserResult | Exception) -> None:
        self.parser_name = name
        self.output = output
        self.calls = 0

    def parse(self, parse_input, inspection):
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return PDFAdapterOutput(
            parser_name=self.parser_name,
            parser_version="test-1",
            result=self.output,
            ocr_used=inspection.suspected_scan,
        )


class PDFQualityTests(unittest.TestCase):
    def test_metrics_cover_pdf_failure_signals_and_thresholds_are_configurable(self) -> None:
        inspection = PDFInspector().inspect(
            (FIXTURE_ROOT / "raw" / "simple-text.pdf").read_bytes()
        )
        evaluator = PDFParseQualityEvaluator(
            PDFParseQualityConfig(
                quality_pass_threshold=0.8,
                quality_fallback_threshold=0.3,
            )
        )
        clean = evaluator.evaluate(
            result_with(
                "A complete native-text paragraph with stable line structure. " * 5,
                pages=(1, 2),
            ),
            inspection,
            parser_error_count=0,
        )
        damaged = evaluator.evaluate(
            result_with("x\n\ufffd\n\ufffd", duplicate=True),
            inspection,
            parser_error_count=1,
        )

        self.assertGreaterEqual(clean.quality_score, 0.8)
        self.assertGreater(clean.quality_score, damaged.quality_score)
        self.assertGreater(damaged.metrics.replacement_char_ratio, 0)
        self.assertGreater(damaged.metrics.suspicious_line_break_ratio, 0)
        self.assertGreater(damaged.metrics.duplicated_line_ratio, 0)
        self.assertEqual(damaged.metrics.parser_error_count, 1)
        self.assertEqual(clean.metrics.chars_per_page, clean.metrics.text_length / 2)
        with self.assertRaises(ValueError):
            PDFParseQualityConfig(
                quality_pass_threshold=0.2,
                quality_fallback_threshold=0.8,
            )


class PDFCascadeTests(unittest.TestCase):
    def _cascade(self, primary, fallback, config=None):
        return PdfCascadeParser(
            adapters={
                PDFBackend.PYMUPDF4LLM: primary,
                PDFBackend.MINERU: primary,
                PDFBackend.DOCLING: fallback,
            },
            quality_evaluator=PDFParseQualityEvaluator(config),
        )

    def test_clean_primary_passes_without_fallback(self) -> None:
        primary = StubAdapter(
            "pymupdf4llm",
            result_with("A complete high quality paragraph. " * 20, pages=(1, 2)),
        )
        fallback = StubAdapter("docling", result_with("fallback" * 100))
        parse_input = parse_input_for("simple-text.pdf", "pdf-cascade-pass")

        execution = self._cascade(primary, fallback).execute(parse_input)

        self.assertIs(execution.report.status, ParseStatus.PASS)
        self.assertEqual(execution.selected_parser, "pymupdf4llm")
        self.assertEqual(len(execution.report.parser_attempts), 1)
        self.assertEqual(fallback.calls, 0)

    def test_weak_scanned_primary_uses_docling_fallback(self) -> None:
        primary = StubAdapter("mineru", result_with("x"))
        fallback = StubAdapter(
            "docling", result_with("Recovered scan paragraph. " * 25)
        )
        parse_input = parse_input_for("scanned.pdf", "pdf-cascade-scan")

        execution = self._cascade(primary, fallback).execute(parse_input)

        self.assertIs(execution.report.status, ParseStatus.FALLBACK_USED)
        self.assertEqual(execution.selected_parser, "docling")
        self.assertEqual(len(execution.report.parser_attempts), 2)
        self.assertIn("quality", execution.report.parser_attempts[1].fallback_reason)
        self.assertTrue(execution.report.parser_attempts[1].ocr_used)

    def test_primary_error_is_recorded_and_fallback_still_runs(self) -> None:
        primary = StubAdapter("pymupdf4llm", ParserError("primary_broke"))
        fallback = StubAdapter(
            "docling",
            result_with("Recovered native text paragraph. " * 25, pages=(1, 2)),
        )
        parse_input = parse_input_for("simple-text.pdf", "pdf-cascade-error")

        execution = self._cascade(primary, fallback).execute(parse_input)

        self.assertIs(execution.report.status, ParseStatus.FALLBACK_USED)
        self.assertEqual(execution.report.parser_attempts[0].error, "primary_broke")
        self.assertEqual(execution.report.metrics.parser_error_count, 1)

    def test_medium_final_result_requires_manual_review_and_empty_fails(self) -> None:
        config = PDFParseQualityConfig(
            quality_pass_threshold=0.95,
            quality_fallback_threshold=0.2,
        )
        parse_input = parse_input_for("simple-text.pdf", "pdf-cascade-review")
        medium = self._cascade(
            StubAdapter("pymupdf4llm", result_with("short paragraph")),
            StubAdapter("docling", result_with("short fallback paragraph")),
            config,
        ).execute(parse_input)
        failed = self._cascade(
            StubAdapter("pymupdf4llm", result_with("")),
            StubAdapter("docling", result_with("")),
            config,
        ).execute(parse_input)

        self.assertIs(medium.report.status, ParseStatus.MANUAL_REVIEW)
        self.assertIsNotNone(medium.result)
        self.assertIs(failed.report.status, ParseStatus.FAILED)
        self.assertIsNone(failed.result)

    def test_forced_mode_never_falls_back(self) -> None:
        primary = StubAdapter("mineru", result_with("x"))
        fallback = StubAdapter("docling", result_with("clean" * 100))
        parse_input = parse_input_for("complex-layout.pdf", "pdf-cascade-forced")

        execution = self._cascade(primary, fallback).execute(
            parse_input, parser_mode="mineru"
        )

        self.assertEqual(primary.calls, 1)
        self.assertEqual(fallback.calls, 0)
        self.assertNotEqual(execution.report.status, ParseStatus.FALLBACK_USED)


if __name__ == "__main__":
    unittest.main()

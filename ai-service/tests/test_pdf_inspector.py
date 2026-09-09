import unittest
from pathlib import Path

from app.rag.parse_normalize.pdf.inspector import (
    PDFInspectionError,
    PDFInspector,
    PDFInspectorConfig,
)
from app.rag.parse_normalize.pdf.models import (
    PDFBackend,
    PDFParserMode,
    PDFProfile,
)
from app.rag.parse_normalize.pdf.router import PDFParserRouter


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "pdf_parse" / "raw"


class PDFInspectorTests(unittest.TestCase):
    def test_native_text_pdf_is_classified_simple_without_ocr(self) -> None:
        inspection = PDFInspector().inspect(
            (FIXTURE_ROOT / "simple-text.pdf").read_bytes()
        )

        self.assertIs(inspection.profile, PDFProfile.SIMPLE_TEXT)
        self.assertEqual(inspection.page_count, 2)
        self.assertTrue(inspection.native_text_available)
        self.assertGreater(inspection.extracted_text_chars, 300)
        self.assertGreater(inspection.average_chars_per_page, 150)
        self.assertEqual(inspection.empty_text_page_ratio, 0)
        self.assertEqual(inspection.image_count, 0)
        self.assertFalse(inspection.suspected_scan)
        self.assertEqual([page.page_number for page in inspection.pages], [1, 2])

    def test_two_column_table_pdf_is_classified_complex(self) -> None:
        inspection = PDFInspector().inspect(
            (FIXTURE_ROOT / "complex-layout.pdf").read_bytes()
        )

        self.assertIs(inspection.profile, PDFProfile.COMPLEX_LAYOUT)
        self.assertEqual(inspection.page_count, 1)
        self.assertTrue(inspection.native_text_available)
        self.assertTrue(inspection.suspected_multi_column)
        self.assertTrue(inspection.table_hint)
        self.assertFalse(inspection.suspected_scan)

    def test_image_only_pdf_is_classified_scanned(self) -> None:
        inspection = PDFInspector().inspect(
            (FIXTURE_ROOT / "scanned.pdf").read_bytes()
        )

        self.assertIs(inspection.profile, PDFProfile.SCANNED)
        self.assertEqual(inspection.page_count, 1)
        self.assertFalse(inspection.native_text_available)
        self.assertEqual(inspection.extracted_text_chars, 0)
        self.assertEqual(inspection.empty_text_page_ratio, 1)
        self.assertEqual(inspection.image_count, 1)
        self.assertTrue(inspection.suspected_scan)

    def test_thresholds_are_configurable_and_validated(self) -> None:
        strict = PDFInspector(
            PDFInspectorConfig(simple_min_average_chars_per_page=10_000)
        ).inspect((FIXTURE_ROOT / "simple-text.pdf").read_bytes())

        self.assertIs(strict.profile, PDFProfile.UNKNOWN)
        with self.assertRaises(ValueError):
            PDFInspectorConfig(scan_min_empty_page_ratio=1.1)
        with self.assertRaises(ValueError):
            PDFInspectorConfig(empty_page_max_chars=-1)

    def test_malformed_pdf_is_an_explicit_inspection_error(self) -> None:
        with self.assertRaisesRegex(PDFInspectionError, "pdf_inspection_failed"):
            PDFInspector().inspect(b"not a pdf")


class PDFParserRouterTests(unittest.TestCase):
    def test_auto_routes_by_profile(self) -> None:
        router = PDFParserRouter()

        self.assertIs(
            router.initial_parser(PDFProfile.SIMPLE_TEXT, PDFParserMode.AUTO),
            PDFBackend.PYMUPDF4LLM,
        )
        self.assertIs(
            router.initial_parser(PDFProfile.COMPLEX_LAYOUT, PDFParserMode.AUTO),
            PDFBackend.MINERU,
        )
        self.assertIs(
            router.initial_parser(PDFProfile.SCANNED, PDFParserMode.AUTO),
            PDFBackend.MINERU,
        )
        self.assertIs(
            router.initial_parser(PDFProfile.UNKNOWN, PDFParserMode.AUTO),
            PDFBackend.PYMUPDF4LLM,
        )

    def test_explicit_mode_always_selects_requested_backend(self) -> None:
        router = PDFParserRouter()

        for mode, expected in (
            (PDFParserMode.PYMUPDF, PDFBackend.PYMUPDF4LLM),
            (PDFParserMode.MINERU, PDFBackend.MINERU),
            (PDFParserMode.DOCLING, PDFBackend.DOCLING),
        ):
            with self.subTest(mode=mode):
                self.assertIs(
                    router.initial_parser(PDFProfile.SCANNED, mode), expected
                )


if __name__ == "__main__":
    unittest.main()

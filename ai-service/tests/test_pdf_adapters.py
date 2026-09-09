import hashlib
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.rag.parse_normalize.base import ParserError
from app.rag.parse_normalize.models import BlockType, ParseInput
from app.rag.parse_normalize.pdf.adapters import (
    PDFParserAdapter,
    PyMuPDF4LLMAdapter,
    VLMPageParser,
)
from app.rag.parse_normalize.pdf.inspector import PDFInspector
from app.rag.source_ingestion.models import RawSnapshotRecord, SourceQuality


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "pdf_parse" / "raw"


def parse_input_for(path: Path, source_id: str) -> ParseInput:
    raw_bytes = path.read_bytes()
    return ParseInput(
        snapshot=RawSnapshotRecord(
            source_id=source_id,
            original_url="https://example.com/test.pdf",
            content_type="application/pdf",
            content_hash=f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}",
            fetched_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
            raw_path=f"tests/fixtures/pdf_parse/raw/{path.name}",
            http_status=200,
            source_quality=SourceQuality.HIGH,
            corpus_version="jay-chou-corpus-v1",
        ),
        raw_bytes=raw_bytes,
    )


class PDFAdapterInterfaceTests(unittest.TestCase):
    def test_page_level_fallback_hooks_are_reserved_but_not_implemented(self) -> None:
        with self.assertRaisesRegex(
            ParserError, "page_level_fallback_not_implemented"
        ):
            PDFParserAdapter.parse_page(object(), 1)
        with self.assertRaisesRegex(
            ParserError, "page_level_fallback_not_implemented"
        ):
            PDFParserAdapter.reparse_failed_pages(object(), [1])

    def test_vlm_page_parser_is_disabled_by_default(self) -> None:
        with self.assertRaisesRegex(ParserError, "vlm_page_parser_disabled"):
            VLMPageParser().parse_page(object(), 1)


class PyMuPDF4LLMAdapterTests(unittest.TestCase):
    def test_native_text_pdf_becomes_page_traceable_parser_result(self) -> None:
        parse_input = parse_input_for(
            FIXTURE_ROOT / "simple-text.pdf", "pdf-simple-text"
        )
        inspection = PDFInspector().inspect(parse_input.raw_bytes)

        output = PyMuPDF4LLMAdapter().parse(parse_input, inspection)

        self.assertEqual(output.parser_name, "pymupdf4llm")
        self.assertFalse(output.ocr_used)
        self.assertEqual(output.backend_metadata["page_count"], 2)
        self.assertEqual(output.result.title, "untitled")
        self.assertGreaterEqual(len(output.result.blocks), 10)
        self.assertEqual(
            {block.metadata["page_number"] for block in output.result.blocks},
            {1, 2},
        )
        self.assertEqual(
            [block.order_index for block in output.result.blocks],
            list(range(len(output.result.blocks))),
        )
        self.assertTrue(
            any(
                block.block_type is BlockType.HEADING
                and block.text == "Music Production Notes"
                for block in output.result.blocks
            )
        )
        self.assertTrue(
            any("bbox" in block.metadata for block in output.result.blocks)
        )
        self.assertEqual(output.artifacts, [])

    def test_adapter_rejects_non_pdf_bytes_as_an_explicit_backend_error(self) -> None:
        valid = parse_input_for(FIXTURE_ROOT / "simple-text.pdf", "pdf-bad")
        bad = valid.model_copy(update={"raw_bytes": b"not a pdf"})

        with self.assertRaisesRegex(ParserError, "pymupdf4llm_parse_failed"):
            PyMuPDF4LLMAdapter().parse(bad, PDFInspector().inspect(valid.raw_bytes))


if __name__ == "__main__":
    unittest.main()

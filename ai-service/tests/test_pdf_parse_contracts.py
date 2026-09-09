import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.rag.parse_normalize.base import BaseParser
from app.rag.parse_normalize.models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    DocumentImage,
    DocumentTable,
    NormalizedDocument,
    OutputArtifact,
    ParseInput,
    ParseMetrics,
    ParseReport,
    ParserExecution,
    ParserResult,
)
from app.rag.parse_normalize.writer import (
    AtomicNormalizedWriter,
    UnsafeOutputArtifactError,
)
from app.rag.source_ingestion.models import RawSnapshotRecord


def _snapshot() -> RawSnapshotRecord:
    return RawSnapshotRecord(
        source_id="pdf-source",
        original_url="https://example.test/source.pdf",
        content_type="application/pdf",
        content_hash="sha256:" + "0" * 64,
        fetched_at=datetime(2026, 9, 9, tzinfo=UTC),
        raw_path="data/raw/pdf/source.pdf",
        http_status=200,
        source_quality="high",
        corpus_version="fixture-v1",
    )


def _block() -> DocumentBlock:
    return DocumentBlock(
        block_id="pdf-source-b000000",
        block_type=BlockType.PARAGRAPH,
        text="PDF body",
        order_index=0,
        metadata={"page_number": 1, "bbox": [10, 20, 30, 40]},
    )


def _report() -> ParseReport:
    return ParseReport(
        source_id="pdf-source",
        parser_name="fixture",
        selected_parser="fixture",
        quality_score=1,
        status="pass",
        warnings=[],
        metrics=ParseMetrics(
            text_length=8,
            heading_count=0,
            paragraph_count=1,
            table_count=0,
            empty_content=False,
            garbled_char_ratio=0,
            duplicated_line_ratio=0,
        ),
        elapsed_ms=1,
    )


def _document() -> NormalizedDocument:
    return NormalizedDocument(
        document_id="doc-pdf-source-fixture",
        source_id="pdf-source",
        title="PDF Fixture",
        language="en",
        original_file_type="pdf",
        raw_path="data/raw/pdf/source.pdf",
        parser_name="fixture",
        parser_version="1.0.0",
        normalization_version="1.0.0",
        raw_content_hash="sha256:" + "0" * 64,
        markdown="PDF body\n",
        blocks=[_block()],
        tables=[],
        images=[
            DocumentImage(
                image_id="image-1",
                page_number=1,
                bbox=(10, 20, 30, 40),
                path="images/cover.png",
                content_type="image/png",
                caption="Cover",
                metadata={},
            )
        ],
        metadata={"parse_fingerprint": "fixture:1.0.0:auto"},
        parse_quality_score=1,
        corpus_version="fixture-v1",
    )


class FixtureParser(BaseParser):
    parser_name = "fixture"
    parser_version = "1.0.0"
    supported_file_types = frozenset({DetectedFileType.PDF})

    def parse(self, parse_input: ParseInput) -> ParserResult:
        return ParserResult(
            language="en",
            blocks=[_block()],
            metadata={"source": "fixture"},
        )


class SharedPdfContractTests(unittest.TestCase):
    def test_base_parser_default_execution_preserves_existing_adapter_contract(self) -> None:
        parse_input = ParseInput(snapshot=_snapshot(), raw_bytes=b"%PDF")

        execution = FixtureParser().execute(parse_input)

        self.assertIsInstance(execution, ParserExecution)
        self.assertEqual(execution.selected_parser, "fixture")
        self.assertEqual(execution.selected_parser_version, "1.0.0")
        self.assertEqual(execution.result.blocks[0].text, "PDF body")
        self.assertIsNone(execution.report)
        self.assertEqual(execution.artifacts, [])
        self.assertEqual(FixtureParser().fingerprint(), "fixture:1.0.0:auto")

    def test_non_pdf_report_fields_default_without_changing_old_payloads(self) -> None:
        report = ParseReport.model_validate(
            {
                "source_id": "html-source",
                "parser_name": "html",
                "quality_score": 1,
                "status": "pass",
                "warnings": [],
                "metrics": {
                    "text_length": 10,
                    "heading_count": 1,
                    "paragraph_count": 1,
                    "table_count": 0,
                    "empty_content": False,
                    "garbled_char_ratio": 0,
                    "duplicated_line_ratio": 0,
                },
                "elapsed_ms": 1,
            }
        )

        self.assertIsNone(report.selected_parser)
        self.assertEqual(report.parser_attempts, [])
        self.assertEqual(report.metrics.chars_per_page, 0)
        self.assertEqual(report.metrics.parser_error_count, 0)

    def test_document_rejects_duplicate_table_and_image_ids(self) -> None:
        table = DocumentTable(
            table_id="table-1",
            page_number=1,
            bbox=None,
            markdown="| A |\n| --- |\n| B |",
            rows=[["A"], ["B"]],
            metadata={},
        )
        image = DocumentImage(
            image_id="image-1",
            page_number=1,
            bbox=None,
            path="images/one.png",
            content_type="image/png",
            metadata={},
        )
        payload = _document().model_dump()

        with self.assertRaises(ValidationError):
            NormalizedDocument.model_validate(
                {**payload, "tables": [table, table.model_copy()]}
            )
        with self.assertRaises(ValidationError):
            NormalizedDocument.model_validate(
                {**payload, "images": [image, image.model_copy()]}
            )


class AtomicPdfArtifactWriterTests(unittest.TestCase):
    def test_writer_persists_only_confined_image_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            writer = AtomicNormalizedWriter(root)
            artifact = OutputArtifact(
                relative_path="images/cover.png",
                content=b"exact-image-bytes",
                content_type="image/png",
            )

            writer.write(_document(), _report(), artifacts=[artifact])

            output = root / "data" / "normalized" / "pdf-source"
            self.assertEqual(
                (output / "images" / "cover.png").read_bytes(),
                b"exact-image-bytes",
            )
            self.assertTrue((output / "document.json").exists())

    def test_writer_rejects_absolute_parent_and_non_image_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            writer = AtomicNormalizedWriter(Path(temporary_directory))
            unsafe_paths = (
                "/absolute.png",
                "../escape.png",
                "images/../escape.png",
                "other/file.png",
            )

            for unsafe_path in unsafe_paths:
                with self.subTest(unsafe_path=unsafe_path):
                    with self.assertRaises(UnsafeOutputArtifactError):
                        writer.write(
                            _document(),
                            _report(),
                            artifacts=[
                                OutputArtifact(
                                    relative_path=unsafe_path,
                                    content=b"unsafe",
                                    content_type="image/png",
                                )
                            ],
                        )

    def test_failure_removes_stale_normalized_images_but_not_raw_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_path = root / "data" / "raw" / "pdf" / "source.pdf"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_bytes(b"immutable raw")
            writer = AtomicNormalizedWriter(root)
            writer.write(
                _document(),
                _report(),
                artifacts=[
                    OutputArtifact(
                        relative_path="images/cover.png",
                        content=b"image",
                        content_type="image/png",
                    )
                ],
            )

            writer.write_failure("pdf-source", _report())

            output = root / "data" / "normalized" / "pdf-source"
            self.assertFalse((output / "images").exists())
            self.assertFalse((output / "document.json").exists())
            self.assertEqual(raw_path.read_bytes(), b"immutable raw")


if __name__ == "__main__":
    unittest.main()

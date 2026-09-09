import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.rag.parse_normalize.base import BaseParser
from app.rag.parse_normalize.detection import FileTypeDetector
from app.rag.parse_normalize.models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    NormalizedDocument,
    ParseInput,
    ParserResult,
)
from app.rag.parse_normalize.registry import DuplicateParserError, ParserRegistry
from app.rag.parse_normalize.router import (
    ParserRouter,
    UnsupportedCurrentStageError,
    UnsupportedFileTypeError,
)
from app.rag.source_ingestion.models import RawSnapshotRecord


def _snapshot(content_type: str, raw_path: str) -> RawSnapshotRecord:
    return RawSnapshotRecord(
        source_id="test-source",
        original_url="https://example.test/source",
        content_type=content_type,
        content_hash="sha256:" + "0" * 64,
        fetched_at=datetime(2026, 9, 9, tzinfo=UTC),
        raw_path=raw_path,
        http_status=200,
        source_quality="high",
        corpus_version="jay-chou-corpus-v1",
    )


def _block(order_index: int, block_id: str | None = None) -> DocumentBlock:
    return DocumentBlock(
        block_id=block_id or f"block-{order_index}",
        block_type=BlockType.PARAGRAPH,
        text=f"paragraph {order_index}",
        level=None,
        order_index=order_index,
        parent_block_id=None,
        metadata={},
    )


def _document_payload(blocks: list[DocumentBlock]) -> dict[str, object]:
    return {
        "document_id": "doc-test-source-000000000000",
        "source_id": "test-source",
        "title": "Test title",
        "author": None,
        "published_at": None,
        "language": "en",
        "original_file_type": "html",
        "raw_path": "data/raw/html/source.html",
        "parser_name": "stub",
        "parser_version": "1.0.0",
        "normalization_version": "1.0.0",
        "raw_content_hash": "sha256:" + "0" * 64,
        "markdown": "paragraph 0\n",
        "blocks": blocks,
        "metadata": {},
        "parse_quality_score": 1.0,
        "corpus_version": "jay-chou-corpus-v1",
    }


class StubParser(BaseParser):
    parser_name = "stub"
    parser_version = "1.0.0"

    def __init__(self, *file_types: DetectedFileType) -> None:
        self.supported_file_types = frozenset(file_types)

    def parse(self, parse_input: ParseInput) -> ParserResult:
        return ParserResult(
            title=None,
            author=None,
            published_at=None,
            language="und",
            blocks=[],
            metadata={},
        )


class FileTypeDetectorTests(unittest.TestCase):
    def test_specific_content_type_takes_precedence_over_extension(self) -> None:
        detector = FileTypeDetector()

        detected = detector.detect(
            _snapshot("text/markdown; charset=utf-8", "data/raw/txt/source.txt")
        )

        self.assertIs(detected, DetectedFileType.MARKDOWN)

    def test_generic_content_type_uses_extension(self) -> None:
        detected = FileTypeDetector().detect(
            _snapshot("application/octet-stream", "data/raw/json/source.json")
        )

        self.assertIs(detected, DetectedFileType.JSON)

    def test_structured_json_suffix_is_detected(self) -> None:
        detected = FileTypeDetector().detect(
            _snapshot("application/vnd.api+json", "data/raw/source.bin")
        )

        self.assertIs(detected, DetectedFileType.JSON)


class ParserRegistryTests(unittest.TestCase):
    def test_router_selects_registered_parser(self) -> None:
        parser = StubParser(DetectedFileType.HTML)
        router = ParserRouter(FileTypeDetector(), ParserRegistry([parser]))

        selected = router.select(_snapshot("text/html", "data/raw/html/source.html"))

        self.assertIs(selected, parser)

    def test_duplicate_registration_is_rejected(self) -> None:
        registry = ParserRegistry([StubParser(DetectedFileType.HTML)])

        with self.assertRaises(DuplicateParserError):
            registry.register(StubParser(DetectedFileType.HTML))

    def test_pdf_is_explicitly_unsupported_for_current_stage(self) -> None:
        router = ParserRouter(FileTypeDetector(), ParserRegistry())

        with self.assertRaisesRegex(
            UnsupportedCurrentStageError, "unsupported_for_current_stage"
        ):
            router.select(_snapshot("application/pdf", "data/raw/pdf/source.pdf"))

    def test_unknown_type_is_rejected(self) -> None:
        router = ParserRouter(FileTypeDetector(), ParserRegistry())

        with self.assertRaises(UnsupportedFileTypeError):
            router.select(
                _snapshot("application/octet-stream", "data/raw/source.unknown")
            )


class NormalizedDocumentTests(unittest.TestCase):
    def test_non_contiguous_block_order_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            NormalizedDocument.model_validate(
                _document_payload([_block(0), _block(2)])
            )

    def test_duplicate_block_ids_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            NormalizedDocument.model_validate(
                _document_payload([_block(0, "duplicate"), _block(1, "duplicate")])
            )

    def test_valid_document_is_frozen_and_serializable(self) -> None:
        document = NormalizedDocument.model_validate(_document_payload([_block(0)]))

        self.assertEqual(document.blocks[0].text, "paragraph 0")
        self.assertEqual(
            document.model_dump(mode="json")["original_file_type"], "html"
        )
        with self.assertRaises(ValidationError):
            document.title = "changed"


if __name__ == "__main__":
    unittest.main()

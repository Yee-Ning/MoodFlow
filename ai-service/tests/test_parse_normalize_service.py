import hashlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.rag.parse_normalize.base import BaseParser, ParserError
from app.rag.parse_normalize.detection import FileTypeDetector
from app.rag.parse_normalize.manifest import (
    ContentHashMismatchError,
    RawPathOutsideProjectError,
    RawSnapshotLoader,
)
from app.rag.parse_normalize.models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    NormalizedDocument,
    ParseInput,
    ParseReport,
    ParserResult,
)
from app.rag.parse_normalize.normalizer import MarkdownNormalizer
from app.rag.parse_normalize.quality import ParseQualityConfig, ParseQualityEvaluator
from app.rag.parse_normalize.registry import ParserRegistry
from app.rag.parse_normalize.router import ParserRouter
from app.rag.parse_normalize.service import (
    OutcomeStatus,
    ParseNormalizeService,
)
from app.rag.parse_normalize.writer import AtomicNormalizedWriter
from app.rag.source_ingestion.models import RawSnapshotRecord


def _paragraph(index: int, text: str) -> DocumentBlock:
    return DocumentBlock(
        block_id=f"source-b{index:06d}",
        block_type=BlockType.PARAGRAPH,
        text=text,
        level=None,
        order_index=index,
        parent_block_id=None,
        metadata={},
    )


def _append_snapshot(
    root: Path,
    *,
    source_id: str,
    content: bytes,
    content_type: str = "text/html; charset=utf-8",
    extension: str = "html",
    declared_hash: str | None = None,
) -> tuple[Path, Path, RawSnapshotRecord]:
    raw_path = root / "data" / "raw" / extension / f"{source_id}.{extension}"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    record = RawSnapshotRecord(
        source_id=source_id,
        original_url=f"https://example.test/{source_id}.{extension}",
        content_type=content_type,
        content_hash=declared_hash or f"sha256:{digest}",
        fetched_at=datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
        raw_path=raw_path.relative_to(root).as_posix(),
        http_status=200,
        source_quality="high",
        corpus_version="jay-chou-corpus-v1",
    )
    manifest_path = root / "data" / "raw" / "sources_manifest.jsonl"
    with manifest_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False))
        handle.write("\n")
    return raw_path, manifest_path, record


class CountingParser(BaseParser):
    parser_name = "counting-html"
    parser_version = "1.2.3"
    supported_file_types = frozenset({DetectedFileType.HTML})

    def __init__(
        self,
        *,
        fail: bool = False,
        empty: bool = False,
        fail_source_ids: frozenset[str] = frozenset(),
    ) -> None:
        self.calls = 0
        self.fail = fail
        self.empty = empty
        self.fail_source_ids = fail_source_ids

    def parse(self, parse_input: ParseInput) -> ParserResult:
        self.calls += 1
        if self.fail or parse_input.snapshot.source_id in self.fail_source_ids:
            raise ParserError("fixture_parser_failed")
        blocks = [] if self.empty else [_paragraph(0, "Parsed body")]
        return ParserResult(
            title="Parsed title",
            author=None,
            published_at=None,
            language="en",
            blocks=blocks,
            metadata={"fixture": True},
        )


def _service(root: Path, parser: BaseParser) -> ParseNormalizeService:
    return ParseNormalizeService(
        project_root=root,
        raw_manifest_path=root / "data" / "raw" / "sources_manifest.jsonl",
        candidate_manifest_path=None,
        loader=RawSnapshotLoader(root),
        router=ParserRouter(FileTypeDetector(), ParserRegistry([parser])),
        detector=FileTypeDetector(),
        normalizer=MarkdownNormalizer(),
        quality_evaluator=ParseQualityEvaluator(),
        writer=AtomicNormalizedWriter(root),
    )


class ParseQualityEvaluatorTests(unittest.TestCase):
    def test_duplicated_lines_lower_score_and_create_warning(self) -> None:
        evaluator = ParseQualityEvaluator(
            ParseQualityConfig(
                warning_score=0.9,
                duplicated_line_warning_ratio=0.4,
            )
        )

        report = evaluator.evaluate(
            "source",
            "txt",
            [_paragraph(0, "same\nsame\nsame\nunique")],
            12.5,
        )

        self.assertEqual(report.status.value, "warning")
        self.assertEqual(report.metrics.duplicated_line_ratio, 0.5)
        self.assertIn("high_duplicated_line_ratio", report.warnings)
        self.assertLess(report.quality_score, 0.9)

    def test_empty_content_is_failed(self) -> None:
        report = ParseQualityEvaluator().evaluate("source", "html", [], 1.0)

        self.assertEqual(report.status.value, "failed")
        self.assertEqual(report.quality_score, 0)
        self.assertTrue(report.metrics.empty_content)
        self.assertIn("empty_content", report.warnings)

    def test_garbled_characters_are_counted(self) -> None:
        report = ParseQualityEvaluator().evaluate(
            "source", "txt", [_paragraph(0, "valid\ufffd\ue000")], 1.0
        )

        self.assertEqual(report.metrics.garbled_char_ratio, 2 / 7)
        self.assertIn("high_garbled_char_ratio", report.warnings)


class RawSnapshotLoaderTests(unittest.TestCase):
    def test_enriched_raw_corpus_manifest_preserves_source_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, manifest_path, record = _append_snapshot(
                root, source_id="enriched-source", content=b"<p>Raw</p>"
            )
            enriched = {
                **record.model_dump(mode="json"),
                "title": "Corpus title",
                "publisher": "Corpus Publisher",
                "author": "Corpus Author",
                "file_type": "html",
                "topics": ["音乐风格"],
            }
            manifest_path.write_text(
                json.dumps(enriched, ensure_ascii=False) + "\n", encoding="utf-8"
            )

            loaded = RawSnapshotLoader(root).load_latest(
                manifest_path, "enriched-source"
            )

            self.assertEqual(loaded.record.source_id, "enriched-source")
            self.assertEqual(loaded.source_metadata["publisher"], "Corpus Publisher")
            self.assertEqual(loaded.source_metadata["topics"], ["音乐风格"])

    def test_hash_mismatch_is_rejected_without_mutating_raw_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_path, manifest_path, _ = _append_snapshot(
                root,
                source_id="hash-source",
                content=b"original",
                declared_hash="sha256:" + "0" * 64,
            )
            before = raw_path.read_bytes()

            with self.assertRaises(ContentHashMismatchError):
                RawSnapshotLoader(root).load_latest(manifest_path, "hash-source")

            self.assertEqual(raw_path.read_bytes(), before)

    def test_raw_path_outside_project_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, manifest_path, record = _append_snapshot(
                root, source_id="escape-source", content=b"safe"
            )
            escaped = record.model_copy(update={"raw_path": "../outside.html"})
            manifest_path.write_text(
                escaped.model_dump_json() + "\n", encoding="utf-8"
            )

            with self.assertRaises(RawPathOutsideProjectError):
                RawSnapshotLoader(root).load_latest(manifest_path, "escape-source")


class ParseNormalizeServiceTests(unittest.TestCase):
    def test_empty_exception_text_is_reported_by_exception_type(self) -> None:
        class EmptyErrorParser(CountingParser):
            def parse(self, parse_input: ParseInput) -> ParserResult:
                raise AssertionError()

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _append_snapshot(root, source_id="empty-error", content=b"<p>Raw</p>")

            outcome = _service(root, EmptyErrorParser()).parse_source("empty-error")

            self.assertEqual(outcome.status, OutcomeStatus.FAILED)
            self.assertEqual(outcome.detail, "AssertionError")

    def test_enriched_manifest_metadata_is_mapped_to_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _, manifest_path, record = _append_snapshot(
                root, source_id="v2-source", content=b"<p>Raw</p>"
            )
            enriched = {
                **record.model_dump(mode="json"),
                "title": "V2 source title",
                "publisher": "V2 Publisher",
                "author": "V2 Author",
                "file_type": "html",
                "topics": ["作品影响"],
            }
            manifest_path.write_text(
                json.dumps(enriched, ensure_ascii=False) + "\n", encoding="utf-8"
            )

            outcome = _service(root, CountingParser()).parse_source(
                "v2-source", manifest_path=manifest_path
            )

            document = NormalizedDocument.model_validate_json(
                (
                    root
                    / "data"
                    / "normalized"
                    / "v2-source"
                    / "document.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(outcome.status, OutcomeStatus.PARSED)
            self.assertEqual(document.title, "V2 source title")
            self.assertEqual(document.author, "V2 Author")
            self.assertEqual(
                document.metadata["source_metadata"]["publisher"], "V2 Publisher"
            )

    def test_success_writes_valid_document_markdown_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _append_snapshot(root, source_id="source", content=b"<h1>Raw</h1>")
            parser = CountingParser()

            outcome = _service(root, parser).parse_source("source")

            output = root / "data" / "normalized" / "source"
            document = NormalizedDocument.model_validate_json(
                (output / "document.json").read_text(encoding="utf-8")
            )
            report = ParseReport.model_validate_json(
                (output / "parse_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(outcome.status, OutcomeStatus.PARSED)
            self.assertEqual((output / "document.md").read_text(encoding="utf-8"), "Parsed body\n")
            self.assertEqual(document.parser_name, "counting-html")
            self.assertEqual(document.parse_quality_score, report.quality_score)
            self.assertEqual(parser.calls, 1)

    def test_matching_fingerprint_skips_and_force_reparses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _append_snapshot(root, source_id="source", content=b"<p>Raw</p>")
            parser = CountingParser()
            service = _service(root, parser)

            first = service.parse_source("source")
            skipped = service.parse_source("source")
            forced = service.parse_source("source", force=True)

            self.assertEqual(first.status, OutcomeStatus.PARSED)
            self.assertEqual(skipped.status, OutcomeStatus.SKIPPED)
            self.assertEqual(forced.status, OutcomeStatus.PARSED)
            self.assertEqual(parser.calls, 2)

    def test_non_pdf_parser_rejects_pdf_backend_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _append_snapshot(root, source_id="source", content=b"<p>Raw</p>")
            parser = CountingParser()

            outcome = _service(root, parser).parse_source(
                "source", parser_mode="mineru"
            )

            self.assertEqual(outcome.status, OutcomeStatus.FAILED)
            self.assertEqual(
                outcome.detail, "parser_override_only_supported_for_pdf"
            )
            self.assertEqual(parser.calls, 0)

    def test_parser_error_writes_failed_report_without_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _append_snapshot(root, source_id="failed-source", content=b"<p>Raw</p>")

            outcome = _service(root, CountingParser(fail=True)).parse_source(
                "failed-source"
            )

            output = root / "data" / "normalized" / "failed-source"
            report = ParseReport.model_validate_json(
                (output / "parse_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(outcome.status, OutcomeStatus.FAILED)
            self.assertEqual(report.status.value, "failed")
            self.assertIn("fixture_parser_failed", report.warnings)
            self.assertFalse((output / "document.json").exists())
            self.assertFalse((output / "document.md").exists())

    def test_pdf_is_reported_as_unsupported_for_current_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _append_snapshot(
                root,
                source_id="pdf-source",
                content=b"%PDF-1.7",
                content_type="application/pdf",
                extension="pdf",
            )

            outcome = _service(root, CountingParser()).parse_source("pdf-source")

            self.assertEqual(
                outcome.status, OutcomeStatus.UNSUPPORTED_FOR_CURRENT_STAGE
            )
            report_path = (
                root / "data" / "normalized" / "pdf-source" / "parse_report.json"
            )
            self.assertTrue(report_path.exists())
            self.assertFalse(report_path.with_name("document.json").exists())

    def test_manifest_batch_continues_after_one_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _append_snapshot(root, source_id="one", content=b"<p>One</p>")
            _, manifest_path, _ = _append_snapshot(
                root, source_id="two", content=b"<p>Two</p>"
            )
            parser = CountingParser(fail_source_ids=frozenset({"one"}))

            outcomes = _service(root, parser).parse_manifest(manifest_path)

            self.assertEqual(len(outcomes), 2)
            self.assertEqual(
                [outcome.status for outcome in outcomes],
                [OutcomeStatus.FAILED, OutcomeStatus.PARSED],
            )


if __name__ == "__main__":
    unittest.main()

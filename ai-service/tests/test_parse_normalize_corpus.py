import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from app.rag.parse_normalize.corpus import (
    CorpusParseRunner,
    CorpusParseReporter,
    RepresentativeBatchSelector,
)
from app.rag.parse_normalize.service import OutcomeStatus, ParseOutcome
import run_parse_corpus
from app.rag.parse_normalize.models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    NormalizedDocument,
    ParseMetrics,
    ParseReport,
    ParseStatus,
    ParserAttempt,
    ParserAttemptStatus,
)


def _append_source(
    root: Path,
    rows: list[dict[str, object]],
    *,
    source_id: str,
    file_type: str,
    publisher: str,
    source_type: str,
    size: int,
    album: str | None = None,
) -> None:
    extension = file_type
    content = ((source_id + " ") * max(1, size // (len(source_id) + 1))).encode()
    raw_path = root / "data" / "raw" / file_type / f"{source_id}.{extension}"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(content)
    rows.append(
        {
            "source_id": source_id,
            "original_url": f"https://example.test/{source_id}.{extension}",
            "content_type": {
                "html": "text/html",
                "json": "application/json",
                "pdf": "application/pdf",
            }[file_type],
            "content_hash": "sha256:" + hashlib.sha256(content).hexdigest(),
            "fetched_at": datetime(2026, 9, 10, tzinfo=UTC).isoformat(),
            "raw_path": raw_path.relative_to(root).as_posix(),
            "http_status": 200,
            "source_quality": "high",
            "corpus_version": "jay-chou-corpus-v1",
            "title": source_id,
            "publisher": publisher,
            "author": None,
            "file_type": file_type,
            "source_type": source_type,
            "source_tier": 2,
            "artist": "周杰伦",
            "album": album,
            "topics": ["音乐风格"],
        }
    )


class RepresentativeBatchSelectorTests(unittest.TestCase):
    def test_selection_is_deterministic_diverse_and_includes_every_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            rows: list[dict[str, object]] = []
            for index, source_type in enumerate(
                ("official_profile", "record_label", "interview", "media_article")
            ):
                _append_source(
                    root,
                    rows,
                    source_id=f"html-{index}",
                    file_type="html",
                    publisher=f"Publisher {index}",
                    source_type=source_type,
                    size=100 + index * 50,
                )
            for index in range(2):
                _append_source(
                    root,
                    rows,
                    source_id=f"pdf-{index}",
                    file_type="pdf",
                    publisher=f"PDF Publisher {index}",
                    source_type="academic_paper",
                    size=200,
                )
            _append_source(
                root,
                rows,
                source_id="json-artist",
                file_type="json",
                publisher="Wikidata",
                source_type="metadata_api",
                size=400,
            )
            _append_source(
                root,
                rows,
                source_id="json-album",
                file_type="json",
                publisher="Wikidata",
                source_type="metadata_api",
                size=200,
                album="范特西",
            )
            manifest = root / "data" / "raw" / "raw_corpus_manifest.jsonl"
            manifest.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            selector = RepresentativeBatchSelector(root, manifest)
            first = selector.select(html_count=3, json_count=2)
            second = selector.select(html_count=3, json_count=2)

            self.assertEqual(first, second)
            self.assertEqual(len(first), 7)
            self.assertEqual(
                {item.source_id for item in first if item.file_type == "pdf"},
                {"pdf-0", "pdf-1"},
            )
            html_publishers = {
                item.publisher for item in first if item.file_type == "html"
            }
            self.assertEqual(len(html_publishers), 3)
            self.assertTrue(all(item.selected_reason for item in first))
            self.assertTrue(all(item.expected_parser for item in first))


class CorpusParseReporterTests(unittest.TestCase):
    def test_report_and_manifest_keep_failures_and_fallback_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            rows: list[dict[str, object]] = []
            _append_source(
                root,
                rows,
                source_id="html-pass",
                file_type="html",
                publisher="Publisher A",
                source_type="media_article",
                size=200,
            )
            _append_source(
                root,
                rows,
                source_id="pdf-review",
                file_type="pdf",
                publisher="Publisher B",
                source_type="academic_paper",
                size=200,
            )
            manifest = root / "data" / "raw" / "raw_corpus_manifest.jsonl"
            manifest.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            self._write_result(root, rows[0], ParseStatus.PASS, fallback=False)
            self._write_result(
                root, rows[1], ParseStatus.MANUAL_REVIEW, fallback=True
            )

            reporter = CorpusParseReporter(root, manifest)
            manifest_rows, report = reporter.build()
            reporter.write(manifest_rows, report)

            self.assertEqual(report["total_sources"], 2)
            self.assertEqual(report["parse_success"], 1)
            self.assertEqual(report["manual_review"], 1)
            self.assertEqual(report["fallback_used"], 1)
            self.assertEqual(report["failed"], 0)
            self.assertTrue(report["raw_integrity"]["all_hashes_match"])
            self.assertTrue(manifest_rows[1].fallback_used)
            self.assertTrue(manifest_rows[1].manual_review)
            self.assertTrue(
                (root / "data/normalized/normalized_corpus_manifest.jsonl").is_file()
            )
            self.assertTrue(
                (root / "data/normalized/parse_corpus_report.md").is_file()
            )

    @staticmethod
    def _write_result(
        root: Path,
        raw: dict[str, object],
        status: ParseStatus,
        *,
        fallback: bool,
    ) -> None:
        output = root / "data" / "normalized" / str(raw["source_id"])
        output.mkdir(parents=True, exist_ok=True)
        block = DocumentBlock(
            block_id=f"{raw['source_id']}-b000000",
            block_type=BlockType.PARAGRAPH,
            text="Normalized content",
            order_index=0,
            metadata={"page_number": 1} if raw["file_type"] == "pdf" else {},
        )
        attempts = []
        if fallback:
            attempts = [
                ParserAttempt(
                    parser="mineru",
                    parser_version="1",
                    status=ParserAttemptStatus.UNAVAILABLE,
                    error="mineru_backend_unavailable",
                    elapsed_ms=1,
                ),
                ParserAttempt(
                    parser="pymupdf4llm",
                    parser_version="1",
                    status=ParserAttemptStatus.SUCCESS,
                    quality_score=0.8,
                    elapsed_ms=2,
                    fallback_reason="native_text_recovery",
                ),
            ]
        report = ParseReport(
            source_id=str(raw["source_id"]),
            parser_name="pymupdf4llm" if fallback else "html",
            selected_parser="pymupdf4llm" if fallback else None,
            parser_attempts=attempts,
            quality_score=0.8 if fallback else 1.0,
            status=status,
            warnings=(
                ["complex_layout_native_text_recovery_requires_review"]
                if fallback
                else []
            ),
            metrics=ParseMetrics(
                text_length=len(block.text),
                heading_count=0,
                paragraph_count=1,
                table_count=0,
                empty_content=False,
                garbled_char_ratio=0,
                duplicated_line_ratio=0,
            ),
            elapsed_ms=3,
        )
        document = NormalizedDocument(
            document_id=f"doc-{raw['source_id']}",
            source_id=str(raw["source_id"]),
            title=str(raw["title"]),
            language="und",
            original_file_type=DetectedFileType(str(raw["file_type"])),
            raw_path=str(raw["raw_path"]),
            parser_name=report.parser_name,
            parser_version="1",
            normalization_version="1.0.0",
            raw_content_hash=str(raw["content_hash"]),
            markdown="Normalized content\n",
            blocks=[block],
            metadata={"source_metadata": raw, "parser_metadata": {}},
            parse_quality_score=report.quality_score,
            corpus_version="jay-chou-corpus-v1",
        )
        (output / "document.md").write_text(document.markdown, encoding="utf-8")
        (output / "document.json").write_text(
            document.model_dump_json(indent=2), encoding="utf-8"
        )
        (output / "parse_report.json").write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )


class CorpusParseRunnerTests(unittest.TestCase):
    def test_full_batch_continues_and_publishes_report_from_actual_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            rows: list[dict[str, object]] = []
            _append_source(
                root,
                rows,
                source_id="html-pass",
                file_type="html",
                publisher="Publisher A",
                source_type="media_article",
                size=200,
            )
            _append_source(
                root,
                rows,
                source_id="json-pass",
                file_type="json",
                publisher="Wikidata",
                source_type="metadata_api",
                size=200,
            )
            manifest = root / "data" / "raw" / "raw_corpus_manifest.jsonl"
            manifest.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            CorpusParseReporterTests._write_result(
                root, rows[0], ParseStatus.PASS, fallback=False
            )
            CorpusParseReporterTests._write_result(
                root, rows[1], ParseStatus.PASS, fallback=False
            )

            service = _RecordingService()
            runner = CorpusParseRunner(root, manifest, service=service)
            outcomes, manifest_rows, report = runner.run_all(
                force=True, parser_mode="auto"
            )

            self.assertEqual(service.calls, [(manifest, True, "auto")])
            self.assertEqual(len(outcomes), 2)
            self.assertEqual(len(manifest_rows), 2)
            self.assertEqual(report["total_sources"], 2)
            self.assertEqual(report["parse_success"], 2)
            self.assertTrue(
                (root / "data/normalized/normalized_corpus_manifest.jsonl").is_file()
            )

    def test_corpus_cli_returns_success_and_prints_batch_summary(self) -> None:
        runner = _StaticRunner()
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            exit_code = run_parse_corpus.main(
                ["--all"],
                project_root=Path("."),
                runner_factory=lambda _root, _manifest: runner,
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(runner.calls, [(False, "auto")])
        self.assertEqual(payload["total_sources"], 2)
        self.assertEqual(payload["batch_outcome_counts"], {"parsed": 2})


class _RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, bool, str]] = []

    def parse_manifest(
        self, manifest: Path, *, force: bool, parser_mode: str
    ) -> list[ParseOutcome]:
        self.calls.append((manifest, force, parser_mode))
        return [
            ParseOutcome(source_id="html-pass", status=OutcomeStatus.PARSED),
            ParseOutcome(source_id="json-pass", status=OutcomeStatus.PARSED),
        ]


class _StaticRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, str]] = []

    def run_all(self, *, force: bool, parser_mode: str):
        self.calls.append((force, parser_mode))
        outcomes = [
            ParseOutcome(source_id="html-pass", status=OutcomeStatus.PARSED),
            ParseOutcome(source_id="json-pass", status=OutcomeStatus.PARSED),
        ]
        return outcomes, [], {
            "total_sources": 2,
            "parse_success": 2,
            "parsed_warning": 0,
            "fallback_used": 0,
            "manual_review": 0,
            "failed": 0,
            "average_quality_score": 1.0,
            "raw_integrity": {"all_hashes_match": True},
            "batch_outcome_counts": {"parsed": 2},
        }


if __name__ == "__main__":
    unittest.main()

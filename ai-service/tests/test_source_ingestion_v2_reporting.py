import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.rag.source_ingestion.attempts import (
    SourceAttemptLedger,
    backfill_v1_failures,
)
from app.rag.source_ingestion.discovery import (
    DiscoveryManifestError,
    load_discovered_candidates,
)
from app.rag.source_ingestion.http_client import RawHttpResponse
from app.rag.source_ingestion.models import CandidateSource
from app.rag.source_ingestion.report import (
    SourceCorpusReportConfig,
    SourceCorpusReporter,
)
from app.rag.source_ingestion.snapshot import RawSnapshotStore
from app.rag.source_ingestion.v2_models import (
    DiscoveredCandidate,
    SourceAttemptRecord,
    SourceAttemptStatus,
)
from app.rag.source_ingestion.validation import SnapshotValidator


FIXED_TIME = datetime(2026, 9, 10, 4, 0, tzinfo=UTC)


def _candidate(source_id: str, *, replaces: str | None = None) -> DiscoveredCandidate:
    return DiscoveredCandidate(
        source_id=source_id,
        title="周杰伦《七里香》制作背景",
        url=f"https://publisher.test/{source_id}.html",
        publisher="Publisher One",
        author=None,
        published_at=None,
        source_type="media_article",
        file_type="html",
        artist="周杰伦",
        album="七里香",
        topics=["制作过程"],
        source_tier=2,
        source_quality="high",
        reason="A traceable report about Jay Chou's album production.",
        discovery_query="周杰伦 七里香 制作",
        replacement_group="qilixiang-production",
        priority=1,
        replaces_source_id=replaces,
    )


class DiscoveryManifestTests(unittest.TestCase):
    def test_manifest_loads_candidates_and_rejects_duplicate_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "discovered.json"
            candidate = _candidate("discovered-one")
            path.write_text(
                json.dumps([candidate.model_dump(mode="json")]), encoding="utf-8"
            )
            self.assertEqual(load_discovered_candidates(path), [candidate])

            path.write_text(
                json.dumps(
                    [
                        candidate.model_dump(mode="json"),
                        candidate.model_dump(mode="json"),
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(DiscoveryManifestError):
                load_discovered_candidates(path)


class AttemptBackfillTests(unittest.TestCase):
    def test_v1_failure_backfill_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "source_attempts.jsonl"
            ledger = SourceAttemptLedger(path, now=lambda: FIXED_TIME)
            discovered = _candidate("failed-old")
            v1_candidate = CandidateSource.model_validate(
                discovered.model_dump(include=set(CandidateSource.model_fields))
            )
            failures = [
                {
                    "source_id": "failed-old",
                    "failure_reason": "HTTPStatusError:404 Not Found",
                    "attempted_at": "2026-09-10T01:00:00Z",
                }
            ]

            backfill_v1_failures([v1_candidate], failures, ledger)
            backfill_v1_failures([v1_candidate], failures, ledger)

            records = ledger.load()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, SourceAttemptStatus.FETCH_FAILED)
            self.assertEqual(records[0].reason, "HTTPStatusError:404 Not Found")


class SourceCorpusReporterTests(unittest.TestCase):
    def test_report_writes_valid_manifest_and_readiness_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidate = _candidate("valid-source", replaces="failed-old")
            response = RawHttpResponse(
                "<html><title>周杰伦 七里香</title><body>"
                "这是一篇关于专辑制作过程的完整文章。"
                "</body></html>".encode(),
                "text/html; charset=utf-8",
                200,
            )
            validator = SnapshotValidator()
            store = RawSnapshotStore(root, now=lambda: FIXED_TIME)
            validation = validator.validate(candidate, response)
            raw_record = store.save_auto_validated(candidate, response, validation)
            ledger = SourceAttemptLedger(
                root / "data/raw/source_attempts.jsonl", now=lambda: FIXED_TIME
            )
            ledger.append(
                SourceAttemptRecord(
                    attempt_id="attempt-one",
                    source_id=candidate.source_id,
                    status="snapshot_valid",
                    occurred_at=FIXED_TIME,
                    url=candidate.url,
                    title=candidate.title,
                    publisher=candidate.publisher,
                    file_type=candidate.file_type,
                    source_tier=candidate.source_tier,
                    topics=candidate.topics,
                    replacement_group=candidate.replacement_group,
                    attempt_number=1,
                    replaces_source_id=candidate.replaces_source_id,
                    reason="snapshot validation passed",
                    raw_path=raw_record.raw_path,
                )
            )
            reporter = SourceCorpusReporter(
                root,
                config=SourceCorpusReportConfig(
                    minimum_snapshots=1,
                    maximum_snapshots=2,
                    minimum_pdf_snapshots=0,
                    maximum_pdf_snapshots=2,
                    minimum_new_pdf_snapshots=0,
                    minimum_html_publishers=1,
                    require_structured_json=False,
                    required_topics=frozenset({"制作过程"}),
                ),
                now=lambda: FIXED_TIME,
            )

            report = reporter.write_outputs(
                existing_candidates=[],
                discovered_candidates=[candidate],
                attempts=ledger.load(),
            )

            self.assertEqual(report["total_valid_snapshots"], 1)
            self.assertEqual(
                report["file_type_counts"],
                {"html": 1, "pdf": 0, "json": 0, "txt": 0, "markdown": 0},
            )
            self.assertEqual(report["publisher_count"], 1)
            self.assertEqual(report["replacement_success"], 1)
            self.assertTrue(report["ready_for_parse_normalize"])
            raw_manifest = root / "data/raw/raw_corpus_manifest.jsonl"
            entry = json.loads(raw_manifest.read_text(encoding="utf-8"))
            self.assertEqual(entry["source_id"], candidate.source_id)
            self.assertEqual(entry["publisher"], candidate.publisher)
            self.assertEqual(entry["content_hash"], raw_record.content_hash)
            self.assertTrue((root / "data/raw/source_corpus_report.json").is_file())
            self.assertTrue((root / "data/raw/source_corpus_report.md").is_file())


if __name__ == "__main__":
    unittest.main()

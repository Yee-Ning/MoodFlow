import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.rag.source_ingestion.attempts import SourceAttemptLedger
from app.rag.source_ingestion.auto_service import AutoSourceIngestionService
from app.rag.source_ingestion.http_client import RawHttpResponse
from app.rag.source_ingestion.quality_filter import AutoSourceQualityFilter
from app.rag.source_ingestion.retry import HostRetryScheduler, SourceRetryConfig
from app.rag.source_ingestion.snapshot import RawSnapshotStore
from app.rag.source_ingestion.v2_models import (
    DiscoveredCandidate,
    SourceAttemptStatus,
)
from app.rag.source_ingestion.validation import SnapshotValidator


FIXED_TIME = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)


def _candidate(
    source_id: str,
    *,
    group: str,
    priority: int,
    replaces_source_id: str | None = None,
    ) -> DiscoveredCandidate:
    return DiscoveredCandidate(
        source_id=source_id,
        title=f"周杰伦《七里香》制作资料 {source_id}",
        url=f"https://example.test/{source_id}.html",
        publisher="Example Music",
        author=None,
        published_at=None,
        source_type="media_article",
        file_type="html",
        artist="周杰伦",
        album="七里香",
        topics=["制作过程"],
        source_tier=2,
        source_quality="high",
        reason="A traceable source about Jay Chou album production.",
        discovery_query="周杰伦 七里香 制作",
        replacement_group=group,
        priority=priority,
        replaces_source_id=replaces_source_id,
    )


def _pdf_candidate(source_id: str, *, group: str) -> DiscoveredCandidate:
    return DiscoveredCandidate(
        source_id=source_id,
        title=f"周杰伦 PDF 研究资料 {source_id}",
        url=f"https://example.test/{source_id}.pdf",
        publisher="Example Academic Publisher",
        author=None,
        published_at=None,
        source_type="academic_paper",
        file_type="pdf",
        artist="周杰伦",
        album="七里香",
        topics=["音乐风格"],
        source_tier=2,
        source_quality="high",
        reason="A traceable PDF about Jay Chou.",
        discovery_query="周杰伦 音乐风格 PDF",
        replacement_group=group,
        priority=1,
    )


class StubHttpClient:
    def __init__(self, outcomes: dict[str, RawHttpResponse | Exception]) -> None:
        self.outcomes = outcomes
        self.urls: list[str] = []

    def fetch(self, url: str) -> RawHttpResponse:
        self.urls.append(url)
        outcome = self.outcomes[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _valid_html(label: str) -> RawHttpResponse:
    return RawHttpResponse(
        (
            "<html><title>周杰伦 七里香</title><body><article>"
            f"这是合法且完整的专辑制作资料：{label}。"
            "</article></body></html>"
        ).encode(),
        "text/html; charset=utf-8",
        200,
    )


class AutoSourceIngestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = RawSnapshotStore(self.root, now=lambda: FIXED_TIME)
        self.ledger = SourceAttemptLedger(
            self.root / "data/raw/source_attempts.jsonl", now=lambda: FIXED_TIME
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _service(self, client: StubHttpClient) -> AutoSourceIngestionService:
        now = [FIXED_TIME]

        def sleep(seconds):
            now[0] += timedelta(seconds=seconds)

        config = SourceRetryConfig()
        return AutoSourceIngestionService(
            snapshot_store=self.store,
            http_client=client,
            validator=SnapshotValidator(),
            quality_filter=AutoSourceQualityFilter(),
            attempt_ledger=self.ledger,
            existing_candidates=[],
            retry_config=config,
            retry_scheduler=HostRetryScheduler(config, now=lambda: now[0], sleeper=sleep, jitter=lambda: 0),
        )

    def test_fetch_and_validation_failures_fall_back_to_third_candidate(self) -> None:
        candidates = [
            _candidate("replacement-one", group="album-production", priority=1, replaces_source_id="old-source"),
            _candidate("replacement-two", group="album-production", priority=2, replaces_source_id="old-source"),
            _candidate("replacement-three", group="album-production", priority=3, replaces_source_id="old-source"),
        ]
        client = StubHttpClient(
            {
                str(candidates[0].url): TimeoutError("timed out"),
                str(candidates[1].url): RawHttpResponse(
                    b"<html><title>404 Not Found</title></html>", "text/html", 200
                ),
                str(candidates[2].url): _valid_html("third"),
            }
        )

        summary = self._service(client).run(
            candidates, target_count=1, max_replacement_attempts=3
        )

        self.assertEqual(summary.fetch_attempted, 3)
        self.assertEqual(summary.fetch_success, 1)
        self.assertEqual(summary.fetch_failed, 2)
        self.assertEqual(summary.replacement_success, 1)
        statuses = [record.status for record in self.ledger.load()]
        self.assertEqual(statuses.count(SourceAttemptStatus.REPLACEMENT_ATTEMPT), 3)
        self.assertEqual(statuses.count(SourceAttemptStatus.FETCH_FAILED), 2)
        self.assertEqual(statuses.count(SourceAttemptStatus.SNAPSHOT_VALID), 1)
        self.assertEqual(len(self.store.manifest_path.read_text().splitlines()), 1)

    def test_replacement_cap_records_unresolved_gap_and_continues_next_group(self) -> None:
        failed = [
            _candidate(f"failed-{index}", group="failed-group", priority=index)
            for index in range(1, 5)
        ]
        success = _candidate("next-group", group="next-group", priority=1)
        client = StubHttpClient(
            {
                **{str(candidate.url): TimeoutError("timed out") for candidate in failed},
                str(success.url): _valid_html("next group"),
            }
        )

        summary = self._service(client).run(
            [*failed, success], target_count=2, max_replacement_attempts=3
        )

        self.assertEqual(sum("failed-" in url for url in client.urls), 3)
        self.assertIn(str(success.url), client.urls)
        self.assertEqual(summary.unresolved_source_gap, 1)
        self.assertEqual(summary.fetch_success, 1)
        statuses = [record.status for record in self.ledger.load()]
        self.assertIn(SourceAttemptStatus.UNRESOLVED_SOURCE_GAP, statuses)

    def test_rerun_does_not_fetch_or_append_terminal_candidate_again(self) -> None:
        candidate = _candidate("stable-source", group="stable-group", priority=1)
        client = StubHttpClient({str(candidate.url): _valid_html("stable")})
        service = self._service(client)

        first = service.run([candidate], target_count=1, max_replacement_attempts=3)
        first_attempt_count = len(self.ledger.load())
        second = service.run([candidate], target_count=1, max_replacement_attempts=3)

        self.assertEqual(first.fetch_success, 1)
        self.assertEqual(second.fetch_attempted, 0)
        self.assertEqual(len(client.urls), 1)
        self.assertEqual(len(self.ledger.load()), first_attempt_count)
        self.assertEqual(len(self.store.manifest_path.read_text().splitlines()), 1)

    def test_underrepresented_pdf_group_is_attempted_before_html_target_is_reached(self) -> None:
        html = _candidate("html-source", group="html-group", priority=1)
        pdf = _pdf_candidate("pdf-source", group="pdf-group")
        client = StubHttpClient(
            {
                str(html.url): _valid_html("html"),
                str(pdf.url): RawHttpResponse(b"%PDF-1.7\n%%EOF", "application/pdf", 200),
            }
        )

        summary = self._service(client).run(
            [html, pdf], target_count=1, max_replacement_attempts=3
        )

        self.assertEqual(summary.fetch_success, 1)
        self.assertEqual(client.urls, [str(pdf.url)])

    def test_reopen_failed_retries_exhausted_group_when_automatic_retry_disabled(self) -> None:
        candidate = _candidate("retry-source", group="retry-group", priority=1)
        client = StubHttpClient({str(candidate.url): TimeoutError("temporary")})
        service = self._service(client)
        service.retry_config = SourceRetryConfig(enabled=False)
        service.run([candidate], target_count=1, max_replacement_attempts=1)
        client.outcomes[str(candidate.url)] = _valid_html("recovered")
        default = service.run([candidate], target_count=1, max_replacement_attempts=3)
        self.assertEqual(default.fetch_attempted, 0)
        recovered = service.run([candidate], target_count=1, max_replacement_attempts=1, reopen_failed=True)
        self.assertEqual(recovered.fetch_success, 1)
        self.assertEqual(len(client.urls), 2)
        again = service.run([candidate], target_count=1, max_replacement_attempts=1, reopen_failed=True)
        self.assertEqual(again.fetch_attempted, 0)

    def test_reopen_preserves_filter_and_duplicate_terminals_and_attempt_cap(self) -> None:
        candidates = [_candidate(f"retry-{i}", group="retry-group", priority=i) for i in range(1, 5)]
        candidates[0] = candidates[0].model_copy(update={"artist": "Other"})
        existing = candidates[1]
        service = self._service(StubHttpClient({str(c.url): TimeoutError("temporary") for c in candidates}))
        service.existing_candidates = [existing]
        first = service.run(candidates, target_count=1, max_replacement_attempts=1)
        second = service.run(candidates, target_count=1, max_replacement_attempts=1, reopen_failed=True)
        self.assertEqual((first.filtered_out, first.duplicate_candidates, first.fetch_attempted), (1, 1, 1))
        self.assertEqual((second.filtered_out, second.duplicate_candidates, second.fetch_attempted), (0, 0, 1))
        self.assertEqual(len(service.http_client.urls), 2)

    def test_invalid_latest_snapshot_is_refetched_even_when_target_was_reached(self) -> None:
        for damage in ("missing", "corrupt", "unreadable"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                store = RawSnapshotStore(root, now=lambda: FIXED_TIME)
                candidate = _candidate("repair-source", group="repair-group", priority=1)
                client = StubHttpClient({str(candidate.url): _valid_html("restored")})
                service = AutoSourceIngestionService(
                    snapshot_store=store, http_client=client, validator=SnapshotValidator(),
                    quality_filter=AutoSourceQualityFilter(),
                    attempt_ledger=SourceAttemptLedger(root / "data/raw/source_attempts.jsonl"),
                    existing_candidates=[],
                )
                self.assertEqual(service.run([candidate], target_count=1, max_replacement_attempts=1).fetch_success, 1)
                old = service._raw_records()[0]
                service.existing_candidates = [candidate]
                client.urls.clear()
                raw = root / old.raw_path
                if damage == "missing":
                    raw.unlink()
                elif damage == "corrupt":
                    raw.write_bytes(b"corrupt")
                original_read = Path.read_bytes

                def read_bytes(path):
                    if damage == "unreadable" and path == raw:
                        raise PermissionError("fixture read denied")
                    return original_read(path)

                service.snapshot_store = RawSnapshotStore(root, now=lambda: FIXED_TIME + timedelta(seconds=1))
                with patch.object(Path, "read_bytes", read_bytes):
                    result = service.run([candidate], target_count=1, max_replacement_attempts=1)
                self.assertEqual((result.fetch_success, result.total_valid_snapshots), (1, 1))
                self.assertEqual(len(store.manifest_path.read_text(encoding="utf-8").splitlines()), 2)

    def test_healthy_latest_snapshot_is_kept_without_fetch(self) -> None:
        candidate = _candidate("healthy-source", group="healthy-group", priority=1)
        response = _valid_html("healthy")
        self.store.save_auto_validated(candidate, response, SnapshotValidator().validate(candidate, response))
        client = StubHttpClient({})
        outcome = self._service(client).run([candidate], target_count=1, max_replacement_attempts=1)
        self.assertEqual(outcome.total_valid_snapshots, 1)
        self.assertEqual(client.urls, [])


if __name__ == "__main__":
    unittest.main()

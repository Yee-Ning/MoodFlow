import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.rag.source_ingestion.attempts import SourceAttemptLedger
from app.rag.source_ingestion.auto_service import AutoSourceIngestionService
from app.rag.source_ingestion.http_client import RawHttpResponse
from app.rag.source_ingestion.quality_filter import AutoSourceQualityFilter
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
        return AutoSourceIngestionService(
            snapshot_store=self.store,
            http_client=client,
            validator=SnapshotValidator(),
            quality_filter=AutoSourceQualityFilter(),
            attempt_ledger=self.ledger,
            existing_candidates=[],
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


if __name__ == "__main__":
    unittest.main()

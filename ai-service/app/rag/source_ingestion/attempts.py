"""Append-only attempt provenance for automatic source ingestion."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .models import CandidateSource
from .v2_models import SourceAttemptRecord, SourceAttemptStatus


class SourceAttemptLedger:
    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = path
        self.now = now or (lambda: datetime.now(UTC))

    def append(self, record: SourceAttemptRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(record.model_dump_json())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    def load(self) -> list[SourceAttemptRecord]:
        if not self.path.exists():
            return []
        return [
            SourceAttemptRecord.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def backfill_v1_failures(
    candidates: list[CandidateSource],
    failures: list[dict[str, str]],
    ledger: SourceAttemptLedger,
) -> int:
    """Record known V1 failures once without changing the success manifest."""

    candidate_by_id = {candidate.source_id: candidate for candidate in candidates}
    existing = {(record.source_id, record.status) for record in ledger.load()}
    appended = 0
    for failure in failures:
        source_id = failure["source_id"]
        if (source_id, SourceAttemptStatus.FETCH_FAILED) in existing:
            continue
        candidate = candidate_by_id.get(source_id)
        if candidate is None:
            raise ValueError(f"unknown V1 failed source_id: {source_id}")
        attempted_at = datetime.fromisoformat(
            failure["attempted_at"].replace("Z", "+00:00")
        )
        ledger.append(
            SourceAttemptRecord(
                attempt_id=f"historical-{source_id}-fetch-failed",
                source_id=source_id,
                status=SourceAttemptStatus.FETCH_FAILED,
                occurred_at=attempted_at,
                url=candidate.url,
                title=candidate.title,
                publisher=candidate.publisher,
                file_type=candidate.file_type,
                source_tier=candidate.source_tier,
                topics=candidate.topics,
                replacement_group=f"v1-failure-{source_id}",
                attempt_number=1,
                reason=failure["failure_reason"],
            )
        )
        existing.add((source_id, SourceAttemptStatus.FETCH_FAILED))
        appended += 1
    return appended

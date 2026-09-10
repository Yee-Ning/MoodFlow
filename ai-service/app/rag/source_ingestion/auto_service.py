"""Automatic filter, deduplication, fetch, validation, and replacement loop."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from .attempts import SourceAttemptLedger
from .dedup import CandidateDeduplicator
from .http_client import PolicyAwareHttpClient
from .models import CandidateSource, RawSnapshotRecord
from .quality_filter import AutoSourceQualityFilter
from .snapshot import RawSnapshotStore
from .v2_models import (
    AutoIngestionRunSummary,
    DiscoveredCandidate,
    SourceAttemptRecord,
    SourceAttemptStatus,
)
from .validation import SnapshotValidator


_TERMINAL_CANDIDATE_STATUSES = frozenset(
    {
        SourceAttemptStatus.FILTERED_OUT,
        SourceAttemptStatus.FETCH_FAILED,
        SourceAttemptStatus.SNAPSHOT_VALID,
        SourceAttemptStatus.DUPLICATE_CANDIDATE,
    }
)


class AutoSourceIngestionService:
    def __init__(
        self,
        *,
        snapshot_store: RawSnapshotStore,
        http_client: PolicyAwareHttpClient,
        validator: SnapshotValidator,
        quality_filter: AutoSourceQualityFilter,
        attempt_ledger: SourceAttemptLedger,
        existing_candidates: Iterable[CandidateSource],
    ) -> None:
        self.snapshot_store = snapshot_store
        self.http_client = http_client
        self.validator = validator
        self.quality_filter = quality_filter
        self.attempt_ledger = attempt_ledger
        self.existing_candidates = list(existing_candidates)

    def run(
        self,
        candidates: list[DiscoveredCandidate],
        *,
        target_count: int,
        max_replacement_attempts: int,
    ) -> AutoIngestionRunSummary:
        if target_count <= 0:
            raise ValueError("target_count must be positive")
        if max_replacement_attempts <= 0:
            raise ValueError("max_replacement_attempts must be positive")

        records = self._raw_records()
        raw_source_ids = {record.source_id for record in records}
        deduplicator = CandidateDeduplicator(self.existing_candidates)
        for record in records:
            deduplicator.register_url(record.source_id, str(record.original_url))

        previous = self.attempt_ledger.load()
        terminal_source_ids = {
            record.source_id
            for record in previous
            if record.status in _TERMINAL_CANDIDATE_STATUSES
        }
        unresolved_groups = {
            record.replacement_group
            for record in previous
            if record.status is SourceAttemptStatus.UNRESOLVED_SOURCE_GAP
        }
        prior_fetch_attempts: dict[str, int] = defaultdict(int)
        for record in previous:
            if record.status in {
                SourceAttemptStatus.FETCH_FAILED,
                SourceAttemptStatus.SNAPSHOT_VALID,
            }:
                prior_fetch_attempts[record.replacement_group] += 1

        groups: dict[str, list[DiscoveredCandidate]] = {}
        for candidate in candidates:
            groups.setdefault(candidate.replacement_group, []).append(candidate)
        for group in groups.values():
            group.sort(key=lambda item: (item.priority, item.source_id))

        filtered_out = 0
        duplicate_candidates = 0
        fetch_attempted = 0
        fetch_success = 0
        fetch_failed = 0
        replacement_success = 0
        unresolved_source_gap = 0

        # Process underrepresented binary/structured formats first so a target
        # count cannot be reached solely by a long run of HTML candidates.
        ordered_groups = sorted(
            groups.items(),
            key=lambda item: (
                0 if any(candidate.file_type.value == "pdf" for candidate in item[1]) else
                1 if any(candidate.file_type.value == "json" for candidate in item[1]) else 2,
                min(candidate.priority for candidate in item[1]),
                item[0],
            ),
        )
        for group_name, group_candidates in ordered_groups:
            if len(raw_source_ids) >= target_count:
                break
            if group_name in unresolved_groups:
                continue
            if any(item.source_id in raw_source_ids for item in group_candidates):
                continue
            group_attempts = prior_fetch_attempts[group_name]
            resolved = False

            for candidate in group_candidates:
                if candidate.source_id in terminal_source_ids:
                    continue
                filter_decision = self.quality_filter.evaluate(candidate)
                if not filter_decision.accepted:
                    self._record(
                        candidate,
                        SourceAttemptStatus.FILTERED_OUT,
                        reason=",".join(filter_decision.reasons),
                    )
                    terminal_source_ids.add(candidate.source_id)
                    filtered_out += 1
                    continue

                duplicate = deduplicator.check(candidate)
                if duplicate.is_duplicate:
                    self._record(
                        candidate,
                        SourceAttemptStatus.DUPLICATE_CANDIDATE,
                        reason=duplicate.reason,
                        duplicate_of=duplicate.duplicate_of,
                    )
                    terminal_source_ids.add(candidate.source_id)
                    duplicate_candidates += 1
                    continue
                if group_attempts >= max_replacement_attempts:
                    break

                group_attempts += 1
                fetch_attempted += 1
                if candidate.replaces_source_id is not None or group_attempts > 1:
                    self._record(
                        candidate,
                        SourceAttemptStatus.REPLACEMENT_ATTEMPT,
                        attempt_number=group_attempts,
                        reason="replacement candidate selected",
                    )
                try:
                    response = self.http_client.fetch(str(candidate.url))
                    validation = self.validator.validate(candidate, response)
                    if not validation.valid:
                        raise ValueError(
                            "snapshot_validation:" + ",".join(validation.reasons)
                        )
                    raw_record = self.snapshot_store.save_auto_validated(
                        candidate, response, validation
                    )
                except Exception as exc:
                    self._record(
                        candidate,
                        SourceAttemptStatus.FETCH_FAILED,
                        attempt_number=group_attempts,
                        reason=self._error_reason(exc),
                    )
                    terminal_source_ids.add(candidate.source_id)
                    fetch_failed += 1
                    continue

                self._record(
                    candidate,
                    SourceAttemptStatus.SNAPSHOT_VALID,
                    attempt_number=group_attempts,
                    reason="snapshot validation passed",
                    raw_path=raw_record.raw_path,
                )
                terminal_source_ids.add(candidate.source_id)
                raw_source_ids.add(candidate.source_id)
                deduplicator.register(candidate)
                fetch_success += 1
                if candidate.replaces_source_id is not None:
                    replacement_success += 1
                resolved = True
                break

            if not resolved and not any(
                item.source_id in raw_source_ids for item in group_candidates
            ):
                representative = group_candidates[0]
                self._record(
                    representative,
                    SourceAttemptStatus.UNRESOLVED_SOURCE_GAP,
                    attempt_number=group_attempts,
                    reason="replacement candidates exhausted",
                )
                unresolved_groups.add(group_name)
                unresolved_source_gap += 1

        return AutoIngestionRunSummary(
            total_discovered=len(candidates),
            filtered_out=filtered_out,
            duplicate_candidates=duplicate_candidates,
            fetch_attempted=fetch_attempted,
            fetch_success=fetch_success,
            fetch_failed=fetch_failed,
            replacement_success=replacement_success,
            unresolved_source_gap=unresolved_source_gap,
            total_valid_snapshots=len(raw_source_ids),
        )

    def _record(
        self,
        candidate: DiscoveredCandidate,
        status: SourceAttemptStatus,
        *,
        attempt_number: int = 0,
        reason: str | None = None,
        duplicate_of: str | None = None,
        raw_path: str | None = None,
    ) -> None:
        occurred_at = self.attempt_ledger.now()
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("attempt ledger clock must be timezone-aware")
        self.attempt_ledger.append(
            SourceAttemptRecord(
                attempt_id=f"attempt-{uuid4()}",
                source_id=candidate.source_id,
                status=status,
                occurred_at=occurred_at,
                url=candidate.url,
                title=candidate.title,
                publisher=candidate.publisher,
                file_type=candidate.file_type,
                source_tier=candidate.source_tier,
                topics=candidate.topics,
                replacement_group=candidate.replacement_group,
                attempt_number=attempt_number,
                replaces_source_id=candidate.replaces_source_id,
                reason=reason,
                duplicate_of=duplicate_of,
                raw_path=raw_path,
            )
        )

    def _raw_records(self) -> list[RawSnapshotRecord]:
        manifest_path = self.snapshot_store.manifest_path
        if not manifest_path.exists():
            return []
        return [
            RawSnapshotRecord.model_validate_json(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @staticmethod
    def _error_reason(exc: Exception) -> str:
        message = str(exc).splitlines()[0].strip() if str(exc) else type(exc).__name__
        return f"{type(exc).__name__}:{message}"[:1_000]

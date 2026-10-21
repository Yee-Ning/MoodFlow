"""Automatic filter, deduplication, fetch, validation, and replacement loop."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
import hashlib
from uuid import uuid4

import httpx

from .attempts import SourceAttemptLedger
from .dedup import CandidateDeduplicator
from .http_client import PolicyAwareHttpClient
from .models import CandidateSource, RawSnapshotRecord
from .quality_filter import AutoSourceQualityFilter
from .retry import (
    HostRetryScheduler, SourceRetryConfig, classify_failure, failure_policy,
    reason_code, retry_after_seconds, retry_host,
)
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
        retry_config: SourceRetryConfig | None = None,
        retry_scheduler: HostRetryScheduler | None = None,
    ) -> None:
        self.snapshot_store = snapshot_store
        self.http_client = http_client
        self.validator = validator
        self.quality_filter = quality_filter
        self.attempt_ledger = attempt_ledger
        self.existing_candidates = list(existing_candidates)
        self.retry_config = retry_config or SourceRetryConfig()
        self.retry_scheduler = retry_scheduler or HostRetryScheduler(self.retry_config)

    def run(
        self,
        candidates: list[DiscoveredCandidate],
        *,
        target_count: int,
        max_replacement_attempts: int,
        reopen_failed: bool = False,
    ) -> AutoIngestionRunSummary:
        if target_count <= 0:
            raise ValueError("target_count must be positive")
        if max_replacement_attempts <= 0:
            raise ValueError("max_replacement_attempts must be positive")

        records = self._raw_records()
        latest = {record.source_id: record for record in sorted(records, key=lambda row: row.fetched_at)}
        raw_source_ids = {source_id for source_id, record in latest.items() if self._snapshot_valid(record)}
        invalid_source_ids = set(latest).difference(raw_source_ids)
        previous = self.attempt_ledger.load()
        self.retry_scheduler.restore(previous)
        if isinstance(self.http_client, PolicyAwareHttpClient):
            self.http_client.retry_wait = (
                lambda url: self.retry_scheduler.wait(retry_host(url))
            )
        source_attempts: dict[str, int] = defaultdict(int)
        last_outcome = {}
        for record in previous:
            if record.status in {SourceAttemptStatus.FETCH_FAILED, SourceAttemptStatus.SNAPSHOT_VALID}:
                source_attempts[record.source_id] = (
                    record.source_attempt_number or source_attempts[record.source_id] + 1
                )
            if record.status in _TERMINAL_CANDIDATE_STATUSES:
                last_outcome[record.source_id] = record
        automatic_ids = {
            source_id for source_id, record in last_outcome.items()
            if record.status is SourceAttemptStatus.FETCH_FAILED
            and self._can_auto_retry(record.reason, source_attempts[source_id])
            and source_id not in raw_source_ids
        }
        reopened_ids = invalid_source_ids | {
            record.source_id for record in previous
            if reopen_failed and record.status is SourceAttemptStatus.FETCH_FAILED
            and record.source_id not in raw_source_ids
        } | automatic_ids
        # Manual reopening starts a fresh bounded budget; automatic reopening
        # keeps the source's lifetime count, including across process restarts.
        for source_id in reopened_ids if reopen_failed else invalid_source_ids:
            source_attempts[source_id] = 0
        deduplicator = CandidateDeduplicator(
            candidate for candidate in self.existing_candidates
            if candidate.source_id not in reopened_ids
        )
        for source_id in raw_source_ids:
            record = latest[source_id]
            deduplicator.register_url(record.source_id, str(record.original_url))
        reopened_groups = {
            record.replacement_group for record in previous
            if reopen_failed and record.status in {
                SourceAttemptStatus.FETCH_FAILED, SourceAttemptStatus.UNRESOLVED_SOURCE_GAP,
            }
        } | {candidate.replacement_group for candidate in candidates if candidate.source_id in reopened_ids}
        terminal_source_ids = {
            record.source_id
            for record in previous
            if record.status in _TERMINAL_CANDIDATE_STATUSES
            and not (
                record.source_id in reopened_ids
                and record.status in {SourceAttemptStatus.FETCH_FAILED, SourceAttemptStatus.SNAPSHOT_VALID}
            )
        }
        unresolved_groups = {
            record.replacement_group
            for record in previous
            if record.status is SourceAttemptStatus.UNRESOLVED_SOURCE_GAP
            and record.replacement_group not in reopened_groups
        }
        prior_fetch_attempts: dict[str, int] = defaultdict(int)
        for record in previous:
            if record.replacement_group not in reopened_groups and record.status in {
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
        rate_limited = 0
        robots_denied = 0
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

            queue = deque(group_candidates)
            while queue:
                candidate = queue.popleft()
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
                if self.retry_config.enabled and source_attempts[candidate.source_id] >= self.retry_config.max_source_attempts:
                    continue

                group_attempts += 1
                source_attempts[candidate.source_id] += 1
                source_attempt = source_attempts[candidate.source_id]
                fetch_attempted += 1
                if candidate.replaces_source_id is not None or group_attempts > 1:
                    self._record(
                        candidate,
                        SourceAttemptStatus.REPLACEMENT_ATTEMPT,
                        attempt_number=group_attempts,
                        reason="replacement candidate selected",
                    )
                try:
                    host = retry_host(str(candidate.url))
                    self.retry_scheduler.wait(host)
                    response = self.http_client.fetch(str(candidate.url))
                    host = retry_host(response.final_url or str(candidate.url))
                    empty_reason = self.retry_scheduler.observe_empty(
                        host, response.http_status == 200 and not response.content.strip()
                    )
                    if empty_reason:
                        raise Empty200Error(empty_reason)
                    validation = self.validator.validate(candidate, response)
                    if not validation.valid:
                        raise ValueError(
                            "snapshot_validation:" + ",".join(validation.reasons)
                        )
                    raw_record = self.snapshot_store.save_auto_validated(
                        candidate, response, validation
                    )
                except Exception as exc:
                    reason = exc.reason if isinstance(exc, Empty200Error) else classify_failure(exc)
                    policy = failure_policy(reason)
                    server_delay = None
                    deadline = None
                    if isinstance(exc, httpx.HTTPStatusError):
                        host = retry_host(str(exc.request.url))
                        if exc.response.status_code in {429, 503}:
                            server_delay = retry_after_seconds(
                                exc.response.headers.get("Retry-After"), self.retry_scheduler.now()
                            )
                    if not isinstance(exc, Empty200Error):
                        self.retry_scheduler.observe_empty(host, False)
                    if self.retry_config.enabled and policy.retryable:
                        deadline = self.retry_scheduler.failed(host, reason, server_delay)
                    elif server_delay is not None:
                        deadline = self.retry_scheduler.server_deadline(host, server_delay)
                    self._record(
                        candidate,
                        SourceAttemptStatus.FETCH_FAILED,
                        attempt_number=group_attempts,
                        reason=reason,
                        source_attempt_number=source_attempt,
                        retry_host=host,
                        retry_after_seconds=server_delay,
                        retry_not_before=deadline,
                        host_failure_streak=self.retry_scheduler.streaks.get(host, 0),
                        empty_200_streak=self.retry_scheduler.empty_streaks.get(host, 0),
                        failure_detail=self._error_reason(exc),
                    )
                    if self._can_auto_retry(reason, source_attempt):
                        queue.append(candidate)
                    else:
                        terminal_source_ids.add(candidate.source_id)
                    if policy.category == "rate_limited":
                        rate_limited += 1
                    elif policy.category == "policy_denied":
                        robots_denied += 1
                    else:
                        fetch_failed += 1
                    continue

                self.retry_scheduler.succeeded(host)
                self._record(
                    candidate,
                    SourceAttemptStatus.SNAPSHOT_VALID,
                    attempt_number=group_attempts,
                    reason="snapshot validation passed",
                    raw_path=raw_record.raw_path,
                    source_attempt_number=source_attempt,
                    retry_host=host,
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
            rate_limited=rate_limited,
            robots_denied=robots_denied,
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
        **retry_fields,
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
                **retry_fields,
            )
        )

    def _can_auto_retry(self, reason: str | None, attempts: int) -> bool:
        policy = failure_policy(reason_code(reason))
        cap = min(self.retry_config.max_source_attempts, policy.max_attempts or self.retry_config.max_source_attempts)
        return self.retry_config.enabled and policy.retryable and attempts < cap

    def _raw_records(self) -> list[RawSnapshotRecord]:
        manifest_path = self.snapshot_store.manifest_path
        if not manifest_path.exists():
            return []
        return [
            RawSnapshotRecord.model_validate_json(line)
            for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _snapshot_valid(self, record: RawSnapshotRecord) -> bool:
        path = (self.snapshot_store.project_root / record.raw_path).resolve()
        if not path.is_relative_to(self.snapshot_store.raw_root.resolve()):
            return False
        try:
            return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == record.content_hash
        except OSError:
            return False

    @staticmethod
    def _error_reason(exc: Exception) -> str:
        message = str(exc).splitlines()[0].strip() if str(exc) else type(exc).__name__
        return f"{type(exc).__name__}:{message}"[:1_000]


class Empty200Error(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

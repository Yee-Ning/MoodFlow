from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from typing import ContextManager, Protocol

from .models import MemoryEvidence, MemoryJob, MemoryPreferences, MemoryRecord, MemoryStatus, MemoryType
from .scopes import is_current, matches_scene


class MemoryStore(Protocol):
    def transaction(
        self, user_id: str, memory_key: str
    ) -> ContextManager[None]: ...

    def get_active(
        self, user_id: str, memory_type: MemoryType, memory_key: str
    ) -> MemoryRecord | None: ...

    def insert(self, memory: MemoryRecord) -> None: ...

    def replace(self, memory: MemoryRecord) -> None: ...

    def list_active_profiles(
        self, user_id: str, *, scene: str | None = None, limit: int = 50, query: str = ""
    ) -> list[MemoryRecord]: ...

    def has_active_episodes(self, user_id: str) -> bool: ...

    def mark_profile_dirty(self, user_id: str, revision: str) -> None: ...
    def pending_profile_revision(self, user_id: str) -> str | None: ...
    def profile_records(self, user_id: str) -> list[MemoryRecord]: ...
    def complete_profile_revision(self, user_id: str, revision: str, omitted_count: int) -> bool: ...
    def get_record(self, user_id: str, memory_id: str) -> MemoryRecord | None: ...
    def get_evidence(self, evidence_id: str) -> MemoryEvidence | None: ...
    def add_evidence(self, evidence: MemoryEvidence) -> None: ...
    def prepare_message(self, user_id: str, message_id: str, message_digest: str) -> bool: ...
    def complete_message(self, user_id: str, message_id: str) -> None: ...
    def preferences(self, user_id: str) -> MemoryPreferences: ...
    def set_enabled(self, user_id: str, enabled: bool) -> MemoryPreferences: ...
    def list_records(self, user_id: str, *, after_id: str = "", limit: int = 50) -> list[MemoryRecord]: ...
    def forget_fact(self, user_id: str, memory_key: str) -> None: ...
    def profile_revocations(self, user_id: str) -> dict: ...
    def enqueue_job(self, job: MemoryJob) -> bool: ...
    def get_job(self, user_id: str, job_id: str) -> MemoryJob | None: ...
    def claim_job(self, owner: str) -> MemoryJob | None: ...
    def dispatch_job(self, job_id: str, owner: str) -> bool: ...
    def observe_job(self, job_id: str, owner: str, tokens: int | None) -> None: ...
    def prepare_job(self, job_id: str, owner: str, extraction: dict) -> None: ...
    def finish_job(self, job_id: str, owner: str, status: str) -> None: ...
    def cancel_pending_jobs(self, user_id: str) -> None: ...

    def search_active_episodes(
        self,
        user_id: str,
        embedding: list[float],
        *,
        scene: str | None = None,
        limit: int = 20,
    ) -> list[tuple[MemoryRecord, float]]: ...


class InMemoryMemoryStore:
    """Test/local adapter preserving the same user-isolation contract."""

    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._lock = RLock()
        self._profile_pending: dict[str, str] = {}
        self._profile_omitted: dict[str, int] = {}
        self._evidence: dict[str, MemoryEvidence] = {}
        self._messages: dict[tuple[str, str], tuple[str, bool]] = {}
        self._preferences: dict[str, MemoryPreferences] = {}
        self._revocations: dict[tuple[str, str], object] = {}
        self._jobs: dict[str, MemoryJob] = {}

    @contextmanager
    def transaction(self, user_id: str, memory_key: str) -> Iterator[None]:
        del user_id, memory_key
        with self._lock:
            snapshot = (self._records.copy(), self._profile_pending.copy(),
                        self._profile_omitted.copy(), self._evidence.copy(), self._messages.copy(),
                        self._preferences.copy(), self._revocations.copy(), self._jobs.copy())
            try:
                yield
            except BaseException:
                (self._records, self._profile_pending, self._profile_omitted, self._evidence,
                 self._messages, self._preferences, self._revocations, self._jobs) = snapshot
                raise

    def get_active(
        self, user_id: str, memory_type: MemoryType, memory_key: str
    ) -> MemoryRecord | None:
        with self._lock:
            for record in self._records.values():
                if (
                    record.user_id == user_id
                    and record.memory_type is memory_type
                    and record.memory_key == memory_key
                    and record.status is MemoryStatus.ACTIVE
                ):
                    return record.model_copy(deep=True)
        return None

    def insert(self, memory: MemoryRecord) -> None:
        with self._lock:
            if memory.memory_id in self._records:
                raise ValueError("memory already exists")
            if self.get_active(
                memory.user_id, memory.memory_type, memory.memory_key
            ) is not None:
                raise ValueError("active memory key already exists")
            self._records[memory.memory_id] = memory.model_copy(deep=True)

    def replace(self, memory: MemoryRecord) -> None:
        with self._lock:
            if memory.memory_id not in self._records:
                raise KeyError(f"unknown memory:{memory.memory_id}")
            self._records[memory.memory_id] = memory.model_copy(deep=True)

    def list_active_profiles(
        self, user_id: str, *, scene: str | None = None, limit: int = 50, query: str = ""
    ) -> list[MemoryRecord]:
        with self._lock:
            rows = [
                record.model_copy(deep=True)
                for record in self._records.values()
                if record.user_id == user_id
                and record.memory_type is MemoryType.PROFILE
                and record.status is MemoryStatus.ACTIVE
                and (
                    matches_scene(record.structured_content.get("scene"), scene)
                    and is_current(record.valid_until)
                )
            ]
        query = "".join(char for char in query.casefold() if char.isalnum())
        def rank(item):
            subject = "".join(char for char in str(item.structured_content.get("subject", "")).casefold() if char.isalnum())
            return (len(subject) >= 2 and subject in query, item.importance, item.updated_at)
        rows.sort(key=rank, reverse=True)
        return rows[:limit]

    def has_active_episodes(self, user_id: str) -> bool:
        with self._lock:
            return any(
                record.user_id == user_id
                and record.memory_type is MemoryType.EPISODIC
                and record.status is MemoryStatus.ACTIVE
                and record.embedding is not None
                for record in self._records.values()
            )

    def search_active_episodes(
        self,
        user_id: str,
        embedding: list[float],
        *,
        scene: str | None = None,
        limit: int = 20,
    ) -> list[tuple[MemoryRecord, float]]:
        with self._lock:
            rows = [
                record.model_copy(deep=True)
                for record in self._records.values()
                if record.user_id == user_id
                and record.memory_type is MemoryType.EPISODIC
                and record.status is MemoryStatus.ACTIVE
                and record.embedding is not None
                and (
                    matches_scene(record.structured_content.get("scene"), scene)
                    and is_current(record.valid_until)
                )
            ]

        def cosine(left: list[float], right: list[float]) -> float:
            if len(left) != len(right) or not left:
                return 0.0
            dot = sum(a * b for a, b in zip(left, right, strict=True))
            left_norm = sum(value * value for value in left) ** 0.5
            right_norm = sum(value * value for value in right) ** 0.5
            if not left_norm or not right_norm:
                return 0.0
            return max(0.0, min(1.0, dot / (left_norm * right_norm)))

        scored = [
            (record, cosine(embedding, record.embedding or [])) for record in rows
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    def all_records(self) -> list[MemoryRecord]:
        with self._lock:
            return deepcopy(list(self._records.values()))

    def mark_profile_dirty(self, user_id, revision):
        with self._lock:
            self._profile_pending[user_id] = revision

    def pending_profile_revision(self, user_id):
        with self._lock:
            return self._profile_pending.get(user_id)

    def profile_records(self, user_id):
        with self._lock:
            return [record.model_copy(deep=True) for record in self._records.values()
                    if record.user_id == user_id and record.memory_type is MemoryType.PROFILE]

    def complete_profile_revision(self, user_id, revision, omitted_count):
        with self._lock:
            if self._profile_pending.get(user_id) != revision:
                return False
            del self._profile_pending[user_id]
            self._profile_omitted[user_id] = omitted_count
            return True

    def get_record(self, user_id, memory_id):
        with self._lock:
            row = self._records.get(memory_id)
            return row.model_copy(deep=True) if row is not None and row.user_id == user_id else None

    def get_evidence(self, evidence_id):
        with self._lock:
            row = self._evidence.get(evidence_id)
            return row.model_copy(deep=True) if row else None

    def add_evidence(self, evidence):
        with self._lock:
            if evidence.evidence_id in self._evidence:
                raise ValueError("memory evidence already exists")
            self._evidence[evidence.evidence_id] = evidence.model_copy(deep=True)

    def prepare_message(self, user_id, message_id, message_digest):
        with self._lock:
            previous = self._messages.get((user_id, message_id))
            if previous is not None and previous[0] != message_digest:
                raise ValueError("memory message idempotency conflict")
            if previous is None:
                self._messages[(user_id, message_id)] = (message_digest, False)
            return previous is not None and previous[1]

    def complete_message(self, user_id, message_id):
        with self._lock:
            previous = self._messages[(user_id, message_id)]
            self._messages[(user_id, message_id)] = (previous[0], True)

    def preferences(self, user_id):
        with self._lock:
            return self._preferences.get(user_id, MemoryPreferences())

    def set_enabled(self, user_id, enabled):
        with self._lock:
            previous = self.preferences(user_id)
            value = MemoryPreferences(enabled=enabled, generation=previous.generation + 1)
            self._preferences[user_id] = value
            return value

    def list_records(self, user_id, *, after_id="", limit=50):
        with self._lock:
            rows = sorted((row for row in self._records.values() if row.user_id == user_id
                           and row.memory_id > after_id), key=lambda row: row.memory_id)
            return [row.model_copy(deep=True) for row in rows[:limit]]

    def forget_fact(self, user_id, memory_key):
        from datetime import UTC, datetime
        from .projection import profile_key
        from app.agent.foundation.long_horizon.history_writer import digest
        with self._lock:
            ids = [row.memory_id for row in self._records.values()
                   if row.user_id == user_id and row.memory_key == memory_key]
            for memory_id in ids:
                del self._records[memory_id]
            self._evidence = {key: row for key, row in self._evidence.items()
                              if not (row.user_id == user_id and row.memory_key == memory_key)}
            self._revocations[(user_id, digest(profile_key(memory_key)))] = datetime.now(UTC)
            self.set_enabled(user_id, self.preferences(user_id).enabled)

    def profile_revocations(self, user_id):
        with self._lock:
            return {key: time for (owner, key), time in self._revocations.items() if owner == user_id}

    def enqueue_job(self, job):
        with self._lock:
            existing = self._jobs.get(job.job_id)
            if existing:
                if existing.source_digest != job.source_digest:
                    raise ValueError("memory job idempotency conflict")
                return False
            self._jobs[job.job_id] = job
            return True

    def get_job(self, user_id, job_id):
        with self._lock:
            row = self._jobs.get(job_id)
            return row.model_copy(deep=True) if row and row.user_id == user_id else None

    def claim_job(self, owner):
        from datetime import UTC, datetime, timedelta
        now = datetime.now(UTC)
        with self._lock:
            for row in sorted(self._jobs.values(), key=lambda row: row.created_at):
                if row.status == "pending" or row.status == "running" and row.lease_until <= now:
                    claimed = row.model_copy(update={"status": "running", "owner": owner,
                        "lease_until": now + timedelta(seconds=120), "attempts": row.attempts + 1})
                    self._jobs[row.job_id] = claimed
                    return claimed
            return None

    def _job_owner(self, job_id, owner):
        from datetime import UTC, datetime
        row = self._jobs[job_id]
        if row.owner != owner or row.status != "running" or row.lease_until <= datetime.now(UTC):
            raise ValueError("memory job lease lost")
        return row

    def dispatch_job(self, job_id, owner):
        with self._lock:
            row = self._job_owner(job_id, owner)
            if row.dispatches >= 2:
                return False
            self._jobs[job_id] = row.model_copy(update={"dispatches": row.dispatches + 1})
            return True

    def observe_job(self, job_id, owner, tokens):
        with self._lock:
            row = self._job_owner(job_id, owner)
            self._jobs[job_id] = row.model_copy(update={
                "reported_tokens": row.reported_tokens + (tokens or 0),
                "observed_dispatches": row.observed_dispatches + 1,
                "usage_known": row.usage_known and tokens is not None})

    def finish_job(self, job_id, owner, status):
        with self._lock:
            row = self._job_owner(job_id, owner)
            self._jobs[job_id] = row.model_copy(update={"status": status,
                "user_message": row.user_message if status == "pending" else "",
                "extraction": row.extraction if status == "pending" else None,
                "owner": None, "lease_until": None})

    def prepare_job(self, job_id, owner, extraction):
        with self._lock:
            row = self._job_owner(job_id, owner)
            self._jobs[job_id] = row.model_copy(update={"extraction": extraction})

    def cancel_pending_jobs(self, user_id):
        with self._lock:
            for key, job in self._jobs.copy().items():
                if job.user_id == user_id and job.status == "pending":
                    self._jobs[key] = job.model_copy(update={"cancelled": True, "user_message": "", "extraction": None})

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from typing import ContextManager, Protocol

from .models import MemoryEvidence, MemoryRecord, MemoryStatus, MemoryType
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

    @contextmanager
    def transaction(self, user_id: str, memory_key: str) -> Iterator[None]:
        del user_id, memory_key
        with self._lock:
            snapshot = (self._records.copy(), self._profile_pending.copy(),
                        self._profile_omitted.copy(), self._evidence.copy(), self._messages.copy())
            try:
                yield
            except BaseException:
                self._records, self._profile_pending, self._profile_omitted, self._evidence, self._messages = snapshot
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

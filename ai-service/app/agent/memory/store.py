from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from typing import ContextManager, Protocol

from .models import MemoryRecord, MemoryStatus, MemoryType


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
        self, user_id: str, *, scene: str | None = None, limit: int = 50
    ) -> list[MemoryRecord]: ...

    def has_active_episodes(self, user_id: str) -> bool: ...

    def mark_profile_dirty(self, user_id: str, revision: str) -> None: ...
    def pending_profile_revision(self, user_id: str) -> str | None: ...
    def profile_records(self, user_id: str) -> list[MemoryRecord]: ...
    def complete_profile_revision(self, user_id: str, revision: str, omitted_count: int) -> bool: ...

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

    @contextmanager
    def transaction(self, user_id: str, memory_key: str) -> Iterator[None]:
        del user_id, memory_key
        with self._lock:
            yield

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
        self, user_id: str, *, scene: str | None = None, limit: int = 50
    ) -> list[MemoryRecord]:
        with self._lock:
            rows = [
                record.model_copy(deep=True)
                for record in self._records.values()
                if record.user_id == user_id
                and record.memory_type is MemoryType.PROFILE
                and record.status is MemoryStatus.ACTIVE
                and (
                    scene is None
                    or record.structured_content.get("scene") in {None, scene}
                )
            ]
        rows.sort(key=lambda item: (item.importance, item.updated_at), reverse=True)
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
                    scene is None
                    or record.structured_content.get("scene") in {None, scene}
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

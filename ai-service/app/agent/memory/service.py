from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from .embeddings import MemoryEmbedder
from .models import (
    EvidenceStrength,
    ExtractedMemory,
    MemoryRecord,
    MemoryStatus,
    MemoryWriteAction,
    MemoryWriteResult,
)
from .store import MemoryStore

_STRENGTH = {
    EvidenceStrength.INFERRED: 1,
    EvidenceStrength.REPEATED_BEHAVIOR: 2,
    EvidenceStrength.EXPLICIT: 3,
}


def _same_fact(left: MemoryRecord, right: ExtractedMemory) -> bool:
    return (
        left.structured_content.get("preference") == right.preference
        and str(left.structured_content.get("value", "")).casefold()
        == right.value.casefold()
    )


class MemoryService:
    """Classification, deduplication and conflict policy for validated memories."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        embedder: MemoryEmbedder | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder

    def remember(
        self,
        *,
        user_id: str,
        source_thread_id: str,
        candidate: ExtractedMemory,
    ) -> MemoryWriteResult:
        normalized_user_id = user_id.strip()
        normalized_thread_id = source_thread_id.strip()
        if not normalized_user_id or not normalized_thread_id:
            raise ValueError("user_id and source_thread_id are required")

        embedding = None
        if candidate.memory_type.value == "episodic":
            if self.embedder is None:
                raise RuntimeError("episodic memory requires an embedding provider")
            vectors = self.embedder.embed_documents([candidate.content])
            if len(vectors) != 1:
                raise ValueError("memory embedding count mismatch")
            embedding = vectors[0]

        now = datetime.now(UTC)
        with self.store.transaction(normalized_user_id, candidate.memory_key):
            existing = self.store.get_active(
                normalized_user_id, candidate.memory_type, candidate.memory_key
            )
            if existing is None:
                created = self._new_record(
                    user_id=normalized_user_id,
                    source_thread_id=normalized_thread_id,
                    candidate=candidate,
                    now=now,
                    embedding=embedding,
                )
                self.store.insert(created)
                self._profile_changed(created)
                return MemoryWriteResult(
                    action=MemoryWriteAction.CREATED, memory=created
                )

            if _same_fact(existing, candidate):
                confirmed = existing.model_copy(
                    update={
                        "confidence": max(existing.confidence, candidate.confidence),
                        "importance": max(existing.importance, candidate.importance),
                        "evidence_strength": (
                            candidate.evidence_strength
                            if _STRENGTH[candidate.evidence_strength]
                            > _STRENGTH[existing.evidence_strength]
                            else existing.evidence_strength
                        ),
                        "confirmations": existing.confirmations + 1,
                        "updated_at": now,
                        "source_thread_id": normalized_thread_id,
                        "embedding": embedding or existing.embedding,
                    }
                )
                self.store.replace(confirmed)
                self._profile_changed(confirmed)
                return MemoryWriteResult(
                    action=MemoryWriteAction.CONFIRMED, memory=confirmed
                )

            existing_is_stronger = _STRENGTH[candidate.evidence_strength] < _STRENGTH[
                existing.evidence_strength
            ]
            repeated_existing_wins = (
                candidate.evidence_strength is not EvidenceStrength.EXPLICIT
                and _STRENGTH[candidate.evidence_strength]
                == _STRENGTH[existing.evidence_strength]
                and existing.confirmations > 1
            )
            if existing_is_stronger or repeated_existing_wins:
                return MemoryWriteResult(
                    action=MemoryWriteAction.IGNORED,
                    memory=existing,
                    previous_memory_id=existing.memory_id,
                )

            replacement = self._new_record(
                user_id=normalized_user_id,
                source_thread_id=normalized_thread_id,
                candidate=candidate,
                now=now,
                embedding=embedding,
                version=existing.version + 1,
            )
            superseded = existing.model_copy(
                update={
                    "status": MemoryStatus.SUPERSEDED,
                    "superseded_by": replacement.memory_id,
                    "updated_at": now,
                }
            )
            self.store.replace(superseded)
            self.store.insert(replacement)
            self._profile_changed(replacement)
            return MemoryWriteResult(
                action=MemoryWriteAction.SUPERSEDED,
                memory=replacement,
                previous_memory_id=existing.memory_id,
            )

    def _profile_changed(self, memory):
        if memory.memory_type.value == "profile":
            self.store.mark_profile_dirty(memory.user_id, uuid4().hex)

    @staticmethod
    def _new_record(
        *,
        user_id: str,
        source_thread_id: str,
        candidate: ExtractedMemory,
        now: datetime,
        embedding: list[float] | None,
        version: int = 1,
    ) -> MemoryRecord:
        return MemoryRecord(
            memory_id=f"memory:{uuid4().hex}",
            user_id=user_id,
            memory_type=candidate.memory_type,
            memory_key=candidate.memory_key,
            content=candidate.content,
            structured_content=candidate.structured_content,
            source_thread_id=source_thread_id,
            confidence=candidate.confidence,
            importance=candidate.importance,
            evidence_strength=candidate.evidence_strength,
            created_at=now,
            updated_at=now,
            version=version,
            embedding=embedding,
        )

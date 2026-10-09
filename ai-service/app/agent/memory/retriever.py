from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from .embeddings import MemoryEmbedder
from .models import (
    MemoryRecord,
    MemoryRetrievalRequest,
    MemoryRetrievalResult,
    MemoryType,
    RetrievedMemory,
)
from .store import MemoryStore
from .scopes import infer_scene


class MemoryRetriever:
    """Unified profile SQL lookup and episodic pgvector retrieval boundary."""

    def __init__(
        self, store: MemoryStore, *, embedder: MemoryEmbedder | None = None
    ) -> None:
        self.store = store
        self.embedder = embedder

    def retrieve(self, request: MemoryRetrievalRequest) -> MemoryRetrievalResult:
        if request.scene is None:
            request = request.model_copy(update={"scene": infer_scene(request.query)})
        candidates: list[tuple[MemoryRecord, float]] = []
        if request.memory_type in {None, MemoryType.PROFILE}:
            profiles = self.store.list_active_profiles(
                request.user_id, scene=request.scene, limit=max(request.top_k * 4, 20)
            )
            candidates.extend((memory, self._profile_relevance(memory, request)) for memory in profiles)

        if (
            request.memory_type in {None, MemoryType.EPISODIC}
            and request.query.strip()
            and self.store.has_active_episodes(request.user_id)
        ):
            if self.embedder is None:
                raise RuntimeError("episodic retrieval requires an embedding provider")
            vector = self.embedder.embed_query(request.query)
            candidates.extend(
                (memory, self._episode_relevance(memory, relevance, request))
                for memory, relevance in self.store.search_active_episodes(
                    request.user_id,
                    vector,
                    scene=request.scene,
                    limit=max(request.top_k * 4, 20),
                )
            )

        unique: dict[str, tuple[MemoryRecord, float]] = {}
        for memory, semantic_relevance in candidates:
            recency = self._recency(memory.updated_at)
            score = max(
                0.0,
                min(
                    1.0,
                    semantic_relevance * 0.6
                    + memory.importance * 0.25
                    + recency * 0.15,
                ),
            )
            previous = unique.get(memory.memory_id)
            if previous is None or score > previous[1]:
                unique[memory.memory_id] = (memory, score)

        ranked = sorted(
            unique.values(),
            key=lambda pair: (pair[1], pair[0].updated_at),
            reverse=True,
        )[: request.top_k]
        return MemoryRetrievalResult(
            user_id=request.user_id,
            items=[
                RetrievedMemory(
                    memory_id=memory.memory_id,
                    memory_type=memory.memory_type,
                    content=memory.content,
                    structured_content=memory.structured_content,
                    confidence=memory.confidence,
                    importance=memory.importance,
                    relevance=score,
                    updated_at=memory.updated_at,
                )
                for memory, score in ranked
            ],
        )

    @staticmethod
    def _profile_relevance(
        memory: MemoryRecord, request: MemoryRetrievalRequest
    ) -> float:
        haystack = " ".join(
            [
                memory.memory_key,
                memory.content,
                str(memory.structured_content.get("scene") or ""),
            ]
        ).casefold()
        terms = [
            term.casefold()
            for term in (request.intent, request.scene)
            if term and term.strip()
        ]
        if terms and any(term in haystack for term in terms):
            return 1.0
        query = re.sub(r"[^\w]+", "", request.query.casefold())
        if not query:
            return 0.75 if not terms else 0.65
        fields = (memory.structured_content.get("subject"),
                  memory.structured_content.get("topic"),
                  memory.structured_content.get("scene"))
        for value in fields:
            if isinstance(value, str):
                normalized = re.sub(r"[^\w]+", "", value.casefold())
                if len(normalized) >= 2 and normalized in query:
                    return 1.0
        content = re.sub(r"[^\w]+", "", memory.content.casefold())
        query_pairs = {query[index:index + 2] for index in range(len(query) - 1)}
        content_pairs = {content[index:index + 2] for index in range(len(content) - 1)}
        overlap = len(query_pairs & content_pairs) / len(query_pairs) if query_pairs else 0.0
        return min(1.0, (0.35 if not terms else 0.3) + 0.65 * overlap)

    @staticmethod
    def _episode_relevance(
        memory: MemoryRecord, relevance: float, request: MemoryRetrievalRequest
    ) -> float:
        del memory
        history_cue = re.search(r"上次|之前|曾经|当时|那天|刚才|过去|回忆", request.query)
        return min(1.0, relevance + (0.3 if history_cue else 0.0))

    @staticmethod
    def _recency(updated_at: datetime) -> float:
        value = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - value).total_seconds() / 86400)
        return math.exp(-age_days / 180.0)

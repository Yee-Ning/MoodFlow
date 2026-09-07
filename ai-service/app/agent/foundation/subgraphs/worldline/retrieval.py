from typing import Protocol

from app.agent.foundation.subgraphs.worldline.models import (
    MusicKnowledgeDocument,
    ResolvedEntity,
)


class MusicKnowledgeRetriever(Protocol):
    """Replaceable boundary for Qdrant/BM25/hybrid retrieval."""

    async def retrieve(
        self,
        entity: ResolvedEntity,
        query: str,
    ) -> list[MusicKnowledgeDocument]: ...


class EmptyMusicKnowledgeRetriever:
    """V1 fallback used until the production music knowledge index exists."""

    async def retrieve(
        self,
        entity: ResolvedEntity,
        query: str,
    ) -> list[MusicKnowledgeDocument]:
        return []

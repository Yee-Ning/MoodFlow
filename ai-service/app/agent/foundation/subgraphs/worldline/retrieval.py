from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings

from app.agent.foundation.subgraphs.worldline.models import (
    MusicKnowledgeDocument,
    ResolvedEntity,
)
from app.config.settings import ENV_FILE
from app.rag.query_understanding.models import IndexProfile, SearchPlan
from app.rag.retrieval import (
    HybridRetrievalService,
    RetrievalConfig,
    RetrievalResult,
    RetrievalStatus,
    load_retrieval_config,
    load_retrieval_index_binding,
)

logger = logging.getLogger(__name__)


class MusicKnowledgeRetriever(Protocol):
    """Replaceable boundary for Qdrant/BM25/hybrid retrieval."""

    async def retrieve(
        self,
        entity: ResolvedEntity,
        plan: SearchPlan,
    ) -> list[MusicKnowledgeDocument]: ...


class EmptyMusicKnowledgeRetriever:
    """Explicit fallback used while online retrieval is disabled."""

    async def retrieve(
        self,
        entity: ResolvedEntity,
        plan: SearchPlan,
    ) -> list[MusicKnowledgeDocument]:
        return []


class RetrievalExecutor(Protocol):
    async def retrieve(self, plan: SearchPlan) -> RetrievalResult: ...


class HybridMusicKnowledgeRetriever:
    """Adapt a general candidate pool to WorldLine's bounded document contract."""

    def __init__(
        self,
        service: RetrievalExecutor,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.service = service
        self.config = config or load_retrieval_config()

    async def retrieve(
        self,
        entity: ResolvedEntity,
        plan: SearchPlan,
    ) -> list[MusicKnowledgeDocument]:
        result = await self.service.retrieve(plan)
        _log_retrieval_result(result, shadow=False)
        if result.status in {RetrievalStatus.EMPTY, RetrievalStatus.UNAVAILABLE}:
            return []
        return self._documents(result)

    def _documents(self, result: RetrievalResult) -> list[MusicKnowledgeDocument]:
        documents: list[MusicKnowledgeDocument] = []
        used_tokens = 0
        selection = self.config.selection
        for candidate in result.candidates:
            candidate_tokens = max(candidate.expanded_token_count, 1)
            if (
                documents
                and used_tokens + candidate_tokens > selection.worldline_token_budget
            ):
                continue
            metadata = {
                "point_id": candidate.point_id,
                "chunk_id": candidate.chunk_id,
                "parent_id": candidate.parent_id,
                "doc_id": candidate.doc_id,
                "matched_child_ids": candidate.matched_child_ids,
                "entity_ids": candidate.entity_ids,
                "supported_claims": candidate.supported_claims,
                "fusion_score": candidate.fusion_score,
                "channel_evidence": [
                    item.model_dump(mode="json") for item in candidate.channel_evidence
                ],
                "retrieval_status": result.status.value,
                "retrieval_version": self.config.retrieval_version,
                "index_profile": result.index_profile.model_dump(mode="json"),
                "retrieval_warnings": result.warnings,
            }
            documents.append(
                MusicKnowledgeDocument(
                    id=candidate.parent_id,
                    title=candidate.title,
                    content=candidate.expanded_content[:8_000],
                    source=candidate.source_name,
                    url=candidate.source_url,
                    metadata=metadata,
                )
            )
            used_tokens += candidate_tokens
            if len(documents) >= selection.worldline_document_limit:
                break
        return documents


class ShadowMusicKnowledgeRetriever:
    """Execute and observe retrieval without changing user-visible answers."""

    def __init__(self, service: RetrievalExecutor) -> None:
        self.service = service

    async def retrieve(
        self,
        entity: ResolvedEntity,
        plan: SearchPlan,
    ) -> list[MusicKnowledgeDocument]:
        result = await self.service.retrieve(plan)
        _log_retrieval_result(result, shadow=True)
        return []


class RetrievalRuntimeSettings(BaseSettings):
    model_config = ConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    retrieval_mode: Literal["empty", "shadow", "hybrid"] = Field(
        default="empty",
        validation_alias="AI_RAG_RETRIEVAL_MODE",
    )
    index_manifest: str | None = Field(
        default=None,
        validation_alias="AI_RAG_INDEX_MANIFEST",
    )


@dataclass(frozen=True, slots=True)
class WorldLineRetrievalRuntime:
    retriever: MusicKnowledgeRetriever
    index_profile: IndexProfile | None = None


def create_worldline_retrieval_runtime(
    *,
    settings: RetrievalRuntimeSettings | None = None,
    project_root: Path | None = None,
) -> WorldLineRetrievalRuntime:
    runtime_settings = settings or RetrievalRuntimeSettings()
    if runtime_settings.retrieval_mode == "empty":
        return WorldLineRetrievalRuntime(EmptyMusicKnowledgeRetriever())
    raw_manifest = (runtime_settings.index_manifest or "").strip()
    if not raw_manifest:
        raise ValueError("AI_RAG_INDEX_MANIFEST is required for online retrieval")
    root = (project_root or Path(__file__).resolve().parents[5]).resolve()
    config = load_retrieval_config()
    binding = load_retrieval_index_binding(root, Path(raw_manifest), config)
    service = HybridRetrievalService(binding, config=config)
    if runtime_settings.retrieval_mode == "shadow":
        retriever: MusicKnowledgeRetriever = ShadowMusicKnowledgeRetriever(service)
    else:
        retriever = HybridMusicKnowledgeRetriever(service, config)
    return WorldLineRetrievalRuntime(retriever, binding.profile)


@lru_cache
def get_worldline_retrieval_runtime() -> WorldLineRetrievalRuntime:
    return create_worldline_retrieval_runtime()


def _log_retrieval_result(result: RetrievalResult, *, shadow: bool) -> None:
    logger.info(
        "worldline.retrieval_result",
        extra={
            "plan_id": result.plan_id,
            "collection_name": result.index_profile.collection_name,
            "retrieval_status": result.status.value,
            "candidate_count": len(result.candidates),
            "failed_channels": [item.value for item in result.failed_channels],
            "retrieval_warnings": result.warnings,
            "retrieval_total_ms": round(result.timings.total_ms, 2),
            "shadow": shadow,
        },
    )

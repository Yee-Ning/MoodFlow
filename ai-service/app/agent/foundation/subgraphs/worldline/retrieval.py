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
from app.rag.context_packing import (
    ContextPack,
    ContextPackBuilder,
    EvidenceSufficiencyStatus,
)
from app.rag.query_understanding.models import IndexProfile, SearchPlan
from app.rag.reranking import (
    CandidateReranker,
    QwenCandidateReranker,
    RerankedCandidate,
    RerankResult,
    RerankStatus,
    load_rerank_config,
)
from app.rag.retrieval import (
    HybridRetrievalService,
    RetrievalCandidate,
    RetrievalConfig,
    RetrievalResult,
    RetrievalStatus,
    load_retrieval_config,
    load_retrieval_index_binding,
)

logger = logging.getLogger(__name__)
RerankMode = Literal["off", "shadow", "on"]


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


class CandidatePoolProvider(Protocol):
    """Produce the fused and diversified pre-rerank candidate pool."""

    async def retrieve(self, plan: SearchPlan) -> RetrievalResult: ...


class WorldLineContextAdapter:
    """Adapt ranked candidates to WorldLine's final 6-10 document context."""

    def __init__(
        self,
        config: RetrievalConfig | None = None,
        *,
        pack_builder: ContextPackBuilder | None = None,
    ) -> None:
        self.config = config or load_retrieval_config()
        self.pack_builder = pack_builder or ContextPackBuilder()

    def build_pack(
        self,
        plan: SearchPlan,
        result: RetrievalResult | RerankResult,
    ) -> ContextPack:
        return self.pack_builder.build(plan, result)

    def adapt(
        self,
        result: RetrievalResult | RerankResult,
        plan: SearchPlan | None = None,
    ) -> list[MusicKnowledgeDocument]:
        if plan is not None:
            pack = self.build_pack(plan, result)
            _log_context_pack(pack)
            if pack.sufficiency.status is not EvidenceSufficiencyStatus.SUFFICIENT:
                return []
            return _pack_documents(pack, result, self.config)
        # Backward-compatible path for callers that do not propagate SearchPlan.
        if _result_has_no_context(result):
            return []
        documents: list[MusicKnowledgeDocument] = []
        used_tokens = 0
        selection = self.config.selection
        for candidate, reranked in _iter_ranked_candidates(result):
            candidate_tokens = max(
                candidate.parent_context.token_count,
                candidate.primary_child_token_count,
                1,
            )
            if (
                documents
                and used_tokens + candidate_tokens > selection.worldline_token_budget
            ):
                continue
            metadata: dict[str, object] = {
                "point_id": candidate.point_id,
                "chunk_id": candidate.primary_child_id,
                "parent_id": candidate.parent_id,
                "doc_id": candidate.doc_id,
                "matched_child_ids": candidate.matched_child_ids,
                "entity_ids": candidate.entity_ids,
                "supported_claims": candidate.supported_claims,
                "fusion_score": candidate.fusion_score,
                "channel_evidence": [
                    item.model_dump(mode="json") for item in candidate.channel_evidence
                ],
                "retrieval_status": _retrieval_status(result).value,
                "retrieval_version": self.config.retrieval_version,
                "index_profile": result.index_profile.model_dump(mode="json"),
                "retrieval_warnings": _retrieval_warnings(result),
            }
            if reranked is None:
                metadata["rerank_status"] = "off"
            else:
                metadata.update(
                    {
                        "retrieval_rank": reranked.retrieval_rank,
                        "rerank_rank": reranked.rerank_rank,
                        "rerank_score": reranked.rerank_score,
                        "rerank_status": result.status.value,
                        "rerank_version": result.rerank_version,
                        "rerank_prompt_version": result.prompt_version,
                        "rerank_model": result.model_name,
                        "rerank_model_revision": result.model_revision,
                        "rerank_fallback_used": result.fallback_used,
                        "rerank_warnings": result.warnings,
                    }
                )
            documents.append(
                MusicKnowledgeDocument(
                    id=candidate.parent_id,
                    title=candidate.title,
                    content=_expand_parent_context(candidate)[:8_000],
                    source=candidate.source_name,
                    url=candidate.source_url,
                    metadata=metadata,
                )
            )
            used_tokens += candidate_tokens
            if len(documents) >= selection.worldline_document_limit:
                break
        return documents


def _result_has_no_context(result: RetrievalResult | RerankResult) -> bool:
    if isinstance(result, RerankResult):
        return result.status in {RerankStatus.EMPTY, RerankStatus.UNAVAILABLE}
    return result.status in {RetrievalStatus.EMPTY, RetrievalStatus.UNAVAILABLE}


def _iter_ranked_candidates(
    result: RetrievalResult | RerankResult,
) -> list[tuple[RetrievalCandidate, RerankedCandidate | None]]:
    if isinstance(result, RerankResult):
        return [(item.candidate, item) for item in result.candidates]
    return [(candidate, None) for candidate in result.candidates]


def _retrieval_status(result: RetrievalResult | RerankResult) -> RetrievalStatus:
    if isinstance(result, RerankResult):
        return result.candidate_pool_status
    return result.status


def _retrieval_warnings(result: RetrievalResult | RerankResult) -> list[str]:
    if isinstance(result, RerankResult):
        return result.candidate_pool_warnings
    return result.warnings


def _expand_parent_context(candidate: RetrievalCandidate) -> str:
    """Perform Parent Expansion only for candidates selected for WorldLine."""
    parts = [
        value.strip()
        for value in (
            candidate.parent_context.breadcrumb,
            candidate.primary_child_breadcrumb,
            candidate.parent_context.content,
        )
        if value.strip()
    ]
    return "\n\n".join(dict.fromkeys(parts))


def _pack_documents(
    pack: ContextPack,
    result: RetrievalResult | RerankResult,
    config: RetrievalConfig,
) -> list[MusicKnowledgeDocument]:
    ranked = {
        candidate.parent_id: (candidate, reranked)
        for candidate, reranked in _iter_ranked_candidates(result)
    }
    documents: list[MusicKnowledgeDocument] = []
    for item in pack.items[: config.selection.worldline_document_limit]:
        candidate, reranked = ranked[item.parent_id]
        metadata: dict[str, object] = {
            "point_id": candidate.point_id,
            "source_id": item.source_id,
            "document_metadata": item.document_metadata,
            **{key: candidate.metadata[key] for key in (
                "source_locator", "source_content_sha256", "parent_content_sha256",
                "source_release_id", "knowledge_input_hash", "index_input_hash", "source_published_at",
                "source_response_sha256",
            ) if key in candidate.metadata},
            "chunk_id": item.child_id,
            "parent_id": item.parent_id,
            "doc_id": item.doc_id,
            "matched_child_ids": candidate.matched_child_ids,
            "entity_ids": item.entity_ids,
            "supported_claims": candidate.supported_claims,
            "fusion_score": candidate.fusion_score,
            "channel_evidence": [
                evidence.model_dump(mode="json")
                for evidence in candidate.channel_evidence
            ],
            "retrieval_status": _retrieval_status(result).value,
            "retrieval_version": config.retrieval_version,
            "index_profile": result.index_profile.model_dump(mode="json"),
            "retrieval_warnings": _retrieval_warnings(result),
            "context_version": pack.context_version,
            "context_warnings": pack.warnings,
            "context_pack_rank": item.pack_rank,
            "context_source_rank": item.source_rank,
            "context_token_count": item.token_count,
            "context_token_budget": pack.token_budget,
            "evidence_span": item.exact_text,
            "subquery_ids": item.subquery_ids,
            "retrieval_legs": item.retrieval_legs,
            "sufficiency_status": pack.sufficiency.status.value,
        }
        if item.relation_source_contexts:
            metadata["relation_source_contexts"] = [c.model_dump(mode="json") for c in item.relation_source_contexts]
        if item.source_evidence_content is not None:
            metadata["source_evidence_content"] = item.source_evidence_content
        if reranked is None:
            metadata["rerank_status"] = "off"
        else:
            metadata.update(
                {
                    "retrieval_rank": reranked.retrieval_rank,
                    "rerank_rank": reranked.rerank_rank,
                    "rerank_score": reranked.rerank_score,
                    "rerank_status": result.status.value,
                    "rerank_version": result.rerank_version,
                    "rerank_prompt_version": result.prompt_version,
                    "rerank_model": result.model_name,
                    "rerank_model_revision": result.model_revision,
                    "rerank_fallback_used": result.fallback_used,
                    "rerank_warnings": result.warnings,
                }
            )
        documents.append(
            MusicKnowledgeDocument(
                id=item.parent_id,
                title=item.title,
                content=item.context,
                source=item.source_name,
                url=item.source_url,
                metadata=metadata,
            )
        )
    return documents


def _log_context_pack(pack: ContextPack) -> None:
    logger.info(
        "worldline.context_pack",
        extra={
            "plan_id": pack.plan_id,
            "context_version": pack.context_version,
            "sufficiency_status": pack.sufficiency.status.value,
            "sufficiency_reasons": pack.sufficiency.reasons,
            "context_count": len(pack.items),
            "context_token_count": pack.token_count,
            "context_token_budget": pack.token_budget,
            "missing_subquery_ids": pack.sufficiency.missing_subquery_ids,
            "missing_entity_ids": pack.sufficiency.missing_entity_ids,
            "context_warnings": pack.warnings,
        },
    )


class HybridMusicKnowledgeRetriever:
    """Orchestrate the candidate-pool boundary consumed by WorldLine."""

    def __init__(
        self,
        service: CandidatePoolProvider,
        config: RetrievalConfig | None = None,
        *,
        context_adapter: WorldLineContextAdapter | None = None,
        reranker: CandidateReranker | None = None,
        rerank_mode: RerankMode = "off",
    ) -> None:
        self.candidate_provider = service
        self.config = config or load_retrieval_config()
        self.context_adapter = context_adapter or WorldLineContextAdapter(self.config)
        self.reranker = reranker
        self.rerank_mode = rerank_mode
        if rerank_mode != "off" and reranker is None:
            raise ValueError("reranker is required when reranking is enabled")

    async def retrieve(
        self,
        entity: ResolvedEntity,
        plan: SearchPlan,
    ) -> list[MusicKnowledgeDocument]:
        candidate_pool = await self.candidate_provider.retrieve(plan)
        _log_retrieval_result(candidate_pool, shadow=False)
        if self.rerank_mode == "off":
            return self.context_adapter.adapt(candidate_pool, plan)
        assert self.reranker is not None
        reranked = await self.reranker.rerank(plan, candidate_pool)
        _log_rerank_result(reranked, shadow=self.rerank_mode == "shadow")
        if self.rerank_mode == "shadow":
            return self.context_adapter.adapt(candidate_pool, plan)
        return self.context_adapter.adapt(reranked, plan)


class ShadowMusicKnowledgeRetriever:
    """Execute and observe retrieval without changing user-visible answers."""

    def __init__(
        self,
        service: CandidatePoolProvider,
        *,
        reranker: CandidateReranker | None = None,
        rerank_mode: RerankMode = "off",
    ) -> None:
        self.service = service
        self.reranker = reranker
        self.rerank_mode = rerank_mode
        if rerank_mode != "off" and reranker is None:
            raise ValueError("reranker is required when reranking is enabled")

    async def retrieve(
        self,
        entity: ResolvedEntity,
        plan: SearchPlan,
    ) -> list[MusicKnowledgeDocument]:
        result = await self.service.retrieve(plan)
        _log_retrieval_result(result, shadow=True)
        if self.rerank_mode != "off":
            assert self.reranker is not None
            reranked = await self.reranker.rerank(plan, result)
            _log_rerank_result(reranked, shadow=True)
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
    rerank_mode: RerankMode = Field(
        default="off",
        validation_alias="AI_RAG_RERANK_MODE",
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
    reranker: CandidateReranker | None = None
    if runtime_settings.rerank_mode != "off":
        reranker = QwenCandidateReranker(load_rerank_config())
    if runtime_settings.retrieval_mode == "shadow":
        retriever: MusicKnowledgeRetriever = ShadowMusicKnowledgeRetriever(
            service,
            reranker=reranker,
            rerank_mode=runtime_settings.rerank_mode,
        )
    else:
        retriever = HybridMusicKnowledgeRetriever(
            service,
            config,
            reranker=reranker,
            rerank_mode=runtime_settings.rerank_mode,
        )
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


def _log_rerank_result(result: RerankResult, *, shadow: bool) -> None:
    logger.info(
        "worldline.rerank_result",
        extra={
            "plan_id": result.plan_id,
            "collection_name": result.index_profile.collection_name,
            "rerank_status": result.status.value,
            "candidate_count": result.input_count,
            "reranked_count": len(result.candidates),
            "rerank_model": result.model_name,
            "rerank_model_revision": result.model_revision,
            "rerank_version": result.rerank_version,
            "rerank_prompt_version": result.prompt_version,
            "rerank_fallback_used": result.fallback_used,
            "rerank_warnings": result.warnings,
            "rerank_inference_ms": round(result.timings.inference_ms, 2),
            "rerank_total_ms": round(result.timings.total_ms, 2),
            "shadow": shadow,
        },
    )

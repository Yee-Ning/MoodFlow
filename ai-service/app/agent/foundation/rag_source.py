"""Read-only hybrid RAG Source; selection remains in the L1 code pipeline."""

import asyncio
import hashlib
import json
import logging
import math
from functools import lru_cache
from html import escape
from pathlib import Path
from threading import RLock

from app.rag.query_understanding.models import (
    MetadataFilter, QueryType, QueryVariant, QueryVariantKind,
    RetrievalStrategy, SearchChannel, SearchPlan,
)
from app.rag.reranking.config import load_rerank_config
from app.rag.reranking.runtime import QwenRerankScorer
from app.rag.reranking.service import QwenCandidateReranker
from app.rag.retrieval.binding import load_retrieval_index_binding
from app.rag.retrieval.config import load_retrieval_config
from app.rag.retrieval.encoder import QwenQueryEncoder
from app.rag.retrieval.service import HybridRetrievalService
from app.rag.retrieval.models import RetrievalStatus

from .rag_usage import AccountedQueryEncoder, AccountedRerankScorer, RetrievalUsage

logger = logging.getLogger(__name__)


class SourceItems(list):
    """A list-compatible observation sidecar, never a source for later selection."""
    def __init__(self, items=(), *, audit=None, status=None):
        super().__init__(items)
        self.audit = audit
        self.status = status
        self.source_audit = {}


@lru_cache
def _local_models():
    retrieval, rerank = load_retrieval_config(), load_rerank_config()
    # Models are lazy and shared; request wrappers and asyncio locks are not.
    return (QwenQueryEncoder(retrieval.dense), RLock(),
            QwenRerankScorer(rerank.model), RLock())


def literal_search_plan(query, profile, limit):
    """No generation, query rewrite or HyDE: one literal hybrid query."""
    text = " ".join(query.split())[:1000]
    if not text:
        raise ValueError("rag_query_blank")
    limit = min(limit, load_rerank_config().maximum_candidates)
    identity = json.dumps([query, profile.model_dump(), limit, "rag-source-v1"],
                          sort_keys=True, ensure_ascii=False)
    return SearchPlan(plan_id="query-plan:" + hashlib.sha256(identity.encode()).hexdigest()[:24],
        original_query=query[:4000], standalone_query=text, query_types=[QueryType.EXPLORATORY],
        queries=[QueryVariant(text=text, kind=QueryVariantKind.ORIGINAL, channel=SearchChannel.BOTH, weight=1)],
        metadata_filter=MetadataFilter(), strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=limit, final_top_k=min(limit, load_rerank_config().top_k),
        needs_rerank=True, analysis_version="v1", prompt_version="v1", index_profile=profile)


class RagSource:
    name = "rag"

    def __init__(self, *, settings=None, binding=None, encoder=None, scorer=None, backend=None):
        self.settings, self.binding = settings, binding
        self.encoder, self.scorer, self.backend = encoder, scorer, backend

    def collect(self, request, *, limit):
        from .subgraphs.worldline.retrieval import RetrievalRuntimeSettings
        usage = RetrievalUsage()
        audit = {"model_usage": usage.records, "collection": {"mode": "empty", "rerank_enabled": True,
                 "retrieval_status": "empty", "rerank_status": "empty", "candidate_limit": limit,
                 "rejected_without_provenance": 0, "warnings": []}}
        try:
            settings = self.settings or RetrievalRuntimeSettings()
            audit["collection"]["mode"] = settings.retrieval_mode
            if settings.retrieval_mode == "empty" or not request.query.strip() or limit <= 0:
                return SourceItems(audit=audit)
            config, rerank_config = load_retrieval_config(), load_rerank_config()
            binding = self.binding
            if binding is None:
                if not (settings.index_manifest or "").strip():
                    raise ValueError("rag_index_manifest_missing")
                binding = load_retrieval_index_binding(Path(__file__).resolve().parents[3],
                                                       Path(settings.index_manifest), config)
            encoder, encoder_lock, scorer, scorer_lock = _local_models()
            service = HybridRetrievalService(binding, config=config, backend=self.backend,
                encoder=AccountedQueryEncoder(self.encoder or encoder, config.dense, usage, encoder_lock))
            reranker = QwenCandidateReranker(rerank_config,
                scorer=AccountedRerankScorer(self.scorer or scorer, rerank_config.model, usage, scorer_lock))
            plan = literal_search_plan(request.query, binding.profile, limit)
            items = asyncio.run(self._run(service, reranker, plan, usage, audit))
            usage.check()  # Existing retriever/reranker fallbacks must not swallow budget refusal.
            if audit["collection"]["retrieval_status"] == "unavailable":
                logger.warning("context.rag_source unavailable code=retrieval_unavailable")
                return SourceItems(audit=audit, status="unavailable:retrieval")
            return SourceItems(items if settings.retrieval_mode == "hybrid" else (), audit=audit)
        except Exception as exc:
            usage.check()
            if "plan_id" not in audit["collection"]:
                audit["collection"]["retrieval_status"] = "unavailable"
            audit["collection"]["warnings"].append("rag_source_unavailable:" + type(exc).__name__)
            logger.warning("context.rag_source unavailable error=%s", type(exc).__name__)
            return SourceItems(audit=audit, status="unavailable:" + type(exc).__name__)

    async def _run(self, service, reranker, plan, usage, audit):
        try:
            return await self._retrieve(service, reranker, plan, usage, audit)
        finally:
            if self.backend is None:
                await service.backend.client.close()

    async def _retrieve(self, service, reranker, plan, usage, audit):
        from .context_sources import source_item
        result = await service.retrieve(plan)
        usage.check()
        observation = audit["collection"]
        observation.update(plan_id=plan.plan_id, index_profile=plan.index_profile.model_dump(),
                           retrieval_status=result.status.value)
        observation["warnings"].extend(result.warnings)
        # Provenance is mandatory even for rerank input. Do not trust bypassed
        # model validation or an oversized backend result at this boundary.
        valid = [candidate for candidate in result.candidates
                 if all(value.strip() for value in (candidate.doc_id, candidate.source_id,
                        candidate.source_name, candidate.primary_child_id, candidate.primary_child_content))
                 and math.isfinite(candidate.fusion_score)]
        observation["rejected_without_provenance"] = len(result.candidates) - len(valid)
        bounded = result.model_copy(update={"candidates": valid[:plan.candidate_top_k],
            "status": RetrievalStatus.EMPTY if not valid and result.status is not RetrievalStatus.UNAVAILABLE
                      else result.status})
        reranked = await reranker.rerank(plan, bounded)
        usage.check()
        observation.update(rerank_status=reranked.status.value, rerank_input_count=reranked.input_count,
                           rerank_fallback_used=reranked.fallback_used)
        observation["warnings"].extend(reranked.warnings)
        items = []
        for ranked in reranked.candidates:
            candidate = ranked.candidate
            score = ranked.rerank_score
            citation = escape(candidate.source_id, quote=True)
            metadata = {"evidence_id": candidate.parent_id, "citation": citation,
                "doc_id": candidate.doc_id, "source_id": candidate.source_id,
                "source_name": candidate.source_name,
                "source_url": str(candidate.source_url) if candidate.source_url else None,
                "title": candidate.title, "chunk_id": candidate.primary_child_id,
                "fusion_score": candidate.fusion_score, "rerank_score": score,
                "score": score if score is not None else candidate.fusion_score,
                "score_basis": "rerank" if score is not None else "rrf_fallback",
                "retrieval_rank": ranked.retrieval_rank, "rerank_rank": ranked.rerank_rank}
            # Child content only: Parent expansion is not a second hidden L1 input.
            # Keep the evidence identity for legacy dedup, but namespace item
            # IDs so trace/manifest rows cannot alias the rag_pack side channel.
            item = source_item("evidence:rag:" + candidate.parent_id, "rag_evidence",
                candidate.primary_child_content, source=self.name, intents=["worldline"],
                metadata=metadata, references=[candidate.doc_id, candidate.source_id])
            items.append(item.model_copy(update={"relevance": score if score is not None
                                                else candidate.fusion_score / (1 + candidate.fusion_score)}))
        return items

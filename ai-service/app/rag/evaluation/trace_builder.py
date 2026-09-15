"""Build canonical evaluation traces from current retrieval and rerank contracts."""

from __future__ import annotations

from collections.abc import Sequence

from app.rag.evaluation.models import (
    EvaluationContext,
    EvaluationVersions,
    RAGEvaluationTrace,
    TraceStageStatuses,
)
from app.rag.query_understanding.models import SearchPlan
from app.rag.reranking.models import RerankResult
from app.rag.retrieval.models import RetrievalCandidate, RetrievalResult


def build_evaluation_trace(
    *,
    run_id: str,
    case_id: str,
    plan: SearchPlan,
    retrieval: RetrievalResult,
    rerank: RerankResult | None = None,
    final_context: Sequence[EvaluationContext] = (),
    actual_answer: str | None = None,
    citation_parent_ids: Sequence[str] = (),
    context_token_count: int | None = None,
    context_token_budget: int | None = None,
    retrieval_version: str | None = None,
    context_version: str | None = None,
    context_status: str | None = None,
    generation_prompt_version: str | None = None,
    generation_model: str | None = None,
    generation_status: str = "not_run",
) -> RAGEvaluationTrace:
    if retrieval.plan_id != plan.plan_id:
        raise ValueError("evaluation trace retrieval plan mismatch")
    if rerank is not None and rerank.plan_id != plan.plan_id:
        raise ValueError("evaluation trace rerank plan mismatch")
    candidates = [
        _candidate_context(candidate, rank)
        for rank, candidate in enumerate(retrieval.candidates[:40], start=1)
    ]
    reranked = (
        [
            _candidate_context(item.candidate, item.rerank_rank)
            for item in rerank.candidates[:12]
        ]
        if rerank is not None
        else []
    )
    warnings = list(
        dict.fromkeys(
            [
                *plan.warnings,
                *retrieval.warnings,
                *(rerank.warnings if rerank is not None else []),
            ]
        )
    )
    timings = {
        "retrieval_total": retrieval.timings.total_ms,
        "retrieval_embedding": retrieval.timings.embedding_ms,
        "retrieval_qdrant": retrieval.timings.qdrant_ms,
        "retrieval_processing": retrieval.timings.processing_ms,
    }
    if rerank is not None:
        timings.update(
            {
                "rerank_total": rerank.timings.total_ms,
                "rerank_inference": rerank.timings.inference_ms,
            }
        )
    return RAGEvaluationTrace(
        run_id=run_id,
        case_id=case_id,
        query=plan.original_query,
        standalone_query=plan.standalone_query,
        statuses=TraceStageStatuses(
            query_understanding="ready",
            retrieval=retrieval.status.value,
            rerank=rerank.status.value if rerank is not None else "not_run",
            context=(
                context_status
                if context_status is not None
                else ("success" if final_context else "not_run")
            ),
            generation=generation_status,
        ),
        candidate_pool=candidates,
        reranked_context=reranked,
        final_context=list(final_context),
        actual_answer=actual_answer,
        citation_parent_ids=list(citation_parent_ids),
        fallback_used=rerank.fallback_used if rerank is not None else False,
        context_token_count=context_token_count,
        context_token_budget=context_token_budget,
        timings_ms=timings,
        versions=EvaluationVersions(
            index_release=retrieval.index_profile.source_release_id,
            index_schema=retrieval.index_profile.index_schema_version,
            query_analysis=plan.analysis_version,
            retrieval=retrieval_version,
            rerank=rerank.rerank_version if rerank is not None else None,
            rerank_prompt=rerank.prompt_version if rerank is not None else None,
            context=context_version,
            generation_prompt=generation_prompt_version,
            generation_model=generation_model,
        ),
        warnings=warnings,
    )


def _candidate_context(
    candidate: RetrievalCandidate,
    rank: int,
) -> EvaluationContext:
    return EvaluationContext(
        parent_id=candidate.parent_id,
        rank=rank,
        title=candidate.title,
        content=candidate.primary_child_content,
        source_id=candidate.source_id,
        source_name=candidate.source_name,
        source_url=candidate.source_url,
        matched_child_id=candidate.primary_child_id,
        token_count=candidate.primary_child_token_count,
    )

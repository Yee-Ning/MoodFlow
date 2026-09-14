"""Build bounded reranker inputs from Parent candidates' best matched Child."""

from __future__ import annotations

from app.rag.query_understanding.models import SearchPlan
from app.rag.retrieval.models import RetrievalCandidate, RetrievalResult

from .config import RerankConfig
from .models import RerankInput


def build_rerank_inputs(
    plan: SearchPlan,
    candidate_pool: RetrievalResult,
    config: RerankConfig,
) -> list[RerankInput]:
    if candidate_pool.plan_id != plan.plan_id:
        raise ValueError("rerank_plan_id_mismatch")
    if plan.index_profile != candidate_pool.index_profile:
        raise ValueError("rerank_index_profile_mismatch")
    if len(candidate_pool.candidates) > config.maximum_candidates:
        raise ValueError("rerank_candidate_pool_too_large")
    result: list[RerankInput] = []
    for index, candidate in enumerate(candidate_pool.candidates):
        evidence = candidate.channel_evidence
        result.append(
            RerankInput(
                candidate_index=index,
                parent_id=candidate.parent_id,
                query=plan.standalone_query,
                document=_document_text(candidate),
                subquery_ids=list(
                    dict.fromkeys(item.subquery_id for item in evidence)
                ),
                retrieval_legs=list(
                    dict.fromkeys(item.retrieval_leg for item in evidence)
                ),
            )
        )
    return result


def _document_text(candidate: RetrievalCandidate) -> str:
    parts = [
        f"Evidence: {candidate.primary_child_content.strip()}",
        f"Title: {candidate.title.strip()}",
        (
            f"Breadcrumb: {candidate.primary_child_breadcrumb.strip()}"
            if candidate.primary_child_breadcrumb.strip()
            else ""
        ),
    ]
    return "\n".join(item for item in parts if item)

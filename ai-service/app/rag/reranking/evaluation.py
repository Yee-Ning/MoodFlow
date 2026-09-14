"""Offline quality metrics for the retrieval-to-rerank boundary."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.rag.retrieval.evaluation import RetrievalEvaluationCase


class RerankCaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    candidate_count: int = Field(ge=0, le=200)
    reranked_count: int = Field(ge=0, le=15)
    candidate_recall_at_40: float = Field(ge=0, le=1)
    rerank_recall_at_12: float = Field(ge=0, le=1)
    candidate_reciprocal_rank_at_10: float = Field(ge=0, le=1)
    rerank_reciprocal_rank_at_10: float = Field(ge=0, le=1)
    candidate_ndcg_at_10: float = Field(ge=0, le=1)
    rerank_ndcg_at_10: float = Field(ge=0, le=1)
    fallback_used: bool = False
    latency_ms: float = Field(default=0, ge=0)


class RerankEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_count: int = Field(ge=1)
    candidate_recall_at_40: float = Field(ge=0, le=1)
    rerank_recall_at_12: float = Field(ge=0, le=1)
    candidate_mrr_at_10: float = Field(ge=0, le=1)
    rerank_mrr_at_10: float = Field(ge=0, le=1)
    mrr_at_10_delta: float = Field(ge=-1, le=1)
    candidate_ndcg_at_10: float = Field(ge=0, le=1)
    rerank_ndcg_at_10: float = Field(ge=0, le=1)
    ndcg_at_10_delta: float = Field(ge=-1, le=1)
    fallback_rate: float = Field(ge=0, le=1)
    latency_p50_ms: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    cases: list[RerankCaseMetrics] = Field(min_length=1)


def evaluate_reranking(
    cases: Sequence[RetrievalEvaluationCase],
    candidate_rankings: Mapping[str, Sequence[str]],
    reranked_rankings: Mapping[str, Sequence[str]],
    *,
    fallback_by_case: Mapping[str, bool] | None = None,
    latency_ms_by_case: Mapping[str, float] | None = None,
) -> RerankEvaluationReport:
    """Compare pre-rerank Top-40 and post-rerank Top-12 quality."""
    if not cases:
        raise ValueError("rerank evaluation requires at least one case")
    fallbacks = fallback_by_case or {}
    latencies = latency_ms_by_case or {}
    scores = [
        _score_case(
            case,
            _unique(candidate_rankings.get(case.case_id, ()))[:40],
            _unique(reranked_rankings.get(case.case_id, ()))[:12],
            fallback_used=bool(fallbacks.get(case.case_id, False)),
            latency_ms=float(latencies.get(case.case_id, 0)),
        )
        for case in cases
    ]
    count = len(scores)
    candidate_mrr = _mean(
        [item.candidate_reciprocal_rank_at_10 for item in scores]
    )
    rerank_mrr = _mean([item.rerank_reciprocal_rank_at_10 for item in scores])
    candidate_ndcg = _mean([item.candidate_ndcg_at_10 for item in scores])
    rerank_ndcg = _mean([item.rerank_ndcg_at_10 for item in scores])
    ordered_latencies = sorted(item.latency_ms for item in scores)
    return RerankEvaluationReport(
        case_count=count,
        candidate_recall_at_40=_mean(
            [item.candidate_recall_at_40 for item in scores]
        ),
        rerank_recall_at_12=_mean([item.rerank_recall_at_12 for item in scores]),
        candidate_mrr_at_10=candidate_mrr,
        rerank_mrr_at_10=rerank_mrr,
        mrr_at_10_delta=rerank_mrr - candidate_mrr,
        candidate_ndcg_at_10=candidate_ndcg,
        rerank_ndcg_at_10=rerank_ndcg,
        ndcg_at_10_delta=rerank_ndcg - candidate_ndcg,
        fallback_rate=sum(item.fallback_used for item in scores) / count,
        latency_p50_ms=_percentile(ordered_latencies, 0.50),
        latency_p95_ms=_percentile(ordered_latencies, 0.95),
        cases=scores,
    )


def _score_case(
    case: RetrievalEvaluationCase,
    candidates: Sequence[str],
    reranked: Sequence[str],
    *,
    fallback_used: bool,
    latency_ms: float,
) -> RerankCaseMetrics:
    if not math.isfinite(latency_ms) or latency_ms < 0:
        raise ValueError(f"rerank evaluation latency invalid:{case.case_id}")
    relevant = set(case.relevant_parent_ids)
    return RerankCaseMetrics(
        case_id=case.case_id,
        candidate_count=len(candidates),
        reranked_count=len(reranked),
        candidate_recall_at_40=_recall(candidates, relevant, 40),
        rerank_recall_at_12=_recall(reranked, relevant, 12),
        candidate_reciprocal_rank_at_10=_reciprocal_rank(candidates, relevant, 10),
        rerank_reciprocal_rank_at_10=_reciprocal_rank(reranked, relevant, 10),
        candidate_ndcg_at_10=_ndcg(candidates, relevant, 10),
        rerank_ndcg_at_10=_ndcg(reranked, relevant, 10),
        fallback_used=fallback_used,
        latency_ms=latency_ms,
    )


def _unique(ranking: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(ranking))


def _recall(ranking: Sequence[str], relevant: set[str], k: int) -> float:
    return len(relevant.intersection(ranking[:k])) / len(relevant)


def _reciprocal_rank(
    ranking: Sequence[str],
    relevant: set[str],
    k: int,
) -> float:
    for rank, parent_id in enumerate(ranking[:k], start=1):
        if parent_id in relevant:
            return 1 / rank
    return 0


def _ndcg(ranking: Sequence[str], relevant: set[str], k: int) -> float:
    dcg = sum(
        1 / math.log2(rank + 1)
        for rank, parent_id in enumerate(ranking[:k], start=1)
        if parent_id in relevant
    )
    ideal = sum(
        1 / math.log2(rank + 1)
        for rank in range(1, min(len(relevant), k) + 1)
    )
    return dcg / ideal


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _percentile(ordered: Sequence[float], percentile: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

"""Deterministic quality report over canonical RAG traces."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.rag.evaluation.models import (
    AnnotationStatus,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
)
from app.rag.reranking.evaluation import RerankEvaluationReport, evaluate_reranking
from app.rag.retrieval.evaluation import (
    RetrievalEvaluationCase,
    RetrievalEvaluationReport,
    evaluate_candidate_pool,
)


class DeterministicEvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "v0"
    dataset_case_count: int = Field(ge=1)
    approved_case_count: int = Field(ge=1)
    evaluated_answerable_count: int = Field(ge=1)
    trace_count: int = Field(ge=1)
    expected_outcome_accuracy: float = Field(ge=0, le=1)
    retrieval: RetrievalEvaluationReport
    rerank: RerankEvaluationReport
    final_context_recall: float = Field(ge=0, le=1)
    citation_presence_rate: float = Field(ge=0, le=1)
    citation_id_validity: float = Field(ge=0, le=1)
    token_budget_compliance: float | None = Field(default=None, ge=0, le=1)
    token_budget_case_count: int = Field(ge=0)


def evaluate_traces(
    cases: Sequence[RAGEvaluationCase],
    traces: Sequence[RAGEvaluationTrace],
) -> DeterministicEvaluationReport:
    if not cases:
        raise ValueError("RAG evaluation requires at least one dataset case")
    trace_by_case = {item.case_id: item for item in traces}
    if len(trace_by_case) != len(traces):
        raise ValueError("RAG evaluation trace case ids must be unique")
    case_ids = {item.case_id for item in cases}
    unknown = sorted(set(trace_by_case).difference(case_ids))
    if unknown:
        raise ValueError(f"RAG evaluation traces contain unknown cases:{','.join(unknown)}")
    approved = [
        item for item in cases if item.annotation_status is AnnotationStatus.APPROVED
    ]
    if not approved:
        raise ValueError("RAG evaluation requires approved cases")
    answerable = [
        item for item in approved if item.expected_outcome is ExpectedOutcome.ANSWERABLE
    ]
    if not answerable:
        raise ValueError("RAG evaluation requires approved answerable cases")
    missing = [item.case_id for item in approved if item.case_id not in trace_by_case]
    if missing:
        raise ValueError(f"RAG evaluation traces missing approved cases:{','.join(missing)}")

    legacy_cases = [
        RetrievalEvaluationCase(
            case_id=item.case_id,
            query=item.query,
            relevant_parent_ids=item.relevant_parent_ids,
            current_song_id=item.current_song_id,
            tags=item.tags,
        )
        for item in answerable
    ]
    candidates = {
        item.case_id: [context.parent_id for context in trace_by_case[item.case_id].candidate_pool]
        for item in answerable
    }
    reranked = {
        item.case_id: [
            context.parent_id
            for context in trace_by_case[item.case_id].reranked_context
        ]
        for item in answerable
    }
    fallbacks = {
        item.case_id: trace_by_case[item.case_id].fallback_used for item in answerable
    }
    latencies = {
        item.case_id: trace_by_case[item.case_id].timings_ms.get("rerank_total", 0)
        for item in answerable
    }
    final_context_recall = _mean(
        [
            _recall(
                trace_by_case[item.case_id].final_context,
                set(item.relevant_parent_ids),
            )
            for item in answerable
        ]
    )
    answerable_traces = [trace_by_case[item.case_id] for item in answerable]
    citation_presence = _mean(
        [1.0 if item.citation_parent_ids else 0.0 for item in answerable_traces]
    )
    citation_validity = _mean(
        [
            1.0
            if set(item.citation_parent_ids).issubset(
                {context.parent_id for context in item.final_context}
            )
            else 0.0
            for item in answerable_traces
        ]
    )
    budgeted = [
        item
        for item in answerable_traces
        if item.context_token_count is not None and item.context_token_budget is not None
    ]
    return DeterministicEvaluationReport(
        dataset_case_count=len(cases),
        approved_case_count=len(approved),
        evaluated_answerable_count=len(answerable),
        trace_count=len(traces),
        expected_outcome_accuracy=_mean(
            [
                1.0 if _outcome_matches(item, trace_by_case[item.case_id]) else 0.0
                for item in approved
            ]
        ),
        retrieval=evaluate_candidate_pool(legacy_cases, candidates),
        rerank=evaluate_reranking(
            legacy_cases,
            candidates,
            reranked,
            fallback_by_case=fallbacks,
            latency_ms_by_case=latencies,
        ),
        final_context_recall=final_context_recall,
        citation_presence_rate=citation_presence,
        citation_id_validity=citation_validity,
        token_budget_compliance=(
            _mean(
                [
                    1.0 if item.context_token_count <= item.context_token_budget else 0.0
                    for item in budgeted
                    if item.context_token_count is not None
                    and item.context_token_budget is not None
                ]
            )
            if budgeted
            else None
        ),
        token_budget_case_count=len(budgeted),
    )


def _recall(contexts, relevant: set[str]) -> float:
    parent_ids = {item.parent_id for item in contexts}
    return len(parent_ids.intersection(relevant)) / len(relevant)


def _outcome_matches(case: RAGEvaluationCase, trace: RAGEvaluationTrace) -> bool:
    if case.expected_outcome is ExpectedOutcome.NEEDS_INPUT:
        return trace.statuses.query_understanding == "needs_input"
    if case.expected_outcome is ExpectedOutcome.INSUFFICIENT_EVIDENCE:
        return trace.statuses.generation == "insufficient_evidence" or (
            trace.statuses.generation == "not_run"
            and trace.statuses.retrieval == "empty"
        )
    return (
        trace.statuses.query_understanding == "ready"
        and trace.statuses.retrieval not in {"empty", "unavailable", "not_run"}
    )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)

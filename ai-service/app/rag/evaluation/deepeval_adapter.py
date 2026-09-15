"""Convert canonical traces to a dependency-free DeepEval interchange payload."""

from __future__ import annotations

from collections.abc import Sequence

from app.rag.evaluation.models import (
    AnnotationStatus,
    DeepEvalCasePayload,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
)


def build_deepeval_payloads(
    cases: Sequence[RAGEvaluationCase],
    traces: Sequence[RAGEvaluationTrace],
) -> list[DeepEvalCasePayload]:
    """Export only reviewed answerable cases with generated output and context."""
    trace_by_case = {item.case_id: item for item in traces}
    if len(trace_by_case) != len(traces):
        raise ValueError("DeepEval export trace case ids must be unique")
    eligible = [
        item
        for item in cases
        if item.annotation_status is AnnotationStatus.APPROVED
        and item.expected_outcome is ExpectedOutcome.ANSWERABLE
    ]
    if not eligible:
        raise ValueError("DeepEval export requires approved answerable cases")
    payloads: list[DeepEvalCasePayload] = []
    for case in eligible:
        trace = trace_by_case.get(case.case_id)
        if trace is None:
            raise ValueError(f"DeepEval export trace missing:{case.case_id}")
        if not trace.actual_answer:
            raise ValueError(f"DeepEval export answer missing:{case.case_id}")
        if not trace.final_context:
            raise ValueError(f"DeepEval export final context missing:{case.case_id}")
        assert case.reference_answer is not None
        payloads.append(
            DeepEvalCasePayload(
                case_id=case.case_id,
                input=case.query,
                actual_output=trace.actual_answer,
                expected_output=case.reference_answer,
                retrieval_context=[
                    f"[{item.parent_id}] {item.title}\n{item.content}"
                    for item in trace.final_context
                ],
                reference_context=[
                    f"[{item.parent_id}] {item.exact_text}"
                    for item in case.supporting_spans
                ],
                tags=case.tags,
                metadata={
                    "run_id": trace.run_id,
                    "index_release": trace.versions.index_release,
                    "retrieval_version": trace.versions.retrieval,
                    "rerank_version": trace.versions.rerank,
                    "context_version": trace.versions.context,
                    "generation_model": trace.versions.generation_model,
                    "rerank_fallback_used": trace.fallback_used,
                },
            )
        )
    return payloads

"""Bootstrap an explicitly non-Gold evaluation set from replayed retrieval traces."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.rag.evaluation.models import (
    AnnotationStatus,
    EvaluationContext,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    SupportingSpanAnnotation,
)

SILVER_TAG = "silver_bootstrap_v0"
BULK_APPROVAL_TAG = "user_bulk_approved"
SILVER_NOTE = (
    "用户选择批量通过；证据标签由 baseline trace 的最终上下文 Top{count} 自动生成，"
    "仅用于链路冒烟与回归基线，不属于逐条人工复核的 Gold 标签。"
)


def bootstrap_silver_dataset(
    cases: Sequence[RAGEvaluationCase],
    traces: Sequence[RAGEvaluationTrace],
    *,
    max_evidence: int = 3,
) -> list[RAGEvaluationCase]:
    """Approve cases while preserving machine-derived label provenance.

    Answerable labels are deliberately derived only from the persisted final context,
    so the resulting retrieval metrics are pipeline smoke metrics rather than an
    independent relevance judgement.
    """
    if not 1 <= max_evidence <= 10:
        raise ValueError("silver bootstrap max_evidence must be between 1 and 10")
    if not cases:
        raise ValueError("silver bootstrap requires at least one case")

    trace_by_case = {item.case_id: item for item in traces}
    if len(trace_by_case) != len(traces):
        raise ValueError("silver bootstrap trace case ids must be unique")
    case_ids = {item.case_id for item in cases}
    unknown = sorted(set(trace_by_case).difference(case_ids))
    if unknown:
        raise ValueError(f"silver bootstrap traces contain unknown cases:{','.join(unknown)}")
    missing = [item.case_id for item in cases if item.case_id not in trace_by_case]
    if missing:
        raise ValueError(f"silver bootstrap traces missing cases:{','.join(missing)}")

    bootstrapped: list[RAGEvaluationCase] = []
    for case in cases:
        if case.annotation_status is not AnnotationStatus.PENDING:
            raise ValueError(f"silver bootstrap requires pending case:{case.case_id}")
        trace = trace_by_case[case.case_id]
        if trace.query != case.query:
            raise ValueError(f"silver bootstrap query mismatch:{case.case_id}")
        bootstrapped.append(
            _bootstrap_case(case, trace, max_evidence=max_evidence)
        )
    return bootstrapped


def _bootstrap_case(
    case: RAGEvaluationCase,
    trace: RAGEvaluationTrace,
    *,
    max_evidence: int,
) -> RAGEvaluationCase:
    tags = list(dict.fromkeys([*case.tags, SILVER_TAG, BULK_APPROVAL_TAG]))
    if case.expected_outcome is ExpectedOutcome.NEEDS_INPUT:
        if trace.statuses.query_understanding != "needs_input":
            raise ValueError(f"needs-input trace status mismatch:{case.case_id}")
        return _updated_case(
            case,
            {
                "annotation_status": AnnotationStatus.APPROVED,
                "tags": tags,
                "notes": _merge_notes(case.notes, "用户批量通过；该样本预期触发追问。"),
            }
        )

    if trace.statuses.retrieval == "empty" or not trace.final_context:
        return _updated_case(
            case,
            {
                "expected_outcome": ExpectedOutcome.INSUFFICIENT_EVIDENCE,
                "annotation_status": AnnotationStatus.APPROVED,
                "relevant_parent_ids": [],
                "supporting_spans": [],
                "required_facts": [],
                "expected_source_ids": [],
                "reference_answer": None,
                "tags": tags,
                "notes": _merge_notes(
                    case.notes,
                    "用户批量通过；真实检索未返回最终上下文，标记为证据不足。",
                ),
            }
        )

    if trace.statuses.query_understanding != "ready":
        raise ValueError(f"answerable query-understanding status mismatch:{case.case_id}")
    if trace.statuses.retrieval != "success" or trace.statuses.context != "success":
        raise ValueError(f"answerable retrieval trace incomplete:{case.case_id}")

    evidence = trace.final_context[:max_evidence]
    spans = [_supporting_span(item) for item in evidence]
    distinct_spans = list(dict.fromkeys(spans))
    source_ids = list(dict.fromkeys(item.source_id for item in evidence))
    return _updated_case(
        case,
        {
            "expected_outcome": ExpectedOutcome.ANSWERABLE,
            "annotation_status": AnnotationStatus.APPROVED,
            "relevant_parent_ids": [item.parent_id for item in evidence],
            "supporting_spans": [
                SupportingSpanAnnotation(
                    parent_id=item.parent_id,
                    exact_text=span,
                    source_id=item.source_id,
                )
                for item, span in zip(evidence, spans, strict=True)
            ],
            "required_facts": distinct_spans,
            "expected_source_ids": source_ids,
            "reference_answer": "\n".join(distinct_spans)[:8_000],
            "tags": tags,
            "notes": _merge_notes(
                case.notes,
                SILVER_NOTE.format(count=len(evidence)),
            ),
        }
    )


def _supporting_span(context: EvaluationContext) -> str:
    content = context.content.strip()
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
    body = [item for item in paragraphs if item != context.title.strip()]
    selected = body[0] if body else content
    if len(selected) <= 1_000:
        return selected
    boundary = max(
        selected.rfind(mark, 0, 1_001)
        for mark in ("。", "！", "？", ".", "!", "?")
    )
    if boundary >= 80:
        return selected[: boundary + 1]
    return selected[:1_000]


def _merge_notes(existing: str | None, addition: str) -> str:
    if not existing:
        return addition
    return f"{existing.rstrip()} {addition}"


def _updated_case(
    case: RAGEvaluationCase,
    updates: dict[str, object],
) -> RAGEvaluationCase:
    payload = case.model_dump(mode="python")
    payload.update(updates)
    return RAGEvaluationCase.model_validate(payload)

"""Apply the owner-approved ambiguity policy as a minimal Gold dataset revision."""

from __future__ import annotations

from collections.abc import Sequence

from app.rag.evaluation.models import ExpectedOutcome, RAGEvaluationCase

NEEDS_INPUT_CASES = {
    "rag-v0-046": "七里香的创作背景",
    "rag-v0-047": "霍元甲的创作背景",
}
POLICY_NOTE = (
    "Owner outcome policy 2026-09-16: ambiguous bare title requires clarification "
    "before retrieval; previous evidence labels remain preserved in Gold V0."
)


def apply_needs_input_outcome_policy(
    cases: Sequence[RAGEvaluationCase],
) -> list[RAGEvaluationCase]:
    """Change only 046/047; never transfer answer evidence into needs-input cases."""
    by_id = {case.case_id: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("Gold case IDs must be unique")
    for case_id, query in NEEDS_INPUT_CASES.items():
        case = by_id.get(case_id)
        if case is None or case.query != query:
            raise ValueError(f"missing or changed policy case: {case_id}")
        if case.expected_outcome is not ExpectedOutcome.INSUFFICIENT_EVIDENCE:
            raise ValueError(f"unexpected prior outcome: {case_id}")

    revised: list[RAGEvaluationCase] = []
    for case in cases:
        if case.case_id not in NEEDS_INPUT_CASES:
            revised.append(case)
            continue
        data = case.model_dump(mode="python")
        data.update(
            expected_outcome=ExpectedOutcome.NEEDS_INPUT,
            relevant_parent_ids=[],
            supporting_spans=[],
            required_facts=[],
            expected_source_ids=[],
            reference_answer=None,
            tags=[*case.tags, "gold_v1_outcome_policy"],
            notes=f"{case.notes or ''} {POLICY_NOTE}".strip(),
        )
        revised.append(RAGEvaluationCase.model_validate(data))
    return revised

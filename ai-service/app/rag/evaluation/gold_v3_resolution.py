"""Freeze reviewed Gold V3 corrections against the immutable Parent release."""

from __future__ import annotations

from collections.abc import Sequence

from app.rag.evaluation.gold_evidence import SourceParent
from app.rag.evaluation.models import (
    ExpectedOutcome,
    RAGEvaluationCase,
    SupportingSpanAnnotation,
)

REVIEW_TAG = "gold_v3_ai_assisted_source_resolution"
REVIEW_NOTE = (
    "Gold V3 source corrections checked against frozen Parents; AI-assisted "
    "review delegated by owner, not an independent human blind annotation."
)


def resolve_gold_v3(
    cases: Sequence[RAGEvaluationCase],
    candidates: Sequence[dict],
    parents: Sequence[SourceParent],
) -> list[RAGEvaluationCase]:
    """Apply reviewed proposals while rejecting invalid source or label references."""
    by_parent = {parent.parent_id: parent for parent in parents}
    by_case = {case.case_id: case for case in cases}
    if len(by_parent) != len(parents) or len(by_case) != len(cases):
        raise ValueError("duplicate frozen Parent or Gold case")
    by_candidate = {row.get("case_id"): row for row in candidates}
    if len(by_candidate) != len(candidates):
        raise ValueError("duplicate Gold V3 candidate case")
    if not set(by_candidate).issubset(by_case):
        raise ValueError("Gold V3 candidate references unknown case")

    for case in cases:
        for parent_id in case.relevant_parent_ids:
            if parent_id not in by_parent:
                raise ValueError(f"baseline has unknown Parent: {case.case_id}:{parent_id}")
        for span in case.supporting_spans:
            _checked_span(span, by_parent)

    revised: list[RAGEvaluationCase] = []
    for case in cases:
        proposal = by_candidate.get(case.case_id)
        if proposal is None:
            revised.append(case)
            continue
        if proposal.get("review_status") != "candidate_not_finalized":
            raise ValueError(f"unknown Gold V3 proposal status: {case.case_id}")
        if not isinstance(proposal.get("rationale"), str) or not proposal["rationale"].strip():
            raise ValueError(f"Gold V3 candidate requires rationale: {case.case_id}")
        if case.expected_outcome is not ExpectedOutcome.ANSWERABLE:
            raise ValueError(f"cannot revise non-answerable case: {case.case_id}")
        if set(proposal) - {
            "case_id", "review_status", "rationale", "add_evidence",
            "remove_parent_ids", "replace_supporting_spans",
            "replace_required_facts", "replace_reference_answer",
        }:
            raise ValueError(f"unknown Gold V3 candidate fields: {case.case_id}")

        data = case.model_dump(mode="python")
        removed = proposal.get("remove_parent_ids", [])
        if not isinstance(removed, list) or len(removed) != len(set(removed)):
            raise ValueError(f"invalid Parent removal list: {case.case_id}")
        if set(removed) - set(case.relevant_parent_ids):
            raise ValueError(f"cannot remove non-Gold Parent: {case.case_id}")
        data["relevant_parent_ids"] = [
            parent_id for parent_id in data["relevant_parent_ids"]
            if parent_id not in removed
        ]
        replacement = proposal.get("replace_supporting_spans")
        if replacement is None:
            spans = [
                span for span in case.supporting_spans
                if span.parent_id not in removed
            ]
        else:
            if not isinstance(replacement, list):
                raise ValueError(f"invalid span replacement: {case.case_id}")
            spans = [SupportingSpanAnnotation.model_validate(row) for row in replacement]

        additions = proposal.get("add_evidence", [])
        if not isinstance(additions, list):
            raise ValueError(f"invalid evidence additions: {case.case_id}")
        for row in additions:
            if not isinstance(row, dict) or set(row) != {
                "parent_id", "source_id", "exact_text",
            }:
                raise ValueError(f"invalid evidence addition: {case.case_id}")
            span = SupportingSpanAnnotation.model_validate(row)
            _checked_span(span, by_parent)
            if span.parent_id not in data["relevant_parent_ids"]:
                data["relevant_parent_ids"].append(span.parent_id)
            spans.append(span)

        if len(spans) != len({(span.parent_id, span.exact_text) for span in spans}):
            raise ValueError(f"duplicate Gold V3 span: {case.case_id}")
        for span in spans:
            _checked_span(span, by_parent)
            if span.parent_id not in data["relevant_parent_ids"]:
                raise ValueError(f"span Parent not relevant: {case.case_id}")
        if set(data["relevant_parent_ids"]) - {span.parent_id for span in spans}:
            raise ValueError(f"relevant Parent has no span: {case.case_id}")
        data["supporting_spans"] = spans
        data["expected_source_ids"] = list(dict.fromkeys(
            by_parent[parent_id].source_id for parent_id in data["relevant_parent_ids"]
        ))
        for field in ("required_facts", "reference_answer"):
            value = proposal.get(f"replace_{field}")
            if value is not None:
                data[field] = value
        data["tags"] = list(dict.fromkeys([*case.tags, REVIEW_TAG]))
        data["notes"] = f"{case.notes or ''} {REVIEW_NOTE}".strip()
        revised.append(RAGEvaluationCase.model_validate(data))
    return revised


def _checked_span(
    span: SupportingSpanAnnotation,
    parents: dict[str, SourceParent],
) -> None:
    parent = parents.get(span.parent_id)
    if parent is None or span.source_id != parent.source_id:
        raise ValueError(f"span source or Parent mismatch: {span.parent_id}")
    if span.exact_text not in parent.content:
        raise ValueError(f"span is not exact frozen Parent text: {span.parent_id}")

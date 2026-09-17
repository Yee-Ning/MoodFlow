"""Regression checks for frozen-source Gold V3 materialization."""

import json
from pathlib import Path

import pytest

from app.rag.evaluation.gold_evidence import SourceParent
from app.rag.evaluation.gold_v3_resolution import REVIEW_TAG, resolve_gold_v3
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.models import (
    AnnotationStatus,
    ExpectedOutcome,
    RAGEvaluationCase,
    SupportingSpanAnnotation,
)
from run_rag_gold_v3_finalize import write_frozen_v3


def _parent(parent_id: str, source_id: str, content: str) -> SourceParent:
    return SourceParent(
        parent_id=parent_id, doc_id="doc", source_id=source_id,
        source_title="title", source_name="name", source_url=None,
        source_document_path="doc.json", breadcrumb="section", content=content,
    )


def _case() -> RAGEvaluationCase:
    return RAGEvaluationCase(
        case_id="case-1", query="question",
        expected_outcome=ExpectedOutcome.ANSWERABLE,
        annotation_status=AnnotationStatus.APPROVED,
        relevant_parent_ids=["parent-old"],
        supporting_spans=[SupportingSpanAnnotation(
            parent_id="parent-old", source_id="source-old", exact_text="old fact",
        )],
        expected_source_ids=["source-old"],
        required_facts=["old fact"], reference_answer="old answer",
    )


def _proposal() -> dict:
    return {
        "case_id": "case-1", "review_status": "candidate_not_finalized",
        "rationale": "source verified", "remove_parent_ids": ["parent-old"],
        "add_evidence": [{
            "parent_id": "parent-new", "source_id": "source-new",
            "exact_text": "new fact",
        }],
        "replace_required_facts": ["new fact"],
        "replace_reference_answer": "new answer",
    }


def test_replaces_evidence_and_source_without_mutating_baseline() -> None:
    baseline = _case()
    revised = resolve_gold_v3(
        [baseline], [_proposal()],
        [_parent("parent-old", "source-old", "old fact"),
         _parent("parent-new", "source-new", "new fact")],
    )[0]
    assert baseline.relevant_parent_ids == ["parent-old"]
    assert revised.relevant_parent_ids == ["parent-new"]
    assert revised.expected_source_ids == ["source-new"]
    assert revised.required_facts == ["new fact"]
    assert revised.reference_answer == "new answer"
    assert REVIEW_TAG in revised.tags


def test_rejects_nonexact_span_and_wrong_source() -> None:
    parents = [
        _parent("parent-old", "source-old", "old fact"),
        _parent("parent-new", "source-new", "new fact"),
    ]
    proposal = _proposal()
    proposal["add_evidence"][0]["exact_text"] = "invented"
    with pytest.raises(ValueError, match="not exact"):
        resolve_gold_v3([_case()], [proposal], parents)
    proposal = _proposal()
    proposal["add_evidence"][0]["source_id"] = "source-old"
    with pytest.raises(ValueError, match="mismatch"):
        resolve_gold_v3([_case()], [proposal], parents)


def test_rejects_duplicate_and_uncovered_parent() -> None:
    parents = [
        _parent("parent-old", "source-old", "old fact"),
        _parent("parent-new", "source-new", "new fact"),
    ]
    proposal = _proposal()
    with pytest.raises(ValueError, match="duplicate"):
        resolve_gold_v3([_case()], [proposal, proposal], parents)
    proposal = _proposal()
    proposal["remove_parent_ids"] = []
    proposal["replace_supporting_spans"] = []
    with pytest.raises(ValueError, match="no span"):
        resolve_gold_v3([_case()], [proposal], parents)


def test_frozen_output_is_create_once_or_verify_identical(tmp_path: Path) -> None:
    path = tmp_path / "gold-v3.jsonl"
    assert write_frozen_v3(path, [_case()]) == "created"
    assert write_frozen_v3(path, [_case()]) == "verified_identical"
    changed = _case().model_copy(update={"reference_answer": "different"})
    with pytest.raises(ValueError, match="already frozen"):
        write_frozen_v3(path, [changed])
    assert load_evaluation_cases(path) == [_case()]


def test_committed_v3_matches_reviewed_candidates_and_only_changes_reviewed_cases() -> None:
    root = Path(__file__).resolve().parents[1]
    baseline = load_evaluation_cases(
        root / "evals/datasets/rag_eval_gold_v2.ai_assisted_source_review.jsonl"
    )
    candidates = [
        json.loads(line)
        for line in (
            root / "evals/reports/gold_v3_source_audit_candidates.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # The large immutable source artifact is intentionally not Git-tracked.
    # This test verifies reproducible amendment logic in a clean checkout;
    # the release-hash and exact-source check runs during V3 materialization.
    parent_text: dict[str, list[str]] = {}
    parent_source: dict[str, str] = {}
    for case in baseline:
        for span in case.supporting_spans:
            parent_text.setdefault(span.parent_id, []).append(span.exact_text)
            assert span.source_id is not None
            parent_source[span.parent_id] = span.source_id
    for row in candidates:
        for evidence in [
            *row.get("replace_supporting_spans", []),
            *row.get("add_evidence", []),
        ]:
            parent_text.setdefault(evidence["parent_id"], []).append(
                evidence["exact_text"]
            )
            parent_source[evidence["parent_id"]] = evidence["source_id"]
    parents = [
        _parent(parent_id, parent_source[parent_id], "\n".join(texts))
        for parent_id, texts in parent_text.items()
    ]
    revised = resolve_gold_v3(baseline, candidates, parents)
    committed = load_evaluation_cases(
        root / "evals/datasets/rag_eval_gold_v3.ai_assisted_source_resolution.jsonl"
    )
    assert revised == committed
    assert len(revised) == len(baseline) == 30
    changed = {
        old.case_id for old, new in zip(baseline, revised, strict=True)
        if old != new
    }
    assert changed == {row["case_id"] for row in candidates}
    by_case = {case.case_id: case for case in revised}
    assert len(by_case["rag-v0-026"].relevant_parent_ids) == 2
    assert "只确认" not in by_case["rag-v0-026"].reference_answer
    assert "2005年整张专辑" in by_case["rag-v0-036"].reference_answer
    assert "2009" not in by_case["rag-v0-039"].reference_answer
    assert all(
        set(case.relevant_parent_ids)
        == {span.parent_id for span in case.supporting_spans}
        for case in revised if case.expected_outcome is ExpectedOutcome.ANSWERABLE
    )

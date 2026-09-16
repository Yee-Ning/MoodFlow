"""Apply source-reviewed evidence amendments without using online rankings."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.rag.evaluation.gold_candidate_audit import audit_proposal_rows
from app.rag.evaluation.gold_evidence import SourceParent, load_frozen_parents
from app.rag.evaluation.gold_outcome_policy import apply_needs_input_outcome_policy
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.models import (
    ExpectedOutcome,
    RAGEvaluationCase,
    SupportingSpanAnnotation,
)

SOURCE_REVIEW_TAG = "gold_v2_ai_assisted_source_review"
SOURCE_REVIEW_NOTE = (
    "Source amendments independently checked against frozen Parent text; "
    "AI-assisted review delegated by project owner, not independent human blind labels."
)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("source decisions must be JSON objects")
    return rows


def adjudicate_source_files(
    v0_path: Path,
    v1_path: Path,
    proposal_path: Path,
    decision_path: Path,
    release_manifest: Path,
) -> tuple[list[RAGEvaluationCase], dict[str, object]]:
    v0 = load_evaluation_cases(v0_path)
    v1 = load_evaluation_cases(v1_path)
    if v1 != apply_needs_input_outcome_policy(v0):
        raise ValueError("source review requires the exact policy-only Gold V1")
    release_id, source_sha256, parents = load_frozen_parents(release_manifest)
    proposals = _read_jsonl(proposal_path)
    audit_proposal_rows(v0, proposals, parents, release_id, source_sha256)
    return adjudicate_source_rows(
        v1, proposals, _read_jsonl(decision_path), parents,
        release_id=release_id, source_sha256=source_sha256,
    )


def adjudicate_source_rows(
    cases: list[RAGEvaluationCase],
    proposals: list[dict[str, object]],
    decisions: list[dict[str, object]],
    parents: list[SourceParent],
    *,
    release_id: str,
    source_sha256: str,
) -> tuple[list[RAGEvaluationCase], dict[str, object]]:
    by_case = {case.case_id: case for case in cases}
    by_parent = {parent.parent_id: parent for parent in parents}
    if len(by_case) != len(cases) or len(by_parent) != len(parents):
        raise ValueError("duplicate Gold case or frozen Parent ID")
    by_proposal: dict[tuple[str, str], dict[str, object]] = {}
    for proposal in proposals:
        key = (proposal.get("case_id"), proposal.get("parent_id"))
        if key in by_proposal:
            raise ValueError(f"duplicate proposal: {key}")
        by_proposal[key] = proposal
    by_decision: dict[tuple[str, str], dict[str, object]] = {}
    for decision in decisions:
        if set(decision) != {
            "case_id", "parent_id", "source_id", "decision", "exact_text", "rationale",
        }:
            raise ValueError("source decision has missing or unexpected fields")
        key = (decision["case_id"], decision["parent_id"])
        if key in by_decision:
            raise ValueError(f"duplicate source decision: {key}")
        by_decision[key] = decision
    if set(by_decision) != set(by_proposal):
        raise ValueError("source decisions must cover every proposal exactly once")

    amendments: dict[str, dict[str, list]] = {}
    counts: Counter[str] = Counter()
    for key, proposal in by_proposal.items():
        decision = by_decision[key]
        case_id, parent_id = key
        case = by_case.get(case_id)
        parent = by_parent.get(parent_id)
        source_id = decision["source_id"]
        if case is None or parent is None or not isinstance(source_id, str):
            raise ValueError(f"unknown case, Parent or source: {key}")
        if source_id != proposal.get("source_id") or source_id != parent.source_id:
            raise ValueError(f"source mismatch: {key}")
        rationale = decision["rationale"]
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"source decision requires rationale: {key}")
        status = decision["decision"]
        exact_text = decision["exact_text"]
        if status == "reject":
            if exact_text is not None:
                raise ValueError(f"rejected decision must not carry a span: {key}")
            counts["rejected"] += 1
            continue
        if status != "accept":
            raise ValueError(f"unknown source decision: {key}")
        if case.expected_outcome is not ExpectedOutcome.ANSWERABLE:
            raise ValueError(f"cannot add answer evidence to non-answerable case: {key}")
        if not isinstance(exact_text, str) or not exact_text or exact_text not in parent.content:
            raise ValueError(f"reviewed span is not exact frozen Parent text: {key}")
        action = proposal.get("action")
        if action == "propose_span":
            if parent_id not in case.relevant_parent_ids:
                raise ValueError(f"accepted span Parent is not already Gold: {key}")
            counts["accepted_existing_parent_span"] += 1
        elif action == "propose_parent_and_span":
            if parent_id in case.relevant_parent_ids:
                raise ValueError(f"accepted new Parent is already Gold: {key}")
            counts["accepted_new_parent_and_span"] += 1
        else:
            raise ValueError(f"unknown proposal action: {key}")
        bucket = amendments.setdefault(case_id, {"spans": [], "parents": [], "sources": []})
        bucket["spans"].append(SupportingSpanAnnotation(
            parent_id=parent_id, exact_text=exact_text, source_id=source_id,
        ))
        if action == "propose_parent_and_span":
            bucket["parents"].append(parent_id)
            bucket["sources"].append(source_id)

    revised: list[RAGEvaluationCase] = []
    for case in cases:
        amendment = amendments.get(case.case_id)
        if amendment is None:
            revised.append(case)
            continue
        data = case.model_dump(mode="python")
        data["relevant_parent_ids"] = list(dict.fromkeys([
            *case.relevant_parent_ids, *amendment["parents"],
        ]))
        data["supporting_spans"] = [
            *case.supporting_spans, *amendment["spans"],
        ]
        data["expected_source_ids"] = list(dict.fromkeys([
            *case.expected_source_ids, *amendment["sources"],
        ]))
        data["tags"] = [*case.tags, SOURCE_REVIEW_TAG]
        data["notes"] = f"{case.notes or ''} {SOURCE_REVIEW_NOTE}".strip()
        revised.append(RAGEvaluationCase.model_validate(data))
    missing = sum(
        parent_id not in {span.parent_id for span in case.supporting_spans}
        for case in revised
        for parent_id in case.relevant_parent_ids
    )
    return revised, {
        "status": "ai_assisted_source_adjudicated",
        "case_count": len(revised),
        "proposal_count": len(proposals),
        "decision_counts": dict(counts),
        "changed_case_count": len(amendments),
        "missing_relevant_parent_spans_after": missing,
        "release_id": release_id,
        "source_sha256": source_sha256,
    }

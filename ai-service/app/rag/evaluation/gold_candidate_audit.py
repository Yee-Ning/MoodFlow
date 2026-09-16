"""Validate source-grounded Gold amendment proposals without approving them."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.rag.evaluation.gold_evidence import SourceParent, load_frozen_parents
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.models import RAGEvaluationCase


def audit_proposals(
    gold_path: Path,
    proposal_path: Path,
    release_manifest: Path,
) -> dict[str, object]:
    gold = load_evaluation_cases(gold_path)
    release_id, source_sha256, parents = load_frozen_parents(release_manifest)
    rows = [
        json.loads(line)
        for line in proposal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return audit_proposal_rows(gold, rows, parents, release_id, source_sha256)


def audit_proposal_rows(
    gold: list[RAGEvaluationCase],
    rows: list[dict[str, str]],
    parents: list[SourceParent],
    release_id: str,
    source_sha256: str,
) -> dict[str, object]:
    if len({case.case_id for case in gold}) != len(gold):
        raise ValueError("duplicate Gold case ID")
    by_case = {case.case_id: case for case in gold}
    by_parent = {parent.parent_id: parent for parent in parents}
    if len(by_parent) != len(parents):
        raise ValueError("duplicate frozen Parent ID")
    seen: set[tuple[str, str]] = set()
    counts: Counter[str] = Counter()
    for row in rows:
        if set(row) != {"case_id", "parent_id", "source_id", "action", "exact_text"}:
            raise ValueError("proposal row has missing or unexpected fields")
        case_id, parent_id = row["case_id"], row["parent_id"]
        key = (case_id, parent_id)
        if key in seen:
            raise ValueError(f"duplicate proposal: {case_id}/{parent_id}")
        seen.add(key)
        case = by_case.get(case_id)
        parent = by_parent.get(parent_id)
        if case is None or parent is None:
            raise ValueError(f"unknown case or frozen Parent: {case_id}/{parent_id}")
        if parent.source_id != row["source_id"]:
            raise ValueError(f"source mismatch: {case_id}/{parent_id}")
        span = row["exact_text"]
        if not span or span not in parent.content:
            raise ValueError(f"non-exact proposed span: {case_id}/{parent_id}")
        action = row["action"]
        existing_spans = {item.parent_id for item in case.supporting_spans}
        if action == "propose_span":
            if parent_id not in case.relevant_parent_ids or parent_id in existing_spans:
                raise ValueError(f"span proposal conflicts with Gold: {case_id}/{parent_id}")
        elif action == "propose_parent_and_span":
            if parent_id in case.relevant_parent_ids:
                raise ValueError(f"new Parent is already Gold: {case_id}/{parent_id}")
        else:
            raise ValueError(f"unknown proposal action: {action}")
        counts[action] += 1
    missing = {
        (case.case_id, parent_id)
        for case in gold
        for parent_id in case.relevant_parent_ids
        if parent_id not in {item.parent_id for item in case.supporting_spans}
    }
    covered = missing & seen
    return {
        "status": "source_validated_not_approved",
        "case_count": len(gold),
        "proposal_count": len(rows),
        "actions": dict(counts),
        "missing_case_parent_spans_before": len(missing),
        "missing_case_parent_spans_covered": len(covered),
        "missing_case_parent_spans_remaining": len(missing - covered),
        "release_id": release_id,
        "source_sha256": source_sha256,
    }

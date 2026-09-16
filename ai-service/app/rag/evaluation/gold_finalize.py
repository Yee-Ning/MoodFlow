"""Convert an owner-approved source review into source-validated Gold labels."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.rag.evaluation.gold_evidence import SourceParent, load_frozen_parents
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.models import (
    AnnotationStatus,
    ExpectedOutcome,
    RAGEvaluationCase,
    SupportingSpanAnnotation,
)

SECTION = re.compile(r"(?ms)^## (rag-v0-\d{3}) · (.*?)(?=^## rag-v0-\d{3} · |\Z)")
CANDIDATE = re.compile(
    r"(?ms)^#### 候选 [A-E] · `(parent-[^`]+)`(.*?)(?=^#### 候选 [A-E] · |^### 汇总审核|\Z)"
)
PARENT_ID = re.compile(r"parent-[0-9a-f]+")


def _line(body: str, prefix: str) -> str:
    match = re.search(rf"(?m)^- {re.escape(prefix)}[^\n]*$", body)
    if not match:
        raise ValueError(f"review field missing: {prefix}")
    return match.group(0)


def _decision(body: str, field: str) -> str:
    match = re.search(
        rf"(?m)^- {re.escape(field)}：APPROVE / CORRECT / REJECT：\*\*(APPROVE|CORRECT|REJECT)\*\*",
        body,
    )
    if not match:
        raise ValueError(f"review decision missing: {field}")
    return match.group(1)


def _outcome(body: str) -> ExpectedOutcome:
    if _decision(body, "审核决定") == "REJECT":
        raise ValueError("rejected expected outcome cannot be finalized")
    match = re.search(r"(?m)^- 最终值与理由：`([^`]+)`", body)
    if not match:
        raise ValueError("final expected outcome missing")
    return ExpectedOutcome(match.group(1))


def _final_content(body: str, field: str) -> str:
    if _decision(body, field) == "REJECT":
        raise ValueError(f"rejected {field} cannot be finalized")
    line = _line(body, field + "：APPROVE / CORRECT / REJECT：")
    marker = "；最终内容："
    if marker not in line:
        raise ValueError(f"final content missing: {field}")
    return line.split(marker, 1)[1].strip()


def _exact_corrected_span(text: str, parent: SourceParent) -> tuple[str, bool]:
    """Accept exact text, or an exact first sentence from a reviewed longer quote."""
    text = text.strip().strip("“”")
    if text in parent.content:
        return text, False
    first_sentence = text.split("。", 1)[0] + "。"
    if len(first_sentence) >= 30 and first_sentence in parent.content:
        return first_sentence, True
    raise ValueError(f"corrected span not found verbatim in {parent.parent_id}")


def finalize_review(
    review_path: Path,
    pending_path: Path,
    candidates_path: Path,
    manifest_path: Path,
) -> tuple[list[RAGEvaluationCase], dict[str, object]]:
    pending = load_evaluation_cases(pending_path)
    release_id, source_sha256, parent_rows = load_frozen_parents(manifest_path)
    parents = {row.parent_id: row for row in parent_rows}
    review_bytes = review_path.read_bytes()
    review = review_bytes.decode("utf-8-sig").replace("\r\n", "\n")
    if f"`{release_id}`" not in review or f"`{source_sha256}`" not in review:
        raise ValueError("review release identity does not match frozen source")
    sections = list(SECTION.finditer(review))
    if len(sections) != len(pending):
        raise ValueError("review and pending case counts differ")
    by_case = {match.group(1): match for match in sections}
    if len(by_case) != len(sections) or set(by_case) != {case.case_id for case in pending}:
        raise ValueError("review case IDs do not match pending Gold")
    candidate_rows = [json.loads(line) for line in candidates_path.read_text(encoding="utf-8").splitlines() if line]
    candidates = {row["case_id"]: row for row in candidate_rows}
    if len(candidates) != len(candidate_rows) or set(candidates) != set(by_case):
        raise ValueError("source candidates and review case IDs differ")
    reviewed: dict[str, dict[str, object]] = {}
    approved_spans_by_parent: dict[str, SupportingSpanAnnotation] = {}
    normalized_spans: list[str] = []
    for case in pending:
        section = by_case[case.case_id]
        body = section.group(2)
        if not body.startswith(case.query + "\n"):
            raise ValueError(f"review query mismatch: {case.case_id}")
        row = candidates[case.case_id]
        if row["query"] != case.query or row["release_id"] != release_id or row["source_sha256"] != source_sha256:
            raise ValueError(f"source candidate identity mismatch: {case.case_id}")
        source_candidates = {item["parent_id"]: item for item in row["candidates"]}
        blocks = list(CANDIDATE.finditer(body))
        if len(blocks) != len(source_candidates) or {block.group(1) for block in blocks} != set(source_candidates):
            raise ValueError(f"review candidate count mismatch: {case.case_id}")
        spans: list[SupportingSpanAnnotation] = []
        approved_ids: set[str] = set()
        for block in blocks:
            parent_id = block.group(1)
            if parent_id not in source_candidates:
                raise ValueError(f"unknown review candidate: {case.case_id}/{parent_id}")
            parent_decision = _decision(block.group(2), "relevant_parent")
            span_decision = _decision(block.group(2), "supporting_span")
            if parent_decision == "CORRECT":
                raise ValueError(f"corrected Parent requires explicit summary-only audit: {case.case_id}/{parent_id}")
            if parent_decision == "APPROVE":
                approved_ids.add(parent_id)
            if parent_decision == "REJECT" and span_decision != "REJECT":
                raise ValueError(f"rejected Parent has approved span: {case.case_id}/{parent_id}")
            if span_decision not in ("APPROVE", "CORRECT"):
                continue
            if parent_decision != "APPROVE":
                raise ValueError(f"span without relevant Parent: {case.case_id}/{parent_id}")
            parent = parents[parent_id]
            if parent.source_id != source_candidates[parent_id]["source_id"]:
                raise ValueError(f"candidate source mismatch: {case.case_id}/{parent_id}")
            if span_decision == "APPROVE":
                exact_text = source_candidates[parent_id]["suggested_supporting_span"]
                if exact_text not in parent.content:
                    raise ValueError(f"approved span not source-exact: {case.case_id}/{parent_id}")
            else:
                corrected = _line(block.group(2), "如修正，填写完整 Parent ID / 精确原文片段：").split("：", 1)[1]
                exact_text, normalized = _exact_corrected_span(corrected, parent)
                if normalized:
                    normalized_spans.append(case.case_id)
            span = SupportingSpanAnnotation(parent_id=parent_id, exact_text=exact_text, source_id=parent.source_id)
            spans.append(span)
            approved_spans_by_parent.setdefault(parent_id, span)
        relevant_ids = PARENT_ID.findall(_line(body, "relevant_parent_ids"))
        if len(relevant_ids) != len(set(relevant_ids)) or approved_ids != set(relevant_ids) & set(source_candidates):
            raise ValueError(f"relevant Parent summary conflicts with decisions: {case.case_id}")
        if any(parent_id not in parents for parent_id in relevant_ids):
            raise ValueError(f"relevant Parent missing from frozen release: {case.case_id}")
        reviewed[case.case_id] = {
            "outcome": _outcome(body),
            "relevant_ids": relevant_ids,
            "spans": spans,
            "facts": _final_content(body, "required_facts").strip("[]"),
            "answer": _final_content(body, "reference_answer"),
        }

    finalized: list[RAGEvaluationCase] = []
    borrowed_spans: list[str] = []
    for case in pending:
        item = reviewed[case.case_id]
        outcome = item["outcome"]
        relevant_ids = item["relevant_ids"]
        spans = list(item["spans"])
        if outcome is ExpectedOutcome.ANSWERABLE and not spans:
            for parent_id in relevant_ids:
                if parent_id in approved_spans_by_parent:
                    spans.append(approved_spans_by_parent[parent_id])
                    borrowed_spans.append(case.case_id)
                    break
        facts_text = item["facts"]
        answer = item["answer"]
        notes = f"Owner-approved AI-assisted source review; review_sha256={hashlib.sha256(review_bytes).hexdigest()}; release={release_id}."
        if case.case_id in normalized_spans:
            notes += " Corrected quote reduced to a verbatim first sentence."
        if case.case_id in borrowed_spans:
            notes += " Exact span reused from an approved annotation of the same Parent in another case."
        if outcome is ExpectedOutcome.NEEDS_INPUT:
            notes += f" Reviewed clarification criteria: {facts_text}; reviewed response: {answer}"
            relevant_ids = []
            spans = []
            facts = []
            answer = None
        else:
            facts = [part.strip() for part in facts_text.split("；") if part.strip()]
        source_ids = list(dict.fromkeys(parents[parent_id].source_id for parent_id in relevant_ids))
        finalized.append(RAGEvaluationCase.model_validate({
            **case.model_dump(),
            "expected_outcome": outcome,
            "annotation_status": AnnotationStatus.APPROVED,
            "relevant_parent_ids": relevant_ids,
            "supporting_spans": spans,
            "required_facts": facts,
            "expected_source_ids": source_ids,
            "reference_answer": answer,
            "tags": ["gold_v0", "owner_approved_ai_assisted_review"],
            "notes": notes,
        }))
    missing_spans = sum(
        len(set(case.relevant_parent_ids) - {span.parent_id for span in case.supporting_spans})
        for case in finalized
    )
    summary: dict[str, object] = {
        "case_count": len(finalized),
        "outcomes": dict(Counter(case.expected_outcome.value for case in finalized)),
        "release_id": release_id,
        "review_sha256": hashlib.sha256(review_bytes).hexdigest(),
        "normalized_exact_span_cases": normalized_spans,
        "cross_case_reused_span_cases": borrowed_spans,
        "relevant_parents_without_case_specific_span": missing_spans,
    }
    return finalized, summary

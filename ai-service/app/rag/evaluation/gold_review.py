"""Prepare a blind, stratified human-review subset from Silver evaluation cases.

This module does not create Gold labels: every exported case remains pending.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from app.rag.evaluation.models import (
    AnnotationStatus,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
)

ANSWERABLE_QUOTAS = {
    "comparison": 5,
    "timeline": 4,
    "relationship": 4,
    "influence_style": 4,
    "exploratory": 5,
    "fact": 1,
    "untyped": 3,
}
NEEDS_INPUT_COUNT = 3
INSUFFICIENT_COUNT = 1
GOLD_CANDIDATE_TAG = "gold_candidate_v0"


def select_blind_review_cases(
    cases: Sequence[RAGEvaluationCase],
) -> list[RAGEvaluationCase]:
    """Select 30 cases using only Silver outcomes/types, then erase Silver labels."""
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Gold review source contains duplicate case IDs")
    buckets: dict[str, list[RAGEvaluationCase]] = defaultdict(list)
    for case in cases:
        if case.annotation_status is not AnnotationStatus.APPROVED:
            raise ValueError(f"Gold review source is not approved Silver:{case.case_id}")
        if "silver_bootstrap_v0" not in case.tags:
            raise ValueError(f"Gold review source lacks Silver provenance:{case.case_id}")
        if case.expected_outcome is ExpectedOutcome.ANSWERABLE:
            key = case.expected_query_types[0] if case.expected_query_types else "untyped"
        else:
            key = case.expected_outcome.value
        buckets[key].append(case)

    quotas = {
        **ANSWERABLE_QUOTAS,
        ExpectedOutcome.NEEDS_INPUT.value: NEEDS_INPUT_COUNT,
        ExpectedOutcome.INSUFFICIENT_EVIDENCE.value: INSUFFICIENT_COUNT,
    }
    selected: list[RAGEvaluationCase] = []
    for key, count in quotas.items():
        available = sorted(buckets[key], key=lambda case: case.case_id)
        if len(available) < count:
            raise ValueError(f"Gold review stratum {key} needs {count}, has {len(available)}")
        selected.extend(available[:count])
    return [_blind_case(case) for case in sorted(selected, key=lambda case: case.case_id)]


def review_summary(cases: Sequence[RAGEvaluationCase]) -> dict[str, object]:
    """Count only selection strata; these are not Gold-quality metrics."""
    outcomes = Counter(case.expected_outcome.value for case in cases)
    if len(cases) != 30 or outcomes != {
        "answerable": 26,
        "needs_input": 3,
        "insufficient_evidence": 1,
    }:
        raise ValueError("Gold review selection does not match the frozen 30-case plan")
    return {
        "selected_cases": len(cases),
        "annotation_status": "pending",
        "seed_outcomes_from_silver": dict(sorted(outcomes.items())),
        "gold_approved_cases": 0,
    }


def blind_review_markdown(cases: Sequence[RAGEvaluationCase]) -> str:
    """Render queries without exposing old candidates, answers or reference labels."""
    lines = [
        "# Gold V0 独立人工复核包（待审核，不是 Gold）",
        "",
        "- 先凭问题判断预期结果，再从冻结的原始语料/索引投影独立找证据。",
        "- 本包不展示 Silver 的旧 Top3、候选排名、参考答案或旧标签。",
        "- JSONL 的 `expected_outcome` 是 Silver 种子值，必须人工复核；其余证据字段已清空。",
        "- 每条记录应核对范围、结果类别、相关 Parent、精确支持片段、必要事实和参考答案。",
        "- `notes` 可选；不要因为原链路曾召回某 Parent 就把它标为 relevant。",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"## {case.case_id}",
                "",
                f"问题：{case.query}",
                "",
                "- [ ] 独立判断预期结果：answerable / needs_input / insufficient_evidence",
                "- [ ] 独立核对相关 Parent 与原文精确支持片段（不适用则留空）",
                "- [ ] 写出必要事实和参考答案（不适用则留空）",
                "- [ ] 核对来源 ID、排除无关片段并签署审核结论",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def audit_silver_provenance(
    cases: Sequence[RAGEvaluationCase],
    traces: Sequence[RAGEvaluationTrace],
    projection_path: Path,
) -> dict[str, object]:
    """Check label lineage and index referential integrity, not semantic relevance."""
    answerable = [case for case in cases if case.expected_outcome is ExpectedOutcome.ANSWERABLE]
    needed = {span.parent_id for case in answerable for span in case.supporting_spans}
    parents = _index_parents(projection_path, needed)
    trace_by_id = {trace.case_id: trace for trace in traces}
    if len(trace_by_id) != len(traces):
        raise ValueError("Silver audit traces contain duplicate case IDs")
    counts = Counter()
    for case in answerable:
        spans = case.supporting_spans
        trace = trace_by_id.get(case.case_id)
        if trace is None:
            raise ValueError(f"Silver audit trace missing:{case.case_id}")
        if case.relevant_parent_ids == [item.parent_id for item in trace.final_context[:3]]:
            counts["labels_equal_old_final_top3"] += 1
        if case.required_facts == [span.exact_text for span in spans]:
            counts["facts_equal_copied_spans"] += 1
        if case.reference_answer == "\n".join(span.exact_text for span in spans):
            counts["answers_equal_joined_spans"] += 1
        if case.expected_source_ids == list(dict.fromkeys(span.source_id for span in spans)):
            counts["source_sets_match_spans"] += 1
        for span in spans:
            content, source_id = parents.get(span.parent_id, ("", None))
            if span.exact_text in content and span.source_id == source_id:
                counts["spans_present_in_index_with_source"] += 1
    return {
        "silver_cases": len(cases),
        "answerable_cases": len(answerable),
        "supporting_spans": sum(len(case.supporting_spans) for case in answerable),
        **{key: counts[key] for key in (
            "labels_equal_old_final_top3",
            "facts_equal_copied_spans",
            "answers_equal_joined_spans",
            "source_sets_match_spans",
            "spans_present_in_index_with_source",
        )},
        "semantic_relevance_verified": False,
    }


def _index_parents(path: Path, needed: set[str]) -> dict[str, tuple[str, str | None]]:
    parents: dict[str, tuple[str, str | None]] = {}
    with path.open(encoding="utf-8") as stream:
        for raw in stream:
            if not raw.strip():
                continue
            row = json.loads(raw)
            parent_id = row.get("parent_id")
            if parent_id not in needed:
                continue
            payload = row["payload"]
            content = payload.get("parent_context", {}).get("content", "")
            value = (content, payload.get("source_id"))
            if parent_id in parents and parents[parent_id] != value:
                raise ValueError(f"Index projection has conflicting Parent:{parent_id}")
            parents[parent_id] = value
    return parents


def _blind_case(case: RAGEvaluationCase) -> RAGEvaluationCase:
    return case.model_copy(
        update={
            "annotation_status": AnnotationStatus.PENDING,
            "expected_entity_ids": [],
            "expected_query_types": [],
            "relevant_parent_ids": [],
            "supporting_spans": [],
            "required_facts": [],
            "forbidden_claims": [],
            "expected_source_ids": [],
            "reference_answer": None,
            "tags": [GOLD_CANDIDATE_TAG, "independent_review_pending"],
            "notes": None,
        }
    )

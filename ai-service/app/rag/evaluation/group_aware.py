"""Optional evidence-group scoring over fixed traces; legacy evaluator stays unchanged."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.rag.evaluation.models import (
    AnnotationStatus,
    EvidenceGroup,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
)
from app.rag.evaluation.service import evaluate_traces


MatchingMode = Literal["compatibility", "group_aware"]


@dataclass(frozen=True)
class StageScore:
    value: float
    hits: tuple[str, ...]
    matched_groups: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class EvidenceCaseScores:
    case_id: str
    recall_at_40: StageScore
    rerank_recall_at_12: StageScore
    final_context_precision: StageScore


@dataclass(frozen=True)
class EvidenceMetrics:
    mode: MatchingMode
    case_count: int
    recall_at_40: float
    rerank_recall_at_12: float
    final_context_precision: float
    cases: tuple[EvidenceCaseScores, ...]


def _unique(items: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def _group_for_anchors(case: RAGEvaluationCase) -> dict[str, tuple[EvidenceGroup, ...]]:
    relevant = set(case.relevant_parent_ids)
    groups: dict[str, list[EvidenceGroup]] = {}
    claims_by_parent: dict[str, set[str]] = {}
    for group in case.evidence_groups:
        for parent_id in group.acceptable_parent_ids:
            claims = claims_by_parent.setdefault(parent_id, set())
            if group.claim in claims:
                raise ValueError(f"overlapping evidence groups for claim:{case.case_id}:{parent_id}:{group.claim}")
            claims.add(group.claim)
        anchors = relevant.intersection(group.acceptable_parent_ids)
        if not anchors:
            raise ValueError(f"evidence group without relevant anchor:{case.case_id}:{group.evidence_group_id}")
        for anchor in anchors:
            groups.setdefault(anchor, []).append(group)
    return {anchor: tuple(items) for anchor, items in groups.items()}


def score_stage(
    case: RAGEvaluationCase,
    parent_ids: Sequence[str],
    *,
    metric: Literal["recall", "precision"],
    mode: MatchingMode,
) -> StageScore:
    """Keep legacy denominators: relevant IDs for recall, context IDs for precision.

    A group substitutes for each relevant anchor it contains. Duplicate and
    equivalent mean any acceptable Parent; complementary means all of them.
    A partial complementary group never counts as a full evidence hit.
    """
    if mode not in ("compatibility", "group_aware"):
        raise ValueError(f"unknown evidence matching mode:{mode}")
    observed = _unique(parent_ids)
    found = set(observed)
    relevant = set(case.relevant_parent_ids)
    groups = _group_for_anchors(case) if mode == "group_aware" else {}
    satisfied: dict[str, bool] = {}
    matched_groups: list[dict[str, object]] = []
    for group in case.evidence_groups if mode == "group_aware" else ():
        acceptable = set(group.acceptable_parent_ids)
        matched = tuple(pid for pid in observed if pid in acceptable)
        complete = (
            acceptable.issubset(found)
            if group.relation == "complementary"
            else bool(matched)
        )
        satisfied[group.evidence_group_id] = complete
        matched_groups.append({
            "evidence_group_id": group.evidence_group_id,
            "relation": group.relation,
            "acceptable_parent_ids": tuple(group.acceptable_parent_ids),
            "retrieved_parent_ids": matched,
            "complete": complete,
        })
    if metric == "recall":
        if not relevant:
            raise ValueError(f"recall requires relevant Parents:{case.case_id}")
        hits = tuple(
            anchor for anchor in case.relevant_parent_ids
            if (all(satisfied[group.evidence_group_id] for group in groups[anchor])
                if anchor in groups else anchor in found)
        )
        denominator = len(relevant)
    elif metric == "precision":
        accepted = relevant.difference(groups)
        for group in case.evidence_groups if mode == "group_aware" else ():
            if satisfied[group.evidence_group_id]:
                accepted.update(group.acceptable_parent_ids)
        hits = tuple(pid for pid in observed if pid in accepted)
        denominator = len(observed)
    else:
        raise ValueError(f"unknown evidence metric:{metric}")
    return StageScore(
        value=len(hits) / denominator if denominator else 0.0,
        hits=hits,
        matched_groups=tuple(matched_groups),
    )


def evaluate_evidence_metrics(
    cases: Sequence[RAGEvaluationCase],
    traces: Sequence[RAGEvaluationTrace],
    *,
    mode: MatchingMode,
) -> EvidenceMetrics:
    """Score only the three requested metrics on the legacy eligible cases.

    Running the old evaluator first preserves its input checks and permits an
    exact compatibility assertion against its aggregate metrics.
    """
    evaluate_traces(cases, traces)
    trace_by_case = {trace.case_id: trace for trace in traces}
    answerable = [
        case for case in cases
        if case.annotation_status is AnnotationStatus.APPROVED
        and case.expected_outcome is ExpectedOutcome.ANSWERABLE
    ]
    scored: list[EvidenceCaseScores] = []
    for case in answerable:
        trace = trace_by_case[case.case_id]
        scored.append(EvidenceCaseScores(
            case_id=case.case_id,
            recall_at_40=score_stage(
                case, _unique([x.parent_id for x in trace.candidate_pool])[:40],
                metric="recall", mode=mode,
            ),
            rerank_recall_at_12=score_stage(
                case, _unique([x.parent_id for x in trace.reranked_context])[:12],
                metric="recall", mode=mode,
            ),
            final_context_precision=score_stage(
                case, [x.parent_id for x in trace.final_context],
                metric="precision", mode=mode,
            ),
        ))
    count = len(scored)
    result = EvidenceMetrics(
        mode=mode,
        case_count=count,
        recall_at_40=sum(x.recall_at_40.value for x in scored) / count,
        rerank_recall_at_12=sum(x.rerank_recall_at_12.value for x in scored) / count,
        final_context_precision=sum(x.final_context_precision.value for x in scored) / count,
        cases=tuple(scored),
    )
    return result

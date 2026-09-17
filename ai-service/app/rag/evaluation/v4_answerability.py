"""V4-only positive-evidence metrics using answerability and existing group matching.

The legacy and group-aware evaluators retain their original case eligibility.
This module scores explicit supported evidence only; it does not infer missing
claim-to-span links from reference answers or required_facts prose.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.rag.evaluation.group_aware import score_stage
from app.rag.evaluation.models import (
    AnnotationStatus,
    EvidenceGroup,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    SupportingSpanAnnotation,
)


@dataclass(frozen=True)
class EvidenceUnit:
    unit_id: str
    kind: Literal["group", "claim", "unlabeled_span"]
    claim: str | None
    anchor_parent_id: str
    group: EvidenceGroup | None
    spans: tuple[SupportingSpanAnnotation, ...]


@dataclass(frozen=True)
class EvidenceUnitResult:
    unit: EvidenceUnit
    top40_hit: bool
    top12_hit: bool
    final_hit: bool
    final_accepted_parent_ids: tuple[str, ...]


@dataclass(frozen=True)
class V4CaseEvidenceScores:
    case_id: str
    answerability_level: str
    evidence_unit_count: int
    evidence_recall_at_40: float
    evidence_rerank_recall_at_12: float
    final_context_precision: float
    units: tuple[EvidenceUnitResult, ...]


@dataclass(frozen=True)
class V4AnswerabilityMetrics:
    eligible_full_cases: int
    eligible_partial_cases: int
    insufficient_cases: int
    needs_input_cases: int
    evidence_unit_count: int
    full_evidence_unit_count: int
    partial_evidence_unit_count: int
    evidence_recall_at_40: float
    evidence_rerank_recall_at_12: float
    final_context_precision: float
    cases: tuple[V4CaseEvidenceScores, ...]


def _unique(ids: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ids))


def supported_evidence_units(case: RAGEvaluationCase) -> tuple[EvidenceUnit, ...]:
    """One explicit group or one distinct supported claim/span per unit.

    Repeated claim labels on one Parent collapse. The same label on different
    Parents without a group is ambiguous and is rejected rather than guessed
    to mean equivalent or complementary evidence.
    """
    if case.answerability_level not in ("fully_answerable", "partially_answerable"):
        return ()
    supported = [span for span in case.supporting_spans if span.support_level in ("full", "partial")]
    unlabeled_level = [span for span in case.supporting_spans if span.support_level is None]
    if unlabeled_level:
        raise ValueError(f"V4 supported span lacks support_level:{case.case_id}")
    if case.answerability_level == "fully_answerable" and len(supported) != len(case.supporting_spans):
        raise ValueError(f"fully answerable case has insufficient span:{case.case_id}")
    groups_by_parent: dict[str, EvidenceGroup] = {}
    units: list[EvidenceUnit] = []
    for group in case.evidence_groups:
        for parent_id in group.acceptable_parent_ids:
            if parent_id in groups_by_parent:
                raise ValueError(f"overlapping evidence groups:{case.case_id}:{parent_id}")
            groups_by_parent[parent_id] = group
        anchors = [pid for pid in case.relevant_parent_ids if pid in group.acceptable_parent_ids]
        spans = tuple(span for span in supported if span.parent_id in group.acceptable_parent_ids)
        if not anchors or not spans:
            raise ValueError(f"evidence group lacks supported anchor/span:{case.case_id}:{group.evidence_group_id}")
        units.append(EvidenceUnit(
            unit_id=f"group:{group.evidence_group_id}",
            kind="group", claim=group.claim, anchor_parent_id=anchors[0],
            group=group, spans=spans,
        ))
    explicit: dict[tuple[str, str], list[SupportingSpanAnnotation]] = {}
    claim_parents: dict[str, str] = {}
    unlabeled_index = 0
    for span in supported:
        if span.parent_id in groups_by_parent:
            continue
        if span.parent_id not in case.relevant_parent_ids:
            raise ValueError(f"supported span Parent is not relevant:{case.case_id}:{span.parent_id}")
        if span.claim is None:
            unlabeled_index += 1
            units.append(EvidenceUnit(
                unit_id=f"span:{unlabeled_index}", kind="unlabeled_span",
                claim=None, anchor_parent_id=span.parent_id, group=None,
                spans=(span,),
            ))
            continue
        prior = claim_parents.setdefault(span.claim, span.parent_id)
        if prior != span.parent_id:
            raise ValueError(f"claim spans multiple Parents without group:{case.case_id}:{span.claim}")
        explicit.setdefault((span.claim, span.parent_id), []).append(span)
    for index, ((claim, parent_id), spans) in enumerate(explicit.items(), start=1):
        units.append(EvidenceUnit(
            unit_id=f"claim:{index}", kind="claim", claim=claim,
            anchor_parent_id=parent_id, group=None, spans=tuple(spans),
        ))
    if case.answerability_level == "fully_answerable":
        covered = {parent_id for unit in units for parent_id in (
            unit.group.acceptable_parent_ids if unit.group else (unit.anchor_parent_id,)
        )}
        missing = set(case.relevant_parent_ids).difference(covered)
        if missing:
            raise ValueError(f"fully answerable relevant Parent lacks evidence:{case.case_id}:{sorted(missing)[0]}")
    if not units:
        raise ValueError(f"V4 positive case has no supported evidence units:{case.case_id}")
    return tuple(units)


def _project(case: RAGEvaluationCase, unit: EvidenceUnit) -> RAGEvaluationCase:
    return case.model_copy(update={
        "relevant_parent_ids": [unit.anchor_parent_id],
        "evidence_groups": [unit.group] if unit.group else [],
    })


def score_v4_case(
    case: RAGEvaluationCase,
    candidate_parent_ids: Sequence[str],
    reranked_parent_ids: Sequence[str],
    final_parent_ids: Sequence[str],
) -> V4CaseEvidenceScores:
    units = supported_evidence_units(case)
    if not units:
        raise ValueError(f"case is not positive evidence eligible:{case.case_id}")
    top40 = _unique(candidate_parent_ids)[:40]
    top12 = _unique(reranked_parent_ids)[:12]
    final = _unique(final_parent_ids)
    results: list[EvidenceUnitResult] = []
    relevant_final: set[str] = set()
    for unit in units:
        projected = _project(case, unit)
        top40_hit = bool(score_stage(projected, top40, metric="recall", mode="group_aware").hits)
        top12_hit = bool(score_stage(projected, top12, metric="recall", mode="group_aware").hits)
        final_hit = bool(score_stage(projected, final, metric="recall", mode="group_aware").hits)
        accepted = score_stage(projected, final, metric="precision", mode="group_aware").hits
        relevant_final.update(accepted)
        results.append(EvidenceUnitResult(
            unit=unit, top40_hit=top40_hit, top12_hit=top12_hit,
            final_hit=final_hit, final_accepted_parent_ids=accepted,
        ))
    count = len(results)
    return V4CaseEvidenceScores(
        case_id=case.case_id,
        answerability_level=case.answerability_level or "",
        evidence_unit_count=count,
        evidence_recall_at_40=sum(item.top40_hit for item in results) / count,
        evidence_rerank_recall_at_12=sum(item.top12_hit for item in results) / count,
        final_context_precision=len(relevant_final) / len(final) if final else 0.0,
        units=tuple(results),
    )


def evaluate_v4_answerability(
    cases: Sequence[RAGEvaluationCase],
    traces: Sequence[RAGEvaluationTrace],
) -> V4AnswerabilityMetrics:
    """Macro-average over approved full + partial cases only, on fixed traces."""
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("V4 answerability requires unique dataset cases")
    if len({trace.case_id for trace in traces}) != len(traces):
        raise ValueError("V4 answerability requires unique traces")
    trace_by_case = {trace.case_id: trace for trace in traces}
    if set(trace_by_case).difference(case.case_id for case in cases):
        raise ValueError("historical traces contain unknown cases")
    approved = [case for case in cases if case.annotation_status is AnnotationStatus.APPROVED]
    positive = [case for case in approved if case.answerability_level in ("fully_answerable", "partially_answerable")]
    if not positive:
        raise ValueError("V4 answerability requires approved positive evidence cases")
    missing = [case.case_id for case in approved if case.case_id not in trace_by_case]
    if missing:
        raise ValueError(f"historical traces missing approved cases:{','.join(missing)}")
    scored = tuple(score_v4_case(
        case,
        [context.parent_id for context in trace_by_case[case.case_id].candidate_pool],
        [context.parent_id for context in trace_by_case[case.case_id].reranked_context],
        [context.parent_id for context in trace_by_case[case.case_id].final_context],
    ) for case in positive)
    count = len(scored)
    return V4AnswerabilityMetrics(
        eligible_full_cases=sum(case.answerability_level == "fully_answerable" for case in positive),
        eligible_partial_cases=sum(case.answerability_level == "partially_answerable" for case in positive),
        insufficient_cases=sum(case.answerability_level == "insufficient_evidence" for case in approved),
        needs_input_cases=sum(case.answerability_level is None for case in approved),
        evidence_unit_count=sum(case.evidence_unit_count for case in scored),
        full_evidence_unit_count=sum(case.evidence_unit_count for case in scored if case.answerability_level == "fully_answerable"),
        partial_evidence_unit_count=sum(case.evidence_unit_count for case in scored if case.answerability_level == "partially_answerable"),
        evidence_recall_at_40=sum(case.evidence_recall_at_40 for case in scored) / count,
        evidence_rerank_recall_at_12=sum(case.evidence_rerank_recall_at_12 for case in scored) / count,
        final_context_precision=sum(case.final_context_precision for case in scored) / count,
        cases=scored,
    )

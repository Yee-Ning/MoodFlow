"""Evidence-group scoring without changing the legacy case denominator."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.rag.evaluation.group_aware import evaluate_evidence_metrics, score_stage
from app.rag.evaluation.io import load_evaluation_cases, load_evaluation_traces
from app.rag.evaluation.models import RAGEvaluationCase
from app.rag.evaluation.service import evaluate_traces


ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "evals/datasets"
TRACES = ROOT / "evals/artifacts"
TRACE_NAMES = (
    "gold_v2_trace_v3_fallback_20260916.jsonl",
    "gold_v2_trace_v3_nav_context6_20260916.jsonl",
)


def case(*, relation: str | None = None) -> RAGEvaluationCase:
    return RAGEvaluationCase.model_validate({
        "case_id": "example",
        "query": "example",
        "expected_outcome": "answerable",
        "annotation_status": "approved",
        "relevant_parent_ids": ["A"],
        "supporting_spans": [{"parent_id": "A", "exact_text": "evidence"}],
        "required_facts": ["claim"],
        "reference_answer": "answer",
        "evidence_groups": ([{
            "evidence_group_id": "claim-1",
            "claim": "claim",
            "relation": relation,
            "acceptable_parent_ids": ["A", "B"],
        }] if relation else []),
    })


class EvidenceGroupScoringTests(unittest.TestCase):
    def test_exact_parent_hit(self) -> None:
        self.assertEqual(score_stage(case(), ["A"], metric="recall", mode="group_aware").value, 1)
        self.assertEqual(score_stage(case(), ["B"], metric="recall", mode="group_aware").value, 0)

    def test_equivalent_a_and_b_each_hit(self) -> None:
        for parent_id in ("A", "B"):
            with self.subTest(parent_id=parent_id):
                self.assertEqual(score_stage(case(relation="equivalent"), [parent_id], metric="recall", mode="group_aware").value, 1)
                self.assertEqual(score_stage(case(relation="equivalent"), [parent_id], metric="precision", mode="group_aware").value, 1)
        self.assertEqual(score_stage(case(relation="equivalent"), ["B"], metric="recall", mode="compatibility").value, 0)

    def test_duplicate_alternative_hit_and_no_group_miss(self) -> None:
        grouped = case(relation="duplicate")
        self.assertEqual(score_stage(grouped, ["B"], metric="recall", mode="group_aware").value, 1)
        self.assertEqual(score_stage(grouped, ["C"], metric="recall", mode="group_aware").value, 0)
        self.assertEqual(score_stage(grouped, ["C"], metric="precision", mode="group_aware").value, 0)

    def test_complementary_partial_is_not_full_hit(self) -> None:
        grouped = case(relation="complementary")
        partial = score_stage(grouped, ["A"], metric="recall", mode="group_aware")
        self.assertEqual(partial.value, 0)
        self.assertFalse(partial.matched_groups[0]["complete"])
        self.assertEqual(score_stage(grouped, ["A"], metric="precision", mode="group_aware").value, 0)
        self.assertEqual(score_stage(grouped, ["A", "B"], metric="recall", mode="group_aware").value, 1)
        self.assertEqual(score_stage(grouped, ["A", "B"], metric="precision", mode="group_aware").value, 1)

    def test_group_without_relevant_anchor_is_rejected(self) -> None:
        grouped = case(relation="equivalent")
        invalid = grouped.model_copy(update={"relevant_parent_ids": ["C"]})
        with self.assertRaisesRegex(ValueError, "without relevant anchor"):
            score_stage(invalid, ["A"], metric="recall", mode="group_aware")

    def test_v3_legacy_compatibility_on_both_historical_traces(self) -> None:
        cases = load_evaluation_cases(DATASETS / "rag_eval_gold_v3.ai_assisted_source_resolution.jsonl")
        self.assertTrue(all(not item.evidence_groups for item in cases))
        for name in TRACE_NAMES:
            with self.subTest(trace=name):
                traces = load_evaluation_traces(TRACES / name)
                old = evaluate_traces(cases, traces)
                compatible = evaluate_evidence_metrics(cases, traces, mode="compatibility")
                aware = evaluate_evidence_metrics(cases, traces, mode="group_aware")
                self.assertEqual(compatible.case_count, old.evaluated_answerable_count)
                self.assertEqual(compatible.recall_at_40, old.retrieval.recall_at_40)
                self.assertEqual(compatible.rerank_recall_at_12, old.rerank.rerank_recall_at_12)
                self.assertEqual(compatible.final_context_precision, old.final_context_precision)
                self.assertEqual(
                    (aware.recall_at_40, aware.rerank_recall_at_12, aware.final_context_precision),
                    (compatible.recall_at_40, compatible.rerank_recall_at_12, compatible.final_context_precision),
                )
                old_by_id = {item.case_id: item for item in old.retrieval.cases}
                rerank_by_id = {item.case_id: item for item in old.rerank.cases}
                for item in compatible.cases:
                    self.assertEqual(item.recall_at_40.value, old_by_id[item.case_id].recall_at_40)
                    self.assertEqual(item.rerank_recall_at_12.value, rerank_by_id[item.case_id].rerank_recall_at_12)
                for a, b in zip(compatible.cases, aware.cases, strict=True):
                    self.assertEqual(a.case_id, b.case_id)
                    self.assertEqual(a.recall_at_40.value, b.recall_at_40.value)
                    self.assertEqual(a.rerank_recall_at_12.value, b.rerank_recall_at_12.value)
                    self.assertEqual(a.final_context_precision.value, b.final_context_precision.value)

    def test_v4_only_026_has_groups(self) -> None:
        cases = load_evaluation_cases(DATASETS / "rag_eval_gold_v4.candidate.jsonl")
        grouped = [item for item in cases if item.evidence_groups]
        self.assertEqual([item.case_id for item in grouped], ["rag-v0-026"])
        self.assertEqual([group.relation for group in grouped[0].evidence_groups], ["duplicate", "duplicate"])
        case_026 = grouped[0]
        alternatives = [group.acceptable_parent_ids[1] for group in case_026.evidence_groups]
        self.assertEqual(
            score_stage(case_026, alternatives, metric="recall", mode="compatibility").value,
            0,
        )
        self.assertEqual(
            score_stage(case_026, alternatives, metric="recall", mode="group_aware").value,
            1,
        )

    def test_v4_historical_traces_have_explained_zero_delta(self) -> None:
        cases = load_evaluation_cases(DATASETS / "rag_eval_gold_v4.candidate.jsonl")
        for name in TRACE_NAMES:
            with self.subTest(trace=name):
                traces = load_evaluation_traces(TRACES / name)
                old = evaluate_evidence_metrics(cases, traces, mode="compatibility")
                aware = evaluate_evidence_metrics(cases, traces, mode="group_aware")
                self.assertEqual(old.case_count, aware.case_count)
                self.assertEqual(
                    (old.recall_at_40, old.rerank_recall_at_12, old.final_context_precision),
                    (aware.recall_at_40, aware.rerank_recall_at_12, aware.final_context_precision),
                )
                for a, b in zip(old.cases, aware.cases, strict=True):
                    for stage in ("recall_at_40", "rerank_recall_at_12", "final_context_precision"):
                        self.assertEqual(getattr(a, stage).value, getattr(b, stage).value)
                case_026 = next(x for x in aware.cases if x.case_id == "rag-v0-026")
                tracklist = next(
                    group for group in case_026.rerank_recall_at_12.matched_groups
                    if group["evidence_group_id"] == "026-tracklist"
                )
                self.assertEqual(len(tracklist["retrieved_parent_ids"]), 2)
                self.assertTrue(tracklist["complete"])


if __name__ == "__main__":
    unittest.main()

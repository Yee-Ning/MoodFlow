"""V4 partial evidence is scored without treating unsupported claims as Gold."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.rag.evaluation.group_aware import evaluate_evidence_metrics
from app.rag.evaluation.io import load_evaluation_cases, load_evaluation_traces
from app.rag.evaluation.models import EvidenceGroup, RAGEvaluationCase
from app.rag.evaluation.service import evaluate_traces
from app.rag.evaluation.v4_answerability import (
    evaluate_v4_answerability,
    score_v4_case,
    supported_evidence_units,
)


ROOT = Path(__file__).resolve().parents[1]


def make_case(
    case_id: str,
    level: str,
    *,
    grouped: str | None = None,
    include_unsupported: bool = False,
) -> RAGEvaluationCase:
    spans = [
        {"parent_id": "A", "exact_text": "fact A", "claim": "supported A", "support_level": "full"},
        {"parent_id": "C" if include_unsupported else "B", "exact_text": "fact B",
         "claim": "unsupported C" if include_unsupported else "supported B",
         "support_level": "insufficient" if include_unsupported else "full"},
    ]
    relevant = ["A", "C"] if include_unsupported else ["A", "B"]
    if grouped:
        spans = spans[:1]
        relevant = ["A"]
    return RAGEvaluationCase.model_validate({
        "case_id": case_id,
        "query": case_id,
        "annotation_status": "approved",
        "expected_outcome": "answerable" if level == "fully_answerable" else "insufficient_evidence",
        "answerability_level": level,
        "relevant_parent_ids": relevant,
        "supporting_spans": spans,
        "evidence_groups": ([{
            "evidence_group_id": "example-group", "claim": "supported A",
            "relation": grouped, "acceptable_parent_ids": ["A", "B"],
        }] if grouped else []),
        "required_facts": ["supported A", "supported B", "unsupported C"],
        "reference_answer": "Only supported evidence is scored.",
    })


class V4AnswerabilityTests(unittest.TestCase):
    def test_full_case_scores_all_supported_evidence(self) -> None:
        case = make_case("full", "fully_answerable")
        result = score_v4_case(case, ["A", "B"], ["A"], ["A", "X"])
        self.assertEqual(result.evidence_unit_count, 2)
        self.assertEqual(result.evidence_recall_at_40, 1)
        self.assertEqual(result.evidence_rerank_recall_at_12, 0.5)
        self.assertEqual(result.final_context_precision, 0.5)

    def test_partial_only_two_supported_claims_enter_denominator(self) -> None:
        case = make_case("partial", "partially_answerable")
        result = score_v4_case(case, ["A", "B"], ["B"], ["A", "B", "X"])
        self.assertEqual(result.evidence_unit_count, 2)
        self.assertEqual([x.unit.claim for x in result.units], ["supported A", "supported B"])
        self.assertEqual(result.evidence_recall_at_40, 1)
        self.assertEqual(result.evidence_rerank_recall_at_12, 0.5)
        self.assertEqual(result.final_context_precision, 2 / 3)

    def test_unsupported_span_is_excluded_even_when_retrieved(self) -> None:
        case = make_case("partial-unsupported", "partially_answerable", include_unsupported=True)
        result = score_v4_case(case, ["C"], ["C"], ["C"])
        self.assertEqual(result.evidence_unit_count, 1)
        self.assertEqual(result.units[0].unit.claim, "supported A")
        self.assertEqual(result.evidence_recall_at_40, 0)
        self.assertEqual(result.evidence_rerank_recall_at_12, 0)
        self.assertEqual(result.final_context_precision, 0)

    def test_partial_equivalent_alternative_hits_one_group_unit(self) -> None:
        case = make_case("partial-equivalent", "partially_answerable", grouped="equivalent")
        second = case.supporting_spans[0].model_copy(update={
            "parent_id": "B", "exact_text": "same claim in alternative Parent",
        })
        case = case.model_copy(update={
            "relevant_parent_ids": ["A", "B"],
            "supporting_spans": [*case.supporting_spans, second],
        })
        result = score_v4_case(case, ["B"], ["B"], ["B", "X"])
        self.assertEqual(result.evidence_unit_count, 1)
        self.assertEqual(result.units[0].unit.kind, "group")
        self.assertTrue(result.units[0].top40_hit)
        self.assertTrue(result.units[0].top12_hit)
        self.assertEqual(result.final_context_precision, 0.5)

    def test_partial_complementary_requires_both_parents(self) -> None:
        case = make_case("partial-complementary", "partially_answerable", grouped="complementary")
        partial = score_v4_case(case, ["A"], ["A"], ["A"])
        self.assertEqual(partial.evidence_unit_count, 1)
        self.assertEqual(partial.evidence_recall_at_40, 0)
        self.assertEqual(partial.final_context_precision, 0)
        complete = score_v4_case(case, ["A", "B"], ["A", "B"], ["A", "B"])
        self.assertEqual(complete.evidence_recall_at_40, 1)
        self.assertEqual(complete.evidence_rerank_recall_at_12, 1)
        self.assertEqual(complete.final_context_precision, 1)

    def test_distinct_claims_reuse_identical_parent_alternatives(self) -> None:
        case = make_case("shared-claim-parents", "fully_answerable")
        spans = [span.model_copy(update={"parent_id": "A"}) for span in case.supporting_spans]
        groups = [{"evidence_group_id": f"song-{index}", "claim": span.claim,
                   "relation": "duplicate", "acceptable_parent_ids": ["A", "B"]}
                  for index, span in enumerate(spans, 1)]
        case = case.model_copy(update={
            "relevant_parent_ids": ["A"], "supporting_spans": spans,
            "evidence_groups": [EvidenceGroup.model_validate(item) for item in groups],
        })
        units = supported_evidence_units(case)
        self.assertEqual(len(units), 2)
        self.assertEqual([unit.claim for unit in units], ["supported A", "supported B"])
        self.assertEqual([len(unit.spans) for unit in units], [1, 1])
        for pid in ("A", "B"):
            with self.subTest(parent_id=pid):
                result = score_v4_case(case, [pid], [pid], [pid])
                self.assertEqual(result.evidence_unit_count, 2)
                self.assertEqual([item.top40_hit for item in result.units], [True, True])
                self.assertEqual([item.top12_hit for item in result.units], [True, True])
                self.assertEqual(result.evidence_recall_at_40, 1)
                self.assertEqual(result.evidence_rerank_recall_at_12, 1)
        missing = score_v4_case(case, [], [], [])
        self.assertEqual(missing.evidence_unit_count, 2)
        self.assertEqual(missing.evidence_recall_at_40, 0)
        duplicate_claim = groups[1] | {"claim": "supported A"}
        bad = case.model_copy(update={"evidence_groups": [
            case.evidence_groups[0], EvidenceGroup.model_validate(duplicate_claim),
        ]})
        with self.assertRaisesRegex(ValueError, "overlapping evidence groups for claim"):
            supported_evidence_units(bad)
        missing_claim = groups[1] | {"claim": "unsupported song"}
        bad = case.model_copy(update={"evidence_groups": [
            case.evidence_groups[0], EvidenceGroup.model_validate(missing_claim),
        ]})
        with self.assertRaisesRegex(ValueError, "lacks supported anchor/span"):
            supported_evidence_units(bad)

    def test_insufficient_case_does_not_enter_positive_metrics(self) -> None:
        trace = load_evaluation_traces(
            ROOT / "evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl"
        )[0]
        full = make_case("full-example", "fully_answerable")
        insufficient = make_case("insufficient-example", "insufficient_evidence")
        full_trace = trace.model_copy(update={"case_id": full.case_id})
        insufficient_trace = trace.model_copy(update={"case_id": insufficient.case_id})
        result = evaluate_v4_answerability([full, insufficient], [full_trace, insufficient_trace])
        self.assertEqual(result.eligible_full_cases, 1)
        self.assertEqual(result.eligible_partial_cases, 0)
        self.assertEqual(result.insufficient_cases, 1)
        self.assertEqual([x.case_id for x in result.cases], [full.case_id])

    def test_ambiguous_same_claim_on_two_parents_requires_group(self) -> None:
        case = make_case("ambiguous", "partially_answerable")
        spans = list(case.supporting_spans)
        spans[1] = spans[1].model_copy(update={"claim": "supported A"})
        invalid = case.model_copy(update={"supporting_spans": spans})
        with self.assertRaisesRegex(ValueError, "without group"):
            supported_evidence_units(invalid)

    def test_v3_legacy_and_compatibility_remain_identical(self) -> None:
        cases = load_evaluation_cases(
            ROOT / "evals/datasets/rag_eval_gold_v3.ai_assisted_source_resolution.jsonl"
        )
        traces = load_evaluation_traces(
            ROOT / "evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl"
        )
        old = evaluate_traces(cases, traces)
        compatible = evaluate_evidence_metrics(cases, traces, mode="compatibility")
        self.assertEqual(compatible.recall_at_40, old.retrieval.recall_at_40)
        self.assertEqual(compatible.rerank_recall_at_12, old.rerank.rerank_recall_at_12)
        self.assertEqual(compatible.final_context_precision, old.final_context_precision)

    def test_real_v4_priority_partial_units_use_only_supported_spans(self) -> None:
        cases = load_evaluation_cases(ROOT / "evals/datasets/rag_eval_gold_v4.candidate.jsonl")
        by_id = {case.case_id[-3:]: case for case in cases}
        for cid, expected in {"003": 3, "034": 4, "036": 5, "039": 2}.items():
            with self.subTest(case_id=cid):
                case = by_id[cid]
                self.assertEqual(case.answerability_level, "partially_answerable")
                units = supported_evidence_units(case)
                self.assertEqual(len(units), expected)
                self.assertTrue(all(
                    span.support_level in ("full", "partial")
                    for unit in units for span in unit.spans
                ))
                self.assertTrue(all(unit.claim is not None for unit in units))
        self.assertEqual(supported_evidence_units(by_id["038"]), ())

    def test_real_v4_counts_on_fixed_historical_trace(self) -> None:
        cases = load_evaluation_cases(ROOT / "evals/datasets/rag_eval_gold_v4.candidate.jsonl")
        traces = load_evaluation_traces(
            ROOT / "evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl"
        )
        result = evaluate_v4_answerability(cases, traces)
        self.assertEqual((result.eligible_full_cases, result.eligible_partial_cases), (13, 8))
        self.assertEqual((result.insufficient_cases, result.needs_input_cases), (6, 3))
        self.assertEqual((result.evidence_unit_count, result.full_evidence_unit_count,
                          result.partial_evidence_unit_count), (68, 47, 21))
        case_026 = next(case for case in result.cases if case.case_id == "rag-v0-026")
        self.assertEqual(case_026.evidence_unit_count, 5)
        self.assertEqual(case_026.evidence_recall_at_40, 1.0)
        self.assertEqual(case_026.evidence_rerank_recall_at_12, 0.8)
        self.assertEqual(sum(unit.unit.kind == "group" for unit in case_026.units), 5)
        self.assertNotIn("rag-v0-038", {case.case_id for case in result.cases})


if __name__ == "__main__":
    unittest.main()

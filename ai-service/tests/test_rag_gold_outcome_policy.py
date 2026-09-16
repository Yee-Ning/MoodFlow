import unittest
from pathlib import Path

from app.rag.evaluation.gold_outcome_policy import apply_needs_input_outcome_policy
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.models import (
    AnnotationStatus,
    ExpectedOutcome,
    RAGEvaluationCase,
    SupportingSpanAnnotation,
)


def _case(case_id: str, query: str) -> RAGEvaluationCase:
    return RAGEvaluationCase(
        case_id=case_id,
        query=query,
        expected_outcome=ExpectedOutcome.INSUFFICIENT_EVIDENCE,
        annotation_status=AnnotationStatus.APPROVED,
        relevant_parent_ids=["parent-1"],
        supporting_spans=[SupportingSpanAnnotation(
            parent_id="parent-1", exact_text="原文", source_id="source-1",
        )],
        required_facts=["事实"],
        expected_source_ids=["source-1"],
        reference_answer="旧答案",
        tags=["gold_v0"],
        notes="Original review.",
    )


class GoldOutcomePolicyTests(unittest.TestCase):
    def test_materialized_policy_dataset_changes_only_two_cases(self) -> None:
        root = Path(__file__).resolve().parents[1]
        baseline = load_evaluation_cases(
            root / "evals/datasets/rag_eval_gold_v0.approved.jsonl"
        )
        materialized = load_evaluation_cases(
            root / "evals/datasets/rag_eval_gold_v1.outcome_policy.approved.jsonl"
        )
        self.assertEqual(materialized, apply_needs_input_outcome_policy(baseline))
        changed = [
            old.case_id for old, new in zip(baseline, materialized, strict=True)
            if old != new
        ]
        self.assertEqual(changed, ["rag-v0-046", "rag-v0-047"])

    def test_only_two_ambiguous_queries_change_and_evidence_stays_in_v0(self) -> None:
        original = [
            _case("rag-v0-046", "七里香的创作背景"),
            _case("rag-v0-047", "霍元甲的创作背景"),
            _case("other", "其他问题"),
        ]
        revised = apply_needs_input_outcome_policy(original)
        self.assertEqual(original[0].expected_outcome, ExpectedOutcome.INSUFFICIENT_EVIDENCE)
        self.assertEqual(original[0].relevant_parent_ids, ["parent-1"])
        self.assertIs(revised[2], original[2])
        for case in revised[:2]:
            self.assertEqual(case.expected_outcome, ExpectedOutcome.NEEDS_INPUT)
            self.assertEqual(case.relevant_parent_ids, [])
            self.assertEqual(case.supporting_spans, [])
            self.assertEqual(case.required_facts, [])
            self.assertEqual(case.expected_source_ids, [])
            self.assertIsNone(case.reference_answer)
            self.assertIn("gold_v1_outcome_policy", case.tags)

    def test_rejects_changed_query_and_prior_outcome(self) -> None:
        cases = [
            _case("rag-v0-046", "七里香的创作背景"),
            _case("rag-v0-047", "霍元甲的创作背景"),
        ]
        cases[0] = cases[0].model_copy(update={"query": "七里香专辑的故事"})
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            apply_needs_input_outcome_policy(cases)
        cases[0] = _case("rag-v0-046", "七里香的创作背景").model_copy(update={
            "expected_outcome": ExpectedOutcome.NEEDS_INPUT,
        })
        with self.assertRaisesRegex(ValueError, "unexpected prior outcome"):
            apply_needs_input_outcome_policy(cases)


if __name__ == "__main__":
    unittest.main()

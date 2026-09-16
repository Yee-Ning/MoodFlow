import json
import tempfile
import unittest
from pathlib import Path

from app.rag.evaluation.gold_review import (
    ANSWERABLE_QUOTAS,
    audit_silver_provenance,
    blind_review_markdown,
    review_summary,
    select_blind_review_cases,
)
from app.rag.evaluation.models import (
    AnnotationStatus,
    EvaluationContext,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    SupportingSpanAnnotation,
    TraceStageStatuses,
)


def silver_case(case_id: str, outcome: ExpectedOutcome, query_type: str = "") -> RAGEvaluationCase:
    evidence = outcome is ExpectedOutcome.ANSWERABLE
    return RAGEvaluationCase(
        case_id=case_id,
        query=f"Question {case_id}",
        expected_outcome=outcome,
        annotation_status=AnnotationStatus.APPROVED,
        expected_query_types=[query_type] if query_type else [],
        relevant_parent_ids=["p1"] if evidence else [],
        supporting_spans=[SupportingSpanAnnotation(parent_id="p1", exact_text="Secret span")]
        if evidence else [],
        required_facts=["Secret span"] if evidence else [],
        reference_answer="Secret answer" if evidence else None,
        tags=["silver_bootstrap_v0"],
        notes="Secret old judgement",
    )


class GoldReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = []
        sequence = 0
        for query_type, count in ANSWERABLE_QUOTAS.items():
            for _ in range(count + 1):
                sequence += 1
                self.cases.append(
                    silver_case(
                        f"case-{sequence:03d}",
                        ExpectedOutcome.ANSWERABLE,
                        "" if query_type == "untyped" else query_type,
                    )
                )
        for _ in range(4):
            sequence += 1
            self.cases.append(silver_case(f"case-{sequence:03d}", ExpectedOutcome.NEEDS_INPUT))
        sequence += 1
        self.cases.append(
            silver_case(f"case-{sequence:03d}", ExpectedOutcome.INSUFFICIENT_EVIDENCE)
        )

    def test_selects_frozen_strata_and_erases_silver_labels(self) -> None:
        selected = select_blind_review_cases(self.cases)
        self.assertEqual(review_summary(selected)["gold_approved_cases"], 0)
        self.assertEqual(len(selected), 30)
        self.assertEqual([item.case_id for item in selected], sorted(item.case_id for item in selected))
        for item in selected:
            self.assertEqual(item.annotation_status, AnnotationStatus.PENDING)
            self.assertFalse(item.relevant_parent_ids)
            self.assertFalse(item.supporting_spans)
            self.assertFalse(item.required_facts)
            self.assertIsNone(item.reference_answer)
            self.assertIsNone(item.notes)
            self.assertNotIn("silver_bootstrap_v0", item.tags)
        pack = blind_review_markdown(selected)
        self.assertNotIn("Secret span", pack)
        self.assertNotIn("Secret answer", pack)
        self.assertNotIn("Secret old judgement", pack)

    def test_rejects_incomplete_stratum(self) -> None:
        with self.assertRaisesRegex(ValueError, "stratum insufficient_evidence"):
            select_blind_review_cases(self.cases[:-1])

    def test_rejects_non_silver_input(self) -> None:
        bad = self.cases[0].model_copy(update={"tags": []})
        with self.assertRaisesRegex(ValueError, "lacks Silver provenance"):
            select_blind_review_cases([bad, *self.cases[1:]])

    def test_rejects_duplicate_case_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate case IDs"):
            select_blind_review_cases([*self.cases, self.cases[0]])

    def test_audit_checks_index_provenance_without_claiming_relevance(self) -> None:
        case = silver_case("case-001", ExpectedOutcome.ANSWERABLE, "fact")
        context = EvaluationContext(
            parent_id="p1", rank=1, title="Title", content="Secret span",
            source_id="s1", source_name="Fixture",
        )
        trace = RAGEvaluationTrace(
            run_id="baseline", case_id=case.case_id, query=case.query,
            statuses=TraceStageStatuses(query_understanding="ready"),
            candidate_pool=[context], final_context=[context],
        )
        case = case.model_copy(update={"expected_source_ids": ["s1"]})
        span = case.supporting_spans[0].model_copy(update={"source_id": "s1"})
        case = case.model_copy(update={"supporting_spans": [span]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projection.jsonl"
            row = {
                "parent_id": "p1",
                "payload": {"parent_context": {"content": "Title. Secret span"}, "source_id": "s1"},
            }
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            result = audit_silver_provenance([case], [trace], path)
        self.assertEqual(result["labels_equal_old_final_top3"], 1)
        self.assertEqual(result["spans_present_in_index_with_source"], 1)
        self.assertFalse(result["semantic_relevance_verified"])


if __name__ == "__main__":
    unittest.main()

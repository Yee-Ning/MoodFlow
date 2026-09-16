import unittest
import json
from pathlib import Path

from app.rag.evaluation.gold_evidence import SourceParent
from app.rag.evaluation.gold_source_adjudication import adjudicate_source_rows
from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.models import (
    AnnotationStatus,
    ExpectedOutcome,
    RAGEvaluationCase,
    SupportingSpanAnnotation,
)


def _parent(parent_id: str, content: str) -> SourceParent:
    return SourceParent(
        parent_id=parent_id, doc_id="doc-1", source_id="source-1",
        source_title="title", source_name="source", source_url=None,
        source_document_path="doc.json", breadcrumb="section", content=content,
    )


def _answerable() -> RAGEvaluationCase:
    return RAGEvaluationCase(
        case_id="answerable", query="音乐风格",
        expected_outcome=ExpectedOutcome.ANSWERABLE,
        annotation_status=AnnotationStatus.APPROVED,
        relevant_parent_ids=["parent-1", "parent-2"],
        supporting_spans=[SupportingSpanAnnotation(
            parent_id="parent-1", exact_text="原有证据", source_id="source-1",
        )],
        required_facts=["事实"], reference_answer="答案",
        expected_source_ids=["source-1"],
    )


def _proposal(case_id: str, parent_id: str, action: str) -> dict[str, object]:
    return {
        "case_id": case_id, "parent_id": parent_id,
        "source_id": "source-1", "action": action, "exact_text": "原始候选",
    }


def _decision(
    case_id: str, parent_id: str, *, accept: bool, text: str | None = None,
) -> dict[str, object]:
    return {
        "case_id": case_id, "parent_id": parent_id, "source_id": "source-1",
        "decision": "accept" if accept else "reject", "exact_text": text,
        "rationale": "题内事实支撑" if accept else "证据不足",
    }


class GoldSourceAdjudicationTests(unittest.TestCase):
    def test_materialized_v2_has_only_reviewed_amendments(self) -> None:
        root = Path(__file__).resolve().parents[1]
        v1 = load_evaluation_cases(
            root / "evals/datasets/rag_eval_gold_v1.outcome_policy.approved.jsonl"
        )
        v2 = load_evaluation_cases(
            root / "evals/datasets/rag_eval_gold_v2.ai_assisted_source_review.jsonl"
        )
        decisions = [
            json.loads(line)
            for line in (
                root / "evals/reports/gold_v2_source_decisions.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(v1), len(v2))
        self.assertEqual(sum(item["decision"] == "accept" for item in decisions), 16)
        self.assertEqual(sum(item["decision"] == "reject" for item in decisions), 2)
        changed = {
            old.case_id for old, new in zip(v1, v2, strict=True) if old != new
        }
        self.assertEqual(changed, {
            "rag-v0-003", "rag-v0-004", "rag-v0-024", "rag-v0-034",
            "rag-v0-036", "rag-v0-037", "rag-v0-040", "rag-v0-042",
            "rag-v0-043", "rag-v0-044",
        })
        missing = {
            (case.case_id, parent_id)
            for case in v2 for parent_id in case.relevant_parent_ids
            if parent_id not in {span.parent_id for span in case.supporting_spans}
        }
        self.assertEqual(missing, {
            ("rag-v0-040", "parent-531672f53462b6956869e0eb"),
        })

    def test_accepts_exact_spans_and_new_parent_but_preserves_rejections(self) -> None:
        original = _answerable()
        proposals = [
            _proposal("answerable", "parent-2", "propose_span"),
            _proposal("answerable", "parent-3", "propose_parent_and_span"),
            _proposal("needs-input", "parent-4", "propose_span"),
        ]
        decisions = [
            _decision("answerable", "parent-2", accept=True, text="审核后证据"),
            _decision("answerable", "parent-3", accept=True, text="新增证据"),
            _decision("needs-input", "parent-4", accept=False),
        ]
        needs_input = RAGEvaluationCase(
            case_id="needs-input", query="这首歌",
            expected_outcome=ExpectedOutcome.NEEDS_INPUT,
            annotation_status=AnnotationStatus.APPROVED,
        )
        revised, summary = adjudicate_source_rows(
            [original, needs_input], proposals, decisions,
            [_parent("parent-1", "原有证据"), _parent("parent-2", "审核后证据"),
             _parent("parent-3", "新增证据"), _parent("parent-4", "不应采用")],
            release_id="release-1", source_sha256="sha256:123",
        )
        self.assertEqual(original.relevant_parent_ids, ["parent-1", "parent-2"])
        self.assertEqual(revised[0].relevant_parent_ids, [
            "parent-1", "parent-2", "parent-3",
        ])
        self.assertEqual(len(revised[0].supporting_spans), 3)
        self.assertIs(revised[1], needs_input)
        self.assertEqual(summary["decision_counts"]["rejected"], 1)
        self.assertEqual(summary["missing_relevant_parent_spans_after"], 0)

    def test_rejects_incomplete_decisions_and_nonexact_reviewed_span(self) -> None:
        proposal = _proposal("answerable", "parent-2", "propose_span")
        parents = [_parent("parent-1", "原有证据"), _parent("parent-2", "原文")]
        with self.assertRaisesRegex(ValueError, "cover every proposal"):
            adjudicate_source_rows(
                [_answerable()], [proposal], [], parents,
                release_id="release-1", source_sha256="sha256:123",
            )
        with self.assertRaisesRegex(ValueError, "not exact"):
            adjudicate_source_rows(
                [_answerable()], [proposal],
                [_decision("answerable", "parent-2", accept=True, text="不存在")],
                parents, release_id="release-1", source_sha256="sha256:123",
            )

    def test_rejects_answer_evidence_for_needs_input(self) -> None:
        needs_input = RAGEvaluationCase(
            case_id="needs-input", query="这首歌",
            expected_outcome=ExpectedOutcome.NEEDS_INPUT,
            annotation_status=AnnotationStatus.APPROVED,
        )
        with self.assertRaisesRegex(ValueError, "non-answerable"):
            adjudicate_source_rows(
                [needs_input],
                [_proposal("needs-input", "parent-4", "propose_parent_and_span")],
                [_decision("needs-input", "parent-4", accept=True, text="证据")],
                [_parent("parent-4", "证据")],
                release_id="release-1", source_sha256="sha256:123",
            )


if __name__ == "__main__":
    unittest.main()

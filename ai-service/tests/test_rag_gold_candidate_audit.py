import unittest

from app.rag.evaluation.gold_candidate_audit import audit_proposal_rows
from app.rag.evaluation.gold_evidence import SourceParent
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


def _case() -> RAGEvaluationCase:
    return RAGEvaluationCase(
        case_id="case-1", query="音乐生涯",
        expected_outcome=ExpectedOutcome.ANSWERABLE,
        annotation_status=AnnotationStatus.APPROVED,
        relevant_parent_ids=["parent-1", "parent-2"],
        supporting_spans=[SupportingSpanAnnotation(
            parent_id="parent-1", exact_text="已标注原文。", source_id="source-1",
        )],
        required_facts=["事实"], reference_answer="回答",
    )


class GoldCandidateAuditTests(unittest.TestCase):
    def test_validates_span_completion_and_new_parent_without_approval(self) -> None:
        rows = [
            {"case_id": "case-1", "parent_id": "parent-2", "source_id": "source-1",
             "action": "propose_span", "exact_text": "遗漏的原文。"},
            {"case_id": "case-1", "parent_id": "parent-3", "source_id": "source-1",
             "action": "propose_parent_and_span", "exact_text": "补充原文。"},
        ]
        result = audit_proposal_rows(
            [_case()], rows,
            [_parent("parent-1", "已标注原文。"),
             _parent("parent-2", "遗漏的原文。"),
             _parent("parent-3", "补充原文。")],
            "release-1", "sha256:123",
        )
        self.assertEqual(result["status"], "source_validated_not_approved")
        self.assertEqual(result["missing_case_parent_spans_remaining"], 0)
        self.assertEqual(result["actions"]["propose_parent_and_span"], 1)

    def test_rejects_nonexact_source_and_mislabeled_existing_parent(self) -> None:
        parents = [_parent("parent-1", "已标注原文。"), _parent("parent-2", "遗漏的原文。")]
        row = {"case_id": "case-1", "parent_id": "parent-2", "source_id": "source-1",
               "action": "propose_span", "exact_text": "不在来源中的内容"}
        with self.assertRaisesRegex(ValueError, "non-exact"):
            audit_proposal_rows([_case()], [row], parents, "release-1", "sha256:123")
        row["exact_text"] = "遗漏的原文。"
        row["action"] = "propose_parent_and_span"
        with self.assertRaisesRegex(ValueError, "already Gold"):
            audit_proposal_rows([_case()], [row], parents, "release-1", "sha256:123")


if __name__ == "__main__":
    unittest.main()

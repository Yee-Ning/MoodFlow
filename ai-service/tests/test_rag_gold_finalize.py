import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.rag.evaluation.gold_evidence import SourceParent
from app.rag.evaluation.gold_finalize import _exact_corrected_span, finalize_review
from app.rag.evaluation.models import AnnotationStatus, RAGEvaluationCase


PARENT = SourceParent(
    parent_id="parent-abcdef", doc_id="doc-1", source_id="source-1",
    source_title="歌曲资料", source_name="唱片公司", source_url=None,
    source_document_path="data/normalized/source-1/document.json",
    breadcrumb="歌曲资料", content="首句原文说明合作事实。第二句提供补充背景。",
)


def review_text(query: str = "合作情况", span_decision: str = "APPROVE") -> str:
    return f"""# 已审批
- 冻结 Release：`release-1`
- Parent 原文 SHA256：`sha256:{'a' * 64}`

## rag-v0-001 · {query}

### expected_outcome
- 审核决定：APPROVE / CORRECT / REJECT：**APPROVE**
- 最终值与理由：`answerable`；原文支持。

### 来源 Parent 候选（非线上候选）
#### 候选 A · `parent-abcdef`
- relevant_parent：APPROVE / CORRECT / REJECT：**APPROVE**
- supporting_span：APPROVE / CORRECT / REJECT：**{span_decision}**
- 如修正，填写完整 Parent ID / 精确原文片段：“首句原文说明合作事实。”

### 汇总审核
- relevant_parent_ids（可增加未列出的真实 Parent）：`parent-abcdef`
- required_facts：APPROVE / CORRECT / REJECT：**CORRECT**；最终内容：[合作事实]
- reference_answer：APPROVE / CORRECT / REJECT：**CORRECT**；最终内容：原文说明了合作事实。
"""


class GoldFinalizeTests(unittest.TestCase):
    def test_corrected_span_must_be_source_exact_or_exact_sentence_prefix(self) -> None:
        first_sentence = "1999年的〈落雨聲〉是周杰倫第一首賣出的歌，由江蕙演唱，也是第一次和方文山合作的知名作品。"
        long_parent = replace(PARENT, content=first_sentence + "後續原文含有不同引號。")
        self.assertEqual(
            _exact_corrected_span("“" + first_sentence + "错误尾句。”", long_parent),
            (first_sentence, True),
        )
        self.assertEqual(
            _exact_corrected_span("“首句原文说明合作事实。”", PARENT),
            ("首句原文说明合作事实。", False),
        )
        with self.assertRaisesRegex(ValueError, "not found verbatim"):
            _exact_corrected_span("不存在的证据", PARENT)

    def test_finalizes_reviewed_case_from_frozen_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review_path = root / "review.md"
            review_path.write_text(review_text(), encoding="utf-8")
            pending_path = root / "pending.jsonl"
            pending_path.write_text(
                RAGEvaluationCase(case_id="rag-v0-001", query="合作情况", expected_outcome="answerable").model_dump_json() + "\n",
                encoding="utf-8",
            )
            candidates_path = root / "candidates.jsonl"
            candidates_path.write_text(json.dumps({
                "case_id": "rag-v0-001", "query": "合作情况", "release_id": "release-1",
                "source_sha256": f"sha256:{'a' * 64}",
                "candidates": [{
                    "parent_id": PARENT.parent_id, "source_id": PARENT.source_id,
                    "suggested_supporting_span": "首句原文说明合作事实。",
                }],
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            with patch(
                "app.rag.evaluation.gold_finalize.load_frozen_parents",
                return_value=("release-1", f"sha256:{'a' * 64}", [PARENT]),
            ):
                cases, summary = finalize_review(
                    review_path, pending_path, candidates_path, root / "manifest.json",
                )
            self.assertEqual(cases[0].annotation_status, AnnotationStatus.APPROVED)
            self.assertEqual(cases[0].expected_source_ids, ["source-1"])
            self.assertEqual(cases[0].supporting_spans[0].exact_text, "首句原文说明合作事实。")
            self.assertEqual(summary["case_count"], 1)

    def test_rejects_query_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            review_path = root / "review.md"
            review_path.write_text(review_text(query="其他问题"), encoding="utf-8")
            pending_path = root / "pending.jsonl"
            pending_path.write_text(
                RAGEvaluationCase(case_id="rag-v0-001", query="合作情况", expected_outcome="answerable").model_dump_json() + "\n",
                encoding="utf-8",
            )
            candidates_path = root / "candidates.jsonl"
            candidates_path.write_text(json.dumps({
                "case_id": "rag-v0-001", "query": "合作情况", "release_id": "release-1",
                "source_sha256": f"sha256:{'a' * 64}",
                "candidates": [{
                    "parent_id": PARENT.parent_id, "source_id": PARENT.source_id,
                    "suggested_supporting_span": "首句原文说明合作事实。",
                }],
            }) + "\n", encoding="utf-8")
            with patch(
                "app.rag.evaluation.gold_finalize.load_frozen_parents",
                return_value=("release-1", f"sha256:{'a' * 64}", [PARENT]),
            ), self.assertRaisesRegex(ValueError, "review query mismatch"):
                finalize_review(review_path, pending_path, candidates_path, root / "manifest.json")


if __name__ == "__main__":
    unittest.main()

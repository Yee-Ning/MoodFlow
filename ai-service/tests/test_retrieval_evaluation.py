import json
import math
import tempfile
import unittest
from pathlib import Path

from app.rag.retrieval import (
    RetrievalEvaluationCase,
    evaluate_candidate_pool,
    load_evaluation_cases,
)


class RetrievalEvaluationTests(unittest.TestCase):
    def test_candidate_pool_metrics_cover_recall_rank_and_ndcg(self) -> None:
        cases = [
            RetrievalEvaluationCase(
                case_id="fact-001",
                query="晴天的创作背景",
                relevant_parent_ids=["parent-a", "parent-c"],
            ),
            RetrievalEvaluationCase(
                case_id="fact-002",
                query="七里香发行时间",
                relevant_parent_ids=["parent-z"],
            ),
        ]

        report = evaluate_candidate_pool(
            cases,
            {
                "fact-001": ["parent-a", "noise", "parent-c", "parent-c"],
                "fact-002": ["noise", "parent-z"],
            },
        )

        self.assertEqual(report.case_count, 2)
        self.assertEqual(report.recall_at_20, 1)
        self.assertEqual(report.recall_at_40, 1)
        self.assertEqual(report.mrr_at_10, 0.75)
        expected_first_ndcg = (1 + 1 / math.log2(4)) / (
            1 + 1 / math.log2(3)
        )
        expected_second_ndcg = 1 / math.log2(3)
        self.assertAlmostEqual(
            report.ndcg_at_10,
            (expected_first_ndcg + expected_second_ndcg) / 2,
        )
        self.assertEqual(report.cases[0].retrieved_count, 3)

    def test_missing_ranking_scores_zero_without_dropping_case(self) -> None:
        case = RetrievalEvaluationCase(
            case_id="missing-001",
            query="缺失结果",
            relevant_parent_ids=["parent-a"],
        )

        report = evaluate_candidate_pool([case], {})

        self.assertEqual(report.recall_at_40, 0)
        self.assertEqual(report.mrr_at_10, 0)
        self.assertEqual(report.ndcg_at_10, 0)

    def test_jsonl_loader_is_strict_and_rejects_duplicate_case_ids(self) -> None:
        payload = {
            "case_id": "duplicate",
            "query": "晴天",
            "relevant_parent_ids": ["parent-a"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "retrieval.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(payload, ensure_ascii=False),
                        json.dumps(payload, ensure_ascii=False),
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "case_id_duplicate"):
                load_evaluation_cases(path)


if __name__ == "__main__":
    unittest.main()

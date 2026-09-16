import math
import unittest

from app.rag.reranking import evaluate_reranking
from app.rag.retrieval import RetrievalEvaluationCase


class RerankingEvaluationTests(unittest.TestCase):
    def test_report_compares_top_40_with_top_12_and_tracks_operations(self) -> None:
        cases = [
            RetrievalEvaluationCase(
                case_id="fact-001",
                query="晴天创作背景",
                relevant_parent_ids=["parent-a", "parent-c"],
            ),
            RetrievalEvaluationCase(
                case_id="fact-002",
                query="七里香发行时间",
                relevant_parent_ids=["parent-z"],
            ),
        ]

        report = evaluate_reranking(
            cases,
            {
                "fact-001": ["parent-a", "noise", "parent-c"],
                "fact-002": ["noise", "parent-z"],
            },
            {
                "fact-001": ["parent-c", "parent-a"],
                "fact-002": ["parent-z"],
            },
            fallback_by_case={"fact-002": True},
            latency_ms_by_case={"fact-001": 10, "fact-002": 30},
        )

        self.assertEqual(report.case_count, 2)
        self.assertEqual(report.candidate_recall_at_40, 1)
        self.assertEqual(report.rerank_recall_at_12, 1)
        self.assertEqual(report.candidate_mrr_at_10, 0.75)
        self.assertEqual(report.rerank_mrr_at_10, 1)
        self.assertEqual(report.mrr_at_10_delta, 0.25)
        self.assertGreater(report.ndcg_at_10_delta, 0)
        self.assertEqual(report.fallback_rate, 0.5)
        self.assertEqual(report.latency_p50_ms, 20)
        self.assertEqual(report.latency_p95_ms, 29)
        self.assertEqual(report.latency_p99_ms, 29.8)

    def test_missing_post_rerank_ranking_is_scored_as_zero(self) -> None:
        case = RetrievalEvaluationCase(
            case_id="missing-001",
            query="缺失结果",
            relevant_parent_ids=["parent-a"],
        )

        report = evaluate_reranking(
            [case],
            {"missing-001": ["parent-a"]},
            {},
        )

        self.assertEqual(report.candidate_recall_at_40, 1)
        self.assertEqual(report.rerank_recall_at_12, 0)
        self.assertEqual(report.mrr_at_10_delta, -1)
        self.assertEqual(report.ndcg_at_10_delta, -1)

    def test_invalid_latency_is_rejected(self) -> None:
        case = RetrievalEvaluationCase(
            case_id="latency-001",
            query="晴天",
            relevant_parent_ids=["parent-a"],
        )

        with self.assertRaisesRegex(ValueError, "latency invalid"):
            evaluate_reranking(
                [case],
                {},
                {},
                latency_ms_by_case={"latency-001": math.nan},
            )


if __name__ == "__main__":
    unittest.main()

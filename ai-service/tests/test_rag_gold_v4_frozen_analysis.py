"""Frozen 50-case Gold and read-only historical-trace analysis regressions."""

from __future__ import annotations

import json
import unittest
from collections import Counter

from app.rag.evaluation.io import load_evaluation_cases
from app.rag.evaluation.v4_answerability import supported_evidence_units
from run_rag_gold_v4_frozen_analysis import (
    ANALYSIS, BASELINE, DETAIL, GOLD, GOLD_SHA256, failure_stage, sha256,
)
from run_rag_gold_v4_expand import OUT as CANDIDATE


class GoldV4FrozenAnalysisTests(unittest.TestCase):
    def test_frozen_gold_matches_reviewed_candidate_exactly(self) -> None:
        self.assertEqual(sha256(GOLD), GOLD_SHA256)
        self.assertEqual(GOLD.read_bytes(), CANDIDATE.read_bytes())
        cases = load_evaluation_cases(GOLD)
        self.assertEqual(len(cases), 50)
        self.assertEqual(Counter(case.answerability_level or "needs_input" for case in cases), {
            "fully_answerable": 29, "partially_answerable": 10,
            "insufficient_evidence": 7, "needs_input": 4,
        })
        self.assertTrue(all(case.annotation_status.value == "approved" for case in cases))
        self.assertEqual(sum(len(supported_evidence_units(case)) for case in cases), 95)

    def test_failure_stage_is_exclusive_and_monotonic(self) -> None:
        self.assertEqual(failure_stage(False, False, False), "RETRIEVER_MISS")
        self.assertEqual(failure_stage(True, False, False), "RERANK_MISS")
        self.assertEqual(failure_stage(True, True, False), "CONTEXT_DROP")
        self.assertEqual(failure_stage(True, True, True), "SUCCESS")
        with self.assertRaisesRegex(ValueError, "non-monotonic"):
            failure_stage(False, True, False)
        with self.assertRaisesRegex(ValueError, "non-monotonic"):
            failure_stage(True, False, True)

    def test_trace_coverage_gap_is_not_scored_as_retrieval_failure(self) -> None:
        rows = [json.loads(line) for line in DETAIL.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 190)
        for label in ("fallback", "nav_context6"):
            selected = [row for row in rows if row["trace_label"] == label]
            self.assertEqual(len(selected), 95)
            uncovered = [row for row in selected if not row["trace_available"]]
            self.assertEqual(len(uncovered), 27)
            self.assertTrue(all(row["case_id"] >= "rag-v0-049" for row in uncovered))
            self.assertTrue(all(row["failure_stage"] == "NO_HISTORICAL_TRACE" for row in uncovered))
            self.assertTrue(all(row["top40_hit"] is None and row["failure_reason"] == "trace_not_collected"
                                for row in uncovered))
            traced = [row for row in selected if row["trace_available"]]
            self.assertEqual(len(traced), 68)
            self.assertEqual(sum(row["top40_hit"] for row in traced), 43)
            self.assertEqual(sum(row["top12_hit"] for row in traced), 27)
            self.assertTrue(all(row["reranker_score"] is None for row in traced))
            self.assertTrue(all(row["retriever_ranking_diagnosis"] ==
                                "insufficient_trace_for_ranking_diagnosis"
                                for row in traced if row["failure_stage"] == "RETRIEVER_MISS"))
        self.assertTrue(BASELINE.is_file())
        self.assertTrue(ANALYSIS.is_file())
        baseline = BASELINE.read_text(encoding="utf-8")
        self.assertIn("不能计算 50 条全量 baseline", baseline)
        self.assertIn("0.641383 | 0.442630 | 0.119048", baseline)
        self.assertIn("0.641383 | 0.442630 | 0.139683", baseline)


if __name__ == "__main__":
    unittest.main()

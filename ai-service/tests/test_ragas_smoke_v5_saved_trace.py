"""Revalidate already paid generation calls offline in the production runtime."""

import json
import unittest

from evals.ragas_smoke_v5 import CASE_IDS, OUTPUT, recover_saved_generation_failure
from evals.ragas_smoke_v5_report import load_calls


class SavedGenerationTests(unittest.TestCase):
    def test_failures_use_production_fallback_and_are_reproducible_without_more_calls(self):
        from app.agent.foundation.subgraphs.worldline.graph import NARRATIVE_UNAVAILABLE_MESSAGE
        rows = {r["case_id"]: r for r in json.loads((OUTPUT / "inputs.json").read_text(encoding="utf-8"))}
        calls = load_calls(OUTPUT / "generation_calls")
        count_before = len(calls)
        for case_id in ("rag-v0-036", "rag-v0-006", "rag-v0-065"):
            with self.subTest(case_id=case_id):
                selected = [c for c in calls if c["case_id"] == case_id]
                recovered = recover_saved_generation_failure(rows[case_id], selected)
                saved = json.loads((OUTPUT / "answers" / (case_id + ".json")).read_text(encoding="utf-8"))
                self.assertEqual(saved["answer"], NARRATIVE_UNAVAILABLE_MESSAGE)
                self.assertEqual(recovered["answer"], saved["answer"])
                self.assertEqual(recovered["generation_failure"]["validation_code"], saved["generation_failure"]["validation_code"])
                self.assertEqual(saved["generation_status"], "failed_production_fallback")
        self.assertEqual(len(load_calls(OUTPUT / "generation_calls")), count_before)

    def test_generation_request_scope_and_retry_count_not_weakened(self):
        from collections import Counter
        calls = load_calls(OUTPUT / "generation_calls")
        counts = Counter(c["case_id"] for c in calls)
        self.assertEqual(set(counts), set(CASE_IDS))
        self.assertEqual(len(calls), 12)
        self.assertLessEqual(max(counts.values()), 2)
        self.assertTrue(all(c["request"]["temperature"] == 0 for c in calls))
        self.assertTrue(all(c["request"]["model"] == "deepseek-flash" for c in calls))


if __name__ == "__main__":
    unittest.main()

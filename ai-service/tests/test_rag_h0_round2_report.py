"""State-stratified means preserve failures, abstention and undefined cells."""

import unittest

from evals import rag_h0_round2_v5_report as report


class StateSummaryTests(unittest.TestCase):
    def test_each_state_has_its_own_denominator_and_nan_is_not_imputed(self):
        rows = [{"answerability": "full", "delivery_state": state, report.KEYS[0]: faithful, report.KEYS[1]: factual}
                for state, faithful, factual in (("answered", .8, .5), ("abstained", None, 0.),
                    ("validation_failed", 0., 0.), ("provider_failed", 0., 0.))]
        summary = report.summarize(rows)
        states = summary["rerun_12"]["by_delivery_state"]
        self.assertEqual(states["answered"][report.KEYS[0]]["mean"], .8)
        self.assertEqual(states["abstained"][report.KEYS[0]]["not_scorable"], 1)
        self.assertIsNone(states["abstained"][report.KEYS[0]]["mean"])
        self.assertEqual(states["abstained"][report.KEYS[1]]["mean"], 0.)
        self.assertEqual(summary["rerun_12"]["all_including_failure_messages"][report.KEYS[0]]["finite"], 3)
        self.assertEqual(sum(r["cases"] for r in states.values()), 4)
        self.assertEqual(summary["by_answerability"]["partial"]["cases"], 0)

    def test_unknown_state_cannot_silently_disappear(self):
        with self.assertRaisesRegex(ValueError, "unknown_delivery_state"):
            report.summarize([{"delivery_state": "success"}])


if __name__ == "__main__":
    unittest.main()

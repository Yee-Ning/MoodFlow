"""H0 before/after rows retain failures and undefined metrics without editing."""

import unittest

from evals import rag_h0_v5_report as report


class H0ReportTests(unittest.TestCase):
    def test_comparison_keeps_null_scores_and_both_failure_reasons(self):
        old = {"case": {"generation_status": "failed_production_fallback",
            "generation_failure": {"validation_code": "paragraph_missing_citation"},
            report.KEYS[0]: 0., report.KEYS[1]: 0.}}
        row = {"case_id": "case", "answerability": "insufficient", "question": "question",
            "generation_status": "success", "generation_failure": None,
            "generation_attempts": 1, "actual_source_counts": [4], report.KEYS[0]: None, report.KEYS[1]: 0.}
        result = report.compare([row], old)[0]
        self.assertEqual(result["before_failure"]["validation_code"], "paragraph_missing_citation")
        self.assertIsNone(result["after_failure"])
        self.assertIsNone(result["after_scores"][report.KEYS[0]])
        self.assertEqual(result["after_scores"][report.KEYS[1]], 0.)

    def test_persistent_failure_is_not_labeled_recovered(self):
        old = {"case": {"generation_status": "failed_production_fallback", "generation_failure": {
            "validation_code": "extra_forbidden:narrative_note"}, report.KEYS[0]: 0., report.KEYS[1]: 0.}}
        row = {"case_id": "case", "answerability": "full", "question": "question",
            "generation_status": "failed_production_fallback", "generation_failure": {
                "validation_code": "paragraph_missing_citation"}, "generation_attempts": 2,
            "actual_source_counts": [12, 12], report.KEYS[0]: 0., report.KEYS[1]: 0.}
        result = report.compare([row], old)[0]
        self.assertEqual(result["after_status"], "failed_production_fallback")
        self.assertNotEqual(result["before_failure"], result["after_failure"])


if __name__ == "__main__":
    unittest.main()

"""H0 evaluator scope and immutable evidence boundaries, zero provider calls."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from evals import rag_h0_v5 as h0


class H0InputTests(unittest.TestCase):
    def test_exact_18_are_original_failures_and_contexts_remain_unchanged(self):
        rows = h0.inputs()
        baseline = {r["case_id"]: r for r in h0.read_json(h0.BASELINE / "inputs.json")}
        self.assertEqual(tuple(r["case_id"] for r in rows), h0.CASE_IDS)
        self.assertEqual(len(rows), 18)
        for row in rows:
            self.assertEqual(row, baseline[row["case_id"]])

    def test_only_three_approved_production_files_differ_from_baseline(self):
        self.assertEqual(len(h0.verify_baseline()), 50)
        with patch.object(h0, "APPROVED_PRODUCTION_FILES", {*h0.APPROVED_PRODUCTION_FILES, "app/agent/structured_output/retry.py"}):
            with self.assertRaisesRegex(ValueError, "unapproved_production"):
                h0.verify_baseline()

    def test_existing_success_32_were_only_replayed_offline_with_exact_scores(self):
        result = h0.read_json(h0.OUTPUT / "offline_success32.json")
        self.assertEqual(result["old_successful_responses_pass_current_production"], 32)
        self.assertEqual(result["saved_scores_recomputed_exact"], 64)
        self.assertEqual(result["real_api_calls"], 0)
        self.assertEqual(len(result["metric_replay"]), 32)
        self.assertFalse(set(result["case_ids"]) & set(h0.CASE_IDS))

    def test_input_tampering_prevents_live_work(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            for name in ("preparation.json", "inputs.json"):
                (output / name).write_bytes((h0.OUTPUT / name).read_bytes())
            rows = h0.read_json(output / "inputs.json")
            rows[0]["contexts"][0] += " forbidden addition"
            (output / "inputs.json").write_text(json.dumps(rows), encoding="utf-8")
            with patch.object(h0, "OUTPUT", output), self.assertRaisesRegex(ValueError, "fixed_18_inputs"):
                h0.inputs()

    def test_saved_template_observations_only_describe_successful_deliveries(self):
        observations = h0.template_delivery_observations()
        self.assertEqual(len(observations), 6)
        self.assertEqual({key for key, value in observations.items() if value["template_paragraphs"]},
                         {"rag-v0-039", "rag-v0-046", "rag-v0-062", "rag-v0-066"})
        self.assertFalse(any(value["pure_template_delivery"] for value in observations.values()))


class H0RequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_32_successful_cases_are_outside_the_live_scope(self):
        rows = h0.inputs()
        with tempfile.TemporaryDirectory() as directory, h0.full.smoke_scope(rows):
            recorder = h0.full.FullRecorder(Path(directory))
            token = h0.full.smoke.SCOPE.set({"stage": "judge", "case_id": "rag-v0-049", "metric": "faithfulness"})
            try:
                with self.assertRaisesRegex(ValueError, "unapproved_provider"):
                    await recorder.request(httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions",
                        json={"model": "deepseek-chat", "temperature": 0}))
            finally:
                h0.full.smoke.SCOPE.reset(token)
            self.assertEqual(recorder.entries, [])


if __name__ == "__main__":
    unittest.main()

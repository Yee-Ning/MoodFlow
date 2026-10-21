"""Fixed round-2 populations, output states and offline regression evidence."""

import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path

import httpx

from evals import rag_h0_round2_v5 as run
from tests.test_worldline_h0_abstention import LIMIT, FACT
from tests.test_worldline_h0_round2 import SENT, G1


class Round2Tests(unittest.TestCase):
    def test_fixed_twelve_inputs_replay_original_raw_contexts(self):
        rows = run.inputs()
        originals = {r["case_id"]: r for r in run.read_json(run.STEP4 / "inputs.json")}
        self.assertEqual(tuple(r["case_id"] for r in rows), run.CASE_IDS)
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(r == originals[r["case_id"]] for r in rows))

    def test_only_approved_files_differ(self):
        self.assertEqual(len(run.verify_priors()), 50)
        with patch.object(run, "CHANGED", run.CHANGED | {"app/agent/structured_output/retry.py"}):
            with self.assertRaisesRegex(ValueError, "unapproved_production"):
                run.verify_priors()

    def test_output_states_do_not_use_gold_labels(self):
        def success(text):
            return {"generation_status": "success", "generation_failure": None, "answer": text, "answerability": "full"}
        self.assertEqual(run.delivery_state(success(LIMIT), SENT), "abstained")
        self.assertEqual(run.delivery_state(success(G1 + "\n" + LIMIT), SENT), "abstained")
        self.assertEqual(run.delivery_state(success(FACT + "\n" + LIMIT), SENT), "answered")
        self.assertEqual(run.delivery_state(success(G1 + "\n事实。"), SENT), "answered")
        for failure, expected in (("schema_validation_error", "validation_failed"),
                                  ("semantic_validation_error", "validation_failed"),
                                  ("parse_error", "validation_failed"), ("model_error", "provider_failed"),
                                  ("truncation", "provider_failed")):
            self.assertEqual(run.delivery_state({"generation_status": "failed_production_fallback",
                "generation_failure": {"failure_type": failure}}), expected)
        self.assertEqual(run.delivery_state({"generation_status": "failed_production_fallback",
            "generation_failure": {"failure_type": "model_error", "validation_code": "normalization_failed"}}), "validation_failed")

    def test_offline_success38_preserves_both_sources_and_76_scores(self):
        record = run.read_json(run.OUTPUT / "offline_success38.json")
        self.assertEqual(record["old_answers_and_citations_exact"], 38)
        self.assertEqual(record["metric_cells_exact"], 76)
        self.assertEqual(record["step4_successes"], 32)
        self.assertEqual(record["round1_successes"], 6)
        self.assertEqual(record["real_api_calls"], 0)
        self.assertEqual(len(record["metric_replay"]), 38)
        self.assertFalse(set(record["case_ids"]) & set(run.CASE_IDS))


class RequestScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_successes_are_blocked_before_live_http(self):
        rows = run.inputs()
        with tempfile.TemporaryDirectory() as directory, run.full.smoke_scope(rows):
            recorder = run.full.FullRecorder(Path(directory))
            token = run.full.smoke.SCOPE.set({"stage": "judge", "case_id": "rag-v0-006", "metric": "faithfulness"})
            try:
                with self.assertRaisesRegex(ValueError, "unapproved_provider"):
                    await recorder.request(httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions",
                        json={"model": "deepseek-chat", "temperature": 0}))
            finally:
                run.full.smoke.SCOPE.reset(token)
            self.assertEqual(recorder.entries, [])


if __name__ == "__main__":
    unittest.main()

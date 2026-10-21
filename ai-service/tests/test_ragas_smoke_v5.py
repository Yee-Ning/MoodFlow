"""Eight-case scope, request temperature, evidence and secret boundary checks."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from evals.ragas_smoke_v5 import CASE_IDS, LABELS, OUTPUT, Recorder, SCOPE, secret_guard
from evals.ragas_smoke_v5_report import load_calls, replay_metric_scores, summarize_calls


class SmokeRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.recorder = Recorder(Path(self.directory.name))

    async def test_judge_temperature_zero_and_no_headers_exported(self):
        token = SCOPE.set({"stage": "judge", "case_id": CASE_IDS[0], "metric": "faithfulness"})
        self.addCleanup(SCOPE.reset, token)
        request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": "not-exported-test-marker"},
            json={"model": "deepseek-chat", "temperature": 0, "messages": []})
        await self.recorder.request(request)
        saved = (Path(self.directory.name) / "001.json").read_text(encoding="utf-8")
        self.assertNotIn("not-exported-test-marker", saved)
        self.assertEqual(json.loads(saved)["request"]["temperature"], 0)

    async def test_wrong_temperature_model_endpoint_or_scope_is_rejected(self):
        token = SCOPE.set({"stage": "judge", "case_id": CASE_IDS[0], "metric": "faithfulness"})
        self.addCleanup(SCOPE.reset, token)
        for url, body in [
            ("https://api.deepseek.com/v1/chat/completions", {"model": "deepseek-chat", "temperature": 0.01}),
            ("https://api.deepseek.com/v1/chat/completions", {"model": "other", "temperature": 0}),
            ("https://example.org/v1/chat/completions", {"model": "deepseek-chat", "temperature": 0}),
        ]:
            with self.subTest(url=url, body=body), self.assertRaises(ValueError):
                await self.recorder.request(httpx.Request("POST", url, json=body))
        self.assertEqual(self.recorder.entries, [])

    async def test_production_exact_text_dedup_is_allowed_but_unique_loss_is_not(self):
        row = {"question": "q", "contexts": ["first", "first", "second"]}
        token = SCOPE.set({"stage": "generation", "case_id": CASE_IDS[0], "row": row, "system_prompt": "real prompt"})
        self.addCleanup(SCOPE.reset, token)
        body = {"model": "model", "temperature": 0, "instructions": "real prompt",
                "input": [{"content": json.dumps({"user_query": "q", "sources": [{"excerpt": "first"}, {"excerpt": "second"}]})}]}
        await self.recorder.request(httpx.Request("POST", "https://api.deepseek.com/responses", json=body))
        body["input"][0]["content"] = json.dumps({"user_query": "q", "sources": [{"excerpt": "first"}]})
        with self.assertRaisesRegex(ValueError, "unique_contexts"):
            await self.recorder.request(httpx.Request("POST", "https://api.deepseek.com/responses", json=body))

    async def test_errors_do_not_export_provider_error_body(self):
        token = SCOPE.set({"stage": "judge", "case_id": CASE_IDS[0]})
        self.addCleanup(SCOPE.reset, token)
        request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions",
                               json={"model": "deepseek-chat", "temperature": 0})
        await self.recorder.request(request)
        await self.recorder.response(httpx.Response(401, request=request, json={"error": "private-error-test-marker"}))
        self.assertNotIn("private-error-test-marker", (Path(self.directory.name) / "001.json").read_text())


class SmokeScopeTests(unittest.TestCase):
    def test_fixed_eight_cases_and_ninety_six_child_texts(self):
        from collections import Counter
        rows = json.loads((OUTPUT / "inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(r["case_id"] for r in rows), CASE_IDS)
        self.assertEqual(Counter(r["answerability"] for r in rows), LABELS)
        self.assertTrue(all(len(r["contexts"]) == 12 for r in rows))
        self.assertTrue(all(r["answer"] is None for r in rows))

    def test_unapproved_secret_path_is_blocked(self):
        for path in ["secret/key", ".env", "other/.env", ".env.production"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                secret_guard("open", (path,))

    def test_provider_usage_aggregation_preserves_cached_subset(self):
        calls = [{"status_code": 200, "usage": {"input_tokens": 10, "output_tokens": 3,
                  "total_tokens": 13, "input_tokens_details": {"cached_tokens": 4}}},
                 {"status_code": 200, "usage": {"prompt_tokens": 20, "completion_tokens": 5,
                  "total_tokens": 25, "prompt_cache_hit_tokens": 7}}, {"status_code": 429}]
        summary = summarize_calls(calls)
        self.assertEqual(summary["http_attempts"], 3)
        self.assertEqual(summary["successful_responses"], 2)
        self.assertEqual(summary["failed_or_pending"], 1)
        self.assertEqual(summary["input_tokens"], 30)
        self.assertEqual(summary["output_tokens"], 8)
        self.assertEqual(summary["total_tokens"], 38)
        self.assertEqual(summary["cached_input_tokens"], 11)

    def test_resume_recorder_keeps_previous_attempts_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "001.json").write_text(json.dumps({"call_number": 1, "stage": "generation"}))
            recorder = Recorder(path, resume=True)
            self.assertEqual(len(recorder.entries), 1)
            self.assertEqual(recorder.entries[0]["call_number"], 1)

    def test_actual_saved_judge_scores_replay_without_more_requests(self):
        rows = [json.loads(line) for line in (OUTPUT / "eight_cases.jsonl").read_text(encoding="utf-8").splitlines()]
        calls = load_calls(OUTPUT / "judge_calls")
        replay = replay_metric_scores(rows, calls)
        self.assertEqual(len(replay), 8)
        self.assertEqual(len(calls), 48)
        rows[0]["faithfulness"] = 0.0
        with self.assertRaisesRegex(ValueError, "score_mismatch"):
            replay_metric_scores(rows, calls)

    def test_raw_score_of_insufficient_failure_is_kept_but_not_mislabeled_hallucination(self):
        rows = json.loads((OUTPUT / "insufficient_review.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["delivered_answer_kind"] == "service_failure" for r in rows))
        self.assertTrue(all(r["raw_faithfulness"] == 0.0 for r in rows))


if __name__ == "__main__":
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    unittest.main()

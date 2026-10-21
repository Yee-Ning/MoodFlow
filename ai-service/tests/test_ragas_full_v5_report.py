"""Strata, failures, null handling and exact raw-verdict replay."""

import copy
import json
import unittest

from evals import ragas_full_v5_report as report
from evals.ragas_gate0 import read_jsonl


class FullReportTests(unittest.TestCase):
    def test_saved_retrieval_loss_counts_match_step2_without_confusing_40_and_80(self):
        cases = read_jsonl(report.run.gate0.STEP2 / "cases.jsonl")
        losses = [report.retrieval_diagnosis(r) for r in cases if r["answerability"] in {"full", "partial"}]
        counts = {key: sum(len(r[key]) for r in losses) for key in losses[0]}
        self.assertEqual(counts, {"rrf40_missing": 18, "rrf80_missing": 13,
                                  "selection_dropped": 1, "rerank_dropped": 5, "top12_missing": 19})

    def test_failure_scores_are_excluded_only_from_normal_answer_means(self):
        rows = [{"answerability": "full", "generation_status": "success", report.KEYS[0]: .8, report.KEYS[1]: .4},
                {"answerability": "full", "generation_status": "failed_production_fallback", report.KEYS[0]: 0., report.KEYS[1]: 0.},
                {"answerability": "needs_input", "generation_status": "success", report.KEYS[0]: None, report.KEYS[1]: None}]
        summary = report.summarize(rows)
        full = summary["by_answerability"]["full"]
        self.assertEqual(full["normal_answers"][report.KEYS[0]]["mean"], .8)
        self.assertEqual(full["all_including_failure_messages"][report.KEYS[0]]["mean"], .4)
        self.assertEqual(full["failure_messages"]["cases"], 1)
        needs = summary["by_answerability"]["needs_input"]["normal_answers"]
        self.assertIsNone(needs[report.KEYS[0]]["mean"])
        self.assertEqual(needs[report.KEYS[0]]["not_scorable"], 1)

    def test_all_sixteen_saved_smoke_scores_recompute_with_new_full_replayer(self):
        smoke_dir = report.run.smoke.OUTPUT
        rows = read_jsonl(smoke_dir / "eight_cases.jsonl")
        calls = report.smoke_report.load_calls(smoke_dir / "judge_calls")
        outcomes = {(r["case_id"], name): {"value": r[key], "status": "finite"}
                    for r in rows for key, name in zip(report.KEYS, ("faithfulness", "factual_correctness"), strict=True)}
        replay = report.replay_scores(rows, calls, outcomes)
        self.assertEqual(len(replay), 8)
        changed = copy.deepcopy(rows)
        changed[0][report.KEYS[0]] = .1
        outcomes[(rows[0]["case_id"], "faithfulness")]["value"] = .1
        with self.assertRaisesRegex(ValueError, "score_mismatch"):
            report.replay_scores(changed, calls, outcomes)

    def test_no_claim_nan_keeps_null_without_imputation(self):
        row = {"case_id": "empty", "answer": "请补充信息", "ground_truth": "参考", "contexts": ["证据"],
               report.KEYS[0]: None, report.KEYS[1]: None}
        call = {"case_id": "empty", "metric": "faithfulness", "request": {"messages": [{"content": 'input: {"answer":"请补充信息"}'}]},
                "response": {"choices": [{"message": {"content": json.dumps({"statements": []})}}]}}
        outcomes = {("empty", "faithfulness"): {"value": None, "status": "not_scorable_nan"},
                    ("empty", "factual_correctness"): {"value": None, "status": "metric_exception"}}
        cells = report.replay_scores([row], [call], outcomes)["empty"]
        self.assertIsNone(cells[report.KEYS[0]]["value"])
        self.assertEqual(cells[report.KEYS[0]]["undefined_reason"], "no_extracted_claims")

    def test_numeric_string_verdicts_follow_installed_schema_integer_coercion(self):
        smoke_dir = report.run.smoke.OUTPUT
        rows = read_jsonl(smoke_dir / "eight_cases.jsonl")
        calls = report.smoke_report.load_calls(smoke_dir / "judge_calls")
        for call in calls:
            value = report.decoded_reply(call)
            if isinstance(value, dict) and isinstance(value.get("statements"), list):
                for statement in value["statements"]:
                    if isinstance(statement, dict) and "verdict" in statement:
                        statement["verdict"] = str(statement["verdict"])
                call["response"]["choices"][0]["message"]["content"] = json.dumps(value)
        outcomes = {(r["case_id"], name): {"value": r[key], "status": "finite"}
                    for r in rows for key, name in zip(report.KEYS, ("faithfulness", "factual_correctness"), strict=True)}
        self.assertEqual(len(report.replay_scores(rows, calls, outcomes)), 8)

    def test_zero_factual_score_with_empty_verdicts_is_not_rewritten_to_nan(self):
        row = {"case_id": "empty", "answer": "answer", "ground_truth": "reference", "contexts": ["evidence"],
               report.KEYS[0]: None, report.KEYS[1]: 0.}
        calls = [{"case_id": "empty", "metric": "factual_correctness", "request": {"messages": [{"content": 'input: ' + json.dumps({"context": text})}]},
                  "response": {"choices": [{"message": {"content": '{"statements":[]}'}}]}} for text in ("answer", "reference")]
        outcomes = {("empty", "faithfulness"): {"value": None, "status": "metric_exception"},
                    ("empty", "factual_correctness"): {"value": 0., "status": "finite"}}
        self.assertEqual(report.replay_scores([row], calls, outcomes)["empty"][report.KEYS[1]]["value"], 0.)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from evals.budget import (
    cited_retrieval_context,
    load_budget_config,
    metric_cache_key,
    select_budget_payloads,
)
from evals.run_deepeval import main as deepeval_main


def judge_config() -> dict:
    return {
        "schema_version": "v0",
        "judge": {
            "provider": "deepseek",
            "model": "judge-model",
            "base_url": "https://judge.example",
            "api_key_env": "JUDGE_KEY",
            "api_key_env_file": None,
            "temperature": 0.0,
            "timeout_seconds": 30,
        },
        "threshold": 0.5,
        "max_concurrent": 2,
    }


def budget_config() -> dict:
    return {
        "schema_version": "v0",
        "mode": "smoke",
        "max_cases": 1,
        "case_ids": ["rag-v0-001"],
        "semantic_metrics": ["Faithfulness", "Answer Relevancy"],
        "faithfulness_context": "cited_only",
        "include_reason": False,
        "cache_enabled": True,
        "retry_errors_only": True,
        "max_judge_metric_runs": 2,
        "max_estimated_source_tokens": 1000,
        "deepeval_task_timeout_seconds": 300,
    }


def evaluation_case() -> dict:
    return {
        "schema_version": "v0",
        "case_id": "rag-v0-001",
        "input": "question",
        "actual_output": "answer [parent-2]",
        "expected_output": "reference answer",
        "retrieval_context": [
            "[parent-1] unused evidence",
            "[parent-2] cited evidence",
        ],
        "reference_context": ["[parent-2] reviewed evidence"],
        "tags": ["smoke"],
        "metadata": {},
    }


def cached_report() -> dict:
    metrics = [
        {
            "name": name,
            "score": 1.0,
            "threshold": 0.5,
            "success": True,
            "reason": "legacy reason",
            "error": None,
        }
        for name in ("Faithfulness", "Answer Relevancy")
    ]
    return {
        "status": "success",
        "case_count": 1,
        "passed_case_count": 1,
        "cases": [
            {"case_id": "rag-v0-001", "success": True, "metrics": metrics}
        ],
        "judge": {
            "provider": "deepseek",
            "model": "judge-model",
            "base_url": "https://judge.example",
            "temperature": 0.0,
            "threshold": 0.5,
        },
    }


class RAGEvalBudgetTests(unittest.TestCase):
    def test_budget_selects_fixed_cases_and_faithfulness_uses_citations_only(self) -> None:
        payload = evaluation_case()
        self.assertEqual(
            cited_retrieval_context(payload),
            ["[parent-2] cited evidence"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "budget.json"
            path.write_text(json.dumps(budget_config()), encoding="utf-8")
            config = load_budget_config(path)

        self.assertEqual(select_budget_payloads([payload], config), [payload])

    def test_cache_key_changes_when_semantic_input_changes(self) -> None:
        payload = evaluation_case()
        judge = judge_config()["judge"]
        first = metric_cache_key(
            payload,
            "Answer Relevancy",
            judge=judge,
            threshold=0.5,
            include_reason=False,
            faithfulness_context="cited_only",
        )
        changed = dict(payload, actual_output="different [parent-2]")
        second = metric_cache_key(
            changed,
            "Answer Relevancy",
            judge=judge,
            threshold=0.5,
            include_reason=False,
            faithfulness_context="cited_only",
        )
        self.assertNotEqual(first, second)

    def test_default_is_plan_only_and_never_resolves_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_fixtures(root)
            output = StringIO()
            with (
                patch(
                    "evals.run_deepeval.resolve_api_key",
                    side_effect=AssertionError("must not resolve a key during planning"),
                ),
                patch(
                    "evals.run_deepeval.run_judge",
                    side_effect=AssertionError("must not call Judge during planning"),
                ),
                redirect_stdout(output),
            ):
                status = deepeval_main(
                    [
                        "--config",
                        str(paths["judge"]),
                        "--budget-config",
                        str(paths["budget"]),
                        "--dataset",
                        str(paths["dataset"]),
                    ]
                )
        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["status"], "planned")
        self.assertEqual(payload["plan"]["planned_judge_metric_runs"], 2)

    def test_execute_reuses_successes_without_paid_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._write_fixtures(root)
            resume = root / "resume.json"
            report = root / "report.json"
            resume.write_text(json.dumps(cached_report()), encoding="utf-8")
            output = StringIO()
            with (
                patch(
                    "evals.run_deepeval.resolve_api_key",
                    side_effect=AssertionError("cached execution must not resolve a key"),
                ),
                patch(
                    "evals.run_deepeval.run_judge",
                    side_effect=AssertionError("cached execution must not call Judge"),
                ),
                redirect_stdout(output),
            ):
                status = deepeval_main(
                    [
                        "--config",
                        str(paths["judge"]),
                        "--budget-config",
                        str(paths["budget"]),
                        "--dataset",
                        str(paths["dataset"]),
                        "--resume-from",
                        str(resume),
                        "--output",
                        str(report),
                        "--execute",
                    ]
                )
            written = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        self.assertEqual(written["budget"]["planned_judge_metric_runs"], 0)
        self.assertEqual(written["budget"]["executed_judge_metric_runs"], 0)
        self.assertTrue(written["cases"][0]["metrics"][0]["cached"])

    @staticmethod
    def _write_fixtures(root: Path) -> dict[str, Path]:
        judge = root / "judge.json"
        budget = root / "budget.json"
        dataset = root / "dataset.jsonl"
        judge.write_text(json.dumps(judge_config()), encoding="utf-8")
        budget.write_text(json.dumps(budget_config()), encoding="utf-8")
        dataset.write_text(json.dumps(evaluation_case()) + "\n", encoding="utf-8")
        return {"judge": judge, "budget": budget, "dataset": dataset}


if __name__ == "__main__":
    unittest.main()

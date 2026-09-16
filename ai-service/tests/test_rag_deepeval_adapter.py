import json
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_rag_eval
from app.rag.evaluation import (
    AnnotationStatus,
    EvaluationContext,
    EvaluationVersions,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    SupportingSpanAnnotation,
    TraceStageStatuses,
    build_deepeval_payloads,
    write_jsonl,
)
from evals.run_deepeval import (
    load_judge_config,
    load_payloads,
    resolve_api_key,
    resolve_judge_config,
    run_judge,
    serialize_result,
)


def judge_config_payload() -> dict:
    return {
        "schema_version": "v0",
        "judge": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "LLM_API_KEY",
            "api_key_env_file": ".env",
            "temperature": 0.0,
            "timeout_seconds": 60,
        },
        "threshold": 0.5,
        "max_concurrent": 4,
    }


def approved_case() -> RAGEvaluationCase:
    return RAGEvaluationCase(
        case_id="rag-v0-001",
        query="晴天的创作背景",
        expected_outcome=ExpectedOutcome.ANSWERABLE,
        annotation_status=AnnotationStatus.APPROVED,
        relevant_parent_ids=["parent-1"],
        supporting_spans=[
            SupportingSpanAnnotation(
                parent_id="parent-1",
                exact_text="这是一段经过人工核对的证据。",
            )
        ],
        required_facts=["创作背景"],
        reference_answer="这是一条经过人工核对的参考答案。",
        tags=["fact"],
    )


def generated_trace() -> RAGEvaluationTrace:
    context = EvaluationContext(
        parent_id="parent-1",
        rank=1,
        title="晴天资料",
        content="用于生成回答的完整上下文。",
        source_id="source-1",
        source_name="测试知识库",
        token_count=20,
    )
    return RAGEvaluationTrace(
        run_id="run-001",
        case_id="rag-v0-001",
        query="晴天的创作背景",
        statuses=TraceStageStatuses(
            query_understanding="ready",
            retrieval="success",
            rerank="success",
            context="success",
            generation="success",
        ),
        candidate_pool=[context],
        reranked_context=[context],
        final_context=[context],
        actual_answer="这是基于证据生成的回答。[parent-1]",
        citation_parent_ids=["parent-1"],
        versions=EvaluationVersions(
            index_release="knowledge-v2",
            retrieval="v1",
            rerank="v1",
            context="v0",
            generation_model="fixture-model",
        ),
    )


class RAGDeepEvalAdapterTests(unittest.TestCase):
    def test_direct_script_execution_adds_project_root_to_import_path(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        script_path = project_root / "evals" / "run_deepeval.py"
        original_path = sys.path[:]
        try:
            sys.path[:] = [str(script_path.parent), *sys.path[1:]]
            runpy.run_path(str(script_path), run_name="deepeval_direct_script_test")
            self.assertIn(str(project_root), sys.path)
        finally:
            sys.path[:] = original_path

    def test_export_maps_reviewed_evidence_and_final_context(self) -> None:
        payloads = build_deepeval_payloads([approved_case()], [generated_trace()])

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0].expected_output, approved_case().reference_answer)
        self.assertEqual(
            payloads[0].reference_context,
            ["[parent-1] 这是一段经过人工核对的证据。"],
        )
        self.assertIn("[parent-1] 晴天资料", payloads[0].retrieval_context[0])
        self.assertEqual(payloads[0].metadata["run_id"], "run-001")

    def test_export_refuses_pending_cases_and_missing_generated_output(self) -> None:
        pending = approved_case().model_copy(
            update={"annotation_status": AnnotationStatus.PENDING}
        )
        with self.assertRaisesRegex(ValueError, "approved answerable"):
            build_deepeval_payloads([pending], [generated_trace()])

        trace = generated_trace().model_copy(update={"actual_answer": None})
        with self.assertRaisesRegex(ValueError, "answer missing"):
            build_deepeval_payloads([approved_case()], [trace])

    def test_isolated_loader_validates_interchange_without_project_imports(self) -> None:
        payload = build_deepeval_payloads([approved_case()], [generated_trace()])[0]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deepeval.jsonl"
            path.write_text(
                json.dumps(payload.model_dump(mode="json"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            loaded = load_payloads(path)

        self.assertEqual(loaded[0]["case_id"], "rag-v0-001")

    def test_isolated_loader_rejects_empty_retrieval_context(self) -> None:
        payload = build_deepeval_payloads([approved_case()], [generated_trace()])[0]
        invalid = payload.model_copy(update={"retrieval_context": []})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deepeval.jsonl"
            path.write_text(
                json.dumps(invalid.model_dump(mode="json"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "retrieval_context_invalid"):
                load_payloads(path)

    def test_cli_exports_only_judge_ready_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset.jsonl"
            traces = root / "traces.jsonl"
            exported = root / "deepeval.jsonl"
            write_jsonl(dataset, [approved_case()])
            write_jsonl(traces, [generated_trace()])
            output = StringIO()
            with redirect_stdout(output):
                status = run_rag_eval.main(
                    [
                        "export-deepeval",
                        "--dataset",
                        str(dataset),
                        "--traces",
                        str(traces),
                        "--output",
                        str(exported),
                    ],
                    project_root=root,
                )
            result = json.loads(output.getvalue())
            loaded = load_payloads(exported)

        self.assertEqual(status, 0)
        self.assertEqual(result["case_count"], 1)
        self.assertEqual(loaded[0]["case_id"], "rag-v0-001")

    def test_deterministic_evaluation_never_invokes_judge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset.jsonl"
            traces = root / "traces.jsonl"
            report = root / "report.json"
            write_jsonl(dataset, [approved_case()])
            write_jsonl(traces, [generated_trace()])
            output = StringIO()
            with (
                patch(
                    "evals.run_deepeval.run_judge",
                    side_effect=AssertionError("Judge must not run"),
                ) as judge,
                redirect_stdout(output),
            ):
                status = run_rag_eval.main(
                    [
                        "evaluate",
                        "--dataset",
                        str(dataset),
                        "--traces",
                        str(traces),
                        "--output",
                        str(report),
                    ],
                    project_root=root,
                )

            payload = json.loads(output.getvalue())

        judge.assert_not_called()
        self.assertEqual(status, 0)
        self.assertEqual(payload["report"]["citation_id_validity"], 1)

    def test_runner_validates_judge_configuration_before_loading_dependency(self) -> None:
        with self.assertRaisesRegex(ValueError, "threshold"):
            run_judge(
                [],
                judge_config=judge_config_payload()["judge"],
                api_key="fixture-key",
                threshold=2,
                max_concurrent=4,
            )

    def test_runner_loads_versioned_non_secret_judge_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "judge.json"
            path.write_text(
                json.dumps(judge_config_payload()),
                encoding="utf-8",
            )

            config = load_judge_config(path)

        self.assertEqual(config["judge"]["provider"], "deepseek")
        self.assertEqual(config["judge"]["model"], "deepseek-v4-flash")
        self.assertEqual(config["judge"]["api_key_env"], "LLM_API_KEY")
        self.assertEqual(config["judge"]["temperature"], 0)

    def test_runner_resolves_openai_compatible_provider_overrides(self) -> None:
        config = judge_config_payload()
        with patch.dict(
            "os.environ",
            {
                "DEEPEVAL_JUDGE_PROVIDER": "openai_compatible",
                "DEEPEVAL_JUDGE_MODEL": "future-judge",
                "DEEPEVAL_JUDGE_BASE_URL": "https://judge.example/v1",
                "DEEPEVAL_JUDGE_API_KEY_ENV": "FUTURE_JUDGE_KEY",
                "DEEPEVAL_JUDGE_TEMPERATURE": "0.2",
            },
            clear=False,
        ):
            resolved = resolve_judge_config(config)

        self.assertEqual(resolved["provider"], "openai_compatible")
        self.assertEqual(resolved["model"], "future-judge")
        self.assertEqual(resolved["base_url"], "https://judge.example/v1")
        self.assertEqual(resolved["api_key_env"], "FUTURE_JUDGE_KEY")
        self.assertEqual(resolved["temperature"], 0.2)

    def test_api_key_can_come_from_configured_env_file_without_leaking(self) -> None:
        config = judge_config_payload()["judge"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text(
                "LLM_API_KEY=fixture-secret\nUNRELATED_SECRET=do-not-load\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {}, clear=True):
                api_key = resolve_api_key(config, root)

                self.assertNotIn("UNRELATED_SECRET", os.environ)

        self.assertEqual(api_key, "fixture-secret")

    def test_runner_rejects_secret_values_in_judge_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "judge.json"
            payload = judge_config_payload()
            payload["judge"]["api_key"] = "must-not-be-stored-here"
            path.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "deepeval_config_invalid"):
                load_judge_config(path)

    def test_result_serializer_keeps_case_and_metric_diagnostics(self) -> None:
        metric = SimpleNamespace(
            name="Faithfulness",
            score=0.8,
            threshold=0.5,
            success=True,
            reason="grounded",
            error=None,
        )
        result = SimpleNamespace(
            test_results=[
                SimpleNamespace(
                    name="rag-v0-001",
                    success=True,
                    metrics_data=[metric],
                )
            ],
            confident_link=None,
        )

        payload = serialize_result(result)

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["passed_case_count"], 1)
        self.assertEqual(payload["cases"][0]["metrics"][0]["score"], 0.8)


if __name__ == "__main__":
    unittest.main()

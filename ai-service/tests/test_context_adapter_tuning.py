import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_context_adapter_tuning
from app.rag.context_packing import load_context_packing_config
from app.rag.evaluation import (
    AnnotationStatus,
    EvaluationContext,
    ExpectedOutcome,
    RAGEvaluationCase,
    SupportingSpanAnnotation,
    load_evaluation_traces,
    write_jsonl,
)
from app.rag.evaluation.context_tuning import (
    ContextTuningInput,
    run_context_experiments,
)
from app.rag.reranking import (
    RerankedCandidate,
    RerankResult,
    RerankStatus,
)
from app.rag.retrieval import RetrievalResult, RetrievalStatus
from tests.reranking_helpers import candidate, plan, profile


def _approved_case() -> RAGEvaluationCase:
    return RAGEvaluationCase(
        case_id="rag-v0-001",
        query="晴天的创作背景",
        expected_outcome=ExpectedOutcome.ANSWERABLE,
        annotation_status=AnnotationStatus.APPROVED,
        relevant_parent_ids=["parent-1"],
        supporting_spans=[
            SupportingSpanAnnotation(
                parent_id="parent-1",
                exact_text="第 1 条 Child 命中证据",
            )
        ],
        required_facts=["包含第一条证据"],
        reference_answer="第一条证据是相关内容。",
    )


def _rerank_result(query_plan=None, count: int = 8) -> RerankResult:
    query_plan = query_plan or plan()
    return RerankResult(
        status=RerankStatus.SUCCESS,
        plan_id=query_plan.plan_id,
        index_profile=profile(),
        model_name="Qwen/Qwen3-Reranker-0.6B",
        model_revision="a" * 40,
        rerank_version="v1",
        prompt_version="v1",
        candidate_pool_status=RetrievalStatus.SUCCESS,
        input_count=count,
        output_top_k=12,
        candidates=[
            RerankedCandidate(
                candidate=candidate(index),
                retrieval_rank=index,
                rerank_rank=index,
                rerank_score=1 - index / 100,
            )
            for index in range(1, count + 1)
        ],
    )


class _Retriever:
    async def retrieve(self, query_plan):
        return RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=query_plan.plan_id,
            index_profile=profile(),
            candidates=[candidate(1), candidate(2)],
        )


class _Reranker:
    async def rerank(self, query_plan, retrieval):
        return _rerank_result(query_plan, count=len(retrieval.candidates))


class ContextAdapterTuningTests(unittest.TestCase):
    def test_replay_restores_full_parent_context_from_index_projection(self) -> None:
        context = EvaluationContext(
            parent_id="parent-1",
            rank=1,
            title="资料 1",
            content="第 1 条 Child 命中证据",
            source_id="source-1",
            source_name="测试知识库",
            matched_child_id="child-1",
            token_count=100,
        )
        rebuilt = run_context_adapter_tuning._rebuild_candidate(
            context,
            plan=plan(),
            projection={
                "point_id": "point-1",
                "payload": {
                    "chunk_id": "child-1",
                    "doc_id": "doc-1",
                    "breadcrumb": "Child breadcrumb",
                    "parent_context": {
                        "content": "完整 Parent 正文",
                        "token_count": 500,
                        "breadcrumb": "Parent breadcrumb",
                    },
                    "entity_ids": ["artist:jay_chou"],
                    "supported_claims": [],
                },
            },
            retrieval_rank=1,
            inferred_subqueries={},
        )

        self.assertEqual(rebuilt.parent_context.content, "完整 Parent 正文")
        self.assertEqual(rebuilt.channel_evidence[0].subquery_id, "q0:original")
        self.assertEqual(rebuilt.entity_ids, ["artist:jay_chou"])

    def test_parameter_plan_scans_every_requested_value_with_fixed_limits(self) -> None:
        baseline = load_context_packing_config()
        report = run_context_experiments(
            [
                ContextTuningInput(
                    case=_approved_case(),
                    plan=plan(),
                    rerank=_rerank_result(),
                )
            ],
            baseline=baseline,
            dataset_case_count=1,
        )
        parameters = [item.parameters for item in report.experiments]

        self.assertEqual(len(parameters), 13)
        for item in parameters[1:]:
            self.assertEqual(item.max_item_tokens, 900)
            self.assertEqual(item.near_duplicate_threshold, 0.90)
        expected = {
            "context_top_k": {4, 6, 8},
            "neighbor_window": {0, 1, 2},
            "source_cap": {1, 2, 3},
            "token_budget": {3_000, 4_500, 6_000},
        }
        for field_name, values in expected.items():
            scanned = {
                item.varied_value
                for item in parameters[1:]
                if item.varied_parameter == field_name
            }
            self.assertEqual(scanned, values)

    def test_experiment_report_contains_required_metrics_and_recommendation(self) -> None:
        baseline = load_context_packing_config()
        report = run_context_experiments(
            [
                ContextTuningInput(
                    case=_approved_case(),
                    plan=plan(),
                    rerank=_rerank_result(),
                )
            ],
            baseline=baseline,
            dataset_case_count=1,
        )

        self.assertEqual(report.tuning_case_count, 1)
        self.assertEqual(len(report.experiments), 13)
        self.assertIn(
            report.recommended_experiment,
            {item.parameters.name for item in report.experiments[1:]},
        )
        baseline_metrics = report.experiments[0].metrics
        self.assertEqual(baseline_metrics.supporting_span_coverage, 1)
        self.assertEqual(baseline_metrics.final_context_recall, 1)
        self.assertEqual(baseline_metrics.subquery_coverage, 1)
        self.assertEqual(baseline_metrics.entity_coverage, 1)
        self.assertGreater(baseline_metrics.average_token_usage, 0)
        self.assertEqual(baseline_metrics.adapter_latency.sample_count, 1)

    def test_cli_writes_report_and_replayable_best_traces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "approved.jsonl"
            report = root / "report.json"
            markdown = root / "report.md"
            traces = root / "best.jsonl"
            write_jsonl(
                dataset,
                [
                    _approved_case(),
                    RAGEvaluationCase(
                        case_id="rag-v0-002",
                        query="这首歌的创作背景是什么？",
                        expected_outcome=ExpectedOutcome.NEEDS_INPUT,
                        annotation_status=AnnotationStatus.APPROVED,
                    ),
                ],
            )
            stdout = StringIO()
            with (
                patch.object(
                    run_context_adapter_tuning,
                    "load_retrieval_index_binding",
                    return_value=SimpleNamespace(profile=profile()),
                ),
                redirect_stdout(stdout),
            ):
                status = run_context_adapter_tuning.main(
                    [
                        "--dataset",
                        str(dataset),
                        "--index-manifest",
                        "manifest.json",
                        "--report",
                        str(report),
                        "--markdown-report",
                        str(markdown),
                        "--best-traces",
                        str(traces),
                        "--run-id",
                        "context-v1-test",
                        "--no-llm",
                    ],
                    project_root=root,
                    retrieval_factory=lambda binding, config: _Retriever(),
                    rerank_factory=lambda config: _Reranker(),
                )

            self.assertEqual(status, 0, stdout.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["experiment_count"], 13)
            self.assertEqual(payload["tuning_case_count"], 1)
            report_payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(report_payload["pipeline_latency"]["retrieval"]["sample_count"], 1)
            self.assertIn("参数实验报告", markdown.read_text(encoding="utf-8"))
            loaded = load_evaluation_traces(traces)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].statuses.context, "success")
            self.assertEqual(loaded[1].statuses.query_understanding, "needs_input")


if __name__ == "__main__":
    unittest.main()

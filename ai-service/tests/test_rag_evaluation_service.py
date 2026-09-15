import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import run_rag_eval
from app.rag.evaluation import (
    AnnotationStatus,
    EvaluationContext,
    ExpectedOutcome,
    RAGEvaluationCase,
    RAGEvaluationTrace,
    SupportingSpanAnnotation,
    TraceStageStatuses,
    build_evaluation_trace,
    build_pending_dataset,
    evaluate_traces,
    load_evaluation_config,
)
from app.rag.reranking import RerankedCandidate, RerankResult, RerankStatus
from tests.reranking_helpers import candidate_pool, plan, profile


def final_context(parent_id: str, rank: int = 1) -> EvaluationContext:
    return EvaluationContext(
        parent_id=parent_id,
        rank=rank,
        title=f"资料 {parent_id}",
        content=f"{parent_id} 的完整 Parent 证据",
        source_id=f"source-{parent_id}",
        source_name="测试知识库",
        token_count=500,
    )


def approved_answerable_case() -> RAGEvaluationCase:
    return RAGEvaluationCase(
        case_id="rag-v0-001",
        query="晴天的创作背景",
        expected_outcome=ExpectedOutcome.ANSWERABLE,
        annotation_status=AnnotationStatus.APPROVED,
        relevant_parent_ids=["parent-2"],
        supporting_spans=[
            SupportingSpanAnnotation(
                parent_id="parent-2",
                exact_text="parent-2 的完整 Parent 证据",
            )
        ],
        required_facts=["创作背景"],
        reference_answer="这是经过人工复核的参考答案。",
    )


class RAGEvaluationServiceTests(unittest.TestCase):
    def test_config_locks_current_pipeline_boundaries(self) -> None:
        config = load_evaluation_config()

        self.assertEqual(config.expected_case_count, 50)
        self.assertEqual(config.candidate_top_k, 40)
        self.assertEqual(config.rerank_top_k, 12)
        self.assertEqual(config.context_top_k, 8)
        self.assertFalse(config.judge_enabled_by_default)

    def test_trace_builder_preserves_top40_top12_lineage_and_versions(self) -> None:
        retrieval = candidate_pool(3)
        rerank = RerankResult(
            status=RerankStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            model_name="Qwen/Qwen3-Reranker-0.6B",
            model_revision="a" * 40,
            rerank_version="v1",
            prompt_version="v1",
            candidate_pool_status=retrieval.status,
            input_count=3,
            output_top_k=12,
            candidates=[
                RerankedCandidate(
                    candidate=retrieval.candidates[1],
                    retrieval_rank=2,
                    rerank_rank=1,
                    rerank_score=0.9,
                ),
                RerankedCandidate(
                    candidate=retrieval.candidates[0],
                    retrieval_rank=1,
                    rerank_rank=2,
                    rerank_score=0.8,
                ),
            ],
        )

        trace = build_evaluation_trace(
            run_id="run-001",
            case_id="rag-v0-001",
            plan=plan(),
            retrieval=retrieval,
            rerank=rerank,
            final_context=[final_context("parent-2")],
            actual_answer="答案 [parent-2]",
            citation_parent_ids=["parent-2"],
            context_token_count=500,
            context_token_budget=6_000,
            retrieval_version="v1",
            context_version="v0",
            generation_status="success",
        )

        self.assertEqual(
            [item.parent_id for item in trace.candidate_pool],
            ["parent-1", "parent-2", "parent-3"],
        )
        self.assertEqual(
            [item.parent_id for item in trace.reranked_context],
            ["parent-2", "parent-1"],
        )
        self.assertEqual(trace.versions.index_release, "knowledge-v2-test")
        self.assertEqual(trace.versions.rerank, "v1")

    def test_deterministic_report_covers_retrieval_rerank_context_and_gates(self) -> None:
        case = approved_answerable_case()
        trace = RAGEvaluationTrace(
            run_id="run-001",
            case_id=case.case_id,
            query=case.query,
            statuses=TraceStageStatuses(
                query_understanding="ready",
                retrieval="success",
                rerank="success",
                context="success",
                generation="success",
            ),
            candidate_pool=[final_context("parent-1", 1), final_context("parent-2", 2)],
            reranked_context=[final_context("parent-2")],
            final_context=[final_context("parent-2")],
            actual_answer="答案 [parent-2]",
            citation_parent_ids=["parent-2"],
            context_token_count=500,
            context_token_budget=400,
            timings_ms={"rerank_total": 20},
        )

        report = evaluate_traces([case], [trace])

        self.assertEqual(report.retrieval.recall_at_40, 1)
        self.assertEqual(report.rerank.rerank_mrr_at_10, 1)
        self.assertEqual(report.final_context_recall, 1)
        self.assertEqual(report.citation_presence_rate, 1)
        self.assertEqual(report.citation_id_validity, 1)
        self.assertEqual(report.token_budget_compliance, 0)

    def test_pending_seed_is_stratified_without_fabricated_answer_labels(self) -> None:
        source = Path(__file__).parent / "fixtures/query_understanding/golden_v1.jsonl"
        cases = build_pending_dataset(source, limit=50)

        self.assertEqual(len(cases), 50)
        self.assertEqual(
            sum(item.expected_outcome is ExpectedOutcome.NEEDS_INPUT for item in cases),
            5,
        )
        self.assertTrue(
            all(item.annotation_status is AnnotationStatus.PENDING for item in cases)
        )
        self.assertTrue(all(not item.relevant_parent_ids for item in cases))
        self.assertTrue(all(item.reference_answer is None for item in cases))

    def test_cli_generates_and_validates_pending_dataset(self) -> None:
        fixture = Path(__file__).parent / "fixtures/query_understanding/golden_v1.jsonl"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_path = root / "rag-v0.jsonl"
            output = StringIO()
            with redirect_stdout(output):
                status = run_rag_eval.main(
                    [
                        "generate-skeleton",
                        "--source",
                        str(fixture),
                        "--output",
                        str(output_path),
                    ],
                    project_root=root,
                )
            generated = json.loads(output.getvalue())
            self.assertEqual(status, 0)
            self.assertEqual(generated["case_count"], 50)

            output = StringIO()
            with redirect_stdout(output):
                status = run_rag_eval.main(
                    ["validate-dataset", "--dataset", str(output_path)],
                    project_root=root,
                )
            validated = json.loads(output.getvalue())

        self.assertEqual(status, 0)
        self.assertEqual(validated["annotation_statuses"], {"pending": 50})
        self.assertFalse(validated["baseline_ready"])


if __name__ == "__main__":
    unittest.main()

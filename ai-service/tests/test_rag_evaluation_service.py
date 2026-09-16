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
    bootstrap_silver_dataset,
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
    def test_silver_bootstrap_approves_answerable_case_with_trace_provenance(self) -> None:
        pending = approved_answerable_case().model_copy(
            update={
                "annotation_status": AnnotationStatus.PENDING,
                "relevant_parent_ids": [],
                "supporting_spans": [],
                "required_facts": [],
                "reference_answer": None,
            }
        )
        trace = RAGEvaluationTrace(
            run_id="run-001",
            case_id=pending.case_id,
            query=pending.query,
            statuses=TraceStageStatuses(
                query_understanding="ready",
                retrieval="success",
                rerank="success",
                context="success",
            ),
            candidate_pool=[final_context("parent-2")],
            reranked_context=[final_context("parent-2")],
            final_context=[final_context("parent-2")],
        )

        approved = bootstrap_silver_dataset([pending], [trace])[0]

        self.assertEqual(approved.annotation_status, AnnotationStatus.APPROVED)
        self.assertEqual(approved.relevant_parent_ids, ["parent-2"])
        self.assertEqual(approved.supporting_spans[0].source_id, "source-parent-2")
        self.assertIn(approved.supporting_spans[0].exact_text, trace.final_context[0].content)
        self.assertIn("silver_bootstrap_v0", approved.tags)
        self.assertIn("不属于逐条人工复核", approved.notes or "")

    def test_silver_bootstrap_deduplicates_identical_required_facts(self) -> None:
        pending = approved_answerable_case().model_copy(
            update={
                "annotation_status": AnnotationStatus.PENDING,
                "relevant_parent_ids": [],
                "supporting_spans": [],
                "required_facts": [],
                "reference_answer": None,
            }
        )
        first = final_context("parent-1", 1)
        second = final_context("parent-2", 2).model_copy(
            update={"content": first.content}
        )
        trace = RAGEvaluationTrace(
            run_id="run-001",
            case_id=pending.case_id,
            query=pending.query,
            statuses=TraceStageStatuses(
                query_understanding="ready",
                retrieval="success",
                rerank="success",
                context="success",
            ),
            candidate_pool=[first, second],
            reranked_context=[first, second],
            final_context=[first, second],
        )

        approved = bootstrap_silver_dataset([pending], [trace])[0]

        self.assertEqual(len(approved.supporting_spans), 2)
        self.assertEqual(approved.required_facts, [first.content])

    def test_silver_bootstrap_marks_empty_retrieval_as_insufficient_evidence(self) -> None:
        pending = approved_answerable_case().model_copy(
            update={
                "annotation_status": AnnotationStatus.PENDING,
                "relevant_parent_ids": [],
                "supporting_spans": [],
                "required_facts": [],
                "reference_answer": None,
            }
        )
        trace = RAGEvaluationTrace(
            run_id="run-001",
            case_id=pending.case_id,
            query=pending.query,
            statuses=TraceStageStatuses(
                query_understanding="ready",
                retrieval="empty",
                rerank="empty",
                context="empty",
            ),
        )

        approved = bootstrap_silver_dataset([pending], [trace])[0]
        report = evaluate_traces(
            [approved, approved_answerable_case().model_copy(update={"case_id": "ok"})],
            [
                trace,
                RAGEvaluationTrace(
                    run_id="run-001",
                    case_id="ok",
                    query="晴天的创作背景",
                    statuses=TraceStageStatuses(
                        query_understanding="ready",
                        retrieval="success",
                        rerank="success",
                        context="success",
                    ),
                    candidate_pool=[final_context("parent-2")],
                    reranked_context=[final_context("parent-2")],
                    final_context=[final_context("parent-2")],
                ),
            ],
        )

        self.assertEqual(approved.expected_outcome, ExpectedOutcome.INSUFFICIENT_EVIDENCE)
        self.assertFalse(approved.relevant_parent_ids)
        self.assertEqual(report.expected_outcome_accuracy, 1)

    def test_silver_bootstrap_approves_needs_input_without_evidence(self) -> None:
        pending = RAGEvaluationCase(
            case_id="rag-v0-050",
            query="那他呢",
            expected_outcome=ExpectedOutcome.NEEDS_INPUT,
        )
        trace = RAGEvaluationTrace(
            run_id="run-001",
            case_id=pending.case_id,
            query=pending.query,
            statuses=TraceStageStatuses(query_understanding="needs_input"),
        )

        approved = bootstrap_silver_dataset([pending], [trace])[0]

        self.assertEqual(approved.annotation_status, AnnotationStatus.APPROVED)
        self.assertFalse(approved.supporting_spans)
        self.assertIsNone(approved.reference_answer)

    def test_silver_bootstrap_rejects_missing_trace(self) -> None:
        pending = RAGEvaluationCase(
            case_id="rag-v0-050",
            query="那他呢",
            expected_outcome=ExpectedOutcome.NEEDS_INPUT,
        )

        with self.assertRaisesRegex(ValueError, "traces missing cases:rag-v0-050"):
            bootstrap_silver_dataset([pending], [])

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
        self.assertEqual(report.final_context_precision, 1)
        self.assertEqual(report.final_context_recall, 1)
        self.assertEqual(report.supporting_span_coverage, 1)
        self.assertEqual(report.citation_presence_rate, 1)
        self.assertEqual(report.citation_id_validity, 1)
        self.assertEqual(report.token_budget_compliance, 0)

    def test_context_metrics_distinguish_parent_recall_precision_and_span_loss(self) -> None:
        case = approved_answerable_case()
        relevant_without_span = final_context("parent-2").model_copy(
            update={"content": "证据在压缩时被截断"}
        )
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
            candidate_pool=[final_context("parent-2"), final_context("parent-3", 2)],
            reranked_context=[final_context("parent-2"), final_context("parent-3", 2)],
            final_context=[relevant_without_span, final_context("parent-3", 2)],
            actual_answer="答案 [parent-2]",
            citation_parent_ids=["parent-2"],
        )

        report = evaluate_traces([case], [trace])

        self.assertEqual(report.final_context_recall, 1)
        self.assertEqual(report.final_context_precision, 0.5)
        self.assertEqual(report.supporting_span_coverage, 0)

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
        self.assertTrue(all(item.notes is None for item in cases))
        self.assertTrue(all("jay_chou_corpus_v1" in item.tags for item in cases))
        queries = {item.query for item in cases}
        self.assertTrue(
            {
                "周杰伦与蔡依林合作过什么",
                "比较周杰伦和林俊杰的音乐风格",
                "王力宏和周杰伦的音乐风格异同",
            }.issubset(queries)
        )
        self.assertTrue(
            {
                "蔡依林的音乐风格",
                "林俊杰受到过哪些影响",
                "王菲的音乐生涯介绍",
                "歌手张学友的音乐故事",
                "G.E.M. 的音乐风格如何",
                "歌手S.H.E有什么代表性风格",
                "对比蔡依林与张惠妹的影响力",
                "Taylor Swift 的音乐风格",
                "华语音乐的发展有什么特点",
                "说点有意思的音乐故事",
            }.isdisjoint(queries)
        )

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

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_rag_eval
import run_rag_trace_collection
from app.rag.evaluation import (
    ExpectedOutcome,
    RAGEvaluationCase,
    collect_evaluation_traces,
    load_evaluation_traces,
    write_jsonl,
)
from app.rag.reranking import RerankedCandidate, RerankResult, RerankStatus
from app.rag.retrieval import RetrievalResult, RetrievalStatus
from tests.reranking_helpers import candidate, profile


class CandidateRetriever:
    async def retrieve(self, plan):
        return RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=plan.plan_id,
            index_profile=profile(),
            candidates=[candidate(1), candidate(2)],
        )


class ReversingReranker:
    async def rerank(self, plan, candidate_pool):
        return RerankResult(
            status=RerankStatus.SUCCESS,
            plan_id=plan.plan_id,
            index_profile=profile(),
            model_name="Qwen/Qwen3-Reranker-0.6B",
            model_revision="a" * 40,
            rerank_version="v1",
            prompt_version="v1",
            candidate_pool_status=candidate_pool.status,
            input_count=2,
            output_top_k=12,
            candidates=[
                RerankedCandidate(
                    candidate=candidate_pool.candidates[1],
                    retrieval_rank=2,
                    rerank_rank=1,
                    rerank_score=0.9,
                ),
                RerankedCandidate(
                    candidate=candidate_pool.candidates[0],
                    retrieval_rank=1,
                    rerank_rank=2,
                    rerank_score=0.8,
                ),
            ],
        )


class FailingGraph:
    async def ainvoke(self, state):
        raise RuntimeError("query service unavailable")


def failing_context_adapter(result, plan):
    raise RuntimeError("context adapter unavailable")


class RAGTraceCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_collector_keeps_a_failed_case_in_the_batch(self) -> None:
        case = RAGEvaluationCase(
            case_id="rag-v0-001",
            query="歌曲《晴天》的创作背景是什么？",
            expected_outcome=ExpectedOutcome.ANSWERABLE,
        )

        traces = await collect_evaluation_traces(
            [case],
            run_id="failed-run",
            graph=FailingGraph(),
            retriever=CandidateRetriever(),
            context_adapter=lambda result, plan: [],
            context_token_budget=6_000,
        )

        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].statuses.query_understanding, "failed")
        self.assertEqual(
            traces[0].warnings,
            ["trace_collection_failed:query_understanding:RuntimeError"],
        )

    async def test_collector_attributes_context_failure_to_context_stage(self) -> None:
        case = RAGEvaluationCase(
            case_id="rag-v0-001",
            query="歌曲《晴天》的创作背景是什么？",
            expected_outcome=ExpectedOutcome.ANSWERABLE,
        )

        traces = await collect_evaluation_traces(
            [case],
            run_id="failed-context-run",
            graph=run_rag_trace_collection.create_query_understanding_graph(),
            retriever=CandidateRetriever(),
            context_adapter=failing_context_adapter,
            reranker=ReversingReranker(),
            context_token_budget=6_000,
        )

        self.assertEqual(traces[0].statuses.query_understanding, "ready")
        self.assertEqual(traces[0].statuses.retrieval, "success")
        self.assertEqual(traces[0].statuses.rerank, "success")
        self.assertEqual(traces[0].statuses.context, "failed")
        self.assertEqual(
            traces[0].warnings,
            ["trace_collection_failed:context:RuntimeError"],
        )

    def test_cli_collects_ready_and_needs_input_then_exports_review_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "pending.jsonl"
            traces_path = root / "traces.jsonl"
            review_path = root / "review.md"
            write_jsonl(
                dataset,
                [
                    RAGEvaluationCase(
                        case_id="rag-v0-001",
                        query="歌曲《晴天》的创作背景是什么？",
                        expected_outcome=ExpectedOutcome.ANSWERABLE,
                    ),
                    RAGEvaluationCase(
                        case_id="rag-v0-002",
                        query="这首歌的创作背景是什么？",
                        expected_outcome=ExpectedOutcome.NEEDS_INPUT,
                    ),
                ],
            )
            collection_stdout = StringIO()
            with (
                patch.object(
                    run_rag_trace_collection,
                    "load_retrieval_index_binding",
                    return_value=SimpleNamespace(profile=profile()),
                ),
                redirect_stdout(collection_stdout),
            ):
                status = run_rag_trace_collection.main(
                    [
                        "--dataset",
                        str(dataset),
                        "--index-manifest",
                        "manifest.json",
                        "--output",
                        str(traces_path),
                        "--run-id",
                        "baseline-v0-test",
                        "--no-llm",
                        "--rerank",
                    ],
                    project_root=root,
                    retrieval_factory=lambda binding, config: CandidateRetriever(),
                    rerank_factory=lambda config: ReversingReranker(),
                )

            self.assertEqual(status, 0)
            payload = json.loads(collection_stdout.getvalue())
            self.assertEqual(payload["query_understanding_statuses"], {"ready": 1, "needs_input": 1})
            self.assertEqual(payload["context_statuses"], {"success": 1, "not_run": 1})
            traces = load_evaluation_traces(traces_path)
            self.assertEqual(traces[0].statuses.retrieval, "success")
            self.assertEqual(traces[0].statuses.rerank, "success")
            self.assertEqual(traces[0].statuses.context, "success")
            self.assertEqual(
                [item.parent_id for item in traces[0].final_context],
                ["parent-2", "parent-1"],
            )
            self.assertEqual(
                traces[0].context_token_count,
                sum(item.token_count for item in traces[0].final_context),
            )
            self.assertEqual(traces[0].context_token_count, 56)
            self.assertEqual(traces[0].versions.context, "context-pack-v1")
            self.assertEqual(traces[1].statuses.query_understanding, "needs_input")
            self.assertEqual(traces[1].statuses.retrieval, "not_run")

            review_stdout = StringIO()
            with redirect_stdout(review_stdout):
                review_status = run_rag_eval.main(
                    [
                        "export-review-pack",
                        "--dataset",
                        str(dataset),
                        "--traces",
                        str(traces_path),
                        "--output",
                        str(review_path),
                    ],
                    project_root=root,
                )

            self.assertEqual(review_status, 0)
            review = review_path.read_text(encoding="utf-8")
            self.assertIn("MoodFlow RAG Eval V0 人工审核包", review)
            self.assertIn("`parent-2`", review)
            self.assertIn("该样本预期触发追问", review)


if __name__ == "__main__":
    unittest.main()

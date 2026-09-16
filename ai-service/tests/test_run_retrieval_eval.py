import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_retrieval_eval
from app.rag.query_understanding.models import IndexProfile
from app.rag.reranking import RerankedCandidate, RerankResult, RerankStatus
from app.rag.retrieval import RetrievalResult, RetrievalStatus
from tests.reranking_helpers import candidate


def profile() -> IndexProfile:
    return IndexProfile(
        collection_name="music_knowledge_test",
        source_release_id="knowledge-v2-test",
        index_schema_version="v2",
        projection_version="v2",
        registry_version="v3",
    )


class EmptyRetriever:
    async def retrieve(self, plan):
        return RetrievalResult(
            status=RetrievalStatus.EMPTY,
            plan_id=plan.plan_id,
            index_profile=profile(),
        )


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


class RunRetrievalEvaluationTests(unittest.TestCase):
    def test_cli_reads_only_approved_answerable_gold_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "gold.jsonl"
            answerable = {
                "case_id": "rag-001",
                "query": "晴天创作背景",
                "expected_outcome": "answerable",
                "annotation_status": "approved",
                "relevant_parent_ids": ["parent-a"],
                "supporting_spans": [
                    {"parent_id": "parent-a", "exact_text": "一段证据"}
                ],
                "required_facts": ["一项事实"],
                "reference_answer": "一段回答",
            }
            needs_input = {
                "case_id": "rag-002",
                "query": "七里香的创作背景",
                "expected_outcome": "needs_input",
                "annotation_status": "approved",
            }
            dataset.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in (answerable, needs_input)),
                encoding="utf-8",
            )
            report_path = root / "report.json"
            output = StringIO()
            with (
                patch.object(
                    run_retrieval_eval,
                    "load_retrieval_index_binding",
                    return_value=SimpleNamespace(profile=profile()),
                ),
                redirect_stdout(output),
            ):
                status = run_retrieval_eval.main(
                    [
                        "--dataset", str(dataset),
                        "--index-manifest", "manifest.json",
                        "--rag-gold", "--no-llm",
                        "--output", str(report_path),
                    ],
                    project_root=root,
                    retrieval_factory=lambda binding, config: EmptyRetriever(),
                )

            self.assertEqual(status, 0)
            self.assertEqual(json.loads(output.getvalue())["metrics"]["case_count"], 1)
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["metrics"]["case_count"], 1)

    def test_cli_scores_empty_candidate_pool_without_applying_rerank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "golden.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "case_id": "fact-001",
                        "query": "晴天创作背景",
                        "relevant_parent_ids": ["parent-a"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with (
                patch.object(
                    run_retrieval_eval,
                    "load_retrieval_index_binding",
                    return_value=SimpleNamespace(profile=profile()),
                ),
                redirect_stdout(output),
            ):
                status = run_retrieval_eval.main(
                    [
                        "--dataset",
                        str(dataset),
                        "--index-manifest",
                        "manifest.json",
                        "--no-llm",
                    ],
                    project_root=root,
                    retrieval_factory=lambda binding, config: EmptyRetriever(),
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["pipeline_stage"], "candidate_pool_evaluation")
        self.assertFalse(payload["rerank_applied"])
        self.assertEqual(payload["metrics"]["case_count"], 1)
        self.assertEqual(payload["metrics"]["recall_at_40"], 0)
        self.assertEqual(payload["case_statuses"], {"fact-001": "empty"})

    def test_cli_compares_pre_and_post_rerank_quality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "golden.jsonl"
            dataset.write_text(
                json.dumps(
                    {
                        "case_id": "fact-001",
                        "query": "晴天创作背景",
                        "relevant_parent_ids": ["parent-2"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with (
                patch.object(
                    run_retrieval_eval,
                    "load_retrieval_index_binding",
                    return_value=SimpleNamespace(profile=profile()),
                ),
                redirect_stdout(output),
            ):
                status = run_retrieval_eval.main(
                    [
                        "--dataset",
                        str(dataset),
                        "--index-manifest",
                        "manifest.json",
                        "--no-llm",
                        "--rerank",
                    ],
                    project_root=root,
                    retrieval_factory=lambda binding, config: CandidateRetriever(),
                    rerank_factory=lambda config: ReversingReranker(),
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(payload["pipeline_stage"], "rerank_evaluation")
        self.assertTrue(payload["rerank_applied"])
        self.assertEqual(payload["rerank_top_k"], 12)
        self.assertEqual(payload["rerank_metrics"]["candidate_mrr_at_10"], 0.5)
        self.assertEqual(payload["rerank_metrics"]["rerank_mrr_at_10"], 1)
        self.assertEqual(payload["rerank_metrics"]["mrr_at_10_delta"], 0.5)
        self.assertEqual(
            payload["case_statuses"],
            {"fact-001": {"retrieval": "success", "rerank": "success"}},
        )


if __name__ == "__main__":
    unittest.main()

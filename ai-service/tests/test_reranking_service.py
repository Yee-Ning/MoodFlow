import time
import unittest

from app.rag.reranking import QwenCandidateReranker, RerankStatus, load_rerank_config
from app.rag.retrieval import RetrievalResult, RetrievalStatus
from tests.reranking_helpers import candidate_pool, plan, profile


class StaticScorer:
    def __init__(self, scores=None, *, error=None, delay: float = 0) -> None:
        self.scores = list(scores or [])
        self.error = error
        self.delay = delay
        self.calls = 0

    def score(self, inputs):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.scores


class PreparingScorer(StaticScorer):
    def __init__(self) -> None:
        super().__init__()
        self.prepare_calls = 0

    def prepare(self) -> None:
        self.prepare_calls += 1


class RerankingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_loads_runtime_outside_inference_timeout(self) -> None:
        scorer = PreparingScorer()
        reranker = QwenCandidateReranker(scorer=scorer)

        await reranker.prepare()

        self.assertEqual(scorer.prepare_calls, 1)

    async def test_top_40_is_scored_and_reduced_to_top_12(self) -> None:
        scores = [index / 40 for index in range(40)]
        reranker = QwenCandidateReranker(scorer=StaticScorer(scores))

        result = await reranker.rerank(plan(), candidate_pool(40))

        self.assertEqual(result.status, RerankStatus.SUCCESS)
        self.assertEqual(result.input_count, 40)
        self.assertEqual(len(result.candidates), 12)
        self.assertEqual(result.candidates[0].candidate.parent_id, "parent-40")
        self.assertEqual(result.candidates[0].retrieval_rank, 40)
        self.assertEqual(result.candidates[0].rerank_rank, 1)

    async def test_model_score_wins_and_fusion_score_is_only_tie_breaker(self) -> None:
        reranker = QwenCandidateReranker(scorer=StaticScorer([0.1, 0.9]))

        result = await reranker.rerank(plan(), candidate_pool(2))

        self.assertEqual(result.candidates[0].candidate.parent_id, "parent-2")
        self.assertGreater(
            result.candidates[1].candidate.fusion_score,
            result.candidates[0].candidate.fusion_score,
        )

    async def test_inference_failure_falls_back_to_rrf_top_12(self) -> None:
        scorer = StaticScorer(error=RuntimeError("private model failure"))
        result = await QwenCandidateReranker(scorer=scorer).rerank(
            plan(), candidate_pool(20)
        )

        self.assertEqual(result.status, RerankStatus.DEGRADED)
        self.assertTrue(result.fallback_used)
        self.assertEqual(len(result.candidates), 12)
        self.assertEqual(result.candidates[0].candidate.parent_id, "parent-1")
        self.assertIsNone(result.candidates[0].rerank_score)
        self.assertEqual(result.warnings, ["rerank_inference_failed"])
        self.assertNotIn("private", result.model_dump_json())

    async def test_timeout_falls_back_without_releasing_live_inference_slot(self) -> None:
        base = load_rerank_config()
        config = base.model_copy(update={"timeout_seconds": 0.01})
        scorer = StaticScorer(scores=[0.5] * 2, delay=0.05)
        reranker = QwenCandidateReranker(config, scorer=scorer)

        result = await reranker.rerank(plan(), candidate_pool(2))

        self.assertEqual(result.status, RerankStatus.DEGRADED)
        self.assertEqual(result.warnings, ["rerank_timeout"])
        self.assertTrue(result.fallback_used)
        await self._wait_for_worker()

    async def test_empty_and_unavailable_pool_never_call_model(self) -> None:
        scorer = StaticScorer([0.5])
        reranker = QwenCandidateReranker(scorer=scorer)
        empty = RetrievalResult(
            status=RetrievalStatus.EMPTY,
            plan_id=plan().plan_id,
            index_profile=profile(),
        )
        unavailable = empty.model_copy(update={"status": RetrievalStatus.UNAVAILABLE})

        empty_result = await reranker.rerank(plan(), empty)
        unavailable_result = await reranker.rerank(plan(), unavailable)

        self.assertEqual(empty_result.status, RerankStatus.EMPTY)
        self.assertEqual(unavailable_result.status, RerankStatus.UNAVAILABLE)
        self.assertEqual(scorer.calls, 0)

    async def test_invalid_score_count_uses_safe_fallback(self) -> None:
        result = await QwenCandidateReranker(
            scorer=StaticScorer([0.5])
        ).rerank(plan(), candidate_pool(2))

        self.assertEqual(result.status, RerankStatus.DEGRADED)
        self.assertEqual(result.warnings, ["rerank_scores_invalid"])

    async def test_oversized_candidate_pool_fails_closed_before_model(self) -> None:
        scorer = StaticScorer([0.5] * 41)

        result = await QwenCandidateReranker(scorer=scorer).rerank(
            plan(), candidate_pool(41)
        )

        self.assertEqual(result.status, RerankStatus.UNAVAILABLE)
        self.assertEqual(result.input_count, 41)
        self.assertEqual(result.warnings, ["rerank_candidate_pool_too_large"])
        self.assertEqual(scorer.calls, 0)

    @staticmethod
    async def _wait_for_worker() -> None:
        import asyncio

        await asyncio.sleep(0.06)


if __name__ == "__main__":
    unittest.main()

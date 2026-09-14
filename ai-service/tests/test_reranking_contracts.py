import unittest

from pydantic import ValidationError

from app.rag.reranking import (
    RerankedCandidate,
    RerankResult,
    RerankStatus,
    build_rerank_inputs,
    load_rerank_config,
)
from tests.reranking_helpers import candidate, candidate_pool, plan, profile


class RerankingContractTests(unittest.TestCase):
    def test_checked_in_policy_matches_approved_topology(self) -> None:
        config = load_rerank_config()

        self.assertEqual(config.maximum_candidates, 40)
        self.assertEqual(config.top_k, 12)
        self.assertEqual(config.model.input_token_limit, 1_024)
        description = type(config.model).model_fields["input_token_limit"].description
        self.assertIn("not the upstream model limit", description)
        self.assertEqual(config.model.batch_size, 8)
        self.assertEqual(config.model.name, "Qwen/Qwen3-Reranker-0.6B")
        self.assertEqual(len(config.model.revision), 40)
        self.assertIn("music knowledge query", config.model.instruction)

    def test_input_uses_primary_child_not_deferred_parent_context(self) -> None:
        config = load_rerank_config()

        inputs = build_rerank_inputs(plan(), candidate_pool(1), config)

        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0].query, "晴天的创作背景")
        self.assertIn("第 1 条 Child 命中证据", inputs[0].document)
        self.assertNotIn("尚未展开的 Parent", inputs[0].document)
        self.assertEqual(inputs[0].subquery_ids, ["q0:original"])
        self.assertEqual(inputs[0].retrieval_legs, ["q0:original:dense"])

    def test_result_preserves_both_ranks_without_score_mixing(self) -> None:
        item = RerankedCandidate(
            candidate=candidate(1),
            retrieval_rank=7,
            rerank_rank=1,
            rerank_score=0.9,
        )

        result = RerankResult(
            status=RerankStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            model_name="Qwen/Qwen3-Reranker-0.6B",
            model_revision="a" * 40,
            rerank_version="v1",
            prompt_version="v1",
            candidate_pool_status="success",
            input_count=40,
            output_top_k=12,
            candidates=[item],
        )

        self.assertEqual(result.candidates[0].rerank_score, 0.9)
        self.assertEqual(result.candidates[0].candidate.fusion_score, 1 / 61)

    def test_invalid_score_and_noncontiguous_rank_fail_closed(self) -> None:
        with self.assertRaises(ValidationError):
            RerankedCandidate(
                candidate=candidate(1),
                retrieval_rank=1,
                rerank_rank=1,
                rerank_score=float("nan"),
            )
        with self.assertRaises(ValidationError):
            RerankResult(
                status=RerankStatus.SUCCESS,
                plan_id=plan().plan_id,
                index_profile=profile(),
                model_name="model",
                model_revision="a" * 40,
                rerank_version="v1",
                prompt_version="v1",
                candidate_pool_status="success",
                input_count=1,
                output_top_k=12,
                candidates=[
                    RerankedCandidate(
                        candidate=candidate(1),
                        retrieval_rank=1,
                        rerank_rank=2,
                        rerank_score=0.5,
                    )
                ],
            )


if __name__ == "__main__":
    unittest.main()

import unittest

from pydantic import ValidationError

from app.rag.query_understanding.models import (
    IndexProfile,
    QueryVariantKind,
    SearchChannel,
)
from app.rag.retrieval import (
    ChannelEvidence,
    DeferredParentContext,
    RetrievalCandidate,
    RetrievalResult,
    RetrievalStatus,
    load_retrieval_config,
)


def profile() -> IndexProfile:
    return IndexProfile(
        collection_name="music_knowledge_release",
        source_release_id="knowledge-v2-abc",
        index_schema_version="v2",
        projection_version="v2",
        registry_version="v3",
    )


def candidate() -> RetrievalCandidate:
    return RetrievalCandidate(
        point_id="point-1",
        parent_id="parent-1",
        doc_id="doc-1",
        title="资料标题",
        primary_child_id="child-1",
        primary_child_content="命中的证据内容",
        primary_child_breadcrumb="资料 > 背景",
        primary_child_token_count=10,
        parent_context=DeferredParentContext(
            chunk_id="parent-1",
            content="尚未展开的父级上下文",
            token_count=20,
        ),
        source_id="source-1",
        source_name="测试来源",
        fusion_score=0.4,
        channel_evidence=[
            ChannelEvidence(
                chunk_id="child-1",
                subquery_id="q0:original",
                retrieval_leg="q0:original:dense",
                variant_index=0,
                variant_kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.DENSE,
                rank=1,
                raw_score=0.8,
                weight=1,
            )
        ],
        matched_child_ids=["child-1"],
    )


class RetrievalContractTests(unittest.TestCase):
    def test_checked_in_config_matches_index_vector_contract(self) -> None:
        config = load_retrieval_config()

        self.assertEqual(config.dense.vector_name, "dense")
        self.assertEqual(config.dense.dimension, 1024)
        self.assertEqual(config.bm25.vector_name, "bm25")
        self.assertEqual(config.bm25.query_options()["tokenizer"], "multilingual")
        self.assertEqual(config.fusion.maximum_legs, 6)
        self.assertEqual(config.selection.worldline_token_budget, 6_000)

    def test_success_requires_candidates_and_unique_parents(self) -> None:
        with self.assertRaises(ValidationError):
            RetrievalResult(
                status=RetrievalStatus.SUCCESS,
                plan_id="query-plan:0123456789abcdef01234567",
                index_profile=profile(),
            )

        duplicate = candidate().model_copy(update={"point_id": "point-2"})
        with self.assertRaises(ValidationError):
            RetrievalResult(
                status=RetrievalStatus.SUCCESS,
                plan_id="query-plan:0123456789abcdef01234567",
                index_profile=profile(),
                candidates=[candidate(), duplicate],
            )

    def test_candidate_requires_primary_chunk_in_evidence(self) -> None:
        payload = candidate().model_dump(mode="python")
        payload["matched_child_ids"] = ["child-2"]
        with self.assertRaises(ValidationError):
            RetrievalCandidate.model_validate(payload)

    def test_unavailable_result_cannot_claim_candidates(self) -> None:
        with self.assertRaises(ValidationError):
            RetrievalResult(
                status=RetrievalStatus.UNAVAILABLE,
                plan_id="query-plan:0123456789abcdef01234567",
                index_profile=profile(),
                candidates=[candidate()],
            )


if __name__ == "__main__":
    unittest.main()

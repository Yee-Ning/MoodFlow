import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.rag.query_understanding.models import (
    ContextDependency,
    ContextResolution,
    ContextResolutionStatus,
    IndexProfile,
    MetadataFilter,
    QueryAnalysis,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    RetrievalStrategy,
    SearchChannel,
    SearchPlan,
)
from app.rag.retrieval import (
    LegSearchResult,
    RawSearchHit,
    RetrievalLeg,
    build_retrieval_legs,
    load_retrieval_config,
    weighted_rrf,
)
from app.rag.retrieval.qdrant import QdrantHybridSearchBackend


def plan() -> SearchPlan:
    context = ContextResolution(
        status=ContextResolutionStatus.RESOLVED,
        dependency=ContextDependency.STANDALONE,
        original_query="晴天的创作背景",
        standalone_query="晴天的创作背景",
    )
    analysis = QueryAnalysis(
        original_query=context.original_query,
        standalone_query=context.standalone_query,
        query_types=[QueryType.FACT],
        context=context,
        confidence=1,
    )
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query=analysis.original_query,
        standalone_query=analysis.standalone_query,
        query_types=analysis.query_types,
        queries=[
            QueryVariant(
                text="晴天的创作背景",
                kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.BOTH,
                weight=1,
            ),
            QueryVariant(
                text="晴天 周杰伦",
                kind=QueryVariantKind.ALIAS,
                channel=SearchChannel.LEXICAL,
                weight=0.5,
            ),
        ],
        metadata_filter=MetadataFilter(),
        strategy=RetrievalStrategy.MULTI_QUERY_HYBRID,
        candidate_top_k=40,
        final_top_k=10,
        needs_rerank=True,
        analysis_version="v1",
        prompt_version="v1",
    )


def binding():
    return SimpleNamespace(
        profile=IndexProfile(
            collection_name="test_collection",
            source_release_id="knowledge-v2-test",
            index_schema_version="v2",
            projection_version="v2",
            registry_version="v3",
        )
    )


class RetrievalLegAndFusionTests(unittest.TestCase):
    def test_plan_expands_to_bounded_weighted_channel_legs(self) -> None:
        config = load_retrieval_config()
        legs = build_retrieval_legs(plan(), {0: [0.1] * 1024}, config)

        self.assertEqual(
            [item.channel for item in legs],
            [SearchChannel.DENSE, SearchChannel.LEXICAL, SearchChannel.LEXICAL],
        )
        self.assertEqual([item.weight for item in legs], [1, 1, 0.5])

    def test_weighted_rrf_merges_rankings_and_preserves_evidence(self) -> None:
        dense = RetrievalLeg(
            variant_index=0,
            variant_kind=QueryVariantKind.ORIGINAL,
            channel=SearchChannel.DENSE,
            text="query",
            weight=1,
            dense_vector=[0.1],
        )
        lexical = RetrievalLeg(
            variant_index=0,
            variant_kind=QueryVariantKind.ORIGINAL,
            channel=SearchChannel.LEXICAL,
            text="query",
            weight=1,
        )
        fused = weighted_rrf(
            [
                LegSearchResult(
                    dense,
                    [
                        RawSearchHit("a", 0.9, {"chunk_id": "a"}),
                        RawSearchHit("b", 0.8, {"chunk_id": "b"}),
                    ],
                ),
                LegSearchResult(
                    lexical,
                    [
                        RawSearchHit("b", 4.0, {"chunk_id": "b"}),
                        RawSearchHit("c", 3.0, {"chunk_id": "c"}),
                    ],
                ),
            ],
            k=60,
        )

        self.assertEqual([item.point_id for item in fused], ["b", "a", "c"])
        self.assertEqual(len(fused[0].evidence), 2)
        self.assertAlmostEqual(fused[0].fusion_score, 1 / 62 + 1 / 61)
        self.assertEqual(
            {item.chunk_id for item in fused[0].evidence},
            {"b"},
        )


class QdrantHybridSearchBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_query_uses_dense_and_core_bm25_contracts(self) -> None:
        config = load_retrieval_config()
        client = AsyncMock()
        client.query_batch_points.return_value = [
            SimpleNamespace(points=[SimpleNamespace(id="a", score=0.8, payload={})]),
            SimpleNamespace(points=[SimpleNamespace(id="b", score=4.2, payload={})]),
        ]
        backend = QdrantHybridSearchBackend(config, client=client)
        legs = [
            RetrievalLeg(
                variant_index=0,
                variant_kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.DENSE,
                text="晴天",
                weight=1,
                dense_vector=[0.1] * 1024,
            ),
            RetrievalLeg(
                variant_index=0,
                variant_kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.LEXICAL,
                text="晴天",
                weight=1,
            ),
        ]

        results = await backend.search(binding(), legs, None, limit=40)

        self.assertEqual([item.hits[0].point_id for item in results], ["a", "b"])
        requests = client.query_batch_points.await_args.kwargs["requests"]
        self.assertEqual(requests[0].using, "dense")
        self.assertEqual(requests[0].params.hnsw_ef, 128)
        self.assertEqual(requests[1].using, "bm25")
        self.assertEqual(requests[1].query.model, "qdrant/bm25")
        self.assertEqual(requests[1].query.options["tokenizer"], "multilingual")

    async def test_batch_failure_isolates_individual_leg_failures(self) -> None:
        config = load_retrieval_config()
        client = AsyncMock()
        client.query_batch_points.side_effect = RuntimeError("batch failed")
        client.query_points.side_effect = [
            SimpleNamespace(points=[SimpleNamespace(id="a", score=0.8, payload={})]),
            RuntimeError("lexical failed"),
        ]
        backend = QdrantHybridSearchBackend(config, client=client)
        legs = [
            RetrievalLeg(
                variant_index=0,
                variant_kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.DENSE,
                text="晴天",
                weight=1,
                dense_vector=[0.1] * 1024,
            ),
            RetrievalLeg(
                variant_index=0,
                variant_kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.LEXICAL,
                text="晴天",
                weight=1,
            ),
        ]

        results = await backend.search(binding(), legs, None, limit=40)

        self.assertIsNone(results[0].error_code)
        self.assertEqual(results[1].error_code, "lexical_query_failed")


if __name__ == "__main__":
    unittest.main()

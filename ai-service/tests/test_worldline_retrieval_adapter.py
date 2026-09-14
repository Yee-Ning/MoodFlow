import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    ResolvedEntity,
)
from app.agent.foundation.subgraphs.worldline.retrieval import (
    EmptyMusicKnowledgeRetriever,
    HybridMusicKnowledgeRetriever,
    RetrievalRuntimeSettings,
    ShadowMusicKnowledgeRetriever,
    WorldLineContextAdapter,
    create_worldline_retrieval_runtime,
)
from app.rag.query_understanding.models import (
    IndexProfile,
    MetadataFilter,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    RetrievalStrategy,
    SearchChannel,
    SearchPlan,
)
from app.rag.retrieval import (
    ChannelEvidence,
    RetrievalCandidate,
    RetrievalResult,
    RetrievalStatus,
    load_retrieval_config,
)


def profile() -> IndexProfile:
    return IndexProfile(
        collection_name="music_knowledge_test",
        source_release_id="knowledge-v2-test",
        index_schema_version="v2",
        projection_version="v2",
        registry_version="v3",
    )


def plan() -> SearchPlan:
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query="晴天创作背景",
        standalone_query="晴天创作背景",
        query_types=[QueryType.FACT],
        queries=[
            QueryVariant(
                text="晴天创作背景",
                kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.BOTH,
                weight=1,
            )
        ],
        metadata_filter=MetadataFilter(),
        strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40,
        final_top_k=10,
        needs_rerank=False,
        analysis_version="v1",
        prompt_version="v1",
        index_profile=profile(),
    )


def candidate(index: int, tokens: int = 500) -> RetrievalCandidate:
    return RetrievalCandidate(
        point_id=f"point-{index}",
        chunk_id=f"child-{index}",
        parent_id=f"parent-{index}",
        doc_id=f"doc-{index}",
        title=f"资料 {index}",
        child_content="命中证据",
        expanded_content="完整父级上下文",
        source_id=f"source-{index}",
        source_name="测试知识库",
        source_url="https://example.com/source",
        entity_ids=["song:qing_tian"],
        supported_claims=[],
        fusion_score=1 / (60 + index),
        channel_evidence=[
            ChannelEvidence(
                chunk_id=f"child-{index}",
                variant_index=0,
                variant_kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.DENSE,
                rank=index,
                raw_score=0.9,
                weight=1,
            )
        ],
        matched_child_ids=[f"child-{index}"],
        child_token_count=100,
        expanded_token_count=tokens,
    )


class FakeRetrievalService:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls = 0

    async def retrieve(self, search_plan: SearchPlan) -> RetrievalResult:
        self.calls += 1
        return self.result


def entity() -> ResolvedEntity:
    return ResolvedEntity(
        entity_id="netease:song:185809",
        entity_type=EntityType.SONG,
        name="晴天",
        source="netease",
    )


class WorldLineRetrievalAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_adapter_bounds_and_traces_final_worldline_context(self) -> None:
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            candidates=[candidate(index) for index in range(1, 13)],
        )
        documents = WorldLineContextAdapter().adapt(result)

        self.assertEqual(len(documents), 8)
        self.assertEqual(documents[0].id, "parent-1")
        self.assertEqual(documents[0].metadata["chunk_id"], "child-1")
        self.assertEqual(documents[0].metadata["retrieval_status"], "success")

    async def test_candidate_pool_stays_separate_from_context_adapter(self) -> None:
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            candidates=[candidate(index) for index in range(1, 41)],
        )
        service = FakeRetrievalService(result)
        retriever = HybridMusicKnowledgeRetriever(service)

        documents = await retriever.retrieve(entity(), plan())

        self.assertEqual(plan().candidate_top_k, 40)
        self.assertEqual(len(result.candidates), 40)
        self.assertEqual(len(documents), 8)
        self.assertEqual(service.calls, 1)

    async def test_worldline_token_budget_skips_oversized_later_context(self) -> None:
        base = load_retrieval_config()
        config = base.model_copy(
            update={
                "selection": base.selection.model_copy(
                    update={"worldline_token_budget": 1_000}
                )
            }
        )
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            candidates=[candidate(1, 700), candidate(2, 700), candidate(3, 200)],
        )
        documents = WorldLineContextAdapter(config).adapt(result)

        self.assertEqual([item.id for item in documents], ["parent-1", "parent-3"])

    async def test_unavailable_and_shadow_results_do_not_change_answer_context(self) -> None:
        result = RetrievalResult(
            status=RetrievalStatus.UNAVAILABLE,
            plan_id=plan().plan_id,
            index_profile=profile(),
            warnings=["qdrant_query_failed"],
        )
        service = FakeRetrievalService(result)

        unavailable = await HybridMusicKnowledgeRetriever(service).retrieve(
            entity(), plan()
        )
        shadow = await ShadowMusicKnowledgeRetriever(service).retrieve(entity(), plan())

        self.assertEqual(unavailable, [])
        self.assertEqual(shadow, [])
        self.assertEqual(service.calls, 2)

    async def test_empty_runtime_mode_uses_explicit_empty_retriever(self) -> None:
        runtime = create_worldline_retrieval_runtime(
            settings=RetrievalRuntimeSettings(
                retrieval_mode="empty",
                _env_file=None,
            )
        )

        self.assertIsInstance(runtime.retriever, EmptyMusicKnowledgeRetriever)
        self.assertIsNone(runtime.index_profile)

    async def test_hybrid_runtime_requires_manifest(self) -> None:
        with self.assertRaisesRegex(ValueError, "AI_RAG_INDEX_MANIFEST"):
            create_worldline_retrieval_runtime(
                settings=RetrievalRuntimeSettings(
                    retrieval_mode="hybrid",
                    _env_file=None,
                )
            )

    async def test_hybrid_runtime_binds_profile_and_adapter(self) -> None:
        fake_binding = SimpleNamespace(profile=profile())
        with (
            patch(
                "app.agent.foundation.subgraphs.worldline.retrieval."
                "load_retrieval_index_binding",
                return_value=fake_binding,
            ),
            patch(
                "app.agent.foundation.subgraphs.worldline.retrieval."
                "HybridRetrievalService"
            ),
        ):
            runtime = create_worldline_retrieval_runtime(
                settings=RetrievalRuntimeSettings(
                    retrieval_mode="hybrid",
                    index_manifest="data/indexing/manifest.json",
                    _env_file=None,
                )
            )

        self.assertIsInstance(runtime.retriever, HybridMusicKnowledgeRetriever)
        self.assertEqual(runtime.index_profile, profile())


if __name__ == "__main__":
    unittest.main()

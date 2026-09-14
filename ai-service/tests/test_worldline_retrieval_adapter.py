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
from app.rag.reranking import RerankedCandidate, RerankResult, RerankStatus
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
        needs_rerank=True,
        analysis_version="v1",
        prompt_version="v1",
        index_profile=profile(),
    )


def candidate(index: int, tokens: int = 500) -> RetrievalCandidate:
    return RetrievalCandidate(
        point_id=f"point-{index}",
        parent_id=f"parent-{index}",
        doc_id=f"doc-{index}",
        title=f"资料 {index}",
        primary_child_id=f"child-{index}",
        primary_child_content="命中证据",
        primary_child_breadcrumb=f"资料 {index} > 证据",
        primary_child_token_count=100,
        parent_context=DeferredParentContext(
            chunk_id=f"parent-{index}",
            content="完整父级上下文",
            token_count=tokens,
        ),
        source_id=f"source-{index}",
        source_name="测试知识库",
        source_url="https://example.com/source",
        entity_ids=["song:qing_tian"],
        supported_claims=[],
        fusion_score=1 / (60 + index),
        channel_evidence=[
            ChannelEvidence(
                chunk_id=f"child-{index}",
                subquery_id="q0:original",
                retrieval_leg="q0:original:dense",
                variant_index=0,
                variant_kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.DENSE,
                rank=index,
                raw_score=0.9,
                weight=1,
            )
        ],
        matched_child_ids=[f"child-{index}"],
    )


class FakeRetrievalService:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls = 0

    async def retrieve(self, search_plan: SearchPlan) -> RetrievalResult:
        self.calls += 1
        return self.result


class FakeReranker:
    def __init__(self) -> None:
        self.calls = 0

    async def rerank(
        self,
        search_plan: SearchPlan,
        candidate_pool: RetrievalResult,
    ) -> RerankResult:
        self.calls += 1
        selected = list(enumerate(candidate_pool.candidates, start=1))[::-1][:12]
        return RerankResult(
            status=RerankStatus.SUCCESS,
            plan_id=search_plan.plan_id,
            index_profile=search_plan.index_profile,
            model_name="Qwen/Qwen3-Reranker-0.6B",
            model_revision="a" * 40,
            rerank_version="v1",
            prompt_version="v1",
            candidate_pool_status=candidate_pool.status,
            candidate_pool_warnings=candidate_pool.warnings,
            input_count=len(candidate_pool.candidates),
            output_top_k=12,
            candidates=[
                RerankedCandidate(
                    candidate=item,
                    retrieval_rank=retrieval_rank,
                    rerank_rank=rerank_rank,
                    rerank_score=1 - (rerank_rank - 1) / 100,
                )
                for rerank_rank, (retrieval_rank, item) in enumerate(
                    selected,
                    start=1,
                )
            ],
        )


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
        self.assertEqual(documents[0].metadata["rerank_status"], "off")
        self.assertIn("完整父级上下文", documents[0].content)
        self.assertNotIn("命中证据", documents[0].content)

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

    async def test_rerank_shadow_observes_but_preserves_rrf_context_order(self) -> None:
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            candidates=[candidate(index) for index in range(1, 21)],
        )
        service = FakeRetrievalService(result)
        reranker = FakeReranker()
        retriever = HybridMusicKnowledgeRetriever(
            service,
            reranker=reranker,
            rerank_mode="shadow",
        )

        documents = await retriever.retrieve(entity(), plan())

        self.assertEqual(reranker.calls, 1)
        self.assertEqual(documents[0].id, "parent-1")
        self.assertEqual(documents[0].metadata["rerank_status"], "off")

    async def test_rerank_on_reduces_top_40_to_top_12_before_adapter_top_8(
        self,
    ) -> None:
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            candidates=[candidate(index) for index in range(1, 41)],
        )
        service = FakeRetrievalService(result)
        reranker = FakeReranker()
        retriever = HybridMusicKnowledgeRetriever(
            service,
            reranker=reranker,
            rerank_mode="on",
        )

        documents = await retriever.retrieve(entity(), plan())

        self.assertEqual(len(result.candidates), 40)
        self.assertEqual(reranker.calls, 1)
        self.assertEqual(len(documents), 8)
        self.assertEqual(documents[0].id, "parent-40")
        self.assertEqual(documents[0].metadata["retrieval_rank"], 40)
        self.assertEqual(documents[0].metadata["rerank_rank"], 1)
        self.assertEqual(documents[0].metadata["rerank_status"], "success")
        self.assertEqual(documents[0].metadata["rerank_version"], "v1")
        self.assertIn("完整父级上下文", documents[0].content)

    async def test_enabled_rerank_requires_explicit_reranker(self) -> None:
        result = RetrievalResult(
            status=RetrievalStatus.EMPTY,
            plan_id=plan().plan_id,
            index_profile=profile(),
        )
        with self.assertRaisesRegex(ValueError, "reranker is required"):
            HybridMusicKnowledgeRetriever(
                FakeRetrievalService(result),
                rerank_mode="on",
            )

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

    async def test_retrieval_shadow_can_exercise_reranker_without_answer_context(
        self,
    ) -> None:
        result = RetrievalResult(
            status=RetrievalStatus.SUCCESS,
            plan_id=plan().plan_id,
            index_profile=profile(),
            candidates=[candidate(index) for index in range(1, 13)],
        )
        service = FakeRetrievalService(result)
        reranker = FakeReranker()

        documents = await ShadowMusicKnowledgeRetriever(
            service,
            reranker=reranker,
            rerank_mode="on",
        ).retrieve(entity(), plan())

        self.assertEqual(documents, [])
        self.assertEqual(reranker.calls, 1)

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

    async def test_hybrid_runtime_constructs_reranker_only_when_enabled(self) -> None:
        fake_binding = SimpleNamespace(profile=profile())
        fake_reranker = SimpleNamespace()
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
            patch(
                "app.agent.foundation.subgraphs.worldline.retrieval."
                "QwenCandidateReranker",
                return_value=fake_reranker,
            ) as reranker_factory,
        ):
            runtime = create_worldline_retrieval_runtime(
                settings=RetrievalRuntimeSettings(
                    retrieval_mode="hybrid",
                    rerank_mode="on",
                    index_manifest="data/indexing/manifest.json",
                    _env_file=None,
                )
            )

        self.assertIs(runtime.retriever.reranker, fake_reranker)
        self.assertEqual(runtime.retriever.rerank_mode, "on")
        reranker_factory.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace

from app.rag.query_understanding.models import (
    FilterCondition,
    FilterGroup,
    FilterOperator,
    IndexProfile,
    MetadataField,
    MetadataFilter,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    RetrievalStrategy,
    SearchChannel,
    SearchPlan,
)
from app.rag.retrieval import (
    HybridRetrievalService,
    LegSearchResult,
    RawSearchHit,
    RetrievalStatus,
)


def profile() -> IndexProfile:
    return IndexProfile(
        collection_name="music_knowledge_test",
        source_release_id="knowledge-v2-test",
        index_schema_version="v2",
        projection_version="v2",
        registry_version="v3",
    )


def plan(*, exclusions=None, index_profile=None) -> SearchPlan:
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query="晴天的创作背景",
        standalone_query="晴天的创作背景",
        query_types=[QueryType.FACT],
        queries=[
            QueryVariant(
                text="晴天的创作背景",
                kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.BOTH,
                weight=1,
            )
        ],
        metadata_filter=MetadataFilter(),
        post_exclusions=list(exclusions or []),
        strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40,
        final_top_k=10,
        needs_rerank=True,
        analysis_version="v1",
        prompt_version="v1",
        index_profile=index_profile or profile(),
    )


def payload(
    chunk_id: str,
    parent_id: str,
    *,
    doc_id: str = "doc-1",
    source_id: str = "source-1",
    content: str = "晴天由周杰伦创作。",
) -> dict:
    return {
        "chunk_id": chunk_id,
        "parent_id": parent_id,
        "doc_id": doc_id,
        "chunk_index": 1,
        "breadcrumb": "晴天 > 创作背景",
        "content": content,
        "retrieval_content": f"晴天 > 创作背景\n\n{content}",
        "token_count": 10,
        "parent_context": {
            "chunk_id": parent_id,
            "content": f"父级上下文：{content}",
            "token_count": 20,
        },
        "source_id": source_id,
        "source_name": "测试来源",
        "source_url": "https://example.com/source",
        "topics": ["创作背景"],
        "keywords": ["晴天"],
        "entity_ids": ["song:qing_tian"],
        "entity_types": ["song"],
        "supported_claims": [],
        "knowledge_status": "success",
        "content_type": "song_background",
        "language": "zh-CN",
    }


class FakeEncoder:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def encode_queries(self, texts):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [[0.1] * 1024 for _ in texts]


class FakeBackend:
    def __init__(
        self,
        hits=None,
        *,
        errors=None,
        validation_error=None,
        search_error=None,
    ) -> None:
        self.hits = list(hits or [])
        self.errors = list(errors or [])
        self.validation_error = validation_error
        self.search_error = search_error
        self.validation_calls = 0
        self.search_calls = 0
        self.legs = []
        self.limit = None

    async def validate_binding(self, binding) -> None:
        self.validation_calls += 1
        if self.validation_error is not None:
            raise self.validation_error

    async def search(self, binding, legs, query_filter, *, limit):
        self.search_calls += 1
        if self.search_error is not None:
            raise self.search_error
        self.legs = list(legs)
        self.limit = limit
        results = []
        for index, leg in enumerate(legs):
            error = self.errors[index] if index < len(self.errors) else None
            hits = self.hits[index] if index < len(self.hits) else []
            results.append(LegSearchResult(leg=leg, hits=hits, error_code=error))
        return results


class SparseEntityBackend(FakeBackend):
    def __init__(self, *, fail_relaxed: bool = False) -> None:
        super().__init__()
        self.fail_relaxed = fail_relaxed
        self.filters = []

    async def search(self, binding, legs, query_filter, *, limit):
        self.search_calls += 1
        self.filters.append(query_filter)
        if query_filter is None and self.fail_relaxed:
            raise RuntimeError("relaxed search failed")
        hits = [RawSearchHit("p1", 0.9, payload("child-1", "parent-1"))]
        if query_filter is None:
            hits.append(
                RawSearchHit(
                    "p2",
                    0.8,
                    payload("child-2", "parent-2", source_id="source-2"),
                )
            )
        return [
            LegSearchResult(leg=leg, hits=hits if index == 0 else [])
            for index, leg in enumerate(legs)
        ]


def sparse_exploratory_plan() -> SearchPlan:
    entity_filter = MetadataFilter(
        root=FilterGroup(
            operator="all",
            children=[
                FilterCondition(
                    field=MetadataField.ENTITY_IDS,
                    operator=FilterOperator.MATCH_ANY,
                    value=["song:qing_tian"],
                )
            ],
        )
    )
    return plan().model_copy(
        update={
            "query_types": [QueryType.EXPLORATORY],
            "metadata_filter": entity_filter,
        }
    )


def binding() -> SimpleNamespace:
    return SimpleNamespace(profile=profile())


class HybridRetrievalServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sparse_exploratory_entity_filter_recovers_broad_candidates(self) -> None:
        backend = SparseEntityBackend()
        encoder = FakeEncoder()
        service = HybridRetrievalService(binding(), encoder=encoder, backend=backend)

        result = await service.retrieve(sparse_exploratory_plan())

        self.assertEqual(result.status, RetrievalStatus.SUCCESS)
        self.assertEqual([item.parent_id for item in result.candidates], ["parent-1", "parent-2"])
        self.assertEqual(backend.search_calls, 2)
        self.assertIsNotNone(backend.filters[0])
        self.assertIsNone(backend.filters[1])
        self.assertEqual(encoder.calls, 1)
        self.assertIn("exploratory_entity_filter_relaxed_for_recall", result.warnings)

    async def test_relaxed_query_failure_keeps_strict_results(self) -> None:
        backend = SparseEntityBackend(fail_relaxed=True)
        service = HybridRetrievalService(
            binding(), encoder=FakeEncoder(), backend=backend
        )

        result = await service.retrieve(sparse_exploratory_plan())

        self.assertEqual(result.status, RetrievalStatus.SUCCESS)
        self.assertEqual([item.parent_id for item in result.candidates], ["parent-1"])
        self.assertIn("exploratory_entity_fallback_failed", result.warnings)

    async def test_entity_fallback_does_not_relax_fact_or_other_constraints(self) -> None:
        for test_plan in (
            sparse_exploratory_plan().model_copy(update={"query_types": [QueryType.FACT]}),
            sparse_exploratory_plan().model_copy(
                update={
                    "metadata_filter": MetadataFilter(
                        root=FilterGroup(
                            operator="all",
                            children=[
                                *sparse_exploratory_plan().metadata_filter.root.children,
                                FilterCondition(
                                    field=MetadataField.YEAR,
                                    operator=FilterOperator.RANGE,
                                    gte=2004,
                                    lte=2004,
                                ),
                            ],
                        )
                    )
                }
            ),
        ):
            backend = SparseEntityBackend()
            service = HybridRetrievalService(
                binding(), encoder=FakeEncoder(), backend=backend
            )

            result = await service.retrieve(test_plan)

            self.assertEqual(result.status, RetrievalStatus.SUCCESS)
            self.assertEqual(backend.search_calls, 1)

    async def test_hybrid_retrieval_collapses_parent_and_tracks_channels(self) -> None:
        backend = FakeBackend(
            hits=[
                [
                    RawSearchHit("p1", 0.9, payload("child-1", "parent-1")),
                    RawSearchHit(
                        "p2",
                        0.8,
                        payload("child-2", "parent-1", content="补充创作信息。"),
                    ),
                ],
                [RawSearchHit("p2", 4.2, payload("child-2", "parent-1"))],
            ]
        )
        service = HybridRetrievalService(
            binding(),
            encoder=FakeEncoder(),
            backend=backend,
        )

        result = await service.retrieve(plan())

        self.assertEqual(result.status, RetrievalStatus.SUCCESS)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(
            result.candidates[0].matched_child_ids,
            ["child-2", "child-1"],
        )
        self.assertEqual(result.candidates[0].primary_child_id, "child-2")
        self.assertEqual(
            result.candidates[0].primary_child_content,
            "补充创作信息。",
        )
        self.assertEqual(len(result.candidates[0].channel_evidence), 3)
        self.assertEqual(
            {
                (item.subquery_id, item.retrieval_leg)
                for item in result.candidates[0].channel_evidence
            },
            {
                ("q0:original", "q0:original:dense"),
                ("q0:original", "q0:original:lexical"),
            },
        )
        self.assertEqual(backend.limit, 80)
        self.assertEqual(backend.validation_calls, 1)

    async def test_dense_failure_degrades_to_lexical_only(self) -> None:
        backend = FakeBackend(
            hits=[[RawSearchHit("p1", 4.2, payload("child-1", "parent-1"))]]
        )
        service = HybridRetrievalService(
            binding(),
            encoder=FakeEncoder(error=RuntimeError("GPU unavailable")),
            backend=backend,
        )

        result = await service.retrieve(plan())

        self.assertEqual(result.status, RetrievalStatus.DEGRADED)
        self.assertEqual(result.failed_channels, [SearchChannel.DENSE])
        self.assertEqual([item.channel for item in backend.legs], [SearchChannel.LEXICAL])

    async def test_exact_post_exclusion_removes_candidate(self) -> None:
        backend = FakeBackend(
            hits=[
                [RawSearchHit("p1", 0.9, payload("child-1", "parent-1", content="现场版录音"))],
                [],
            ]
        )
        service = HybridRetrievalService(
            binding(),
            encoder=FakeEncoder(),
            backend=backend,
        )

        result = await service.retrieve(plan(exclusions=["现场版"]))

        self.assertEqual(result.status, RetrievalStatus.EMPTY)
        self.assertIn("post_exclusions_applied:1", result.warnings)

    async def test_index_profile_mismatch_fails_closed_before_io(self) -> None:
        backend = FakeBackend()
        other = profile().model_copy(update={"collection_name": "other"})
        service = HybridRetrievalService(
            binding(),
            encoder=FakeEncoder(),
            backend=backend,
        )

        result = await service.retrieve(plan(index_profile=other))

        self.assertEqual(result.status, RetrievalStatus.UNAVAILABLE)
        self.assertEqual(backend.validation_calls, 0)
        self.assertEqual(backend.search_calls, 0)

    async def test_invalid_payload_is_dropped_without_failing_request(self) -> None:
        backend = FakeBackend(
            hits=[
                [RawSearchHit("bad", 0.9, {"chunk_id": "missing-fields"})],
                [],
            ]
        )
        service = HybridRetrievalService(
            binding(),
            encoder=FakeEncoder(),
            backend=backend,
        )

        result = await service.retrieve(plan())

        self.assertEqual(result.status, RetrievalStatus.EMPTY)
        self.assertIn("invalid_retrieval_payloads:1", result.warnings)

    async def test_document_cap_prioritizes_a_diverse_candidate_before_refill(self) -> None:
        dense_hits = [
            RawSearchHit(
                f"p{index}",
                1 - index / 10,
                payload(
                    f"child-{index}",
                    f"parent-{index}",
                    doc_id="doc-1" if index < 4 else "doc-2",
                    source_id=f"source-{index}",
                ),
            )
            for index in range(5)
        ]
        backend = FakeBackend(hits=[dense_hits, []])
        service = HybridRetrievalService(
            binding(),
            encoder=FakeEncoder(),
            backend=backend,
        )

        result = await service.retrieve(plan())

        self.assertEqual(
            [item.doc_id for item in result.candidates],
            ["doc-1", "doc-1", "doc-2", "doc-1", "doc-1"],
        )
        self.assertIn("selection_caps_relaxed_for_recall", result.warnings)

    async def test_all_channel_failures_are_unavailable_and_safe(self) -> None:
        backend = FakeBackend(errors=["dense_query_failed", "lexical_query_failed"])
        service = HybridRetrievalService(
            binding(),
            encoder=FakeEncoder(),
            backend=backend,
        )

        result = await service.retrieve(plan())

        self.assertEqual(result.status, RetrievalStatus.UNAVAILABLE)
        self.assertIn("all_retrieval_channels_failed", result.warnings)
        self.assertNotIn("private", result.model_dump_json())

    async def test_backend_exception_becomes_typed_unavailable_result(self) -> None:
        backend = FakeBackend(search_error=RuntimeError("private qdrant detail"))
        service = HybridRetrievalService(
            binding(),
            encoder=FakeEncoder(),
            backend=backend,
        )

        result = await service.retrieve(plan())

        self.assertEqual(result.status, RetrievalStatus.UNAVAILABLE)
        self.assertEqual(result.warnings, ["qdrant_query_failed"])
        self.assertNotIn("private", result.model_dump_json())

    async def test_binding_is_validated_only_once(self) -> None:
        backend = FakeBackend(hits=[[], []])
        service = HybridRetrievalService(
            binding(),
            encoder=FakeEncoder(),
            backend=backend,
        )

        await service.retrieve(plan())
        await service.retrieve(plan())

        self.assertEqual(backend.validation_calls, 1)


if __name__ == "__main__":
    unittest.main()

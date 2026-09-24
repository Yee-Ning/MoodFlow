import unittest
from unittest.mock import patch

from app.agent.foundation.subgraphs.worldline.entity_bridge import (
    EntityBridge,
    WorldLineEntityReference,
)
from app.agent.foundation.subgraphs.worldline.genre_executor import (
    GenreExecutor,
    GenreExecutorError,
    GenreExecutorInput,
    GenreSearchPlanFactory,
)
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicKnowledgeDocument,
    ResolvedEntity,
    WorldLineEdge,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
)
from app.rag.knowledge.entity_linking.models import (
    EntityRegistryDocument,
    EntityType as KnowledgeEntityType,
    RegistryEntity,
)
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.query_understanding.models import (
    CatalogReference,
    ContextResolution,
    ContextResolutionStatus,
    MetadataFilter,
    QueryAnalysis,
    QueryConstraints,
    QueryEntityStatus,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    ResolvedQueryEntity,
    RetrievalStrategy,
    SearchChannel,
    SearchPlan,
)


def search_plan() -> SearchPlan:
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query="查找 Hip-Hop 歌手",
        standalone_query="查找 Hip-Hop 歌手",
        query_types=[QueryType.RELATIONSHIP],
        entities=[
            ResolvedQueryEntity(
                surface="Hip-Hop",
                canonical_name="Hip-Hop",
                entity_type=KnowledgeEntityType.GENRE,
                knowledge_entity_id="genre:hip_hop",
                status=QueryEntityStatus.RESOLVED,
                confidence=1,
            )
        ],
        queries=[
            QueryVariant(
                text="查找 Hip-Hop 歌手",
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
    )


class Planner:
    async def build(self, genre):
        return search_plan()


class QueryService:
    def __init__(self):
        self.entity = None

    async def resolve_context(self, query):
        return ContextResolution(
            status=ContextResolutionStatus.RESOLVED,
            dependency="standalone",
            original_query=query,
            standalone_query=query,
        )

    async def analyze(self, context, entities):
        self.entity = entities[0]
        return QueryAnalysis(
            original_query=context.original_query,
            standalone_query=context.standalone_query,
            query_types=[QueryType.RELATIONSHIP],
            entities=entities,
            constraints=QueryConstraints(
                entity_ids=["genre:hip_hop"],
                entity_types=[KnowledgeEntityType.GENRE],
            ),
            context=context,
            confidence=1,
        )

    def plan(self, analysis):
        return search_plan().model_copy(
            update={"entities": analysis.entities}
        )


class Retriever:
    def __init__(self, documents=None, *, fail=False):
        self.documents = documents or []
        self.fail = fail
        self.anchor = None
        self.plan = None

    async def retrieve(self, entity, plan):
        self.anchor = entity
        self.plan = plan
        if self.fail:
            raise RuntimeError("retrieval failed")
        return self.documents


class MusicService:
    def __init__(self, candidates=None, *, fail=False):
        self.candidates = candidates or []
        self.fail = fail

    async def search_entities(self, query, expected_entity_type, *, limit=5):
        if self.fail:
            raise RuntimeError("catalog failed")
        return self.candidates

    async def load_entity_facts(self, entity):
        raise AssertionError("genre executor must not load catalog facts")


def knowledge_registry() -> EntityRegistry:
    return EntityRegistry(
        EntityRegistryDocument(
            registry_version="v99",
            entities=[
                RegistryEntity(
                    entity_id="artist:source_artist",
                    entity_type=KnowledgeEntityType.ARTIST,
                    canonical_name="Source Artist",
                ),
                RegistryEntity(
                    entity_id="artist:new_artist",
                    entity_type=KnowledgeEntityType.ARTIST,
                    canonical_name="New Artist",
                    aliases=["New Artist Alias"],
                ),
            ],
        )
    )


def reference(
    node_id: str,
    knowledge_id: str,
    name: str,
) -> WorldLineEntityReference:
    if knowledge_id.startswith("genre:"):
        return WorldLineEntityReference(
            node_id=node_id,
            node_type=WorldLineNodeType.GENRE,
            canonical_name=name,
            knowledge_entity_id=knowledge_id,
        )
    raw_id = node_id.rsplit(":", 1)[-1]
    return WorldLineEntityReference(
        node_id=node_id,
        node_type=WorldLineNodeType.ARTIST,
        canonical_name=name,
        knowledge_entity_id=knowledge_id,
        catalog_reference=CatalogReference(
            provider="netease",
            entity_id=raw_id,
            entity_type=KnowledgeEntityType.ARTIST,
            name=name,
        ),
    )


def current_worldline():
    source_reference = reference(
        "netease:artist:1",
        "artist:source_artist",
        "Source Artist",
    )
    genre_reference = reference(
        "genre:hip_hop",
        "genre:hip_hop",
        "Hip-Hop",
    )
    trigger = EvidenceSource(
        id="knowledge:rel:trigger",
        source_type=EvidenceType.KNOWLEDGE,
        title="Source Artist 与 Hip-Hop",
        source="test-knowledge",
        excerpt="Source Artist 属于 Hip-Hop。",
        metadata={
            "claim_id": "claim:trigger",
            "parent_id": "trigger-parent",
            "child_id": "trigger-child",
            "validator_version": "v1",
            "relation_key": (
                "netease:artist:1|genre:hip_hop|associated_with"
            ),
        },
    )
    graph = WorldLineGraph(
        root="netease:artist:1",
        nodes=[
            WorldLineNode(
                id=source_reference.node_id,
                type=WorldLineNodeType.ARTIST,
                name=source_reference.canonical_name,
                metadata={
                    "entity_reference": source_reference.model_dump(mode="json")
                },
            ),
            WorldLineNode(
                id=genre_reference.node_id,
                type=WorldLineNodeType.GENRE,
                name=genre_reference.canonical_name,
                metadata={
                    "entity_reference": genre_reference.model_dump(mode="json"),
                    "registry_version": "v2",
                    "executable": True,
                    "expandable": True,
                },
            ),
        ],
        edges=[
            WorldLineEdge(
                source=source_reference.node_id,
                target=genre_reference.node_id,
                relation=WorldLineRelation.ASSOCIATED_WITH,
                evidence_ids=[trigger.id],
            )
        ],
    )
    return graph, [trigger], source_reference, genre_reference


def document(
    *,
    confidence: float = 0.94,
    sufficiency: str = "sufficient",
    artist_id: str = "artist:new_artist",
    genre_id: str = "genre:hip_hop",
) -> MusicKnowledgeDocument:
    span = "New Artist 的音乐风格属于 Hip-Hop。"
    return MusicKnowledgeDocument(
        id="genre-parent-1",
        title="Hip-Hop artist evidence",
        content=f"背景。{span}更多内容。",
        source="test-knowledge",
        url="https://example.com/genre",
        metadata={
            "parent_id": "genre-parent-1",
            "chunk_id": "genre-child-1",
            "context_version": "context-v1",
            "retrieval_version": "retrieval-v1",
            "sufficiency_status": sufficiency,
            "supported_claims": [
                {
                    "claim_id": "claim:000000000000000000000099",
                    "statement": "New Artist 具有 Hip-Hop 风格",
                    "subject_entity_id": artist_id,
                    "predicate": "has_genre",
                    "object_entity_id": genre_id,
                    "source_chunk_id": "genre-child-1",
                    "evidence_span": span,
                    "extraction_confidence": confidence,
                    "verification_confidence": confidence,
                }
            ],
        },
    )


class GenreExecutorTests(unittest.IsolatedAsyncioTestCase):
    def build_executor(self, documents=None, candidates=None, **kwargs):
        registry = GenreVocabularyRegistry.load()
        bridge = EntityBridge(knowledge_registry(), registry)
        retriever = Retriever(documents, fail=kwargs.get("retrieval_fail", False))
        music = MusicService(
            candidates,
            fail=kwargs.get("catalog_fail", False),
        )
        executor = GenreExecutor(
            registry=registry,
            entity_bridge=bridge,
            planner=Planner(),
            retriever=retriever,
            music_service=music,
        )
        return executor, retriever

    def executor_input(self) -> GenreExecutorInput:
        graph, sources, source_reference, genre_reference = current_worldline()
        return GenreExecutorInput(
            session_id="session-1",
            genre_id="genre:hip_hop",
            genre_name="Hip-Hop",
            genre_reference=genre_reference,
            source_artist_reference=source_reference,
            trigger_evidence_ids=["knowledge:rel:trigger"],
            current_graph=graph,
            current_sources=sources,
            visited_nodes=[graph.root],
            exploration_depth=0,
        )

    async def test_projects_evidence_backed_catalog_artist(self) -> None:
        catalog_artist = ResolvedEntity(
            entity_id="netease:artist:2",
            entity_type=EntityType.ARTIST,
            name="New Artist",
            artist="New Artist",
            source="netease",
        )
        executor, retriever = self.build_executor(
            [document()],
            [catalog_artist],
        )

        result = await executor.execute(self.executor_input())

        self.assertEqual(len(result.candidate_artists), 1)
        self.assertEqual(len(result.accepted_artists), 1)
        self.assertEqual(
            result.accepted_artists[0].reference.node_id,
            "netease:artist:2",
        )
        self.assertEqual(result.delta_graph.root, "genre:hip_hop")
        self.assertEqual(
            {node.id for node in result.delta_graph.nodes},
            {"genre:hip_hop", "netease:artist:2"},
        )
        edge = result.delta_graph.edges[0]
        self.assertEqual(edge.source, "genre:hip_hop")
        self.assertEqual(edge.target, "netease:artist:2")
        self.assertEqual(edge.relation, WorldLineRelation.ASSOCIATED_WITH)
        self.assertEqual(edge.evidence_ids, result.evidence_ids)
        self.assertEqual(result.delta_sources[0].excerpt, document().metadata[
            "supported_claims"
        ][0]["evidence_span"])
        self.assertEqual(
            result.delta_sources[0].metadata["claim_subject_entity_id"],
            "artist:new_artist",
        )
        self.assertEqual(retriever.anchor.entity_id, "netease:artist:1")
        self.assertEqual(
            retriever.plan.entities[0].knowledge_entity_id,
            "genre:hip_hop",
        )

    async def test_search_plan_factory_uses_controlled_genre_identity(self) -> None:
        query_service = QueryService()
        factory = GenreSearchPlanFactory(query_service)
        _, _, _, genre_reference = current_worldline()

        plan = await factory.build(genre_reference)

        self.assertEqual(
            plan.entities[0].knowledge_entity_id,
            "genre:hip_hop",
        )
        self.assertIsNone(plan.entities[0].catalog_reference)
        self.assertEqual(
            query_service.entity.knowledge_entity_id,
            "genre:hip_hop",
        )

    async def test_rejects_missing_evidence_and_unresolved_catalog_artist(self) -> None:
        insufficient, _ = self.build_executor(
            [document(sufficiency="insufficient")],
            [],
        )
        unresolved, _ = self.build_executor([document()], [])

        insufficient_result = await insufficient.execute(self.executor_input())
        unresolved_result = await unresolved.execute(self.executor_input())

        self.assertEqual(insufficient_result.accepted_artists, [])
        self.assertEqual(
            insufficient_result.rejected_reason,
            {"insufficient_context": 1},
        )
        self.assertEqual(unresolved_result.accepted_artists, [])
        self.assertEqual(
            unresolved_result.rejected_reason,
            {"catalog_artist_not_found": 1},
        )
        self.assertEqual(unresolved_result.delta_graph.nodes[0].id, "genre:hip_hop")
        self.assertEqual(unresolved_result.delta_graph.edges, [])

    async def test_rejects_current_artist_and_wrong_genre_claim(self) -> None:
        executor, _ = self.build_executor(
            [document(artist_id="artist:source_artist")],
            [],
        )
        wrong_genre, _ = self.build_executor(
            [document(genre_id="genre:jazz")],
            [],
        )

        result = await executor.execute(self.executor_input())
        wrong_genre_result = await wrong_genre.execute(self.executor_input())

        self.assertEqual(len(result.candidate_artists), 1)
        self.assertEqual(result.accepted_artists, [])
        self.assertEqual(
            result.rejected_reason,
            {"artist_already_in_graph": 1},
        )
        self.assertEqual(wrong_genre_result.candidate_artists, [])

    async def test_external_failures_raise_without_returning_delta(self) -> None:
        retrieval_failure, _ = self.build_executor(retrieval_fail=True)
        catalog_failure, _ = self.build_executor(
            [document()],
            catalog_fail=True,
        )

        with self.assertRaises(GenreExecutorError) as retrieval_error:
            await retrieval_failure.execute(self.executor_input())
        with self.assertRaises(GenreExecutorError) as catalog_error:
            await catalog_failure.execute(self.executor_input())

        self.assertEqual(retrieval_error.exception.code, "genre_retrieval_failed")
        self.assertEqual(catalog_error.exception.code, "catalog_artist_search_failed")

    async def test_projection_validation_failure_uses_executor_error(self) -> None:
        catalog_artist = ResolvedEntity(
            entity_id="netease:artist:2",
            entity_type=EntityType.ARTIST,
            name="New Artist",
            artist="New Artist",
            source="netease",
        )
        executor, _ = self.build_executor([document()], [catalog_artist])

        with patch.object(
            executor,
            "_project",
            side_effect=ValueError("invalid graph"),
        ):
            with self.assertRaises(GenreExecutorError) as error:
                await executor.execute(self.executor_input())

        self.assertEqual(error.exception.code, "genre_projection_invalid")

    async def test_requires_trigger_evidence_and_executable_registry_genre(self) -> None:
        executor, _ = self.build_executor([], [])
        value = self.executor_input()
        missing_trigger = value.model_copy(
            update={"trigger_evidence_ids": ["knowledge:rel:missing"]}
        )
        jazz_reference = reference("genre:jazz", "genre:jazz", "Jazz")
        jazz = value.model_copy(
            update={
                "genre_id": "genre:jazz",
                "genre_name": "Jazz",
                "genre_reference": jazz_reference,
            }
        )

        with self.assertRaises(GenreExecutorError) as trigger_error:
            await executor.execute(missing_trigger)
        with self.assertRaises(GenreExecutorError) as genre_error:
            await executor.execute(jazz)

        self.assertEqual(
            trigger_error.exception.code,
            "genre_trigger_evidence_missing",
        )
        self.assertEqual(genre_error.exception.code, "genre_not_executable")


if __name__ == "__main__":
    unittest.main()

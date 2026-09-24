import unittest

from app.agent.foundation.subgraphs.worldline.entity_bridge import (
    CatalogResolutionStatus,
    EntityBridge,
)
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    ResolvedEntity,
    WorldLineNodeType,
)
from app.rag.knowledge.entity_linking.models import (
    EntityRegistryDocument,
    EntityType as KnowledgeEntityType,
    RegistryEntity,
)
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.query_understanding.models import (
    CatalogReference,
    MetadataFilter,
    QueryEntityStatus,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    ResolvedQueryEntity,
    RetrievalStrategy,
    SearchChannel,
    SearchPlan,
)


def knowledge_registry() -> EntityRegistry:
    return EntityRegistry(
        EntityRegistryDocument(
            registry_version="v99",
            entities=[
                RegistryEntity(
                    entity_id="artist:test_artist",
                    entity_type=KnowledgeEntityType.ARTIST,
                    canonical_name="Test Artist",
                    aliases=["测试歌手"],
                ),
                RegistryEntity(
                    entity_id="artist:other_artist",
                    entity_type=KnowledgeEntityType.ARTIST,
                    canonical_name="Other Artist",
                ),
            ],
        )
    )


def artist() -> ResolvedEntity:
    return ResolvedEntity(
        entity_id="netease:artist:42",
        entity_type=EntityType.ARTIST,
        name="Test Artist",
        artist="Test Artist",
        source="netease",
    )


def search_plan(*, include_mapping: bool = True) -> SearchPlan:
    entities = []
    if include_mapping:
        entities.append(
            ResolvedQueryEntity(
                surface="Test Artist",
                canonical_name="Test Artist",
                entity_type=KnowledgeEntityType.ARTIST,
                knowledge_entity_id="artist:test_artist",
                catalog_reference=CatalogReference(
                    provider="netease",
                    entity_id="42",
                    entity_type=KnowledgeEntityType.ARTIST,
                    name="Test Artist",
                ),
                aliases=["测试歌手"],
                status=QueryEntityStatus.RESOLVED,
                confidence=1,
            )
        )
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query="探索 Test Artist",
        standalone_query="探索 Test Artist",
        query_types=[QueryType.EXPLORATORY],
        entities=entities,
        queries=[
            QueryVariant(
                text="探索 Test Artist",
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


class WorldLineEntityBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = EntityBridge(
            knowledge_registry(),
            GenreVocabularyRegistry.load(),
        )

    def test_maps_knowledge_artist_to_catalog_identity(self) -> None:
        result = self.bridge.resolve_artist(
            "artist:test_artist",
            search_plan(),
        )

        self.assertEqual(result.status, CatalogResolutionStatus.RESOLVED)
        self.assertIsNotNone(result.reference)
        assert result.reference is not None
        self.assertEqual(result.reference.node_id, "netease:artist:42")
        self.assertEqual(
            result.reference.knowledge_entity_id,
            "artist:test_artist",
        )
        self.assertEqual(result.reference.catalog_reference.entity_id, "42")

    def test_preserves_dual_identity_for_catalog_artist(self) -> None:
        result = self.bridge.reference_for_catalog_artist(
            artist(),
            search_plan(),
        )

        self.assertEqual(result.status, CatalogResolutionStatus.RESOLVED)
        assert result.reference is not None
        self.assertEqual(result.reference.node_type, WorldLineNodeType.ARTIST)
        self.assertEqual(result.reference.node_id, artist().entity_id)
        self.assertEqual(
            result.reference.knowledge_entity_id,
            "artist:test_artist",
        )

    def test_resolves_artist_and_genre_aliases(self) -> None:
        artist_result = self.bridge.resolve_artist("测试歌手", search_plan())
        genre_result = self.bridge.resolve_genre("爵士嘻哈")

        self.assertEqual(
            artist_result.status,
            CatalogResolutionStatus.RESOLVED,
        )
        self.assertEqual(genre_result.status, CatalogResolutionStatus.RESOLVED)
        assert genre_result.reference is not None
        self.assertEqual(genre_result.reference.node_id, "genre:jazz_hip_hop")
        self.assertIsNone(genre_result.reference.catalog_reference)

    def test_rejects_unmapped_or_unregistered_entities(self) -> None:
        unmapped_artist = self.bridge.resolve_artist(
            "artist:test_artist",
            search_plan(include_mapping=False),
        )
        unknown_genre = self.bridge.resolve_genre("Future Bass")

        self.assertEqual(
            unmapped_artist.status,
            CatalogResolutionStatus.UNRESOLVED,
        )
        self.assertIsNone(unmapped_artist.reference)
        self.assertEqual(
            unknown_genre.status,
            CatalogResolutionStatus.UNRESOLVED,
        )
        self.assertIsNone(unknown_genre.reference)


if __name__ == "__main__":
    unittest.main()

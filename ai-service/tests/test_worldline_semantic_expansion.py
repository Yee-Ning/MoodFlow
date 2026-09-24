import unittest

from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicKnowledgeDocument,
    ResolvedEntity,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
    WorldLineSessionResult,
)
from app.agent.foundation.subgraphs.worldline.semantic_expansion import (
    SemanticExpansionService,
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


def registry() -> EntityRegistry:
    return EntityRegistry(
        EntityRegistryDocument(
            registry_version="v99",
            entities=[
                RegistryEntity(
                    entity_id="artist:test_artist",
                    entity_type=KnowledgeEntityType.ARTIST,
                    canonical_name="测试歌手",
                ),
                RegistryEntity(
                    entity_id="genre:hip_hop",
                    entity_type=KnowledgeEntityType.GENRE,
                    canonical_name="Hip-Hop",
                ),
            ],
        )
    )


def artist() -> ResolvedEntity:
    return ResolvedEntity(
        entity_id="netease:artist:42",
        entity_type=EntityType.ARTIST,
        name="测试歌手",
        artist="测试歌手",
        source="netease",
    )


def search_plan() -> SearchPlan:
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query="探索测试歌手",
        standalone_query="探索测试歌手",
        query_types=[QueryType.EXPLORATORY],
        entities=[
            ResolvedQueryEntity(
                surface="测试歌手",
                canonical_name="测试歌手",
                entity_type=KnowledgeEntityType.ARTIST,
                knowledge_entity_id="artist:test_artist",
                catalog_reference=CatalogReference(
                    provider="netease",
                    entity_id="42",
                    entity_type=KnowledgeEntityType.ARTIST,
                    name="测试歌手",
                ),
                status=QueryEntityStatus.RESOLVED,
                confidence=1,
            )
        ],
        queries=[
            QueryVariant(
                text="探索测试歌手",
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


def directory_graph() -> tuple[WorldLineGraph, list[EvidenceSource]]:
    root = artist()
    source = EvidenceSource(
        id="music-api:artist:42",
        source_type=EvidenceType.MUSIC_API,
        title="测试歌手目录",
        source="test-catalog",
    )
    return (
        WorldLineGraph(
            root=root.entity_id,
            nodes=[
                WorldLineNode(
                    id=root.entity_id,
                    type=WorldLineNodeType.ARTIST,
                    name=root.name,
                )
            ],
        ),
        [source],
    )


def knowledge_document(
    index: int = 1,
    *,
    sufficiency: str = "sufficient",
    evidence_span: str = "测试歌手的音乐风格属于 Hip-Hop。",
    content: str | None = None,
    confidence: float = 0.94,
    subject_id: str = "artist:test_artist",
    predicate: str = "has_genre",
    target_id: str = "genre:hip_hop",
) -> MusicKnowledgeDocument:
    child_id = f"child-{index}"
    return MusicKnowledgeDocument(
        id=f"parent-{index}",
        title=f"风格资料 {index}",
        content=content or f"背景。{evidence_span}更多资料。",
        source="test-knowledge",
        url=f"https://example.com/{index}",
        metadata={
            "parent_id": f"parent-{index}",
            "chunk_id": child_id,
            "context_version": "context-v1",
            "retrieval_version": "retrieval-v1",
            "sufficiency_status": sufficiency,
            "supported_claims": [
                {
                    "claim_id": f"claim:{index:024x}",
                    "statement": "测试歌手具有 Hip-Hop 风格",
                    "subject_entity_id": subject_id,
                    "predicate": predicate,
                    "object_entity_id": target_id,
                    "source_chunk_id": child_id,
                    "evidence_span": evidence_span,
                    "extraction_confidence": confidence,
                    "verification_confidence": confidence,
                }
            ],
        },
    )


class WorldLineSemanticExpansionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SemanticExpansionService(registry())
        self.graph, self.sources = directory_graph()

    def expand(self, documents):
        return self.service.expand_artist_genres(
            artist(),
            search_plan(),
            self.graph,
            self.sources,
            documents,
        )

    def test_projects_grounded_artist_genre_relation(self) -> None:
        document = knowledge_document()

        result = self.expand([document])

        self.assertEqual(len(result.accepted), 1)
        genre = next(node for node in result.graph.nodes if node.type is WorldLineNodeType.GENRE)
        edge = next(
            edge
            for edge in result.graph.edges
            if edge.relation is WorldLineRelation.ASSOCIATED_WITH
        )
        self.assertEqual(genre.id, "genre:hip_hop")
        self.assertEqual(edge.source, artist().entity_id)
        self.assertEqual(edge.target, genre.id)
        self.assertEqual(len(edge.evidence_ids), 1)
        semantic_source = next(
            source for source in result.sources if source.id == edge.evidence_ids[0]
        )
        self.assertTrue(semantic_source.id.startswith("knowledge:rel:"))
        self.assertEqual(
            semantic_source.excerpt,
            "测试歌手的音乐风格属于 Hip-Hop。",
        )
        self.assertEqual(semantic_source.metadata["claim_id"], "claim:000000000000000000000001")
        self.assertEqual(result.graph.root, artist().entity_id)

    def test_no_supported_evidence_leaves_graph_unchanged(self) -> None:
        result = self.expand([])

        self.assertEqual(result.graph, self.graph)
        self.assertEqual(result.sources, self.sources)
        self.assertEqual(result.accepted, [])

    def test_rejects_insufficient_or_unlocatable_evidence(self) -> None:
        insufficient = self.expand(
            [knowledge_document(sufficiency="insufficient")]
        )
        missing_span = self.expand(
            [knowledge_document(content="正文中没有声明片段。")]
        )

        self.assertEqual(insufficient.accepted, [])
        self.assertIn(
            "insufficient_context",
            insufficient.rejected[0].rejection_reasons,
        )
        self.assertEqual(missing_span.accepted, [])
        self.assertIn(
            "evidence_span_missing",
            missing_span.rejected[0].rejection_reasons,
        )

    def test_rejects_low_confidence_relation(self) -> None:
        result = self.expand([knowledge_document(confidence=0.84)])

        self.assertEqual(result.accepted, [])
        self.assertIn(
            "confidence_below_threshold",
            result.rejected[0].rejection_reasons,
        )

    def test_ignores_unsupported_predicate_and_unknown_genre(self) -> None:
        unsupported = self.expand(
            [knowledge_document(predicate="contains_music_element")]
        )
        unknown = self.expand(
            [knowledge_document(target_id="genre:not_registered")]
        )

        self.assertEqual(unsupported.accepted, [])
        self.assertEqual(unsupported.graph, self.graph)
        self.assertEqual(unknown.accepted, [])
        self.assertEqual(unknown.graph, self.graph)

    def test_deduplicates_relation_and_unions_independent_evidence(self) -> None:
        first = knowledge_document(1)
        first = first.model_copy(
            update={
                "metadata": {
                    **first.metadata,
                    "supported_claims": [
                        *first.metadata["supported_claims"],
                        *first.metadata["supported_claims"],
                    ],
                }
            }
        )
        second = knowledge_document(
            2,
            evidence_span="测试歌手也被归类为 Hip-Hop 音乐人。",
        )

        result = self.expand([first, second])

        edges = [
            edge
            for edge in result.graph.edges
            if edge.relation is WorldLineRelation.ASSOCIATED_WITH
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(len(edges[0].evidence_ids), 2)
        self.assertEqual(len(set(edges[0].evidence_ids)), 2)

    def test_semantic_source_can_be_returned_as_narrative_citation(self) -> None:
        result = self.expand([knowledge_document()])
        relation_source = next(
            source
            for source in result.sources
            if source.id.startswith("knowledge:rel:")
        )

        worldline = WorldLineSessionResult(
            root_entity=artist(),
            graph=result.graph,
            narrative=f"测试歌手与 Hip-Hop 相关联。[{relation_source.id}]",
            branches=[],
            sources=result.sources,
            citations=[relation_source],
        )

        self.assertEqual(worldline.citations[0], relation_source)


if __name__ == "__main__":
    unittest.main()

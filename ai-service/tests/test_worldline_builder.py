import unittest

from app.agent.foundation.subgraphs.worldline.builder import (
    MAX_WORLDLINE_EDGES,
    MAX_WORLDLINE_NODES,
    build_worldline_graph,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicEntityFacts,
    MusicFact,
    MusicKnowledgeDocument,
    ResolvedEntity,
    WorldLineRelation,
)


def entity(entity_id: int, entity_type: EntityType, name: str) -> ResolvedEntity:
    return ResolvedEntity(
        entity_id=f"netease:{entity_type.value}:{entity_id}",
        entity_type=entity_type,
        name=name,
        artist="测试歌手",
        album="测试专辑" if entity_type is EntityType.SONG else None,
        source="netease",
    )


class WorldLineBuilderTests(unittest.TestCase):
    def test_builds_catalog_nodes_and_grounded_edges(self) -> None:
        song = entity(1, EntityType.SONG, "测试歌曲")
        artist = entity(2, EntityType.ARTIST, "测试歌手")
        source = EvidenceSource(
            id="music-api:song:1",
            source_type=EvidenceType.MUSIC_API,
            title="音乐目录实体",
            source="MoodFlow Java Music Service",
        )
        facts = MusicEntityFacts(
            entity=song,
            related_entities=[artist],
            facts=[
                MusicFact(
                    id="fact:1",
                    subject_id=song.entity_id,
                    predicate="performed_by",
                    value="测试歌曲由测试歌手演唱",
                    object_entity_id=artist.entity_id,
                    evidence_ids=[source.id],
                )
            ],
            sources=[source],
        )

        graph, sources = build_worldline_graph(facts, [])

        self.assertEqual(graph.root, song.entity_id)
        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(graph.edges[0].relation, WorldLineRelation.PERFORMED_BY)
        self.assertEqual(graph.edges[0].evidence_ids, [source.id])
        self.assertEqual(sources, [source])

    def test_plain_knowledge_prose_adds_source_but_no_fabricated_edge(self) -> None:
        song = entity(1, EntityType.SONG, "测试歌曲")
        facts = MusicEntityFacts(entity=song)
        document = MusicKnowledgeDocument(
            id="doc-1",
            title="背景资料",
            content="这是一段未经结构化的背景资料。",
            source="test-index",
        )

        graph, sources = build_worldline_graph(facts, [document])

        self.assertEqual(graph.edges, [])
        self.assertEqual(sources[0].id, "knowledge:doc-1")

    def test_structured_knowledge_relationship_is_accepted_with_evidence(self) -> None:
        song = entity(1, EntityType.SONG, "测试歌曲")
        facts = MusicEntityFacts(entity=song)
        document = MusicKnowledgeDocument(
            id="doc-2",
            title="风格资料",
            content="资料明确标注测试歌曲属于测试风格。",
            source="test-index",
            metadata={
                "graph_nodes": [
                    {
                        "id": "genre:test",
                        "type": "genre",
                        "name": "测试风格",
                        "description": "来自知识库的结构化标签",
                        "metadata": {},
                    }
                ],
                "graph_edges": [
                    {
                        "source": song.entity_id,
                        "target": "genre:test",
                        "relation": "belongs_to_genre",
                        "description": "资料明确标注的风格",
                    }
                ],
            },
        )

        graph, _ = build_worldline_graph(facts, [document])

        self.assertEqual(len(graph.nodes), 2)
        self.assertEqual(graph.edges[0].evidence_ids, ["knowledge:doc-2"])

    def test_structured_metadata_cannot_inject_associated_with(self) -> None:
        artist = entity(2, EntityType.ARTIST, "测试歌手")
        document = MusicKnowledgeDocument(
            id="doc-semantic-bypass",
            title="未验证语义关系",
            content="这条结构化边没有经过语义验证器。",
            source="test-index",
            metadata={
                "graph_nodes": [
                    {
                        "id": "genre:test",
                        "type": "genre",
                        "name": "测试风格",
                    }
                ],
                "graph_edges": [
                    {
                        "source": artist.entity_id,
                        "target": "genre:test",
                        "relation": "associated_with",
                    }
                ],
            },
        )

        graph, _ = build_worldline_graph(
            MusicEntityFacts(entity=artist),
            [document],
        )

        self.assertFalse(
            any(
                edge.relation is WorldLineRelation.ASSOCIATED_WITH
                for edge in graph.edges
            )
        )

    def test_graph_size_is_bounded(self) -> None:
        root = entity(1, EntityType.ARTIST, "测试歌手")
        related = [
            entity(index, EntityType.SONG, f"歌曲 {index}")
            for index in range(2, 22)
        ]
        facts = MusicEntityFacts(
            entity=root,
            related_entities=related,
            facts=[
                MusicFact(
                    id=f"fact:{index}",
                    subject_id=item.entity_id,
                    predicate="performed_by",
                    value=f"{item.name} 由测试歌手演唱",
                    object_entity_id=root.entity_id,
                )
                for index, item in enumerate(related)
            ],
        )

        graph, _ = build_worldline_graph(facts, [])

        self.assertLessEqual(len(graph.nodes), MAX_WORLDLINE_NODES)
        self.assertLessEqual(len(graph.edges), MAX_WORLDLINE_EDGES)


if __name__ == "__main__":
    unittest.main()

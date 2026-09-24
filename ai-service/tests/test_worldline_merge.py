import unittest

from app.agent.foundation.subgraphs.worldline.merge import WorldLineMergeError, merge_worldline_graph
from app.agent.foundation.subgraphs.worldline.models import (
    EvidenceSource, EvidenceType, WorldLineEdge, WorldLineGraph,
    WorldLineNode, WorldLineNodeType, WorldLineRelation,
)


def source(source_id):
    return EvidenceSource(id=source_id, source_type=EvidenceType.MUSIC_API,
                          title=source_id, source="test")


def node(node_id, kind):
    return WorldLineNode(id=node_id, type=kind, name=node_id)


class WorldLineMergeTests(unittest.TestCase):
    def test_deduplicates_nodes_edges_and_unions_evidence(self):
        song = node("netease:song:1", WorldLineNodeType.SONG)
        artist = node("netease:artist:2", WorldLineNodeType.ARTIST)
        album = node("netease:album:3", WorldLineNodeType.ALBUM)
        first = WorldLineGraph(root=song.id, nodes=[song, artist], edges=[
            WorldLineEdge(source=song.id, target=artist.id,
                          relation=WorldLineRelation.PERFORMED_BY, evidence_ids=["s1"])
        ])
        second = WorldLineGraph(root=artist.id, nodes=[artist, song, album], edges=[
            WorldLineEdge(source=song.id, target=artist.id,
                          relation=WorldLineRelation.PERFORMED_BY, evidence_ids=["s2"]),
            WorldLineEdge(source=artist.id, target=album.id,
                          relation=WorldLineRelation.RELATED_TO, evidence_ids=["s2"]),
        ])
        merged, sources = merge_worldline_graph(first, second, [source("s1")], [source("s2")],
                                                 target_node_id=artist.id)
        self.assertEqual(merged.root, song.id)
        self.assertEqual(len(merged.nodes), 3)
        self.assertEqual(len(merged.edges), 2)
        self.assertEqual(merged.edges[0].evidence_ids, ["s1", "s2"])
        self.assertEqual([s.id for s in sources], ["s1", "s2"])
        self.assertEqual(first.edges[0].evidence_ids, ["s1"])

    def test_type_conflict_does_not_mutate_old_graph(self):
        old_node = node("netease:artist:2", WorldLineNodeType.ARTIST)
        old = WorldLineGraph(root=old_node.id, nodes=[old_node])
        new = WorldLineGraph(root=old_node.id, nodes=[node(old_node.id, WorldLineNodeType.ALBUM)])
        with self.assertRaisesRegex(WorldLineMergeError, "node_type_conflict"):
            merge_worldline_graph(old, new, [], [], target_node_id=old_node.id)
        self.assertEqual(old.nodes[0].type, WorldLineNodeType.ARTIST)

    def test_missing_evidence_rejected(self):
        song = node("netease:song:1", WorldLineNodeType.SONG)
        artist = node("netease:artist:2", WorldLineNodeType.ARTIST)
        old = WorldLineGraph(root=song.id, nodes=[song, artist])
        new = WorldLineGraph(root=artist.id, nodes=[artist, song], edges=[
            WorldLineEdge(source=song.id, target=artist.id,
                          relation=WorldLineRelation.PERFORMED_BY, evidence_ids=["missing"])
        ])
        with self.assertRaisesRegex(WorldLineMergeError, "missing_edge_evidence"):
            merge_worldline_graph(old, new, [], [], target_node_id=artist.id)

    def test_semantic_edge_is_deduplicated_and_evidence_is_unioned(self):
        artist = node("netease:artist:2", WorldLineNodeType.ARTIST)
        genre = node("genre:hip_hop", WorldLineNodeType.GENRE)
        old = WorldLineGraph(root=artist.id, nodes=[artist, genre], edges=[
            WorldLineEdge(
                source=artist.id,
                target=genre.id,
                relation=WorldLineRelation.ASSOCIATED_WITH,
                evidence_ids=["relation-source-1"],
            )
        ])
        increment = WorldLineGraph(
            root=artist.id,
            nodes=[artist, genre],
            edges=[
                WorldLineEdge(
                    source=artist.id,
                    target=genre.id,
                    relation=WorldLineRelation.ASSOCIATED_WITH,
                    evidence_ids=["relation-source-2"],
                )
            ],
        )

        merged, _ = merge_worldline_graph(
            old,
            increment,
            [source("relation-source-1")],
            [source("relation-source-2")],
            target_node_id=artist.id,
        )

        self.assertEqual(len(merged.edges), 1)
        self.assertEqual(
            merged.edges[0].evidence_ids,
            ["relation-source-1", "relation-source-2"],
        )
        self.assertEqual(old.edges[0].evidence_ids, ["relation-source-1"])

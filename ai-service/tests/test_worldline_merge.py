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
    def stable_source(self):
        return EvidenceSource(
            id="knowledge:stable", source_type=EvidenceType.KNOWLEDGE,
            title="Published source", source="Publisher", url="https://example.org/source",
            excerpt="Original exact evidence.", metadata={
                "source_id": "source-1", "chunk_id": "child-1", "claim_id": "claim-1",
                "parent_id": "parent-1", "doc_id": "doc-1", "point_id": "point-1",
                "source_content_sha256": "a" * 64, "source_response_sha256": "b" * 64,
                "parent_content_sha256": "c" * 64, "content_sha256": "d" * 64,
                "knowledge_input_hash": "sha256:knowledge", "index_input_hash": "sha256:index",
                "source_release_id": "release-1", "source_locator": "p[2]",
                "exact_span": "Original exact evidence.", "exact_span_start": 10, "exact_span_end": 34,
                "relation_source_contexts": [{"claim_id": "claim-1", "content_sha256": "e" * 64}],
                "matched_child_ids": ["child-1", "child-2", "child-3"],
                "fusion_score": 0.4, "channel_evidence": [{"channel": "dense", "rank": 2}],
                "subquery_ids": ["q0"], "retrieval_legs": ["q0:dense"],
            },
        )

    def merge_same_source(self, previous, increment):
        artist = node("netease:artist:2", WorldLineNodeType.ARTIST)
        graph = WorldLineGraph(root=artist.id, nodes=[artist])
        return merge_worldline_graph(graph, graph, [previous], [increment], target_node_id=artist.id)

    def test_matched_hit_subset_and_query_observations_do_not_change_source_identity(self):
        previous = self.stable_source()
        increment = previous.model_copy(update={"metadata": {
            **previous.metadata, "matched_child_ids": ["child-1", "child-2"],
            "fusion_score": 0.7, "channel_evidence": [{"channel": "lexical", "rank": 9}],
            "subquery_ids": ["q2"], "retrieval_legs": ["q2:lexical"], "context_pack_rank": 4,
        }})
        _, merged = self.merge_same_source(previous, increment)
        self.assertEqual(merged, [previous])
        self.assertEqual(previous.metadata["matched_child_ids"], ["child-1", "child-2", "child-3"])
        self.assertEqual(increment.metadata["matched_child_ids"], ["child-1", "child-2"])

    def test_matched_hit_changes_do_not_hide_span_url_or_provenance_tampering(self):
        previous = self.stable_source()
        for field in ("url", "excerpt", "title", "source"):
            with self.subTest(field=field):
                increment = previous.model_copy(update={field: "changed", "metadata": {
                    **previous.metadata, "matched_child_ids": ["child-1"],
                }})
                with self.assertRaisesRegex(WorldLineMergeError, "source_conflict"):
                    self.merge_same_source(previous, increment)
        protected = {
            k: "changed" for k in previous.metadata
            if k not in {"matched_child_ids", "fusion_score", "channel_evidence", "subquery_ids", "retrieval_legs"}
        }
        protected["relation_source_contexts"] = [{"claim_id": "claim-1", "content_sha256": "f" * 64}]
        for field, value in protected.items():
            with self.subTest(field=field):
                increment = previous.model_copy(update={"metadata": {
                    **previous.metadata, "matched_child_ids": ["child-1"], field: value,
                }})
                with self.assertRaisesRegex(WorldLineMergeError, "source_conflict"):
                    self.merge_same_source(previous, increment)

    def test_unindexed_document_hash_is_not_treated_as_query_observation(self):
        previous = source("unindexed").model_copy(update={"metadata": {"context_document_sha256": "a" * 64}})
        increment = previous.model_copy(update={"metadata": {"context_document_sha256": "b" * 64}})
        with self.assertRaisesRegex(WorldLineMergeError, "source_conflict"):
            self.merge_same_source(previous, increment)

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

import unittest

from app.agent.foundation.subgraphs.worldline.genre_actions import (
    GenreActionError,
    GenreActionPolicy,
)
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    ResolvedEntity,
    WorldLineBranch,
    WorldLineBranchTarget,
    WorldLineBranchType,
    WorldLineSessionResult,
)
from tests.test_worldline_genre_executor import current_worldline


class GenreActionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = GenreActionPolicy(GenreVocabularyRegistry.load())
        self.graph, self.sources, _, _ = current_worldline()

    def result(self) -> WorldLineSessionResult:
        artist = self.graph.nodes[0]
        return WorldLineSessionResult(
            root_entity=ResolvedEntity(
                entity_id=artist.id,
                entity_type=EntityType.ARTIST,
                name=artist.name,
                source="netease",
            ),
            graph=self.graph,
            narrative="Source Artist 与 Hip-Hop 相关。[knowledge:rel:trigger]",
            branches=[
                WorldLineBranch(
                    id="branch:artist",
                    title="探索 Source Artist",
                    description="继续探索",
                    branch_type=WorldLineBranchType.ARTIST,
                    target_entity=WorldLineBranchTarget(
                        id=artist.id,
                        type=artist.type,
                        name=artist.name,
                    ),
                )
            ],
            sources=self.sources,
            citations=self.sources,
        )

    def test_enables_registered_genre_only_with_relation_evidence(self) -> None:
        context = self.policy.resolve(
            self.graph,
            self.sources,
            self.graph.nodes[1],
        )

        self.assertEqual(
            context.genre_reference.knowledge_entity_id,
            "genre:hip_hop",
        )
        self.assertEqual(
            context.source_artist_reference.node_id,
            "netease:artist:1",
        )
        self.assertEqual(
            context.trigger_evidence_ids,
            ["knowledge:rel:trigger"],
        )

    def test_rejects_missing_evidence_or_disabled_registry_version(self) -> None:
        with self.assertRaises(GenreActionError) as missing:
            self.policy.resolve(self.graph, [], self.graph.nodes[1])
        disabled = self.graph.nodes[1].model_copy(
            update={
                "metadata": {
                    **self.graph.nodes[1].metadata,
                    "registry_version": "v1",
                }
            }
        )

        with self.assertRaises(GenreActionError) as version:
            self.policy.resolve(self.graph, self.sources, disabled)

        self.assertEqual(missing.exception.code, "genre_evidence_missing")
        self.assertEqual(version.exception.code, "genre_not_executable")

    def test_adds_one_server_genre_branch_before_catalog_branches(self) -> None:
        updated = self.policy.augment_branches(
            self.result(),
            visited_nodes=["netease:artist:1"],
        )

        self.assertEqual(len(updated.branches), 1)
        self.assertEqual(updated.branches[0].branch_type, WorldLineBranchType.GENRE)
        self.assertEqual(updated.branches[0].target_entity.id, "genre:hip_hop")

    def test_visited_genre_is_not_returned_again(self) -> None:
        updated = self.policy.augment_branches(
            self.result(),
            visited_nodes=["netease:artist:1", "genre:hip_hop"],
        )

        self.assertEqual(updated.branches, [])


if __name__ == "__main__":
    unittest.main()

import asyncio
import unittest

from pydantic import ValidationError

from app.agent.foundation.subgraphs.worldline.models import (
    BranchesOutput,
    EntityResolution,
    EntityResolutionStatus,
    EntityType,
    ResolvedEntity,
    WorldLineBranch,
    WorldLineBranchTarget,
    WorldLineBranchType,
    WorldLineEdge,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
)
from app.agent.foundation.subgraphs.worldline.retrieval import (
    EmptyMusicKnowledgeRetriever,
)


def entity() -> ResolvedEntity:
    return ResolvedEntity(
        entity_id="netease:song:185809",
        entity_type=EntityType.SONG,
        name="晴天",
        artist="周杰伦",
        album="叶惠美",
        source="netease",
    )


class WorldLineModelTests(unittest.TestCase):
    def test_resolved_outcome_requires_entity(self) -> None:
        with self.assertRaises(ValidationError):
            EntityResolution(
                status=EntityResolutionStatus.RESOLVED,
                query="晴天",
            )

    def test_ambiguous_outcome_requires_multiple_candidates(self) -> None:
        with self.assertRaises(ValidationError):
            EntityResolution(
                status=EntityResolutionStatus.AMBIGUOUS,
                query="七里香",
                candidates=[entity()],
            )

    def test_graph_rejects_unknown_edge_endpoint(self) -> None:
        with self.assertRaises(ValidationError):
            WorldLineGraph(
                root="song:1",
                nodes=[
                    WorldLineNode(
                        id="song:1",
                        type=WorldLineNodeType.SONG,
                        name="晴天",
                    )
                ],
                edges=[
                    WorldLineEdge(
                        source="song:1",
                        target="artist:missing",
                        relation=WorldLineRelation.PERFORMED_BY,
                    )
                ],
            )

    def test_graph_accepts_valid_nodes_and_edges(self) -> None:
        graph = WorldLineGraph(
            root="song:1",
            nodes=[
                WorldLineNode(
                    id="song:1",
                    type=WorldLineNodeType.SONG,
                    name="晴天",
                ),
                WorldLineNode(
                    id="artist:2",
                    type=WorldLineNodeType.ARTIST,
                    name="周杰伦",
                ),
            ],
            edges=[
                WorldLineEdge(
                    source="song:1",
                    target="artist:2",
                    relation=WorldLineRelation.PERFORMED_BY,
                )
            ],
        )

        self.assertEqual(graph.root, "song:1")
        self.assertEqual(graph.edges[0].relation, WorldLineRelation.PERFORMED_BY)

    def test_structured_branches_require_exactly_three(self) -> None:
        branch = WorldLineBranch(
            id="branch-1",
            title="歌手线",
            description="继续探索歌手。",
            branch_type=WorldLineBranchType.ARTIST,
            target_entity=WorldLineBranchTarget(
                id="artist:2",
                type=WorldLineNodeType.ARTIST,
                name="周杰伦",
            ),
        )
        with self.assertRaises(ValidationError):
            BranchesOutput(branches=[branch, branch])

    def test_empty_retriever_is_explicit_fallback(self) -> None:
        docs = asyncio.run(
            EmptyMusicKnowledgeRetriever().retrieve(entity(), "晴天")
        )

        self.assertEqual(docs, [])


if __name__ == "__main__":
    unittest.main()

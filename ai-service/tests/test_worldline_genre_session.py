import unittest

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.subgraphs.worldline.entity_bridge import EntityBridge
from app.agent.foundation.subgraphs.worldline.genre_executor import GenreExecutor
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    ResolvedEntity,
)
from app.agent.foundation.subgraphs.worldline.session import WorldLineSessionService
from app.agent.foundation.subgraphs.worldline.service import WorldLineExecutionError
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from tests.test_worldline_genre_executor import (
    MusicService,
    Planner,
    Retriever,
    document,
)
from tests.test_worldline_graph import (
    FakeMusicService,
    SemanticCitationGenerator,
    SemanticDocsRetriever,
    entity,
)


def genre_executor(*, documents=None, candidates=None) -> GenreExecutor:
    registry = GenreVocabularyRegistry.load()
    return GenreExecutor(
        registry=registry,
        entity_bridge=EntityBridge(EntityRegistry.load(), registry),
        planner=Planner(),
        retriever=Retriever(documents),
        music_service=MusicService(candidates),
    )


class WorldLineGenreSessionTests(unittest.IsolatedAsyncioTestCase):
    def service(self, executor: GenreExecutor) -> WorldLineSessionService:
        jay = entity(2, EntityType.ARTIST, "周杰伦")
        turn_graph = create_worldline_subgraph(
            music_service=FakeMusicService(jay),
            retriever=SemanticDocsRetriever(),
            generator=SemanticCitationGenerator(),
        )
        return WorldLineSessionService(
            create_memory_checkpointer(),
            turn_graph=turn_graph,
            genre_executor=executor,
        )

    async def test_artist_genre_artist_session_loop(self) -> None:
        eason = ResolvedEntity(
            entity_id="netease:artist:9",
            entity_type=EntityType.ARTIST,
            name="陈奕迅",
            artist="陈奕迅",
            source="netease",
        )
        service = self.service(
            genre_executor(
                documents=[
                    document_for_artist(
                        "artist:eason_chan",
                        "陈奕迅的音乐风格属于 Hip-Hop。",
                    )
                ],
                candidates=[eason],
            )
        )

        created = await service.create("探索歌手周杰伦的世界线")
        genre_action = next(
            action
            for action in created.available_actions
            if action.target_node_id == "genre:hip_hop"
        )
        expanded = await service.explore(created.session_id, genre_action.id)

        self.assertTrue(genre_action.enabled)
        self.assertEqual(expanded.current_node, "genre:hip_hop")
        self.assertEqual(expanded.exploration_depth, 1)
        self.assertEqual(expanded.revision, 2)
        self.assertTrue(
            {node.id for node in created.graph.nodes}.issubset(
                {node.id for node in expanded.graph.nodes}
            )
        )
        self.assertIn(
            "netease:artist:9",
            {node.id for node in expanded.graph.nodes},
        )
        new_edge = next(
            edge
            for edge in expanded.graph.edges
            if edge.source == "genre:hip_hop"
            and edge.target == "netease:artist:9"
        )
        self.assertTrue(new_edge.evidence_ids)
        self.assertIn(f"[{new_edge.evidence_ids[0]}]", expanded.narrative)
        self.assertEqual(
            expanded.citations[0].id,
            new_edge.evidence_ids[0],
        )
        artist_action = next(
            action
            for action in expanded.available_actions
            if action.target_node_id == "netease:artist:9"
        )
        self.assertTrue(artist_action.enabled)
        restored = await service.load(created.session_id)
        self.assertEqual(restored.current_graph, expanded.graph)
        self.assertEqual(restored.evidence_history, expanded.sources)
        self.assertEqual(
            restored.visited_nodes,
            ["netease:artist:2", "genre:hip_hop"],
        )

    async def test_no_grounded_artist_leaves_checkpoint_unchanged(self) -> None:
        service = self.service(genre_executor(documents=[], candidates=[]))
        created = await service.create("探索歌手周杰伦的世界线")
        action = next(
            item
            for item in created.available_actions
            if item.target_node_id == "genre:hip_hop"
        )

        with self.assertRaises(WorldLineExecutionError) as error:
            await service.explore(created.session_id, action.id)

        self.assertEqual(
            error.exception.code,
            "no_grounded_genre_connections",
        )
        restored = await service.load(created.session_id)
        self.assertEqual(restored.revision, 1)
        self.assertEqual(restored.current_graph, created.graph)
        self.assertEqual(restored.evidence_history, created.sources)


def document_for_artist(
    artist_id: str,
    span: str,
):
    value = document(artist_id=artist_id)
    claim = {
        **value.metadata["supported_claims"][0],
        "subject_entity_id": artist_id,
        "statement": span,
        "evidence_span": span,
    }
    return value.model_copy(
        update={
            "content": f"背景。{span}更多内容。",
            "metadata": {
                **value.metadata,
                "supported_claims": [claim],
            },
        }
    )


if __name__ == "__main__":
    unittest.main()

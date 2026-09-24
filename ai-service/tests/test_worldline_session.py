import unittest
from unittest.mock import AsyncMock, patch

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType, EvidenceSource, EvidenceType, MusicEntityFacts, MusicFact,
    WorldLineBranch, WorldLineBranchTarget, WorldLineBranchType, WorldLineNodeType,
)
from app.agent.foundation.subgraphs.worldline.session import (
    WorldLineSessionService, _canonical_target, get_worldline_session_service,
)
from app.agent.foundation.subgraphs.worldline.service import WorldLineExecutionError
from tests.test_worldline_graph import EmptyRetriever, entity


class Catalog:
    def __init__(self):
        self.song = entity(1, EntityType.SONG, "Feather")
        self.artist = entity(2, EntityType.ARTIST, "Nujabes")
        self.album = entity(3, EntityType.ALBUM, "Modal Soul")
        self.other = entity(4, EntityType.SONG, "Reflection Eternal")
        self.fail = False

    async def search_entities(self, query, expected_entity_type, *, limit=5):
        if "Jay-Z" in query:
            return [entity(11, EntityType.ARTIST, "Jay-Z")]
        return [self.song]

    async def load_entity_facts(self, root):
        if self.fail:
            raise RuntimeError("catalog unavailable")
        related = {
            self.song.entity_id: [self.artist, self.album],
            self.artist.entity_id: [self.song, self.album, self.other],
            self.album.entity_id: [self.artist, self.other],
        }.get(root.entity_id, [])
        source_id = f"music-api:{root.entity_type.value}:{root.entity_id.rsplit(':', 1)[-1]}"
        source = EvidenceSource(id=source_id, source_type=EvidenceType.MUSIC_API,
                                title=root.name, source="test-catalog")
        facts = []
        for index, item in enumerate(related):
            subject, target = (item, root) if root.entity_type is EntityType.ARTIST and item.entity_type is EntityType.SONG else (root, item)
            relation = "performed_by" if {root.entity_type, item.entity_type} == {EntityType.SONG, EntityType.ARTIST} else "related_to"
            facts.append(MusicFact(id=f"fact:{index}", subject_id=subject.entity_id,
                                   predicate=relation, value="catalog relation",
                                   object_entity_id=target.entity_id, evidence_ids=[source_id]))
        return MusicEntityFacts(entity=root, related_entities=related,
                                facts=facts, sources=[source])


class Generator:
    def __init__(self):
        self.fail = False

    async def generate_narrative(self, entity, facts, documents, graph, sources):
        if self.fail:
            raise RuntimeError("LLM failed")
        return f"当前聚焦 {entity.name}，累计 {len(graph.nodes)} 个节点。"

    async def generate_branches(self, entity, graph, sources):
        branches = []
        for node in graph.nodes[-3:]:
            kind = (WorldLineBranchType.ARTIST if node.type.value == "artist"
                    else WorldLineBranchType.ALBUM if node.type.value == "album"
                    else WorldLineBranchType.RELATED_WORK)
            branches.append(WorldLineBranch(
                id=f"branch:{len(branches)}", title=f"探索 {node.name}",
                description="继续探索", branch_type=kind,
                target_entity=WorldLineBranchTarget(id=node.id, type=node.type, name=node.name),
            ))
        return branches


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.catalog = Catalog()
        self.generator = Generator()
        self.saver = create_memory_checkpointer()
        graph = create_worldline_subgraph(
            music_service=self.catalog, retriever=EmptyRetriever(), generator=self.generator,
        )
        self.service = WorldLineSessionService(self.saver, turn_graph=graph)

    async def test_create_explore_restore_and_isolation(self):
        first = await self.service.create("歌曲《Feather》的世界线")
        self.assertEqual(first.exploration_depth, 0)
        self.assertEqual(first.revision, 1)
        self.assertEqual(first.graph.root, self.catalog.song.entity_id)
        self.assertEqual(len(first.branches), 2)
        stored = await self.service.load(first.session_id)
        self.assertEqual(stored.current_graph, first.graph)
        artist_action = next(a for a in first.available_actions if a.target_node_id == self.catalog.artist.entity_id)
        second = await self.service.explore(first.session_id, artist_action.id)
        self.assertEqual(second.graph.root, first.graph.root)
        self.assertEqual(second.current_node, self.catalog.artist.entity_id)
        self.assertIn(self.catalog.other.entity_id, {n.id for n in second.graph.nodes})
        self.assertEqual(second.exploration_depth, 1)
        self.assertEqual(second.revision, 2)
        self.assertEqual((await self.service.load(first.session_id)).visited_nodes,
                         [self.catalog.song.entity_id, self.catalog.artist.entity_id])
        next_action = next(a for a in second.available_actions if a.target_node_id == self.catalog.other.entity_id)
        third = await self.service.explore(first.session_id, next_action.id)
        self.assertEqual(third.revision, 3)
        self.assertEqual(third.exploration_depth, 2)
        self.assertEqual(third.outcome, "no_new_connections")
        self.assertEqual(third.graph, second.graph)
        self.assertTrue({s.id for s in second.sources}.issubset({s.id for s in third.sources}))
        with self.assertRaises(WorldLineExecutionError) as error:
            await self.service.explore(first.session_id, artist_action.id)
        self.assertEqual(error.exception.code, "duplicate_exploration")
        stale_action = next(a for a in first.available_actions if a.target_node_id == self.catalog.album.entity_id)
        with self.assertRaises(WorldLineExecutionError) as stale_error:
            await self.service.explore(first.session_id, stale_action.id)
        self.assertEqual(stale_error.exception.code, "invalid_branch_id")
        other = await self.service.create("歌手《Jay-Z》的世界线")
        self.assertNotEqual(first.session_id, other.session_id)
        self.assertEqual((await self.service.load(first.session_id)).revision, 3)

    async def test_failures_leave_checkpoint_unchanged(self):
        created = await self.service.create("歌曲《Feather》的世界线")
        action = next(a for a in created.available_actions if a.target_node_id == self.catalog.artist.entity_id)
        for failing in ("api", "llm"):
            self.catalog.fail = failing == "api"
            self.generator.fail = failing == "llm"
            with self.assertRaises(WorldLineExecutionError):
                await self.service.explore(created.session_id, action.id)
            state = await self.service.load(created.session_id)
            self.assertEqual(state.revision, 1)
            self.assertEqual(state.current_graph, created.graph)
        with self.assertRaises(WorldLineExecutionError) as error:
            await self.service.explore("missing", action.id)
        self.assertEqual(error.exception.code, "session_not_found")

    async def test_checkpoint_write_failure_keeps_old_snapshot(self):
        created = await self.service.create("歌曲《Feather》的世界线")
        action = next(a for a in created.available_actions if a.target_node_id == self.catalog.artist.entity_id)
        with patch.object(self.service._snapshots, "ainvoke", new=AsyncMock(side_effect=RuntimeError("write failed"))):
            with self.assertRaises(WorldLineExecutionError) as error:
                await self.service.explore(created.session_id, action.id)
        self.assertEqual(error.exception.code, "checkpoint_write_failed")
        restored = await self.service.load(created.session_id)
        self.assertEqual(restored.revision, 1)
        self.assertEqual(restored.current_graph, created.graph)


class ProductionCheckpointerTests(unittest.TestCase):
    def tearDown(self):
        get_worldline_session_service.cache_clear()

    def test_memory_backend_fails_closed(self):
        get_worldline_session_service.cache_clear()
        with patch("app.agent.foundation.subgraphs.worldline.session.checkpoint_backend", return_value="memory"):
            with self.assertRaises(WorldLineExecutionError) as error:
                get_worldline_session_service()
        self.assertEqual(error.exception.code, "persistent_checkpoint_required")

    def test_genre_target_is_rejected(self):
        with self.assertRaises(WorldLineExecutionError) as error:
            _canonical_target("netease:genre:5", WorldLineNodeType.GENRE, "Jazz Hip-Hop")
        self.assertEqual(error.exception.code, "unsupported_target_type")

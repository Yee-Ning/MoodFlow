"""Explorer final feature round: persistent branches, hearts and honest roots."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.models import (
    WorldLineNode,
    WorldLineEdge,
)
from app.agent.handoff.context import exploration_projection
from app.agent.handoff.explorer import execute_exploration
from app.agent.handoff.explorer_session import ExplorerSessions, owner_key
from app.agent.handoff.explorer_preferences import preference_weights, read_likes
from app.agent.handoff.explorer_roots import root_guidance
from app.agent.handoff.journey import JourneyTransferPayload, build_listening_journey
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import create_handoff_state
from app.schemas.context import MusicAgentContext
from app.schemas.music import LikedSongsResult
from tests.test_handoff_contracts import make_run
from tests.test_explorer_journey_v1 import (
    captured_facts,
    knowledge_world,
    world_from_facts,
)
from tests.test_worldline_graph import (
    SemanticDocsRetriever,
    FakeGenerator,
    EmptyRetriever,
)


class BranchCatalog:
    def __init__(self):
        self.searches = []
        self.loads = []
        self.fail = False

    async def search_entities(self, query, expected_entity_type, *, limit=5):
        self.searches.append(query)
        return [captured_facts().entity]

    async def load_entity_facts(self, entity):
        self.loads.append(entity.entity_id)
        if self.fail:
            raise RuntimeError("catalog unavailable")
        facts = captured_facts()
        if entity.entity_type.value == "song":
            song = next(
                item
                for item in facts.related_entities
                if item.entity_id == entity.entity_id
            )
            return facts.model_copy(
                update={"entity": song, "related_entities": [facts.entity]}
            )
        if len(self.loads) == 1:
            return facts.model_copy(
                update={
                    "related_entities": facts.related_entities[:1],
                    "facts": facts.facts[:1],
                }
            )
        return facts


class RnbKnowledge:
    async def retrieve(self, entity, plan):
        docs = await SemanticDocsRetriever().retrieve(entity, plan)
        # Controlled existing knowledge fixture with a different registry style;
        # this is not a claim about real music history.
        return [
            type(doc).model_validate(
                json.loads(
                    doc.model_dump_json()
                    .replace("Hip-Hop", "R&B")
                    .replace("genre:hip_hop", "genre:r_and_b")
                )
            )
            for doc in docs
        ]


def setup(*, catalog=None, retriever=None, saver=None, generator=None):
    music = catalog or BranchCatalog()
    graph = create_worldline_subgraph(
        music_service=music,
        retriever=retriever or RnbKnowledge(),
        generator=generator or FakeGenerator(grounded=True),
    )
    return music, graph, ExplorerSessions(saver or create_memory_checkpointer())


def view_for(text, *, conversation="explorer-v2", previous=()):
    run = make_run(RouteKey.WORLDLINE, entry=AgentKey.EXPLORER, texts=[text])
    view = create_handoff_state(run, routing_context=RoutingContext())
    view["continuity_context"] = {"conversation_id": conversation}
    view["task_results"] = list(previous)
    return view, run


async def invoke(
    text,
    graph,
    sessions,
    *,
    user="u1",
    conversation="explorer-v2",
    previous=(),
    authenticated=False,
):
    view, run = view_for(text, conversation=conversation, previous=previous)
    context = MusicAgentContext(
        user_id=user, session_cookie="JSESSIONID=test-only" if authenticated else None
    )
    return await execute_exploration(
        view,
        run,
        SimpleNamespace(context=context),
        graph=graph,
        context_tool=None,
        sessions=sessions,
    )


def hearts(monkeypatch, ids=(), *, status=200, malformed=False):
    seen = []
    original = httpx.AsyncClient

    def backend(request):
        seen.append((request.method, request.url.path))
        assert request.method == "GET" and request.url.path == "/api/user/liked-songs"
        assert request.headers.get("cookie") == "JSESSIONID=test-only"
        return httpx.Response(
            status,
            json={
                "code": status,
                "message": "fixture",
                "data": ["bad"] if malformed else list(ids),
            },
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(backend)),
    )
    return seen


@pytest.mark.asyncio
async def test_jay_then_rnb_keeps_same_graph_revision_visited_and_sources():
    music, graph, sessions = setup()
    initial = await invoke("探索周杰伦", graph, sessions)
    assert initial.status is TaskStatus.SUCCESS
    before = initial.data["explorer_session"]
    assert any(
        branch["target_entity"]["id"] == "genre:r_and_b"
        for branch in before["branches"]
    )
    expanded = await invoke("继续沿 R&B 分支", graph, sessions, previous=[initial])
    assert expanded.status is TaskStatus.SUCCESS, expanded
    after = expanded.data["explorer_session"]
    assert after["session_id"] == before["session_id"]
    assert after["revision"] == 2 and after["graph"]["root"] == before["graph"]["root"]
    assert after["current_node"] == "genre:r_and_b"
    assert set(before["visited_nodes"]).issubset(after["visited_nodes"])
    assert "genre:r_and_b" in after["visited_nodes"]
    assert {source["id"] for source in before["sources"]}.issubset(
        source["id"] for source in after["sources"]
    )
    assert {node["id"] for node in before["graph"]["nodes"]}.issubset(
        node["id"] for node in after["graph"]["nodes"]
    )
    assert "netease:song:5257138" in {
        node["id"] for node in after["graph"]["nodes"]
    } - {node["id"] for node in before["graph"]["nodes"]}
    assert len(music.searches) == 1 and len(music.loads) == 2
    assert any(node["song_id"] == 5257138 for node in expanded.data["journey_queue"])
    assert all(
        node["narration"]["source_ids"]
        for node in expanded.data["listening_journey"]["nodes"]
    )
    assert not GenreVocabularyRegistry.load().get("genre:r_and_b").executable
    assert exploration_projection([expanded])["source_ids"]


@pytest.mark.asyncio
async def test_restore_new_adapter_no_task_history_still_resumes_server_pointer():
    saver = create_memory_checkpointer()
    _, graph, sessions = setup(saver=saver)
    initial = await invoke("探索周杰伦", graph, sessions)
    restored = ExplorerSessions(saver)
    expanded = await invoke("刚才 RnB 那个节点展开讲讲", graph, restored)
    assert expanded.status is TaskStatus.SUCCESS
    assert (
        expanded.data["explorer_session"]["session_id"]
        == initial.data["explorer_session"]["session_id"]
    )
    assert expanded.data["explorer_session"]["revision"] == 2


@pytest.mark.parametrize(
    "text",
    [
        "继续沿这个分支探索",
        "刚才那个节点展开讲讲",
        "继续沿 不存在 分支",
        "继续沿 R&B 和 布拉格广场 分支",
    ],
)
@pytest.mark.asyncio
async def test_branch_ambiguity_preserves_snapshot_without_new_graph(text):
    music, graph, sessions = setup()
    initial = await invoke("探索周杰伦", graph, sessions)
    result = await invoke(text, graph, sessions)
    assert result.status is TaskStatus.NEEDS_INPUT
    assert result.data["journey_queue"] == [] and "请明确" in result.content
    state = await sessions.service.load(initial.data["explorer_session"]["session_id"])
    assert state.revision == 1 and state.current_node == state.current_graph.root
    assert len(music.searches) == 1 and len(music.loads) == 1


@pytest.mark.parametrize(
    "user,conversation", [("u2", "explorer-v2"), ("u1", "another-chat")]
)
@pytest.mark.asyncio
async def test_accounts_and_conversations_cannot_resume_foreign_pointer(
    user, conversation
):
    music, graph, sessions = setup()
    initial = await invoke("探索周杰伦", graph, sessions)
    foreign = await invoke(
        "继续沿 R&B 分支",
        graph,
        sessions,
        user=user,
        conversation=conversation,
        previous=[initial],
    )
    assert foreign.status is TaskStatus.NEEDS_INPUT
    assert "没有可恢复" in foreign.content and len(music.loads) == 1


@pytest.mark.asyncio
async def test_catalog_failure_preserves_last_revision_and_can_retry():
    music, graph, sessions = setup()
    initial = await invoke("探索周杰伦", graph, sessions)
    music.fail = True
    failed = await invoke("继续沿 R&B 分支", graph, sessions)
    assert (
        failed.status is TaskStatus.NEEDS_INPUT and failed.data["journey_queue"] == []
    )
    state = await sessions.service.load(initial.data["explorer_session"]["session_id"])
    assert state.revision == 1 and state.visited_nodes == [state.current_graph.root]
    music.fail = False
    retried = await invoke("继续沿 R&B 分支", graph, sessions)
    assert (
        retried.status is TaskStatus.SUCCESS
        and retried.data["explorer_session"]["revision"] == 2
    )


@pytest.mark.asyncio
async def test_duplicate_focus_does_not_increment_revision_or_rebuild():
    music, graph, sessions = setup()
    initial = await invoke("探索周杰伦", graph, sessions)
    await invoke("继续沿 R&B 分支", graph, sessions)
    repeated = await invoke("继续沿 R&B 分支", graph, sessions)
    assert repeated.status is TaskStatus.NEEDS_INPUT
    assert (
        await sessions.service.load(initial.data["explorer_session"]["session_id"])
    ).revision == 2
    assert len(music.loads) == 2


@pytest.mark.asyncio
async def test_fresh_root_starts_new_graph_and_pointer_without_changing_old_graph():
    _, graph, sessions = setup()
    first = await invoke("探索周杰伦", graph, sessions)
    second = await invoke("重新探索歌手《周杰伦》", graph, sessions)
    assert (
        first.data["explorer_session"]["session_id"]
        != second.data["explorer_session"]["session_id"]
    )
    assert (
        await sessions.service.load(first.data["explorer_session"]["session_id"])
    ).revision == 1


@pytest.mark.asyncio
async def test_hearts_change_real_path_order_and_annotations_use_actual_tool(
    monkeypatch,
):
    returned_ids = []
    seen = hearts(monkeypatch, returned_ids)
    facts = captured_facts()

    class FullCatalog(BranchCatalog):
        async def load_entity_facts(self, entity):
            return facts

    _, graph, sessions = setup(catalog=FullCatalog())
    empty = await invoke("探索周杰伦", graph, sessions, authenticated=True)
    returned_ids.append(210049)
    liked = await invoke("探索周杰伦", graph, sessions, authenticated=True, user="u2")
    plain_ids = [item["node_id"] for item in empty.data["journey_queue"]]
    liked_ids = [item["node_id"] for item in liked.data["journey_queue"]]
    assert plain_ids != liked_ids
    assert (
        liked_ids[1] == "netease:song:210049" and plain_ids[1] == "netease:song:5257138"
    )
    assert liked.data["listening_journey"]["ordering"] == "preference_then_timeline"
    assert "因你红心收藏《布拉格广场》" in liked.content
    assert "因你" not in empty.content
    assert all(
        set(item["narration"]["preference_song_ids"]).issubset({210049})
        for item in liked.data["listening_journey"]["nodes"]
    )
    assert empty.data["personalization"]["status"] == "empty"
    assert "genre:r_and_b" in liked.data["personalization"]["matched_node_ids"]
    assert seen == [("GET", "/api/user/liked-songs")] * 2
    assert JourneyTransferPayload.model_validate_json(
        json.dumps({"journey_queue": liked.data["journey_queue"]})
    ).journey_queue


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ids,status,malformed,expected",
    [
        ((), 200, False, "empty"),
        ((999999,), 200, False, "available"),
        ((), 401, False, "unavailable"),
        ((), 200, True, "unavailable"),
    ],
)
async def test_empty_unmatched_unreadable_hearts_do_not_invent_preferences(
    monkeypatch, ids, status, malformed, expected
):
    seen = hearts(monkeypatch, ids, status=status, malformed=malformed)
    _, graph, sessions = setup()
    result = await invoke("探索周杰伦", graph, sessions, authenticated=True)
    assert result.status is TaskStatus.SUCCESS
    assert result.data["personalization"]["status"] == expected
    assert result.data["listening_journey"]["ordering"] == "timeline"
    assert "因你" not in result.content and "通用路径" in result.content
    assert len(seen) == 1


def test_preferences_weight_evidence_backed_artist_genre_era_and_ignore_unknown_ids():
    world, facts = knowledge_world()
    weights, reasons, ids = preference_weights(
        world, [facts], LikedSongsResult(song_ids=[5257138])
    )
    assert weights[facts.entity.entity_id] == 3
    assert weights["genre:fixture"] == 2 and weights["era:fixture"] == 2
    assert ids["era:fixture"] == (5257138,) and "屋顶" in reasons["genre:fixture"]
    assert preference_weights(world, [facts], LikedSongsResult(song_ids=[999999])) == (
        {},
        {},
        {},
    )


def test_personalization_never_admits_sourceless_nodes_or_coerces_catalog_ids():
    world, facts = knowledge_world()
    fake = WorldLineNode(
        id="fake:genre", type="genre", name="无来源风格", metadata={"liked": True}
    )
    world = world.model_copy(
        update={
            "graph": world.graph.model_copy(
                update={
                    "nodes": [*world.graph.nodes, fake],
                    "edges": [
                        *world.graph.edges,
                        WorldLineEdge(
                            source=facts.entity.entity_id,
                            target=fake.id,
                            relation="belongs_to_genre",
                            evidence_ids=["missing"],
                        ),
                    ],
                }
            )
        }
    )
    weights, reasons, ids = preference_weights(
        world, [facts], LikedSongsResult(song_ids=[5257138])
    )
    assert fake.id not in weights
    journey = build_listening_journey(
        world,
        facts,
        node_priorities=weights,
        recommendation_reasons=reasons,
        preference_song_ids=ids,
    )
    assert fake.id in journey.excluded_node_ids
    assert all(item.song_id in {None, 210049, 5257138} for item in journey.nodes)


@pytest.mark.parametrize(
    "text",
    [
        "探索 R&B",
        "探索 爵士",
        "探索 90 年代港乐",
        "探索 1990 年代",
        "探索 台北音乐场景",
        "探索音乐流派",
        "探索某个场景",
    ],
)
@pytest.mark.asyncio
async def test_genre_era_scene_roots_get_actionable_guidance_without_catalog_failure(
    text,
):
    music, graph, sessions = setup()
    result = await invoke(text, graph, sessions)
    assert result.status is TaskStatus.NEEDS_INPUT
    assert result.data["reason"] == "explorer_topic_root_guidance"
    assert "具体歌曲、歌手或专辑" in result.content and "你想从" in result.content
    assert not music.searches and not music.loads and result.data["journey_queue"] == []


def test_era_or_genre_words_in_explicit_catalog_titles_still_use_catalog_resolver():
    assert root_guidance("探索歌曲《黄金年代》") is None
    assert root_guidance("探索专辑《爵士》") is None


@pytest.mark.asyncio
async def test_ambiguous_catalog_entity_still_asks_existing_clarification():
    music = BranchCatalog()

    async def ambiguous(*args, **kwargs):
        root = captured_facts().entity
        return [root, root.model_copy(update={"entity_id": "netease:artist:999999"})]

    music.search_entities = ambiguous
    _, graph, sessions = setup(catalog=music, retriever=EmptyRetriever())
    result = await invoke("探索歌手《周杰伦》", graph, sessions)
    assert result.status is TaskStatus.NEEDS_INPUT and not music.loads
    assert result.data["journey_queue"] == []


@pytest.mark.asyncio
async def test_unscoped_followup_honestly_declines_instead_of_rebuilding():
    music, graph, sessions = setup()
    view, run = view_for("继续沿 R&B 分支")
    view["continuity_context"] = {}
    result = await execute_exploration(
        view,
        run,
        SimpleNamespace(context=None),
        graph=graph,
        context_tool=None,
        sessions=sessions,
    )
    assert result.status is TaskStatus.NEEDS_INPUT and not music.searches
    assert owner_key(view, SimpleNamespace(context=None)) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("new_window", [False, True])
async def test_compiled_handoff_two_chat_turns_use_session_without_router_changes(
    monkeypatch,
    new_window,
):
    from app.agent.handoff.graph import build_handoff_graph
    from tests.test_handoff_network import transfers, G, C, E
    from tests.test_handoff_runtime import Router, input_state

    music, worldline, sessions = setup()
    monkeypatch.setattr(
        "app.agent.handoff.explorer.get_explorer_sessions", lambda: sessions
    )
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=transfers((G, C, E)),
        worldline_subgraph=worldline,
    ).compile(checkpointer=create_memory_checkpointer())
    config = {
        "configurable": {
            "thread_id": "explorer-real-chat-two-turns",
            "session_id": "window-1",
        }
    }
    context = MusicAgentContext(user_id="u1")
    first = await graph.ainvoke(input_state("探索周杰伦"), config, context=context)
    initial = first["task_results"][-1].data["explorer_session"]
    if new_window:
        config = {
            "configurable": {
                "thread_id": "explorer-real-chat-two-turns",
                "session_id": "window-2",
            }
        }
    second = await graph.ainvoke(
        input_state("继续沿 R&B 分支"), config, context=context
    )
    expanded = second["task_results"][-1].data["explorer_session"]
    assert expanded["session_id"] == initial["session_id"] and expanded["revision"] == 2
    assert len(music.searches) == 1 and len(music.loads) == 2
    assert expanded["current_node"] == "genre:r_and_b"


@pytest.mark.asyncio
async def test_catalog_song_branch_and_depth_bound_preserve_existing_service_rules():
    class LatestNodesGenerator(FakeGenerator):
        async def generate_branches(self, entity, graph, sources):
            # Offer the newest actual graph nodes; the service still filters
            # visited targets. The default fixture always offers the first three.
            return await super().generate_branches(
                entity,
                graph.model_copy(update={"nodes": list(reversed(graph.nodes))}),
                sources,
            )

    music, graph, sessions = setup(generator=LatestNodesGenerator(grounded=True))
    initial = await invoke("探索周杰伦", graph, sessions)
    second = await invoke("继续沿 R&B 分支", graph, sessions)
    song = await invoke("继续沿 布拉格广场 分支", graph, sessions)
    assert song.status is TaskStatus.SUCCESS, song
    assert song.data["explorer_session"]["revision"] == 3
    assert song.data["explorer_session"]["current_node"] == "netease:song:210049"
    final = await invoke("继续沿 屋顶 分支", graph, sessions)
    assert final.status is TaskStatus.SUCCESS, final
    assert final.data["explorer_session"]["revision"] == 4
    assert set(initial.data["explorer_session"]["visited_nodes"]).issubset(
        final.data["explorer_session"]["visited_nodes"]
    )
    assert set(second.data["explorer_session"]["visited_nodes"]).issubset(
        final.data["explorer_session"]["visited_nodes"]
    )
    assert (
        await sessions.service.load(initial.data["explorer_session"]["session_id"])
    ).exploration_depth == 3
    assert len(music.loads) == 4


@pytest.mark.asyncio
async def test_failed_checkpoint_write_leaves_previous_graph(monkeypatch):
    _, graph, sessions = setup()
    initial = await invoke("探索周杰伦", graph, sessions)
    monkeypatch.setattr(
        sessions.service._snapshots,
        "ainvoke",
        AsyncMock(side_effect=RuntimeError("write unavailable")),
    )
    result = await invoke("继续沿 R&B 分支", graph, sessions)
    assert (
        result.status is TaskStatus.NEEDS_INPUT
        and result.data["reason"] == "checkpoint_write_failed"
    )
    assert (
        await sessions.service.load(initial.data["explorer_session"]["session_id"])
    ).revision == 1


@pytest.mark.asyncio
async def test_like_read_uses_existing_journal_and_never_swallows_fencing_conflict(
    monkeypatch,
):
    from app.agent.foundation.long_horizon.operations import active_operations
    from app.agent.foundation.long_horizon.execution import ContinuityConflict

    seen = hearts(monkeypatch, [210049])
    calls = []

    async def call(request, execute):
        calls.append(request.tool_call)
        return await execute(request)

    journal = SimpleNamespace(call=call)
    runtime = SimpleNamespace(
        context=MusicAgentContext(user_id="u1", session_cookie="JSESSIONID=test-only")
    )
    token = active_operations.set(journal)
    try:
        likes, receipt = await read_likes(runtime)
        assert likes.song_ids == [210049] and receipt["status"] == "available"
        assert (
            len(calls) == 1
            and calls[0]["name"] == "get_liked_songs"
            and calls[0]["args"] == {}
        )
        journal.call = AsyncMock(side_effect=ContinuityConflict("fence changed"))
        with pytest.raises(ContinuityConflict):
            await read_likes(runtime)
        assert len(seen) == 1
    finally:
        active_operations.reset(token)


@pytest.mark.asyncio
async def test_selected_genre_without_new_evidence_keeps_graph_and_reports_no_connections():
    class SameCatalog(BranchCatalog):
        async def load_entity_facts(self, entity):
            self.loads.append(entity.entity_id)
            facts = captured_facts()
            return facts.model_copy(
                update={
                    "related_entities": facts.related_entities[:1],
                    "facts": facts.facts[:1],
                }
            )

    _, graph, sessions = setup(catalog=SameCatalog())
    initial = await invoke("探索周杰伦", graph, sessions)
    followup = await invoke("继续沿 R&B 分支", graph, sessions)
    assert followup.status is TaskStatus.SUCCESS
    assert followup.data["explorer_session"]["outcome"] == "no_new_connections"
    assert (
        followup.data["explorer_session"]["graph"]
        == initial.data["explorer_session"]["graph"]
    )
    assert "未发现新的有来源关联" in followup.content


def test_branch_catalog_receipts_cite_only_their_own_sources():
    facts = captured_facts()
    world = world_from_facts(facts)
    first = facts.model_copy(
        update={
            "related_entities": facts.related_entities[:1],
            "facts": facts.facts[:1],
        }
    )
    later_source = facts.sources[0].model_copy(update={"id": "music_api:later-song"})
    later_song = facts.related_entities[1]
    later_fact = facts.facts[1].model_copy(update={"evidence_ids": [later_source.id]})
    later = facts.model_copy(
        update={
            "entity": later_song,
            "related_entities": [facts.entity],
            "sources": [later_source],
            "facts": [later_fact],
        }
    )
    edges = [
        edge.model_copy(update={"evidence_ids": [later_source.id]})
        if edge.source == later_song.entity_id
        else edge
        for edge in world.graph.edges
    ]
    world = world.model_copy(
        update={
            "sources": [*world.sources, later_source],
            "graph": world.graph.model_copy(update={"edges": edges}),
        }
    )
    journey = build_listening_journey(world, None, catalog_history=(first, later))
    nodes = {node.node_id: node for node in journey.nodes}
    assert set(nodes[facts.related_entities[0].entity_id].narration.source_ids) == {
        facts.sources[0].id
    }
    assert set(nodes[later_song.entity_id].narration.source_ids) == {later_source.id}
    # A source present elsewhere in the graph does not support this catalog fact.
    wrong_edges = [
        edge.model_copy(update={"evidence_ids": [facts.sources[0].id]})
        if edge.source == later_song.entity_id
        else edge
        for edge in edges
    ]
    wrong = world.model_copy(
        update={"graph": world.graph.model_copy(update={"edges": wrong_edges})}
    )
    rejected = build_listening_journey(wrong, None, catalog_history=(first, later))
    assert later_song.entity_id not in {node.node_id for node in rejected.nodes}


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["刚才那个节点展开讲讲", "刚才 RnB 节点展开讲讲"])
async def test_deictic_detail_uses_actual_selection_without_rebuilding(text):
    music, graph, sessions = setup()
    await invoke("探索周杰伦", graph, sessions)
    selected = await invoke("继续沿 R&B 分支", graph, sessions)
    detail = await invoke(text, graph, sessions)
    assert detail.status is TaskStatus.SUCCESS
    assert detail.data["explorer_followup_mode"] == "explain_selected"
    assert detail.data["explorer_session"] == selected.data["explorer_session"]
    assert "沿刚才选中的节点讲解" in detail.content and "R&B" in detail.content
    assert len(music.loads) == 2


@pytest.mark.asyncio
async def test_deictic_continue_accepts_only_unique_actual_branch():
    _, graph, sessions = setup(retriever=EmptyRetriever())
    initial = await invoke("探索周杰伦", graph, sessions)
    assert len(initial.data["explorer_session"]["branches"]) == 1
    follow = await invoke("继续沿这个分支探索", graph, sessions)
    assert follow.status is TaskStatus.SUCCESS
    assert follow.data["explorer_session"]["revision"] == 2
    assert (
        follow.data["explorer_session"]["session_id"]
        == initial.data["explorer_session"]["session_id"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("with_pause", [False, True])
async def test_topic_guidance_preserves_completed_actions_without_reading_catalog(
    with_pause,
):
    from app.agent.handoff.graph import build_handoff_graph
    from app.agent.handoff.streaming import invoke_handoff_assistant
    from tests.test_handoff_runtime import Router, ToolModel, call, player_context
    from tests.test_worldline_graph import FakeMusicService

    music = FakeMusicService(None)
    music.search_entities = AsyncMock(
        side_effect=AssertionError("topic must be clarified first")
    )
    worldline = create_worldline_subgraph(
        music_service=music, retriever=EmptyRetriever(), generator=FakeGenerator()
    )
    routes = (
        (RouteKey.TOOL_CALLING, RouteKey.WORLDLINE)
        if with_pause
        else (RouteKey.WORLDLINE,)
    )
    graph = build_handoff_graph(
        intent_router=Router(*routes),
        transfer_model=ToolModel(
            responses=[call(), call("transfer_to_explorer_agent", call_id="explore")]
        ),
        worldline_subgraph=worldline,
    ).compile()
    response = await invoke_handoff_assistant(
        message="暂停一下；探索90年代港乐" if with_pause else "探索90年代港乐",
        thread_id="topic-guide",
        routing_context=player_context(),
        graph=graph,
    )
    assert response.status == TaskStatus.NEEDS_INPUT and len(response.results) == len(
        routes
    )
    assert response.results[-1].status == TaskStatus.NEEDS_INPUT
    assert "从具体歌曲、歌手或专辑开始" in response.results[-1].answer
    music.search_entities.assert_not_awaited()
    if with_pause:
        assert (
            response.results[0].metadata["client_actions"][0]["name"] == "pause_player"
        )

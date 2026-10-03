"""Phase 3 controlled acceptance of all capabilities and four transfer edges."""

import asyncio
from itertools import product
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.subgraphs.tool_calling.client_tools import CLIENT_ACTION_TOOLS
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import EntityType
from app.agent.handoff.graph import build_handoff_graph, create_handoff_graph
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import HandoffRun, RunLimits, RunStatus
from app.agent.handoff.streaming import (
    invoke_handoff_assistant,
    stream_handoff_assistant,
)
from app.main import app
from app.rag.query_understanding.analyzer import QueryAnalyzer, QueryTypeOutput
from app.rag.query_understanding.config import load_query_understanding_config
from app.rag.query_understanding.models import QueryType
from app.rag.query_understanding.service import QueryUnderstandingService
from app.schemas.chat import (
    ClientActionStreamEvent,
    MessageStreamEvent,
    UpdateStreamEvent,
)
from app.schemas.music import MusicSearchResult, MusicTrack, SearchMusicArgs
from tests.test_handoff_runtime import (
    Router,
    ToolModel,
    call,
    input_state,
    parse_sse,
    player_context,
    song,
)
from tests.test_worldline_graph import (
    EmptyRetriever,
    FakeGenerator,
    FakeMusicService,
    FailingRetriever,
    EraDocsRetriever,
    SceneDocsRetriever,
    InfluenceDocsRetriever,
    entity,
)

TEXT = {
    RouteKey.GENERAL_CHAT: "陪我聊聊",
    RouteKey.CURRENT_SONG: "分析这首歌",
    RouteKey.TOOL_CALLING: "暂停一下",
    RouteKey.WORLDLINE: "探索歌手《周杰伦》的世界线",
}
G, C, E = AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.EXPLORER
OWNER = {
    RouteKey.GENERAL_CHAT: G,
    RouteKey.CURRENT_SONG: C,
    RouteKey.TOOL_CALLING: C,
    RouteKey.WORLDLINE: E,
}
CHAINS = {
    (G, G): (G,),
    (G, C): (G, C),
    (G, E): (G, C, E),
    (C, G): (G, C, G),
    (C, C): (G, C),
    (C, E): (G, C, E),
    (E, G): (G, C, E, C, G),
    (E, C): (G, C, E, C),
    (E, E): (G, C, E),
}


@pytest.fixture(autouse=True)
def offline_models(monkeypatch):
    monkeypatch.setattr(
        "app.agent.handoff.agents.get_base_chat_model",
        lambda: ToolModel(responses=[AIMessage(content="受控回答。")]),
    )


def worldline(*, service=None, retriever=None, generator=None):
    class OfflineQueryRuntime:
        async def analyze(self, query):
            # Scene-only queries use the analyzer's LLM fallback. Keep this
            # controlled business-network suite independent of provider calls.
            return QueryTypeOutput(query_types=[QueryType.EXPLORATORY], confidence=0.94)

    config = load_query_understanding_config()
    return create_worldline_subgraph(
        music_service=service
        or FakeMusicService(entity(21, EntityType.ARTIST, "周杰伦")),
        retriever=retriever or EmptyRetriever(),
        generator=generator or FakeGenerator(grounded=True),
        query_understanding=QueryUnderstandingService(
            analyzer=QueryAnalyzer(config, runtime=OfflineQueryRuntime()),
        ),
    )


def transfers(chain):
    return ToolModel(
        responses=[
            call(f"transfer_to_{target.value}_agent", call_id=f"hop-{index}")
            for index, target in enumerate(chain[1:], start=1)
        ]
        or [AIMessage(content="没有转交")]
    )


def provider():
    result = AsyncMock()
    result.get_song.return_value = song()
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second", list(product(RouteKey, repeat=2)))
async def test_all_sixteen_capability_pairs_use_real_nodes_and_preserve_tasks(
    first, second
):
    chain = CHAINS[(OWNER[first], OWNER[second])]
    model = transfers(chain)
    graph = build_handoff_graph(
        intent_router=Router(first, second),
        transfer_model=model,
        general_model=ToolModel(responses=[AIMessage(content="陪伴回答。")]),
        song_model=ToolModel(responses=[AIMessage(content="歌曲分析。")]),
        provider=provider(),
        worldline_subgraph=worldline(),
    ).compile()
    text = f"{TEXT[first]}；{TEXT[second]}"
    output = await graph.ainvoke(input_state(text))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED
    assert run.handoff_chain == chain and run.handoff_count == len(chain) - 1
    assert [result.route_key for result in run.results] == [first, second]
    assert [result.task_id for result in run.results] == ["task-0", "task-1"]
    assert run.original_input == text and len(run.plan.tasks) == 2
    assert run.plan.tasks[1].depends_on == ["task-0"]
    assert len(model.seen_messages) == run.handoff_count
    for names in model.bound_names:
        assert all(name.startswith("transfer_to_") for name in names)
    for result in run.results:
        if result.route_key == RouteKey.WORLDLINE:
            assert result.data["worldline_result"]["citations"]


def search_tool(track, searches):
    @tool("search_music", args_schema=SearchMusicArgs)
    async def search(keyword: str, limit: int = 10) -> str:
        """受控搜索音乐目录。"""
        searches.append((keyword, limit))
        return MusicSearchResult(
            keyword=keyword, limit=limit, songs=[track]
        ).model_dump_json()

    return search


def golden_graph():
    track = MusicTrack(song_id=31, name="晴天", artists=["周杰伦"], album="叶惠美")
    searches = []
    curator = ToolModel(
        responses=[
            call(
                "search_music", args={"keyword": "晴天", "limit": 1}, call_id="search"
            ),
            call("play_song", args={"song": track.model_dump()}, call_id="play"),
            AIMessage(content="已发送播放指令。"),
        ]
    )
    graph = build_handoff_graph(
        intent_router=Router(
            RouteKey.GENERAL_CHAT,
            RouteKey.TOOL_CALLING,
            RouteKey.WORLDLINE,
            RouteKey.TOOL_CALLING,
            RouteKey.GENERAL_CHAT,
        ),
        transfer_model=transfers((G, C, E, C, G)),
        general_model=ToolModel(
            responses=[
                AIMessage(content="我在这里。"),
                AIMessage(content="可以继续聊。"),
            ]
        ),
        curator_model=curator,
        tool_tools=[search_tool(track, searches), *CLIENT_ACTION_TOOLS],
        worldline_subgraph=worldline(
            service=FakeMusicService(
                entity(21, EntityType.ARTIST, "周杰伦"),
                [entity(31, EntityType.SONG, "晴天")],
            )
        ),
    ).compile()
    return graph, searches


GOLDEN_TEXT = (
    "陪我聊聊；暂停一下；探索歌手《周杰伦》的世界线；搜索晴天并播放；继续陪我聊聊"
)


@pytest.mark.asyncio
async def test_golden_network_chat_music_exploration_play_and_return():
    graph, searches = golden_graph()
    output = await graph.ainvoke(input_state(GOLDEN_TEXT))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED and run.handoff_chain == (G, C, E, C, G)
    assert searches == [("晴天", 1)]
    assert len(run.results) == 5
    assert run.results[1].data["client_actions"][0]["name"] == "pause_player"
    assert run.results[2].data["worldline_result"]["citations"]
    assert (
        run.results[3].data["client_actions"][0]["arguments"]["song"]["song_id"] == 31
    )
    calls = [
        item
        for item in output["messages"]
        if isinstance(item, AIMessage) and item.tool_calls
    ]
    replies = [item for item in output["messages"] if isinstance(item, ToolMessage)]
    assert len(calls) == len(replies) == 4
    assert {item.tool_calls[0]["id"] for item in calls} == {
        item.tool_call_id for item in replies
    }


@pytest.mark.asyncio
async def test_golden_sse_keeps_sources_and_only_curator_emits_actions():
    graph, searches = golden_graph()
    events = [
        event
        async for event in stream_handoff_assistant(
            message=GOLDEN_TEXT,
            thread_id="golden-stream",
            routing_context=player_context(),
            graph=graph,
        )
    ]
    final = events[-1].response
    assert final.status == TaskStatus.SUCCESS and len(final.results) == 5
    assert final.results[2].citations and final.results[2].sources
    assert final.results[2].subgraph == "worldline"
    actions = [
        event.action for event in events if isinstance(event, ClientActionStreamEvent)
    ]
    assert [item.name for item in actions] == ["pause_player", "play_song"]
    assert len({item.action_id for item in actions}) == 2
    assert searches == [("晴天", 1)]
    assert (
        "".join(
            event.delta for event in events if isinstance(event, MessageStreamEvent)
        )
        == final.message
    )
    assert any(
        isinstance(event, UpdateStreamEvent) and event.node == "worldline_subgraph"
        for event in events
    )
    wire = "".join(event.model_dump_json() for event in events)
    assert all(
        word not in wire
        for word in (
            "private",
            "transfer_envelope",
            "context_summary",
            "trusted_target",
            "retrieved_docs",
        )
    )


def test_explorer_json_and_sse_keep_old_worldline_protocol():
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=transfers((G, C, E)),
        worldline_subgraph=worldline(),
    ).compile()
    with (
        patch(
            "app.api.chat.get_settings",
            return_value=SimpleNamespace(agent_graph_mode="handoff"),
        ),
        patch("app.agent.foundation.streaming.get_handoff_graph", return_value=graph),
    ):
        payload = {"message": TEXT[RouteKey.WORLDLINE], "thread_id": "api-network"}
        response = TestClient(app).post("/api/ai/handoff", json=payload)
        stream = TestClient(app).post("/api/ai/chat", json=payload)
    assert response.status_code == stream.status_code == 200
    events = parse_sse(stream.text)
    assert next(data for name, data in events if name == "final") == response.json()
    assert response.json()["results"][0]["subgraph"] == "worldline"
    assert response.json()["results"][0]["citations"]
    assert events[-1] == ("done", {}) and all(name != "action" for name, _ in events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["facts", "retrieval", "generation", "missing", "ambiguous"]
)
async def test_explorer_dependency_outcomes_preserve_existing_fallback_and_stop_rules(
    kind,
):
    root = entity(21, EntityType.ARTIST, "周杰伦")
    service = FakeMusicService(root)
    retriever, generator = EmptyRetriever(), FakeGenerator(grounded=True)
    expected = TaskStatus.FAILED
    if kind == "facts":
        service.facts_error = RuntimeError("private failure")
    elif kind == "retrieval":
        retriever = FailingRetriever()
        expected = TaskStatus.SUCCESS
    elif kind == "generation":
        generator = FakeGenerator(fail_branches=True)
    elif kind == "missing":
        service.root = None
        expected = TaskStatus.NEEDS_INPUT
    else:

        async def ambiguous(*args, **kwargs):
            return [root, root.model_copy(update={"entity_id": "netease:artist:22"})]

        service.search_entities = ambiguous
        expected = TaskStatus.NEEDS_INPUT
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING),
        transfer_model=transfers((G, C, E, C)),
        worldline_subgraph=worldline(
            service=service, retriever=retriever, generator=generator
        ),
    ).compile()
    response = await invoke_handoff_assistant(
        message=f"{TEXT[RouteKey.WORLDLINE]}；暂停一下",
        thread_id=f"failure-{kind}",
        routing_context=player_context(),
        graph=graph,
    )
    assert response.status == expected
    assert len(response.results) == (2 if kind == "retrieval" else 1)
    assert (
        response.results[0].subgraph == "worldline"
        and "private" not in response.model_dump_json()
    )
    assert not response.results[0].metadata.get("client_actions")
    if kind == "retrieval":
        assert all(
            source["source_type"] == "music_api"
            for source in response.results[0].sources
        )
        assert (
            response.results[1].metadata["client_actions"][0]["name"] == "pause_player"
        )


@pytest.mark.asyncio
async def test_explorer_timeout_is_bounded_and_does_not_run_later_action():
    class SlowRetriever:
        async def retrieve(self, *args):
            await asyncio.sleep(2)
            return []

    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING),
        transfer_model=transfers((G, C, E, C)),
        worldline_subgraph=worldline(retriever=SlowRetriever()),
        limits=RunLimits(agent_timeout_seconds=1, run_timeout_seconds=5),
    ).compile()
    async with asyncio.timeout(6):
        output = await graph.ainvoke(
            input_state(f"{TEXT[RouteKey.WORLDLINE]}；暂停一下")
        )
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.FAILED and len(run.results) == 1
    assert run.handoff_count == 2 and run.results[0].status is TaskStatus.FAILED


@pytest.mark.asyncio
async def test_explorer_checkpoint_resume_keeps_two_hops_and_does_not_relay_twice():
    model = transfers((G, C, E))
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=model,
        worldline_subgraph=worldline(),
    ).compile(
        checkpointer=create_memory_checkpointer(), interrupt_before=["explorer_agent"]
    )
    config = {"configurable": {"thread_id": "explorer-resume"}}
    paused = await graph.ainvoke(input_state(TEXT[RouteKey.WORLDLINE]), config)
    assert HandoffRun.model_validate(paused["handoff_run"]).handoff_count == 2
    resumed = await graph.ainvoke(None, config)
    run = HandoffRun.model_validate(resumed["handoff_run"])
    assert run.status is RunStatus.COMPLETED and run.handoff_count == 2
    assert len(model.seen_messages) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,text,retriever",
    [
        ("era", "歌手周杰伦的 2000 年代音乐时期", EraDocsRetriever),
        ("scene", "歌手周杰伦的音乐场景", SceneDocsRetriever),
        ("influence", "歌手周杰伦的音乐影响", InfluenceDocsRetriever),
    ],
)
async def test_explorer_keeps_existing_era_scene_influence_evidence(
    kind, text, retriever
):
    related = entity(7, EntityType.SONG, "七里香").model_copy(
        update={
            "metadata": {
                "entity_reference": {"knowledge_entity_id": "song:qi_li_xiang"}
            },
        }
    )
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=transfers((G, C, E)),
        worldline_subgraph=worldline(
            service=FakeMusicService(entity(2, EntityType.ARTIST, "周杰伦"), [related]),
            retriever=retriever(),
        ),
    ).compile()
    response = await invoke_handoff_assistant(
        message=text, thread_id=kind, routing_context=RoutingContext(), graph=graph
    )
    assert response.status == TaskStatus.SUCCESS
    result = response.results[0].metadata["worldline_result"]
    assert len(result[f"{kind}_relations"]) == 1
    evidence = result[f"{kind}_relations"][0]["evidence"][0]["source_id"]
    assert evidence in {source["id"] for source in response.results[0].sources}
    assert any(evidence in edge["evidence_ids"] for edge in result["graph"]["edges"])


class RecordingMusicService(FakeMusicService):
    def __init__(self, root):
        super().__init__(root)
        self.searches, self.loaded = [], []

    async def search_entities(self, query, expected_entity_type, *, limit=5):
        self.searches.append(query)
        return await super().search_entities(query, expected_entity_type, limit=limit)

    async def load_entity_facts(self, root):
        self.loaded.append(root)
        return await super().load_entity_facts(root)


@pytest.mark.asyncio
async def test_explorer_current_song_uses_scoped_read_tool_and_skips_catalog_guess():
    music = RecordingMusicService(None)
    context = provider()
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=transfers((G, C, E)),
        provider=context,
        worldline_subgraph=worldline(service=music),
    ).compile()
    response = await invoke_handoff_assistant(
        message="探索当前歌曲的世界线",
        thread_id="current-explore",
        routing_context=player_context(),
        graph=graph,
    )
    assert response.status == TaskStatus.SUCCESS
    context.get_song.assert_awaited_once_with(11)
    assert music.searches == [] and music.loaded[0].entity_id == "netease:song:11"
    assert (
        response.results[0].metadata["worldline_result"]["root_entity"]["entity_id"]
        == "netease:song:11"
    )
    assert "private lyrics" not in response.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["search", "play"])
async def test_curator_to_explorer_preserves_catalog_target_and_transfer_entities(
    source,
):
    track = MusicTrack(song_id=31, name="晴天", artists=["周杰伦"], album="叶惠美")
    responses = [
        call("search_music", args={"keyword": "晴天", "limit": 1}, call_id="search")
    ]
    if source == "play":
        responses.append(
            call("play_song", args={"song": track.model_dump()}, call_id="play")
        )
    responses.append(AIMessage(content="已处理音乐任务。"))
    text = "搜索晴天，不要播放" if source == "search" else "搜索晴天并播放"
    music, context, searches = RecordingMusicService(None), provider(), []
    context.get_song.return_value = song().model_copy(
        update={"song_id": 31, "name": "晴天", "artist": "周杰伦"}
    )
    model = transfers((G, C, E))
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE),
        transfer_model=model,
        curator_model=ToolModel(responses=responses),
        tool_tools=[search_tool(track, searches), *CLIENT_ACTION_TOOLS],
        provider=context,
        worldline_subgraph=worldline(service=music),
    ).compile(
        checkpointer=create_memory_checkpointer(), interrupt_before=["explorer_agent"]
    )
    config = {"configurable": {"thread_id": f"target-{source}"}}
    paused = await graph.ainvoke(
        input_state(
            f"{text}；探索这首歌的世界线",
            context=RoutingContext(has_current_song=True, current_song_id=99),
        ),
        config,
    )
    assert paused["transfer_envelope"]["entities"]["songs"][0]["song_id"] == 31
    output = await graph.ainvoke(None, config)
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED and len(run.results) == 2
    assert music.searches == [] and music.loaded[0].entity_id == "netease:song:31"
    assert run.plan.tasks[1].input_text == "探索这首歌的世界线"
    if source == "search":
        context.get_song.assert_not_awaited()
        assert not run.results[0].data["client_actions"]
    else:
        context.get_song.assert_awaited_once_with(31)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "problem", ["missing", "context_error", "pending", "many_songs"]
)
async def test_song_reference_problems_stop_exploration_before_facts_or_later_actions(
    problem,
):
    from app.agent.foundation.models import TaskResult
    from app.agent.foundation.subgraphs.tool_calling.models import (
        ReferencedEntities,
        SongReference,
    )

    context, music = provider(), RecordingMusicService(None)
    routes, text, subgraphs = (
        (RouteKey.WORLDLINE, RouteKey.TOOL_CALLING),
        "探索当前歌曲的世界线；暂停一下",
        None,
    )
    routing = RoutingContext() if problem == "missing" else player_context()
    if problem == "context_error":
        context.get_song.side_effect = RuntimeError("private token")
    if problem in {"pending", "many_songs"}:
        routes = (RouteKey.TOOL_CALLING, RouteKey.WORLDLINE, RouteKey.TOOL_CALLING)
        text = "选择歌曲；探索这首歌的世界线；暂停一下"
        data = (
            {
                "client_actions": [
                    {
                        "action_id": "task-0:next",
                        "task_id": "task-0",
                        "name": "play_next",
                        "arguments": {},
                    }
                ]
            }
            if problem == "pending"
            else {
                "entities": ReferencedEntities(
                    songs=[
                        SongReference(position=1, song_id=31, name="晴天"),
                        SongReference(position=2, song_id=32, name="晴天"),
                    ]
                ).model_dump()
            }
        )
        from langgraph.graph import StateGraph, START, END
        from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState

        stub = StateGraph(ToolCallingState)
        stub.add_node(
            "fixture",
            lambda state: {
                "task_result": TaskResult(
                    task_id=state["task_id"],
                    route_key=RouteKey.TOOL_CALLING,
                    status=TaskStatus.SUCCESS,
                    content="已发送切歌指令。"
                    if problem == "pending"
                    else "搜索结果。",
                    data=data,
                )
            },
        )
        stub.add_edge(START, "fixture")
        stub.add_edge("fixture", END)
        subgraphs = {RouteKey.TOOL_CALLING: stub.compile()}
    graph = build_handoff_graph(
        intent_router=Router(*routes),
        transfer_model=transfers((G, C, E, C)),
        provider=context,
        worldline_subgraph=worldline(service=music),
        subgraphs=subgraphs,
    ).compile()
    output = await graph.ainvoke(input_state(text, context=routing))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is (
        RunStatus.FAILED if problem == "context_error" else RunStatus.NEEDS_INPUT
    )
    assert (
        run.results[-1].route_key == RouteKey.WORLDLINE and len(run.pending_tasks) == 1
    )
    assert music.loaded == [] and music.searches == []
    assert "private" not in run.final_response
    if problem != "context_error":
        context.get_song.assert_not_awaited()


@pytest.mark.asyncio
async def test_explorer_cannot_turn_catalog_graph_identity_into_playable_track():
    track = MusicTrack(song_id=31, name="晴天", artists=["周杰伦"], album="叶惠美")
    curator = ToolModel(
        responses=[
            call("play_song", args={"song": track.model_dump()}),
            AIMessage(content="需要先搜索确认歌曲。"),
        ]
    )
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING),
        transfer_model=transfers((G, C, E, C)),
        curator_model=curator,
        worldline_subgraph=worldline(
            service=FakeMusicService(
                entity(21, EntityType.ARTIST, "周杰伦"),
                [entity(31, EntityType.SONG, "晴天")],
            )
        ),
    ).compile()
    output = await graph.ainvoke(input_state(f"{TEXT[RouteKey.WORLDLINE]}；播放晴天"))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert (
        run.status is RunStatus.FAILED and run.results[0].status is TaskStatus.SUCCESS
    )
    assert not run.results[1].data["client_actions"]
    assert any(
        "untrusted_entity_reference" in str(item.content)
        for item in curator.seen_messages[-1]
        if isinstance(item, ToolMessage)
    )


@pytest.mark.asyncio
async def test_new_turn_worldline_results_do_not_accumulate_under_reused_task_ids():
    graph = create_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=transfers((G, C, E)),
        worldline_subgraph=worldline(),
        checkpointer=create_memory_checkpointer(),
    )
    for _ in range(2):
        response = await invoke_handoff_assistant(
            message=TEXT[RouteKey.WORLDLINE],
            thread_id="repeat-explore",
            routing_context=RoutingContext(),
            graph=graph,
        )
        assert response.status == TaskStatus.SUCCESS and len(response.results) == 1
    state = await graph.aget_state(
        {"configurable": {"thread_id": "handoff:v1:repeat-explore"}}
    )
    run = HandoffRun.model_validate(state.values["handoff_run"])
    assert run.handoff_count == 2 and len(run.results) == 1


@pytest.mark.asyncio
async def test_curator_receives_bounded_exploration_projection_and_searches_before_play():
    from app.agent.handoff.context import exploration_projection

    track = MusicTrack(song_id=31, name="晴天", artists=["周杰伦"], album="叶惠美")
    searches = []
    curator = ToolModel(
        responses=[
            call(
                "search_music", args={"keyword": "晴天", "limit": 1}, call_id="catalog"
            ),
            call("play_song", args={"song": track.model_dump()}, call_id="listen"),
            AIMessage(content="已发送播放指令。"),
        ]
    )
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING),
        transfer_model=transfers((G, C, E, C)),
        curator_model=curator,
        tool_tools=[search_tool(track, searches), *CLIENT_ACTION_TOOLS],
        worldline_subgraph=worldline(
            service=FakeMusicService(
                entity(21, EntityType.ARTIST, "周杰伦"),
                [entity(31, EntityType.SONG, "晴天")],
            )
        ),
    ).compile()
    output = await graph.ainvoke(
        input_state(f"{TEXT[RouteKey.WORLDLINE]}；播放探索中发现的那首歌")
    )
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED and searches == [("晴天", 1)]
    prompt = "".join(str(item.content) for item in curator.seen_messages[0])
    assert (
        "completed_exploration" in prompt
        and "晴天" in prompt
        and "action_authority" in prompt
    )
    projection = exploration_projection(run.results)
    assert "worldline_result" not in projection and "playable" not in str(projection)
    assert len(projection["entities"]) <= 8 and len(projection["branches"]) <= 3
    assert run.results[1].data["client_actions"][0]["name"] == "play_song"


@pytest.mark.asyncio
async def test_search_artist_reference_is_grounded_before_explorer_query():
    track = MusicTrack(song_id=31, name="晴天", artists=["周杰伦"], album="叶惠美")
    music, context = (
        RecordingMusicService(entity(21, EntityType.ARTIST, "周杰伦")),
        provider(),
    )
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE),
        transfer_model=transfers((G, C, E)),
        curator_model=ToolModel(
            responses=[
                call("search_music", args={"keyword": "周杰伦", "limit": 1}),
                AIMessage(content="找到了歌曲。"),
            ]
        ),
        tool_tools=[search_tool(track, []), *CLIENT_ACTION_TOOLS],
        provider=context,
        worldline_subgraph=worldline(service=music),
    ).compile()
    output = await graph.ainvoke(input_state("搜索周杰伦，不要播放；探索他的世界线"))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED and music.searches == ["周杰伦"]
    assert run.plan.tasks[1].input_text == "探索他的世界线"
    context.get_song.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_general_to_explorer_is_rejected_before_any_exploration():
    music = RecordingMusicService(None)
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=ToolModel(responses=[call("transfer_to_explorer_agent")]),
        worldline_subgraph=worldline(service=music),
    ).compile()
    output = await graph.ainvoke(input_state(TEXT[RouteKey.WORLDLINE]))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert (
        run.status is RunStatus.FAILED and run.handoff_count == 0 and run.results == ()
    )
    assert music.searches == [] and music.loaded == []


@pytest.mark.asyncio
async def test_curator_relay_does_not_bind_or_execute_music_business_tools():
    curator = ToolModel(responses=[call("pause_player", args={})])
    transfer = transfers((G, C, E))
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=transfer,
        curator_model=curator,
        worldline_subgraph=worldline(),
    ).compile()
    output = await graph.ainvoke(input_state(TEXT[RouteKey.WORLDLINE]))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert (
        run.status is RunStatus.COMPLETED
        and curator.seen_messages == []
        and curator.bound_names == []
    )
    assert len(run.results) == 1 and run.results[0].route_key == RouteKey.WORLDLINE
    assert set(transfer.bound_names[1]) == {
        "transfer_to_general_chat_agent",
        "transfer_to_explorer_agent",
    }


@pytest.mark.asyncio
async def test_explorer_must_relay_through_curator_to_return_to_general():
    general = ToolModel(responses=[AIMessage(content="不应执行")])
    transfer = ToolModel(
        responses=[
            call(),
            call("transfer_to_explorer_agent", call_id="explore"),
            call("transfer_to_general_chat_agent", call_id="illegal"),
        ]
    )
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE, RouteKey.GENERAL_CHAT),
        transfer_model=transfer,
        general_model=general,
        worldline_subgraph=worldline(),
    ).compile()
    output = await graph.ainvoke(input_state(f"{TEXT[RouteKey.WORLDLINE]}；陪我聊聊"))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert (
        run.status is RunStatus.FAILED
        and run.handoff_count == 2
        and len(run.results) == 1
    )
    assert transfer.bound_names[-1] == ["transfer_to_curator_agent"]
    assert general.seen_messages == []


@pytest.mark.asyncio
async def test_sixth_hop_stops_network_with_completed_results_preserved():
    transfer = transfers((G, C, E, C, G, C, E))
    graph = build_handoff_graph(
        intent_router=Router(
            RouteKey.WORLDLINE, RouteKey.GENERAL_CHAT, RouteKey.WORLDLINE
        ),
        transfer_model=transfer,
        general_model=ToolModel(responses=[AIMessage(content="可以聊聊。")]),
        worldline_subgraph=worldline(),
    ).compile()
    output = await graph.ainvoke(
        input_state(f"{TEXT[RouteKey.WORLDLINE]}；陪我聊聊；{TEXT[RouteKey.WORLDLINE]}")
    )
    run = HandoffRun.model_validate(output["handoff_run"])
    assert (
        run.status is RunStatus.FAILED
        and run.handoff_count == 5
        and len(run.results) == 2
    )
    assert run.handoff_chain == (G, C, E, C, G, C)
    assert run.pending_tasks[0].task_id == "task-2"
    assert len(transfer.seen_messages) == 6


@pytest.mark.asyncio
async def test_default_explorer_factory_is_lazy_cached_and_uses_existing_worldline(
    monkeypatch,
):
    from app.agent.handoff.explorer import get_worldline_subgraph

    built = []

    def factory():
        built.append(True)
        return worldline()

    get_worldline_subgraph.cache_clear()
    monkeypatch.setattr("app.agent.handoff.explorer.create_worldline_subgraph", factory)
    try:
        graph = create_handoff_graph(
            intent_router=Router(RouteKey.WORLDLINE),
            transfer_model=transfers((G, C, E)),
            checkpointer=create_memory_checkpointer(),
        )
        assert built == []
        for turn in range(2):
            response = await invoke_handoff_assistant(
                message=TEXT[RouteKey.WORLDLINE],
                thread_id=f"lazy-{turn}",
                routing_context=RoutingContext(),
                graph=graph,
            )
            assert response.status == TaskStatus.SUCCESS
        assert built == [True]
    finally:
        get_worldline_subgraph.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["task_id", "route", "schema", "target"])
async def test_mismatched_explorer_result_is_failed_before_publication_or_action(
    problem,
):
    normal = (
        worldline(service=RecordingMusicService(None))
        if problem == "target"
        else worldline()
    )

    class CorruptGraph:
        async def ainvoke(self, private, **kwargs):
            output = await normal.ainvoke(private, **kwargs)
            result = output["task_result"]
            if problem == "task_id":
                result = result.model_copy(update={"task_id": "other"})
            elif problem == "route":
                result = result.model_copy(update={"route_key": RouteKey.GENERAL_CHAT})
            elif problem == "schema":
                result = result.model_copy(
                    update={"data": {"worldline_result": {"private": "invalid"}}}
                )
            else:
                data = result.model_dump()["data"]
                data["worldline_result"]["root_entity"]["entity_id"] = (
                    "netease:song:999"
                )
                data["worldline_result"]["graph"]["root"] = "netease:song:999"
                data["worldline_result"]["graph"]["nodes"][0]["id"] = "netease:song:999"
                result = result.model_copy(update={"data": data})
            return {"task_result": result}

    text = "探索当前歌曲的世界线" if problem == "target" else TEXT[RouteKey.WORLDLINE]
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING),
        transfer_model=transfers((G, C, E, C)),
        provider=provider(),
        worldline_subgraph=CorruptGraph(),
    ).compile()
    response = await invoke_handoff_assistant(
        message=f"{text}；暂停一下",
        thread_id=f"corrupt-{problem}",
        routing_context=player_context(),
        graph=graph,
    )
    assert response.status == TaskStatus.FAILED and len(response.results) == 1
    assert (
        response.results[0].status == TaskStatus.FAILED
        and response.results[0].subgraph == "worldline"
    )
    metadata = response.results[0].metadata
    assert "private" not in response.model_dump_json()
    # Only parent-owned failure/progress data may escape; the corrupt child
    # graph, target and metadata remain excluded from the public result.
    assert set(metadata) == {"reason"}
    assert metadata["reason"] == "task_failed"

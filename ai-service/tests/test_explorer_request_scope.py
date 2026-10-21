"""A split journey task keeps its current-request root and evidenced node count."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RouteKey, RoutingContext, RouteDecision, RouteTask, IntentItem, IntentType, IntentSource
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import EntityType
from app.agent.handoff.explorer import execute_exploration, journey_node_limit, scoped_journey_query, explicit_journey_song_id
from app.agent.handoff.journey_seed import read_journey_seed
from app.agent.handoff.journey import MAX_JOURNEY_NODES, build_listening_journey
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.song_context import create_song_context_tool
from app.agent.handoff.state import create_handoff_state
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.state import HandoffRun, RunStatus
from tests.test_explorer_journey_v1 import knowledge_world
from tests.test_handoff_contracts import make_run
from tests.test_handoff_runtime import song, ToolModel, call, input_state
from tests.test_worldline_graph import FakeGenerator, FakeMusicService, EmptyRetriever, entity


ORIGINAL = json.loads((Path(__file__).parent / "fixtures/tool_call_limit_traces.json").read_text(encoding="utf-8"))["item5"]["request"]["message"]
CHILD = "规划一个3节点的音乐探索旅程"


def state_for(original=ORIGINAL):
    child = CHILD if CHILD in original else original
    run = make_run(RouteKey.WORLDLINE, entry=AgentKey.EXPLORER, texts=[child])
    start = original.index(child)
    intent = run.plan.intents[0].model_copy(update={"start": start, "end": start + len(child)})
    plan = run.plan.model_copy(update={"intents": [intent]})
    run = type(run).model_validate({**run.model_dump(), "original_input": original, "plan": plan.model_dump()})
    routing = RoutingContext(has_current_song=True, current_song_id=186016)
    return run, create_handoff_state(run, routing_context=routing)


@pytest.mark.asyncio
async def test_split_child_uses_verified_current_request_seed_and_emits_three_nodes():
    run, view = state_for()
    task_before = run.pending_tasks[0].model_dump_json()
    root = entity(186016, EntityType.SONG, "晴天")
    music = FakeMusicService(root, [entity(6452, EntityType.ARTIST, "周杰伦"), entity(18918, EntityType.ALBUM, "测试专辑")])
    music.search_entities = AsyncMock(wraps=music.search_entities)
    graph = create_worldline_subgraph(music_service=music, retriever=EmptyRetriever(), generator=FakeGenerator(grounded=True))
    provider = SimpleNamespace(get_song=AsyncMock(return_value=song().model_copy(update={"song_id": 186016, "name": "晴天", "artist": "周杰伦"})))
    context_tool = create_song_context_tool(view["routing_context"], provider=provider)
    result = await execute_exploration(view, run, SimpleNamespace(context=None), graph=graph, context_tool=context_tool)
    assert result.status is TaskStatus.SUCCESS, result.content
    assert result.data["worldline_result"]["root_entity"]["entity_id"] == "netease:song:186016"
    assert len(result.data["listening_journey"]["nodes"]) == len(result.data["journey_queue"]) == 3
    assert all(node["narrative"] and node["narration"]["source_ids"] for node in result.data["listening_journey"]["nodes"])
    assert all(node["song_id"] in {None, 186016} for node in result.data["listening_journey"]["nodes"])
    assert not result.data.get("client_actions")
    provider.get_song.assert_awaited_once_with(186016)
    music.search_entities.assert_not_awaited()
    assert run.pending_tasks[0].model_dump_json() == task_before and run.original_input == ORIGINAL


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,expected", [({"name": "稻香"}, "explicit_song_target_mismatch"), ({"song_id": 186017}, "context_unavailable")])
async def test_seed_mismatch_never_reaches_worldline_or_creates_a_journey(changes, expected):
    run, view = state_for()
    graph = SimpleNamespace(ainvoke=AsyncMock())
    data = song().model_copy(update={"song_id": 186016, "name": "晴天", **changes})
    tool = create_song_context_tool(view["routing_context"], provider=SimpleNamespace(get_song=AsyncMock(return_value=data)))
    result = await execute_exploration(view, run, SimpleNamespace(context=None), graph=graph, context_tool=tool)
    assert result.status in {TaskStatus.NEEDS_INPUT, TaskStatus.FAILED}
    assert result.data["reason"] == expected and not result.data.get("journey_queue")
    graph.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("original", ["规划0节点音乐旅程", "规划3节点旅程，也要规划4节点旅程"])
async def test_invalid_node_count_refuses_before_reads(original):
    run, view = state_for(original)
    graph = SimpleNamespace(ainvoke=AsyncMock())
    context = SimpleNamespace(ainvoke=AsyncMock())
    result = await execute_exploration(view, run, SimpleNamespace(context=None), graph=graph, context_tool=context)
    assert result.status is TaskStatus.NEEDS_INPUT and result.data["reason"] == "invalid_journey_node_count"
    graph.ainvoke.assert_not_awaited()
    context.ainvoke.assert_not_awaited()


def test_other_explicit_root_ambiguous_parent_and_branch_followup_stay_scoped():
    own = "探索歌曲《稻香》的世界线"
    assert scoped_journey_query(own, ORIGINAL) == own
    assert explicit_journey_song_id(own, ORIGINAL) is None
    assert scoped_journey_query(CHILD, "以《晴天》和《稻香》为起点") == CHILD
    assert scoped_journey_query("继续展开刚才的分支", ORIGINAL) == "继续展开刚才的分支"
    assert scoped_journey_query("探索当前歌曲的世界线", ORIGINAL) == "探索当前歌曲的世界线"
    assert explicit_journey_song_id("探索《晴天》", "探索《晴天》，song_id=186016.5") is None


@pytest.mark.parametrize("text,count", [(CHILD, 3), ("规划三个节点旅程", 3), ("规划99节点旅程", MAX_JOURNEY_NODES), ("展开第3个节点", MAX_JOURNEY_NODES)])
def test_node_count_preserves_existing_hard_bound(text, count):
    assert journey_node_limit(text) == count


def test_requested_prefix_keeps_catalog_ids_evidence_and_null_knowledge_ids():
    world, facts = knowledge_world()
    complete = build_listening_journey(world, facts)
    prefix = build_listening_journey(world, facts, node_limit=2)
    assert prefix.nodes == complete.nodes[:2]
    assert all(node.narration.source_ids for node in prefix.nodes)
    assert prefix.nodes[0].node_id == world.graph.root
    for invalid in (0, -1, 9, True):
        with pytest.raises(ValueError, match="existing bound"):
            build_listening_journey(world, facts, node_limit=invalid)


@pytest.mark.asyncio
async def test_seed_only_child_reads_once_and_preserves_existing_phase_counts():
    text = ORIGINAL.split("，规划", 1)[0]
    task = make_run(RouteKey.TOOL_CALLING, texts=[text]).pending_tasks[0]
    routing = RoutingContext(has_current_song=True, current_song_id=186016)
    provider = SimpleNamespace(get_song=AsyncMock(return_value=song().model_copy(update={"song_id": 186016, "name": "晴天"})))
    result = await read_journey_seed(task, ORIGINAL, routing, create_song_context_tool(routing, provider=provider))
    assert result.status is TaskStatus.SUCCESS and result.task_id == task.task_id
    assert result.data["entities"]["songs"][0]["song_id"] == 186016
    assert result.data["tool_phase_counts"] == {"retrieval": 1, "execution": 0, "total": 1}
    assert not result.data.get("client_actions")
    provider.get_song.assert_awaited_once_with(186016)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["播放歌曲《晴天》，以它为起点", "搜索歌曲《晴天》，以它为起点", CHILD])
async def test_seed_shortcut_does_not_replace_an_assigned_action_or_planning_task(text):
    task = make_run(RouteKey.TOOL_CALLING, texts=[text]).pending_tasks[0]
    context = SimpleNamespace(ainvoke=AsyncMock())
    result = await read_journey_seed(task, ORIGINAL, RoutingContext(has_current_song=True, current_song_id=186016), context)
    assert result is None
    context.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_general_curator_explorer_chain_completes_read_only_three_node_journey():
    seed = ORIGINAL.split("，规划", 1)[0]
    clauses = [seed, CHILD]
    routes = [RouteKey.TOOL_CALLING, RouteKey.WORLDLINE]
    intents = [IntentItem(intent=IntentType(route.value), source=IntentSource.RULE, confidence=1,
                          evidence=text, start=ORIGINAL.index(text), end=ORIGINAL.index(text)+len(text), order=index)
               for index, (route, text) in enumerate(zip(routes, clauses, strict=True))]
    tasks = [RouteTask(task_id=f"scope-{index}", route_key=route, input_text=text, intent_orders=[index],
                       depends_on=["scope-0"] if index else [])
             for index, (route, text) in enumerate(zip(routes, clauses, strict=True))]
    decision = RouteDecision(intents=intents, tasks=tasks, is_complete=True, coverage=1)
    class Router:
        async def route(self, text, context):
            assert text == ORIGINAL
            return decision
    root = entity(186016, EntityType.SONG, "晴天")
    music = FakeMusicService(root, [entity(6452, EntityType.ARTIST, "周杰伦"), entity(18918, EntityType.ALBUM, "测试专辑")])
    worldline = create_worldline_subgraph(music_service=music, retriever=EmptyRetriever(), generator=FakeGenerator(grounded=True))
    provider = SimpleNamespace(get_song=AsyncMock(return_value=song().model_copy(update={"song_id": 186016, "name": "晴天"})))
    transfers = ToolModel(responses=[call(), call("transfer_to_explorer_agent", call_id="to-explorer")])
    curator = SimpleNamespace(ainvoke=AsyncMock(side_effect=AssertionError("seed-only task must use verified facts")))
    graph = build_handoff_graph(intent_router=Router(), transfer_model=transfers, curator_model=curator,
                                worldline_subgraph=worldline, provider=provider, tool_tools=[]).compile()
    output = await graph.ainvoke(input_state(ORIGINAL, context=RoutingContext(has_current_song=True, current_song_id=186016)))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED
    assert run.handoff_chain == (AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.EXPLORER)
    assert run.original_input == ORIGINAL and run.plan.tasks == tasks
    assert run.results[0].data["tool_phase_counts"] == {"retrieval": 1, "execution": 0, "total": 1}
    journey = run.results[1].data["listening_journey"]
    assert len(journey["nodes"]) == len(run.results[1].data["journey_queue"]) == 3
    assert all(node["narration"]["source_ids"] for node in journey["nodes"])
    assert not any(result.data.get("client_actions") for result in run.results)
    curator.ainvoke.assert_not_awaited()

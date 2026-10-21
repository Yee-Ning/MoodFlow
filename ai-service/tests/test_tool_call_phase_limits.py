"""Saved real prefixes plus explicitly controlled continuations; no live writes."""

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.foundation.state.main import create_main_state
from app.agent.foundation.subgraphs.tool_calling import create_tool_calling_subgraph
from app.agent.foundation.subgraphs.tool_calling.client_tools import set_volume_tool
from app.agent.foundation.subgraphs.tool_calling.graph import (
    MAX_TOOL_CALLS,
    TOOL_LIMIT_MESSAGE,
    TOOL_PHASE_LIMITS,
    _tool_call_phase,
)
from app.schemas.context import MusicAgentContext
from app.schemas.music import AddSongToPlaylistArgs, MusicSearchResult, SearchMusicArgs
from tests.test_tool_calling_subgraph import scripted_assistant, tool_call

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/tool_call_limit_traces.json").read_text(
        encoding="utf-8"
    )
)


def batch(calls):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": f"call-{index}-{row['name']}",
                "name": row["name"],
                "args": row.get("args", {}),
                "type": "tool_call",
            }
            for index, row in enumerate(calls)
        ],
    )


def reads(count, prefix="read"):
    return [
        tool_call("search_music", f"{prefix}-{i}", {"keyword": f"{prefix} {i}"})
        for i in range(count)
    ]


def commands(count):
    return [
        tool_call("set_volume", f"volume-{i}", {"volume": i / 10}) for i in range(count)
    ]


def search_tool(case, seen):
    @tool("search_music", args_schema=SearchMusicArgs)
    async def search(keyword: str, limit: int = 10) -> str:
        """Return catalog tracks saved by the original validation request."""
        seen.append(
            {"name": "search_music", "args": {"keyword": keyword, "limit": limit}}
        )
        return MusicSearchResult(
            keyword=keyword, limit=limit, songs=case["songs"]
        ).model_dump_json()

    return search


async def invoke(graph, text="搜索并设置音量"):
    return await graph.ainvoke(
        {
            "messages": [HumanMessage(content=text)],
            "current_input": text,
            "task_id": "phase-limit-test",
            "routing_context": RoutingContext(),
            "routing_intents": [],
            "prior_entities": [],
            "tool_calls": [],
            "tool_results": [],
            "client_actions": [],
            "tool_call_count": 0,
            "direct_complete": False,
            "answer": None,
            "task_result": None,
        },
        context=MusicAgentContext(),
        config={"recursion_limit": 200},
    )


@pytest.mark.parametrize("row", FIXTURE["phase_inventory"], ids=lambda r: r["name"])
def test_registered_tool_phase_matches_gate0_inventory(row):
    assert _tool_call_phase(row["name"]) == row["proposed_phase"]


@pytest.mark.asyncio
async def test_item3_saved_prefix_and_required_three_additions_complete():
    case, seen = FIXTURE["item3"], []

    @tool("create_playlist_tool")
    async def create(name: str, privacy: int = 0) -> str:
        """Return the original saved creation receipt without touching a service."""
        seen.append(
            {"name": "create_playlist_tool", "args": {"name": name, "privacy": privacy}}
        )
        return json.dumps(
            {
                "playlist_id": case["playlist_id"],
                "name": name,
                "status": "created",
                "message": "已创建",
            }
        )

    @tool("add_song_to_playlist", args_schema=AddSongToPlaylistArgs)
    async def add(playlist_id: int, song_id: int) -> str:
        """Simulate only the minimum additions required by the original request."""
        seen.append(
            {
                "name": "add_song_to_playlist",
                "args": {"playlist_id": playlist_id, "song_id": song_id},
            }
        )
        return json.dumps(
            {
                "playlist_id": playlist_id,
                "song_id": song_id,
                "status": "added",
                "message": "已添加",
            }
        )

    prefix = [
        tool_call(r["name"], f"saved-{i}", r["args"])
        for i, r in enumerate(case["observed_calls"])
    ]
    additions = batch(
        [
            {
                "name": "add_song_to_playlist",
                "args": {"playlist_id": case["playlist_id"], "song_id": s["song_id"]},
            }
            for s in case["songs"][:3]
        ]
    )
    graph = create_tool_calling_subgraph(
        tools=[search_tool(case, seen), create, add],
        assistant=scripted_assistant(
            [*prefix, additions, AIMessage(content="已完成。")]
        ),
    )
    result = (await invoke(graph, case["request"]["message"]))["task_result"]
    assert result.status is TaskStatus.SUCCESS
    assert len([r for r in seen if r["name"] == "add_song_to_playlist"]) == 3
    assert seen[:3] == case["observed_calls"]
    assert result.data["tool_calls"] == 6
    assert result.data["tool_call_counts_by_phase"] == {"retrieval": 2, "execution": 4}
    assert "tool_budget_stop" not in result.data


@pytest.mark.asyncio
async def test_item5_saved_prefix_and_controlled_read_batch_allow_worldline():
    case, seen, world_seen = FIXTURE["item5"], [], []
    prefix = [
        tool_call(r["name"], f"saved-{i}", r["args"])
        for i, r in enumerate(case["observed_calls"])
    ]
    # The refused original batch was not persisted. These two explicit boundary
    # reads reproduce the old 4+2 rejection, without inventing original output.
    extra = batch(
        [
            {
                "name": "search_music",
                "args": {"keyword": f"受控补充目录 {i}", "limit": 5},
            }
            for i in range(2)
        ]
    )
    tools = create_tool_calling_subgraph(
        tools=[search_tool(case, seen)],
        assistant=scripted_assistant([*prefix, extra, AIMessage(content="前置完成。")]),
    )

    class World:
        async def ainvoke(self, state, **kwargs):
            world_seen.append(state)
            return {
                "task_result": TaskResult(
                    task_id=state["task_id"],
                    route_key=RouteKey.WORLDLINE,
                    status=TaskStatus.SUCCESS,
                    content="受控worldline已执行。",
                )
            }

    graph = build_main_graph(
        development_routes=(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE),
        tool_calling_subgraph=tools,
        worldline_subgraph=World(),
    ).compile(checkpointer=False)
    result = await graph.ainvoke(
        create_main_state(
            case["request"]["message"],
            routing_context=RoutingContext(
                current_song_id=186016, has_current_song=True
            ),
        ),
        context=MusicAgentContext(),
        config={"recursion_limit": 200},
    )
    assert seen[:4] == case["observed_calls"]
    assert len(world_seen) == 1
    assert [r.status for r in result["task_results"]] == [
        TaskStatus.SUCCESS,
        TaskStatus.SUCCESS,
    ]
    assert result["task_results"][0].data["tool_call_counts_by_phase"] == {
        "retrieval": 6,
        "execution": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["retrieval", "execution"])
async def test_single_phase_loop_stops_at_phase_budget_with_original_signal(
    phase, caplog
):
    seen = []
    responses = reads(9) if phase == "retrieval" else commands(9)
    graph = create_tool_calling_subgraph(
        tools=[search_tool(FIXTURE["item5"], seen), set_volume_tool],
        assistant=scripted_assistant(responses),
    )
    result = (await invoke(graph))["task_result"]
    assert result.status is TaskStatus.FAILED
    assert result.content == result.error == TOOL_LIMIT_MESSAGE
    assert result.data["tool_calls"] == TOOL_PHASE_LIMITS[phase] == 8
    stop = result.data["tool_budget_stop"]
    assert stop["budget"] == phase
    assert stop["used"][phase] == 8 and stop["used"]["total"] == 8
    assert stop["requested"][phase] == 1
    assert "Tool call budget exceeded" in caplog.text and phase in caplog.text
    assert len(result.data["client_actions"]) == (8 if phase == "execution" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["retrieval", "execution"])
async def test_exact_phase_budget_is_allowed(phase):
    seen = []
    responses = reads(8) if phase == "retrieval" else commands(8)
    graph = create_tool_calling_subgraph(
        tools=[search_tool(FIXTURE["item5"], seen), set_volume_tool],
        assistant=scripted_assistant([*responses, AIMessage(content="已完成。")]),
    )
    result = (await invoke(graph))["task_result"]
    assert result.status is TaskStatus.SUCCESS
    assert (
        result.data["tool_calls"] == 8
        and result.data["tool_call_counts_by_phase"][phase] == 8
    )
    assert "tool_budget_stop" not in result.data


@pytest.mark.asyncio
async def test_total_fallback_preserves_prior_actions_without_exceeding_either_phase():
    seen = []
    graph = create_tool_calling_subgraph(
        tools=[search_tool(FIXTURE["item5"], seen), set_volume_tool],
        assistant=scripted_assistant([*reads(8), *commands(5)]),
    )
    result = (await invoke(graph))["task_result"]
    assert result.status is TaskStatus.FAILED and result.content == TOOL_LIMIT_MESSAGE
    assert result.data["tool_calls"] == MAX_TOOL_CALLS == 12
    assert result.data["tool_call_counts_by_phase"] == {"retrieval": 8, "execution": 4}
    assert len(seen) == 8 and len(result.data["client_actions"]) == 4
    assert result.data["tool_budget_stop"]["budget"] == "total"
    assert result.data["tool_budget_stop"]["exceeded_budgets"] == ["total"]
    assert result.data["tool_budget_stop"]["requested"] == {
        "retrieval": 0,
        "execution": 1,
        "total": 1,
    }


@pytest.mark.asyncio
async def test_oversized_first_batch_is_entirely_rejected():
    seen = []
    graph = create_tool_calling_subgraph(
        tools=[search_tool(FIXTURE["item5"], seen)],
        assistant=scripted_assistant(
            [
                batch(
                    [
                        {"name": "search_music", "args": {"keyword": str(i)}}
                        for i in range(9)
                    ]
                )
            ]
        ),
    )
    result = (await invoke(graph))["task_result"]
    assert not seen and not result.data["tool_call_details"]
    assert result.data["tool_calls"] == 0
    assert result.data["tool_call_counts_by_phase"] == {"retrieval": 0, "execution": 0}
    assert result.content == TOOL_LIMIT_MESSAGE


@pytest.mark.asyncio
async def test_mixed_batch_is_entirely_rejected_when_only_read_phase_overflows():
    seen = []
    mixed = batch(
        [
            {"name": "search_music", "args": {"keyword": "over"}},
            {"name": "set_volume", "args": {"volume": 0.5}},
        ]
    )
    graph = create_tool_calling_subgraph(
        tools=[search_tool(FIXTURE["item5"], seen), set_volume_tool],
        assistant=scripted_assistant([*reads(8), mixed]),
    )
    result = (await invoke(graph))["task_result"]
    assert len(seen) == 8 and not result.data["client_actions"]
    assert result.data["tool_calls"] == 8
    assert result.data["tool_budget_stop"]["budget"] == "retrieval"
    assert result.data["tool_budget_stop"]["requested"] == {
        "retrieval": 1,
        "execution": 1,
        "total": 2,
    }


@pytest.mark.asyncio
async def test_later_validation_rejections_still_consume_admitted_attempts():
    seen = []
    unknown = batch([{"name": "unknown_tool", "args": {}} for _ in range(8)])
    graph = create_tool_calling_subgraph(
        tools=[search_tool(FIXTURE["item5"], seen)],
        assistant=scripted_assistant(
            [unknown, tool_call("search_music", "next", {"keyword": "new"})]
        ),
    )
    result = (await invoke(graph))["task_result"]
    assert not seen and result.data["tool_calls"] == 8
    assert len(result.data["tool_call_details"]) == 8
    assert result.data["tool_call_counts_by_phase"] == {"retrieval": 8, "execution": 0}
    assert result.data["tool_budget_stop"]["budget"] == "retrieval"


@pytest.mark.asyncio
async def test_item7_search_prefix_stays_compatible_and_each_invocation_resets():
    case, seen = FIXTURE["item7-search"], []
    prefix = [
        tool_call(r["name"], f"saved-{i}", r["args"])
        for i, r in enumerate(case["observed_calls"])
    ]
    graph = create_tool_calling_subgraph(
        tools=[search_tool(case, seen)],
        assistant=scripted_assistant(
            [*prefix, AIMessage(content="完成。"), *prefix, AIMessage(content="完成。")]
        ),
    )
    for _ in range(2):
        result = (await invoke(graph, case["request"]["message"]))["task_result"]
        assert result.status is TaskStatus.SUCCESS
        assert result.data["tool_calls"] == 2
        assert result.data["tool_call_counts_by_phase"] == {
            "retrieval": 2,
            "execution": 0,
        }
        assert "tool_budget_stop" not in result.data
    assert seen == case["observed_calls"] * 2

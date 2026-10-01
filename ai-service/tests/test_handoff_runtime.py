"""Phase 2 graph, tool and HTTP/SSE acceptance with controlled dependencies."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from pydantic import Field, ValidationError

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.subgraphs.tool_calling.client_tools import CLIENT_ACTION_TOOLS
from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    RouteDecision,
    RouteKey,
    RouteTask,
    RoutingContext,
)
from app.agent.foundation.state.main import create_main_state
from app.agent.foundation.streaming import invoke_foundation_assistant
from app.agent.handoff.graph import build_handoff_graph, create_handoff_graph
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.song_context import SongContextRead, create_song_context_tool
from app.agent.handoff.state import HandoffRun, RunLimits, RunStatus, new_run
from app.agent.handoff.streaming import (
    invoke_handoff_assistant,
    stream_handoff_assistant,
)
from app.agent.handoff.tools import execute_transfer, rejected_command
from app.config.settings import Settings
from app.main import app
from app.schemas.chat import (
    ClientActionStreamEvent,
    MessageStreamEvent,
)
from app.schemas.context import MusicAgentContext, SongContext
from app.schemas.music import MusicSearchResult, MusicTrack, SearchMusicArgs


class ToolModel(FakeMessagesListChatModel):
    bound_names: list[list[str]] = Field(default_factory=list)
    seen_messages: list[list] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.bound_names.append([item.name for item in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen_messages.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.fixture(autouse=True)
def offline_models(monkeypatch):
    def model():
        return ToolModel(responses=[AIMessage(content="离线测试回答。")])

    monkeypatch.setattr("app.agent.handoff.agents.get_base_chat_model", model)


class Router:
    def __init__(self, *routes):
        self.routes = routes

    async def route(self, text, context):
        clauses = text.split("；")
        tasks, intents, offset = [], [], 0
        for index, (route, clause) in enumerate(zip(self.routes, clauses, strict=True)):
            intents.append(
                IntentItem(
                    intent=IntentType(route.value),
                    sub_intent="pause_music"
                    if route is RouteKey.TOOL_CALLING and clause.strip() == "暂停一下"
                    else None,
                    source=IntentSource.RULE,
                    confidence=1,
                    evidence=clause,
                    start=offset,
                    end=offset + len(clause),
                    order=index,
                )
            )
            tasks.append(
                RouteTask(
                    task_id=f"task-{index}",
                    route_key=route,
                    input_text=clause,
                    intent_orders=[index],
                    depends_on=[f"task-{index - 1}"] if index else [],
                )
            )
            offset += len(clause) + 1
        return RouteDecision(intents=intents, tasks=tasks, is_complete=True, coverage=1)


def call(name="transfer_to_curator_agent", *, args=None, call_id="handoff-call"):
    return AIMessage(
        content="private control reasoning",
        tool_calls=[
            {
                "id": call_id,
                "name": name,
                "args": args
                if args is not None
                else {
                    "reason": "交给音乐执行者",
                    "context_summary": "private transfer summary",
                },
                "type": "tool_call",
            }
        ],
    )


def input_state(text="暂停一下", context=None):
    return create_main_state(
        text,
        messages=[HumanMessage(content=text)],
        routing_context=context or player_context(),
    )


def player_context():
    return RoutingContext(has_current_song=True, current_song_id=11)


def pause_graph(**kwargs):
    return build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call()]),
        **kwargs,
    ).compile()


def song():
    return SongContext(
        song_id=11,
        name="歌曲",
        artist="歌手",
        album="专辑",
        original_lyrics="private lyrics",
        translated_lyrics="",
        lyrics_status="AVAILABLE",
    )


def parse_sse(body):
    result = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        result.append(
            (
                next(line[7:] for line in lines if line.startswith("event: ")),
                json.loads(
                    next(line[6:] for line in lines if line.startswith("data: "))
                ),
            )
        )
    return result


@pytest.mark.asyncio
async def test_general_curator_pause_really_runs_command_and_guarded_action():
    graph = pause_graph()
    output = await graph.ainvoke(input_state())
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED and run.handoff_count == 1
    assert run.handoff_chain == (AgentKey.GENERAL_CHAT, AgentKey.CURATOR)
    action = run.results[0].data["client_actions"][0]
    assert action["name"] == "pause_player" and action["task_id"] == "task-0"
    assert "发送暂停指令" in output["final_response"]
    assistant = next(
        item
        for item in output["messages"]
        if isinstance(item, AIMessage) and item.tool_calls
    )
    tool = next(item for item in output["messages"] if isinstance(item, ToolMessage))
    assert assistant.tool_calls[0]["id"] == tool.tool_call_id
    assert assistant.content == ""


@pytest.mark.asyncio
async def test_curator_returns_to_general_only_for_remaining_chat_task():
    transfer_model = ToolModel(
        responses=[call(), call("transfer_to_general_chat_agent", call_id="return")]
    )
    general_model = ToolModel(responses=[AIMessage(content="可以聊聊。")])
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT),
        transfer_model=transfer_model,
        general_model=general_model,
    ).compile()
    output = await graph.ainvoke(input_state("暂停一下；然后陪我聊聊"))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.handoff_count == 2
    assert [item.task_id for item in run.results] == ["task-0", "task-1"]
    assert output["final_response"].endswith("可以聊聊。")
    assert all(
        not isinstance(item, ToolMessage) and not getattr(item, "tool_calls", [])
        for item in general_model.seen_messages[0]
    )


@pytest.mark.asyncio
async def test_current_song_context_tool_and_curator_analysis_are_read_only():
    provider = AsyncMock()
    provider.get_song.return_value = song()
    model = ToolModel(responses=[AIMessage(content="这首歌表达了怀念。")])
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.CURRENT_SONG),
        transfer_model=ToolModel(responses=[call()]),
        provider=provider,
        song_model=model,
    ).compile()
    response = await invoke_handoff_assistant(
        message="分析这首歌",
        thread_id="analysis",
        routing_context=RoutingContext(has_current_song=True, current_song_id=11),
        graph=graph,
    )
    assert response.status == TaskStatus.SUCCESS
    assert response.results[0].subgraph == "current_song"
    assert response.results[0].metadata["song"]["song_id"] == 11
    assert "client_actions" not in response.results[0].metadata
    provider.get_song.assert_awaited_once_with(11)
    assert not model.bound_names


@pytest.mark.asyncio
async def test_general_chat_can_read_song_but_never_gets_music_or_player_tools():
    provider = AsyncMock()
    provider.get_song.return_value = song()
    model = ToolModel(
        responses=[
            call("get_current_song_context", args={}),
            AIMessage(content="可以聊聊这首歌。"),
        ]
    )
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.GENERAL_CHAT),
        general_model=model,
        provider=provider,
    ).compile()
    response = await invoke_handoff_assistant(
        message="聊聊这首歌",
        thread_id="chat-song",
        routing_context=RoutingContext(has_current_song=True, current_song_id=11),
        graph=graph,
    )
    assert response.status == TaskStatus.SUCCESS
    assert model.bound_names == [["get_current_song_context"]]
    provider.get_song.assert_awaited_once_with(11)
    assert any(isinstance(item, ToolMessage) for item in model.seen_messages[-1])


@pytest.mark.asyncio
async def test_general_attempting_player_action_is_stopped_without_action():
    model = ToolModel(responses=[call("pause_player", args={})])
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model
    ).compile()
    output = await graph.ainvoke(input_state("陪我聊聊"))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.FAILED and run.handoff_count == 0
    assert not run.results[0].data.get("client_actions")


@pytest.mark.asyncio
async def test_curator_uses_existing_negation_authorization_before_tool_execution():
    model = ToolModel(
        responses=[call("play_next", args={}), AIMessage(content="没有执行播放操作。")]
    )
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call()]),
        curator_model=model,
    ).compile()
    output = await graph.ainvoke(input_state("搜索歌曲，不要播放"))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert not run.results[0].data.get("client_actions")
    assert run.results[0].status is TaskStatus.FAILED
    observations = [
        item for item in model.seen_messages[-1] if isinstance(item, ToolMessage)
    ]
    assert any("unauthorized_action" in item.content for item in observations)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "blank",
        "parallel",
        "unknown",
        "authority",
        "malformed",
        "missing_id",
        "duplicate_id",
    ],
)
async def test_invalid_transfer_is_paired_and_cannot_execute_business_tools(failure):
    message = call()
    if failure == "blank":
        message = AIMessage(content="普通文本不能替代转交")
    elif failure == "parallel":
        message.tool_calls.append(
            {"id": "business", "name": "pause_player", "args": {}, "type": "tool_call"}
        )
    elif failure == "unknown":
        message.tool_calls[0]["name"] = "transfer_to_explorer_agent"
    elif failure == "authority":
        message.tool_calls[0]["args"]["authorized_actions"] = ["pause_player"]
    elif failure == "missing_id":
        message.tool_calls[0]["id"] = ""
    elif failure == "duplicate_id":
        message.tool_calls.append(dict(message.tool_calls[0]))
    elif failure == "malformed":
        message.invalid_tool_calls = [
            {
                "id": "bad",
                "name": "transfer_to_curator_agent",
                "args": "invalid JSON",
                "error": "invalid",
                "type": "invalid_tool_call",
            }
        ]
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[message]),
    ).compile()
    output = await graph.ainvoke(input_state())
    run = HandoffRun.model_validate(output["handoff_run"])
    assert (
        run.status is RunStatus.FAILED and run.handoff_count == 0 and run.results == ()
    )
    calls = [
        item
        for item in output["messages"]
        if isinstance(item, AIMessage) and item.tool_calls
    ]
    replies = [item for item in output["messages"] if isinstance(item, ToolMessage)]
    if calls:
        assert {item["id"] for item in calls[0].tool_calls} == {
            item.tool_call_id for item in replies
        }
        assert len(replies) == len(calls[0].tool_calls)
        assert len({item.tool_call_id for item in replies}) == len(replies)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "routes,text",
    [
        ((RouteKey.WORLDLINE,), "探索时代"),
        ((RouteKey.TOOL_CALLING, RouteKey.WORLDLINE), "暂停一下；探索时代"),
    ],
)
async def test_explorer_dependency_failure_preserves_completed_music_results(
    routes, text
):
    from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
    from tests.test_worldline_graph import (
        EmptyRetriever,
        FakeGenerator,
        FakeMusicService,
    )

    transfer_model = ToolModel(
        responses=[call(), call("transfer_to_explorer_agent", call_id="explore")]
    )
    worldline = create_worldline_subgraph(
        music_service=FakeMusicService(
            None, search_error=RuntimeError("private music failure")
        ),
        retriever=EmptyRetriever(),
        generator=FakeGenerator(),
    )
    graph = build_handoff_graph(
        intent_router=Router(*routes),
        transfer_model=transfer_model,
        worldline_subgraph=worldline,
    ).compile()
    response = await invoke_handoff_assistant(
        message=text,
        thread_id="unsupported",
        routing_context=player_context(),
        graph=graph,
    )
    assert response.status == TaskStatus.FAILED and len(response.results) == len(routes)
    assert response.results[-1].subgraph == "worldline"
    assert response.results[-1].status == TaskStatus.FAILED
    assert "private" not in response.model_dump_json()
    if len(routes) == 2:
        assert (
            response.results[0].metadata["client_actions"][0]["name"] == "pause_player"
        )


@pytest.mark.asyncio
async def test_clarification_stops_before_model_or_tools():
    class Clarify:
        async def route(self, text, context):
            return RouteDecision(
                needs_clarification=True, clarification_question="你希望听歌还是聊天？"
            )

    model = ToolModel(responses=[call()])
    graph = build_handoff_graph(intent_router=Clarify(), transfer_model=model).compile()
    response = await invoke_handoff_assistant(
        message="处理一下",
        thread_id="clarify",
        routing_context=RoutingContext(),
        graph=graph,
    )
    assert response.status == TaskStatus.NEEDS_INPUT and response.results == []
    assert model.seen_messages == []


@pytest.mark.asyncio
async def test_router_failure_is_a_sanitized_failed_final():
    class Fail:
        async def route(self, *args):
            raise RuntimeError("private router credentials")

    graph = build_handoff_graph(intent_router=Fail()).compile()
    response = await invoke_handoff_assistant(
        message="请求",
        thread_id="failure",
        routing_context=RoutingContext(),
        graph=graph,
    )
    assert response.status == TaskStatus.FAILED
    assert "private" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_missing_song_stops_later_tasks_and_provider_is_not_called():
    provider = AsyncMock()
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.CURRENT_SONG, RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call()]),
        provider=provider,
    ).compile()
    output = await graph.ainvoke(
        input_state("分析这首歌；暂停一下", context=RoutingContext())
    )
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.NEEDS_INPUT and len(run.results) == 1
    provider.get_song.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["exception", "wrong_id"])
async def test_song_provider_failures_are_sanitized(bad):
    provider = AsyncMock()
    if bad == "exception":
        provider.get_song.side_effect = RuntimeError("private upstream token")
    else:
        provider.get_song.return_value = song().model_copy(update={"song_id": 12})
    tool = create_song_context_tool(
        RoutingContext(has_current_song=True, current_song_id=11), provider=provider
    )
    read = SongContextRead.model_validate_json(await tool.ainvoke({}))
    assert read.status is TaskStatus.FAILED and read.reason == "context_unavailable"
    assert "private" not in read.model_dump_json()


@pytest.mark.asyncio
async def test_song_context_has_no_model_controlled_id_or_cookie():
    provider = AsyncMock()
    tool = create_song_context_tool(
        RoutingContext(has_current_song=True, current_song_id=11), provider=provider
    )
    with pytest.raises(ValidationError):
        await tool.ainvoke({"song_id": 12, "session_cookie": "forbidden"})
    provider.get_song.assert_not_awaited()


@pytest.mark.asyncio
async def test_control_messages_and_lyrics_are_hidden_in_sse_and_action_emits_once():
    events = [
        event
        async for event in stream_handoff_assistant(
            message="暂停一下",
            thread_id="stream",
            routing_context=player_context(),
            graph=pause_graph(),
        )
    ]
    wire = "".join(event.model_dump_json() for event in events)
    assert (
        "private" not in wire
        and "context_summary" not in wire
        and "transfer_envelope" not in wire
    )
    assert (
        len([event for event in events if isinstance(event, ClientActionStreamEvent)])
        == 1
    )
    assert events[-1].response.status == TaskStatus.SUCCESS
    assert (
        "".join(
            event.delta for event in events if isinstance(event, MessageStreamEvent)
        )
        == events[-1].response.message
    )


@pytest.mark.asyncio
async def test_checkpoint_resume_preserves_handoff_count_and_does_not_transfer_twice():
    saver = create_memory_checkpointer()
    model = ToolModel(responses=[call()])
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING), transfer_model=model
    ).compile(checkpointer=saver, interrupt_before=["curator_agent"])
    config = {
        "configurable": {"thread_id": "resume", "session_id": "handoff:v1:session"}
    }
    paused = await graph.ainvoke(input_state(), config)
    assert HandoffRun.model_validate(paused["handoff_run"]).handoff_count == 1
    resumed = await graph.ainvoke(None, config)
    run = HandoffRun.model_validate(resumed["handoff_run"])
    assert run.status is RunStatus.COMPLETED and run.handoff_count == 1
    assert len(model.seen_messages) == 1


@pytest.mark.asyncio
async def test_new_turn_resets_run_and_public_results_even_if_router_reuses_task_ids():
    graph = create_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call()]),
        checkpointer=create_memory_checkpointer(),
    )
    first = await invoke_handoff_assistant(
        message="暂停一下",
        thread_id="repeat",
        routing_context=player_context(),
        graph=graph,
    )
    second = await invoke_handoff_assistant(
        message="暂停一下",
        thread_id="repeat",
        routing_context=player_context(),
        graph=graph,
    )
    assert len(first.results) == len(second.results) == 1
    assert first.status == second.status == TaskStatus.SUCCESS
    assert second.results[0].metadata["client_actions"][0]["name"] == "pause_player"
    snapshot = await graph.aget_state(
        {"configurable": {"thread_id": "handoff:v1:repeat"}}
    )
    run = HandoffRun.model_validate(snapshot.values["handoff_run"])
    assert run.handoff_count == 1 and len(run.results) == 1


@pytest.mark.asyncio
async def test_same_public_thread_has_separate_foundation_and_handoff_checkpoints():
    from app.agent.foundation.main_graph import build_main_graph

    saver = create_memory_checkpointer()
    foundation = build_main_graph(intent_router=Router(RouteKey.TOOL_CALLING)).compile(
        checkpointer=saver
    )
    handoff = create_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call()]),
        checkpointer=saver,
    )
    await invoke_foundation_assistant(
        message="暂停一下",
        thread_id="same",
        routing_context=player_context(),
        graph=foundation,
    )
    await invoke_handoff_assistant(
        message="暂停一下",
        thread_id="same",
        routing_context=player_context(),
        graph=handoff,
    )
    old = await foundation.aget_state({"configurable": {"thread_id": "same"}})
    new = await handoff.aget_state({"configurable": {"thread_id": "handoff:v1:same"}})
    assert "handoff_run" not in old.values and new.values["handoff_run"]


@pytest.mark.asyncio
async def test_production_continuity_sessions_stay_separate_when_switching_modes(
    tmp_path,
):
    from app.agent.foundation.long_horizon.service import LongHorizonContextService
    from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
    from app.agent.foundation.main_graph import build_main_graph

    class FreshRouter(Router):
        async def route(self, text, context):
            decision = await super().route(text, context)
            # Production task builders issue a new ID on each request.
            return decision.model_copy(
                update={
                    "tasks": [
                        decision.tasks[0].model_copy(
                            update={"task_id": f"task-{uuid4().hex}"}
                        )
                    ],
                }
            )

    service = LongHorizonContextService(
        SQLiteContinuityStore(tmp_path / "continuity.sqlite")
    )
    saver = create_memory_checkpointer()
    foundation = build_main_graph(
        intent_router=FreshRouter(RouteKey.TOOL_CALLING)
    ).compile(checkpointer=saver)
    handoff = create_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call()]),
        checkpointer=saver,
    )
    with (
        patch(
            "app.agent.foundation.streaming.get_long_horizon_service",
            return_value=service,
        ),
        patch(
            "app.agent.foundation.streaming.get_foundation_graph",
            return_value=foundation,
        ),
        patch("app.agent.foundation.streaming.get_handoff_graph", return_value=handoff),
    ):
        old = await invoke_foundation_assistant(
            message="暂停一下", thread_id="switch", routing_context=player_context()
        )
        old_session = service.open_conversation("switch")
        new = await invoke_handoff_assistant(
            message="暂停一下", thread_id="switch", routing_context=player_context()
        )
        new_session = service.open_conversation("handoff:v1:switch")
        rolled_back = await invoke_foundation_assistant(
            message="暂停一下", thread_id="switch", routing_context=player_context()
        )
    assert old.status == new.status == rolled_back.status == TaskStatus.SUCCESS
    assert old_session.session_id != new_session.session_id
    assert service.open_conversation("switch").session_id == old_session.session_id
    old_state = await foundation.aget_state(
        {"configurable": {"thread_id": "switch", "session_id": old_session.session_id}}
    )
    new_state = await handoff.aget_state(
        {
            "configurable": {
                "thread_id": "handoff:v1:switch",
                "session_id": new_session.session_id,
            }
        }
    )
    assert "handoff_run" not in old_state.values and new_state.values["handoff_run"]


@pytest.mark.asyncio
async def test_owned_agent_timeout_has_a_failed_final_instead_of_hanging():
    class SlowModel(ToolModel):
        async def _agenerate(self, *args, **kwargs):
            await asyncio.sleep(2)
            return await super()._agenerate(*args, **kwargs)

    model = SlowModel(responses=[AIMessage(content="迟到")])
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.GENERAL_CHAT),
        general_model=model,
        limits=RunLimits(agent_timeout_seconds=1, run_timeout_seconds=2),
    ).compile()
    async with asyncio.timeout(3):
        response = await invoke_handoff_assistant(
            message="聊天",
            thread_id="timeout",
            routing_context=RoutingContext(),
            graph=graph,
        )
    assert response.status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_transfer_tool_returns_command_and_denials_do_not_increment_count():
    decision = await Router(RouteKey.TOOL_CALLING).route("暂停一下", RoutingContext())
    run = new_run(
        "run",
        "暂停一下",
        decision,
        started_at=datetime.now(timezone.utc),
        entry_agent=AgentKey.GENERAL_CHAT,
    )
    command = await execute_transfer(call(), run)
    assert isinstance(command, Command) and command.goto == "curator_agent"
    accepted = HandoffRun.model_validate(command.update["handoff_run"])
    denied = await execute_transfer(call("transfer_to_general_chat_agent"), accepted)
    stopped = HandoffRun.model_validate(denied.update["handoff_run"])
    assert denied.goto == "final_response" and stopped.handoff_count == 1


@pytest.mark.asyncio
async def test_rejected_call_id_stays_bounded_with_maximum_length_run_id():
    decision = await Router(RouteKey.TOOL_CALLING).route("暂停一下", player_context())
    run = new_run(
        "r" * 128, "暂停一下", decision, started_at=datetime.now(timezone.utc)
    )
    message = call(call_id="")
    first = rejected_command(run, message)
    replay = rejected_command(run, message)
    call_id = first.update["messages"][0].tool_calls[0]["id"]
    assert 1 <= len(call_id) <= 128
    assert call_id == first.update["messages"][1].tool_call_id
    assert first.update["messages"] == replay.update["messages"]


def test_handoff_setting_is_opt_in_and_unknown_mode_is_invalid(monkeypatch):
    for key, value in {
        "LLM_API_KEY": "placeholder",
        "LLM_BASE_URL": "https://example.test",
        "LLM_MODEL": "test",
        "LLM_TIMEOUT_SECONDS": "5",
        "SPRING_BOOT_BASE_URL": "http://127.0.0.1:8080",
        "SPRING_BOOT_TIMEOUT_SECONDS": "5",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AI_AGENT_GRAPH_MODE", raising=False)
    assert Settings(_env_file=None).agent_graph_mode == "foundation"
    monkeypatch.setenv("AI_AGENT_GRAPH_MODE", "handoff")
    assert Settings(_env_file=None).agent_graph_mode == "handoff"
    monkeypatch.setenv("AI_AGENT_GRAPH_MODE", "unknown")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_json_and_sse_handoff_endpoints_keep_old_response_and_action_protocol():
    graph = pause_graph()
    client = TestClient(app)
    payload = {
        "message": "暂停一下",
        "thread_id": f"api-{uuid4().hex}",
        "player_context": player_context().model_dump(mode="json"),
    }
    with (
        patch(
            "app.api.chat.get_settings",
            return_value=SimpleNamespace(agent_graph_mode="handoff"),
        ),
        patch("app.agent.foundation.streaming.get_handoff_graph", return_value=graph),
    ):
        json_response = client.post("/api/ai/handoff", json=payload)
        stream = client.post("/api/ai/chat", json=payload)
    assert json_response.status_code == stream.status_code == 200
    events = parse_sse(stream.text)
    final = next(data for name, data in events if name == "final")
    assert final == json_response.json()
    assert final["results"][0]["subgraph"] == "tool_calling"
    assert [name for name, _ in events].count("action") == 1 and events[-1] == (
        "done",
        {},
    )
    assert "private" not in stream.text and "context_summary" not in stream.text


@pytest.mark.parametrize(
    "payload",
    [
        {"message": " "},
        {"message": "x" * 4_001},
        {"message": "测试", "thread_id": "x" * 129},
        {"message": "测试", "player_context": {"volume": 2}},
    ],
)
def test_handoff_endpoint_preserves_request_validation(payload):
    assert TestClient(app).post("/api/ai/handoff", json=payload).status_code == 422


def test_handoff_endpoint_unhandled_error_returns_sanitized_502():
    async def fail(**kwargs):
        raise RuntimeError("private account credentials")

    with patch("app.api.chat.invoke_handoff_assistant", fail):
        response = TestClient(app).post("/api/ai/handoff", json={"message": "测试"})
    assert response.status_code == 502 and "private" not in response.text


def test_handoff_sse_unhandled_error_still_ends_with_done():
    async def fail(**kwargs):
        raise RuntimeError("private upstream")
        yield  # Async-generator signature, no successful events.

    with (
        patch(
            "app.api.chat.get_settings",
            return_value=SimpleNamespace(agent_graph_mode="handoff"),
        ),
        patch("app.api.chat.stream_handoff_assistant", fail),
    ):
        response = TestClient(app).post("/api/ai/chat", json={"message": "测试"})
    assert [name for name, _ in parse_sse(response.text)] == ["error", "done"]
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_curator_search_play_then_analysis_uses_real_guard_and_prior_target():
    searches = []
    track = MusicTrack(song_id=11, name="晴天", artists=["歌手"], album="专辑")

    @tool("search_music", args_schema=SearchMusicArgs)
    async def search(keyword: str, limit: int = 10) -> str:
        """受控音乐搜索。"""
        searches.append((keyword, limit))
        return MusicSearchResult(
            keyword=keyword, limit=limit, songs=[track]
        ).model_dump_json()

    provider = AsyncMock()
    provider.get_song.return_value = song()
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
        intent_router=Router(RouteKey.TOOL_CALLING, RouteKey.CURRENT_SONG),
        transfer_model=ToolModel(responses=[call()]),
        curator_model=curator,
        song_model=ToolModel(responses=[AIMessage(content="这首歌表达了怀念。")]),
        tool_tools=[search, *CLIENT_ACTION_TOOLS],
        provider=provider,
    ).compile()
    response = await invoke_handoff_assistant(
        message="搜索晴天并播放；再分析这首歌",
        thread_id="play-analysis",
        routing_context=RoutingContext(has_current_song=True, current_song_id=99),
        graph=graph,
    )
    assert response.status == TaskStatus.SUCCESS
    assert searches == [("晴天", 1)]
    assert [item.subgraph for item in response.results] == [
        "tool_calling",
        "current_song",
    ]
    assert (
        response.results[0].metadata["client_actions"][0]["arguments"]["song"][
            "song_id"
        ]
        == 11
    )
    assert response.results[1].metadata["target_source"] == "prior_play_action"
    provider.get_song.assert_awaited_once_with(11)
    assert "private lyrics" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_pending_queue_action_does_not_analyze_stale_player_snapshot():
    provider = AsyncMock()
    prior = TaskResult(
        task_id="before",
        route_key=RouteKey.TOOL_CALLING,
        status=TaskStatus.SUCCESS,
        content="已发送切歌指令。",
        data={
            "client_actions": [
                {
                    "action_id": "before:next",
                    "task_id": "before",
                    "name": "play_next",
                    "arguments": {},
                }
            ]
        },
    )
    read_tool = create_song_context_tool(player_context(), [prior], provider=provider)
    read = SongContextRead.model_validate_json(await read_tool.ainvoke({}))
    assert (
        read.status is TaskStatus.NEEDS_INPUT and read.reason == "pending_queue_change"
    )
    provider.get_song.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_route_without_question_still_has_needs_input_json_and_sse():
    class Incomplete:
        async def route(self, text, context):
            return RouteDecision(needs_llm=True)

    graph = build_handoff_graph(intent_router=Incomplete()).compile()
    response = await invoke_handoff_assistant(
        message="处理一下",
        thread_id="incomplete",
        routing_context=RoutingContext(),
        graph=graph,
    )
    events = [
        event
        async for event in stream_handoff_assistant(
            message="处理一下",
            thread_id="incomplete-stream",
            routing_context=RoutingContext(),
            graph=graph,
        )
    ]
    assert response.status == events[-1].response.status == TaskStatus.NEEDS_INPUT
    assert response.results == [] and response.message


@pytest.mark.asyncio
async def test_hop_limit_preserves_completed_action_and_stops_remaining_task():
    general = ToolModel(responses=[AIMessage(content="不应执行")])
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT),
        transfer_model=ToolModel(
            responses=[call(), call("transfer_to_general_chat_agent", call_id="return")]
        ),
        general_model=general,
        limits=RunLimits(max_handoffs=1),
    ).compile()
    output = await graph.ainvoke(input_state("暂停一下；然后聊聊"))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.FAILED and run.handoff_count == 1
    assert len(run.results) == 1 and run.results[0].status is TaskStatus.SUCCESS
    assert run.results[0].data["client_actions"][0]["name"] == "pause_player"
    assert general.seen_messages == []


@pytest.mark.asyncio
async def test_total_deadline_stops_before_model_or_business_tools():
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ticks = iter([start, start + timedelta(seconds=120)])
    model = ToolModel(responses=[call()])
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=model,
        clock=lambda: next(ticks),
    ).compile()
    output = await graph.ainvoke(input_state())
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.FAILED and run.results == ()
    assert model.seen_messages == []


def test_json_handoff_passes_only_trusted_session_identity_to_curator_runtime():
    contexts = []

    @tool("get_user_playlists")
    async def playlists(runtime: ToolRuntime[MusicAgentContext]) -> str:
        """返回受控用户歌单。"""
        contexts.append(runtime.context)
        return '{"playlists":[]}'

    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call()]),
        curator_model=ToolModel(
            responses=[
                call("get_user_playlists", args={}),
                AIMessage(content="没有歌单。"),
            ]
        ),
        tool_tools=[playlists],
    ).compile()
    identity = AsyncMock(return_value="42")
    with (
        patch("app.api.chat.UserIdentityProvider.resolve", identity),
        patch("app.agent.foundation.streaming.get_handoff_graph", return_value=graph),
    ):
        response = TestClient(app).post(
            "/api/ai/handoff",
            json={"message": "查看我的歌单", "thread_id": "identity"},
            headers={"Cookie": "JSESSIONID=offline-session; tracking=private-cookie"},
        )
    assert response.status_code == 200 and response.json()["status"] == "success"
    identity.assert_awaited_once_with("JSESSIONID=offline-session")
    assert len(contexts) == 1 and contexts[0].user_id == "42"
    assert contexts[0].session_cookie == "JSESSIONID=offline-session"
    assert (
        "private-cookie" not in response.text and "offline-session" not in response.text
    )

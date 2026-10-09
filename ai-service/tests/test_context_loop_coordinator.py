"""Real saver/Writer lifecycle tests with controlled business dependencies."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.long_horizon.accounting import DurableAccounting, active_accounting
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator, ContinuityCheckpointer
from app.agent.foundation.long_horizon.drilldown import HistoryDrilldown
from app.agent.foundation.long_horizon.execution import ContinuityConflict, ExecutionLocks
from app.agent.foundation.long_horizon.models import HistoryEventType, HistoryPointer, PublicTaskResult
from app.agent.foundation.long_horizon.operations import OperationJournal
from app.agent.foundation.long_horizon.public_events import PublicDecision, PublicText, PublicTool
from app.agent.foundation.long_horizon.runner import invoke, stream, workers
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.graph import create_handoff_graph
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import AgentUsage, RunLimits
from app.llm.runtime import RequestLedger, accounted_call, request_scope
from app.schemas.chat import ChatRequest
from tests.test_handoff_network import worldline, transfers
from tests.test_handoff_runtime import Router, ToolModel, player_context


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from app.agent.handoff import graph as handoff_graph
    errors = []
    execute = handoff_graph.execute_owned_task
    async def capture(*args, **kwargs):
        try:
            return await execute(*args, **kwargs)
        except Exception as exc:
            errors.append((type(exc).__name__, str(exc)))
            raise
    monkeypatch.setattr(handoff_graph, "execute_owned_task", capture)
    store = SQLiteContinuityStore(tmp_path / "history.db")
    coordinator = ContinuityCoordinator(LongHorizonContextService(store))
    graph = create_handoff_graph(checkpointer=ContinuityCheckpointer(create_memory_checkpointer(), coordinator),
        transfer_journal=coordinator.journal, intent_router=Router(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING),
        transfer_model=transfers((AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.EXPLORER, AgentKey.CURATOR)),
        curator_model=ToolModel(responses=[AIMessage(content="完成")]), worldline_subgraph=worldline())
    return SimpleNamespace(store=store, coordinator=coordinator, graph=graph, errors=errors)


def options(h, **changes):
    return dict(message="探索歌手《周杰伦》的世界线；暂停一下", thread_id="p1-coordinator", routing_context=player_context(),
                graph_mode="handoff", coordinator=h.coordinator, graph=h.graph, request_id="one", **changes)


@pytest.mark.asyncio
async def test_committed_graph_writes_public_events_and_target_hooks_without_transport_writes(setup):
    h = setup
    response = await invoke(**options(h))
    assert str(response.status) == "success", h.errors
    task = h.store.active_task("handoff:v1:p1-coordinator")
    events = h.store.query_task_history(task.task_id, limit=500)
    assert all(event.source == "typed_writer:v1" for event in events)
    kinds = {event.event_type for event in events}
    assert {HistoryEventType.USER_MESSAGE, HistoryEventType.ROUTE_DECISION, HistoryEventType.CHECKPOINT,
            HistoryEventType.ASSISTANT_MESSAGE, HistoryEventType.RUN_PROGRESS, HistoryEventType.MODEL_USAGE,
            HistoryEventType.CLIENT_ACTION}.issubset(kinds)
    phases = [event.payload["phase"] for event in events if event.event_type == HistoryEventType.AGENT_TRANSFER]
    assert phases.count("committed") == 3
    assert "target_started" in phases and "target_finished" in phases
    assert "scratchpad" not in str([event.payload for event in events])
    results = [event for event in events if event.event_type == HistoryEventType.STRUCTURED_OUTPUT]
    assert len(results) == 2
    assert all(set(event.payload) <= set(PublicTaskResult.model_fields) for event in results)
    replay = await invoke(**options(h))
    assert replay == response
    after = h.store.query_task_history(task.task_id, limit=500)
    assert len(after) == len(events)


@pytest.mark.asyncio
async def test_sse_consumer_disconnect_does_not_cancel_worker_or_final_history(setup):
    h = setup
    iterator = stream(**options(h))
    await iterator.__anext__()
    await iterator.aclose()
    await asyncio.gather(*list(workers))
    task = h.store.active_task("handoff:v1:p1-coordinator")
    final = h.store.query_task_history(task.task_id, event_type=HistoryEventType.ASSISTANT_MESSAGE)
    assert len(final) == 1
    assert final[0].payload["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_saver", [False, True])
async def test_coordinator_injects_journal_into_old_compiled_handoff_graph(setup, existing_saver):
    from app.agent.handoff.graph import build_handoff_graph

    h = setup
    graph = build_handoff_graph(intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=transfers((AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.EXPLORER)),
        worldline_subgraph=worldline()).compile(checkpointer=create_memory_checkpointer() if existing_saver else None)
    response = await invoke(**{**options(h), "graph": graph, "message": "探索歌手《周杰伦》的世界线"})
    assert str(response.status) == "success", h.errors
    task = h.store.active_task("handoff:v1:p1-coordinator")
    phases = [event.payload["phase"] for event in h.store.query_task_history(task.task_id, limit=500)
              if event.event_type == HistoryEventType.AGENT_TRANSFER]
    assert phases.count("proposed") == phases.count("committed") == 2
    assert "target_started" in phases and "target_finished" in phases


@pytest.mark.asyncio
@pytest.mark.parametrize("route_key", [RouteKey.GENERAL_CHAT, RouteKey.CURRENT_SONG])
async def test_coordinator_streams_only_visible_answer_tokens_before_result_checkpoint(setup, route_key):
    from unittest.mock import AsyncMock
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from app.agent.foundation.main_graph import build_main_graph
    from app.agent.foundation.routing import RoutingContext
    from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
    from app.agent.foundation.subgraphs.current_song import create_current_song_subgraph
    from app.schemas.chat import MessageStreamEvent, UpdateStreamEvent
    from app.schemas.context import SongContext

    h = setup
    answer = "生产入口逐字可见回复"
    model = FakeListChatModel(responses=[answer], sleep=0.01)
    provider = AsyncMock()
    provider.get_song.return_value = SongContext(song_id=1, name="测试歌曲", artist="测试歌手", album="测试专辑",
        original_lyrics="测试歌词", translated_lyrics="", lyrics_status="AVAILABLE")
    graph = build_main_graph(development_routes=[route_key],
        general_chat_subgraph=create_general_chat_subgraph(model=model),
        current_song_subgraph=create_current_song_subgraph(provider=provider, model=model)).compile()
    iterator = stream(message="聊天" if route_key == RouteKey.GENERAL_CHAT else "分析这首歌",
        thread_id="answer-stream", routing_context=RoutingContext(has_current_song=True, current_song_id=1),
        graph_mode="foundation", coordinator=h.coordinator, graph=graph, request_id="stream-one")
    events = []
    async with asyncio.timeout(10):
        async for event in iterator:
            events.append(event)
            if isinstance(event, MessageStreamEvent):
                break
    assert not any(isinstance(event, UpdateStreamEvent) and event.node == route_key.value + "_subgraph" for event in events)
    events.extend([event async for event in iterator])
    deltas = [event.delta for event in events if isinstance(event, MessageStreamEvent)]
    assert len(deltas) > 1 and "".join(deltas) == answer
    assert events[-1].response.message == answer
    task = h.store.active_task("answer-stream")
    final = h.store.query_task_history(task.task_id, event_type=HistoryEventType.ASSISTANT_MESSAGE)
    assert len(final) == 1 and final[0].payload["text"] == answer
    usage = h.store.query_task_history(task.task_id, event_type=HistoryEventType.MODEL_USAGE)
    assert sum(event.payload["phase"] == "reserved" for event in usage) == 1
    assert sum(event.payload["phase"] == "settled" for event in usage) == 1


@pytest.mark.asyncio
async def test_foundation_budget_refusal_is_public_and_does_not_dispatch(setup, monkeypatch):
    from app.agent.foundation.main_graph import build_main_graph
    from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
    from app.agent.foundation.context_sources import load_context_policy
    from app.llm.runtime import BUDGET_LIMIT_MESSAGE
    configured = {**load_context_policy()["budget"], "l3_tokens": 200}
    monkeypatch.setattr("app.llm.budget.policy", lambda: configured)
    model = ToolModel(responses=[AIMessage(content="不应调用")])
    graph = build_main_graph(development_routes=[RouteKey.GENERAL_CHAT],
        general_chat_subgraph=create_general_chat_subgraph(model=model)).compile()
    response = await invoke(message="聊聊音乐", thread_id="foundation-budget-refusal", routing_context=player_context(),
        graph_mode="foundation", coordinator=setup.coordinator, graph=graph)
    assert response.status == "needs_input" and response.message == BUDGET_LIMIT_MESSAGE
    assert model.i == 0
    task = setup.store.active_task("foundation-budget-refusal")
    events = setup.store.query_task_history(task.task_id, event_type=HistoryEventType.MODEL_USAGE)
    budget_events = [event.payload for event in events if event.payload["phase"] == "budget"]
    assert [event["budget_stage"] for event in budget_events] == ["task_degradation", "refuse"]
    assert len({event["run_id"] for event in budget_events}) == 1


@pytest.mark.asyncio
async def test_budget_refusal_excludes_previous_request_results(setup, monkeypatch):
    from app.agent.foundation.main_graph import build_main_graph
    from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
    from app.agent.foundation.context_sources import load_context_policy
    graph = build_main_graph(development_routes=[RouteKey.GENERAL_CHAT],
        general_chat_subgraph=create_general_chat_subgraph(model=ToolModel(responses=[AIMessage(content="上一轮结果")]))).compile()
    common = dict(thread_id="budget-result-scope", routing_context=player_context(), graph_mode="foundation",
                  coordinator=setup.coordinator, graph=graph)
    first = await invoke(**common, message="先聊聊", request_id="first")
    assert first.status == "success" and len(first.results) == 1
    configured = {**load_context_policy()["budget"], "l3_tokens": 200}
    monkeypatch.setattr("app.llm.budget.policy", lambda: configured)
    refused = await invoke(**common, message="再聊聊", request_id="second")
    assert refused.status == "needs_input" and refused.results == []
    assert "上一轮结果" not in refused.model_dump_json()


@pytest.mark.asyncio
async def test_auxiliary_overshoot_is_refused_before_final_and_replay_keeps_refusal(setup):
    from unittest.mock import AsyncMock
    from app.agent.foundation.main_graph import build_main_graph
    from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
    from app.agent.memory.models import MemoryRetrievalResult
    from app.schemas.chat import FinalStreamEvent
    extracts = []
    class Memory:
        async def retrieve(self, request):
            return MemoryRetrievalResult(user_id=request.user_id, items=[])
        async def extract_and_store(self, **kwargs):
            extracts.append(kwargs)
            total = RunLimits().run_token_budget + 3000
            await accounted_call(AsyncMock(return_value=AIMessage(content="提取", usage_metadata={
                "input_tokens": total - 1, "output_tokens": 1, "total_tokens": total})), "memory-extraction",
                output_tokens=50, model="deepseek-chat")
    graph = build_main_graph(development_routes=[RouteKey.GENERAL_CHAT],
        general_chat_subgraph=create_general_chat_subgraph(model=ToolModel(responses=[AIMessage(content="本轮已完成结果")]))).compile()
    events = []
    common = dict(message="聊聊爵士", thread_id="aux-overshoot", routing_context=player_context(), graph_mode="foundation",
                  coordinator=setup.coordinator, graph=graph, user_id="alice", memory_runtime=Memory(), request_id="one")
    result = await invoke(**common, emit=events.append)
    assert result.status == "needs_input" and "预算已用完" in result.message
    assert len(result.results) == 1 and result.results[0].answer == "本轮已完成结果"
    assert sum(isinstance(event, FinalStreamEvent) for event in events) == 1
    assert await invoke(**common) == result
    assert len(extracts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["foundation", "handoff"])
async def test_auxiliary_memory_calls_share_original_request_budget(setup, mode):
    from unittest.mock import AsyncMock
    from app.agent.foundation.main_graph import build_main_graph
    from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
    from app.agent.memory.models import MemoryRetrievalResult
    from app.llm.runtime import active_ledger
    scopes = []
    class Memory:
        async def retrieve(self, request):
            scopes.append(active_ledger.get().run_id)
            await accounted_call(AsyncMock(return_value=AIMessage(content="筛选", usage_metadata={
                "input_tokens": 10, "output_tokens": 1, "total_tokens": 11})), "memory-filter",
                output_tokens=50, model="jev-latest", billing_agent="jev")
            return MemoryRetrievalResult(user_id=request.user_id, items=[])
        async def extract_and_store(self, **kwargs):
            scopes.append(active_ledger.get().run_id)
            await accounted_call(AsyncMock(return_value=AIMessage(content="提取", usage_metadata={
                "input_tokens": 30, "output_tokens": 3, "total_tokens": 33})), "memory-extraction",
                output_tokens=50, model="deepseek-chat")
    graph = setup.graph if mode == "handoff" else build_main_graph(development_routes=[RouteKey.GENERAL_CHAT],
        general_chat_subgraph=create_general_chat_subgraph(model=ToolModel(responses=[AIMessage(content="已完成")]))).compile()
    result = await invoke(**{**options(setup), "graph": graph, "graph_mode": mode, "thread_id": "aux-" + mode,
        "user_id": "alice", "memory_runtime": Memory()})
    assert result.status == "success"
    task = setup.store.active_task(("handoff:v1:" if mode == "handoff" else "") + "aux-" + mode)
    events = setup.store.query_task_history(task.task_id, event_type=HistoryEventType.MODEL_USAGE, limit=500)
    assert len(set(scopes)) == 1
    assert {event.payload["run_id"] for event in events} == set(scopes)
    settled = [event.payload for event in events if event.payload["phase"] == "settled"]
    assert any(event["model"] == "jev-latest" and event["billing_agent"] == "jev" and event["tokens"] == 11 for event in settled)
    assert any(event["model"] == "deepseek-chat" and event["tokens"] == 33 for event in settled)


@pytest.mark.asyncio
async def test_scope_and_pending_request_guards_release_lease(setup):
    h = setup
    call = await h.coordinator.begin_call(**{key: value for key, value in options(h).items() if key != "coordinator"})
    with pytest.raises(ContinuityConflict, match="already executing"):
        await h.coordinator.begin_call(**{key: value for key, value in options(h).items() if key != "coordinator"})
    await call.close()
    with pytest.raises(ContinuityConflict, match="authentication scope"):
        await h.coordinator.begin_call(**{key: value for key, value in options(h, user_id="another-user").items() if key != "coordinator"})


@pytest.mark.asyncio
async def test_curator_current_song_keeps_public_progress_node_and_hides_control_tokens(setup):
    from unittest.mock import AsyncMock
    from app.schemas.chat import UpdateStreamEvent, MessageStreamEvent
    from tests.test_handoff_runtime import song

    provider = AsyncMock()
    provider.get_song.return_value = song()
    graph = create_handoff_graph(checkpointer=create_memory_checkpointer(), intent_router=Router(RouteKey.CURRENT_SONG),
        transfer_model=transfers((AgentKey.GENERAL_CHAT, AgentKey.CURATOR)),
        song_model=ToolModel(responses=[AIMessage(content="歌曲解读")]), provider=provider)
    events = [event async for event in stream(**{**options(setup), "graph": graph, "message": "分析这首歌"})]
    assert events[-1].response.status == "success"
    assert any(isinstance(event, UpdateStreamEvent) and event.node == "current_song_subgraph" for event in events)
    assert "".join(event.delta for event in events if isinstance(event, MessageStreamEvent)) == "歌曲解读"
    assert "private control reasoning" not in "".join(event.model_dump_json() for event in events)
    task = setup.store.active_task("handoff:v1:p1-coordinator")
    calls = setup.store.query_task_history(task.task_id, event_type=HistoryEventType.TOOL_CALL)
    results = setup.store.query_task_history(task.task_id, event_type=HistoryEventType.TOOL_RESULT)
    assert len(calls) == len(results) == 1
    assert calls[0].payload["name"] == "get_current_song_context"
    assert results[0].payload["result_ref"]["kind"] == "artifact"


@pytest.mark.asyncio
async def test_general_chat_direct_context_tool_is_publicly_journaled_once(setup):
    from unittest.mock import AsyncMock
    from tests.test_handoff_runtime import song, call
    provider = AsyncMock()
    provider.get_song.return_value = song()
    model = ToolModel(responses=[call("get_current_song_context", args={}, call_id="context-read"), AIMessage(content="歌曲资料已读取")])
    graph = create_handoff_graph(checkpointer=create_memory_checkpointer(), intent_router=Router(RouteKey.GENERAL_CHAT),
        general_model=model, provider=provider)
    response = await invoke(**{**options(setup), "graph": graph, "message": "陪我聊聊"})
    assert response.status == "success"
    task = setup.store.active_task("handoff:v1:p1-coordinator")
    calls = setup.store.query_task_history(task.task_id, event_type=HistoryEventType.TOOL_CALL)
    results = setup.store.query_task_history(task.task_id, event_type=HistoryEventType.TOOL_RESULT)
    assert len(calls) == len(results) == 1
    assert calls[0].payload["parameters"] == {} and calls[0].payload["effect"] == "read"
    assert calls[0].payload["operation_id"] == results[0].payload["operation_id"]
    provider.get_song.assert_awaited_once_with(11)


@pytest.mark.asyncio
async def test_foundation_tool_model_dispatch_is_durably_reserved_and_settled(setup):
    from app.agent.foundation.main_graph import build_main_graph
    from app.agent.foundation.subgraphs.tool_calling import create_tool_calling_subgraph
    model = ToolModel(responses=[AIMessage(content="工具任务已处理")])
    graph = build_main_graph(development_routes=[RouteKey.TOOL_CALLING],
        tool_calling_subgraph=create_tool_calling_subgraph(model=model)).compile()
    response = await invoke(message="查找歌曲", thread_id="foundation-tool-usage", routing_context=player_context(),
        graph_mode="foundation", coordinator=setup.coordinator, graph=graph)
    assert response.status == "success"
    task = setup.store.active_task("foundation-tool-usage")
    usage = setup.store.query_task_history(task.task_id, event_type=HistoryEventType.MODEL_USAGE)
    assert sum(event.payload["phase"] == "reserved" for event in usage) == 1
    assert sum(event.payload["phase"] == "settled" for event in usage) == 1


@pytest.mark.asyncio
async def test_bound_chat_model_keeps_tool_schema_and_disables_hidden_sdk_retries(monkeypatch):
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    from app.llm.runtime import instrument_chat_model
    from app.tools.music import MUSIC_AGENT_TOOLS
    base = ChatOpenAI(api_key="offline-validation-placeholder", base_url="https://invalid.example/v1",
        model="offline-test", max_retries=2, stream_usage=False)
    bound = base.bind_tools([MUSIC_AGENT_TOOLS[0]])
    wrapped = instrument_chat_model(bound)
    assert wrapped.model.bound is not base
    assert wrapped.model.bound.max_retries == 0 and wrapped.model.bound.stream_usage
    assert base.max_retries == 2 and not base.stream_usage
    payloads = []
    async def accounted(operation, payload, **kwargs):
        payloads.append(payload)
        return AIMessage(content="受控响应")
    monkeypatch.setattr("app.llm.runtime.accounted_call", accounted)
    await wrapped.ainvoke([HumanMessage(content="查询")])
    assert payloads[0]["tools"] == bound.kwargs["tools"]


@pytest.mark.asyncio
async def test_ack_is_bound_to_original_action_scope_and_immutable(setup):
    h = setup
    response = await invoke(**options(h))
    action_id = response.results[-1].metadata["client_actions"][0]["action_id"]
    result = await h.coordinator.acknowledge(graph_mode="handoff", thread_id="p1-coordinator", user_id=None,
                                            action_id=action_id, status="applied")
    assert result["status"] == "applied"
    assert await h.coordinator.acknowledge(graph_mode="handoff", thread_id="p1-coordinator", user_id=None,
                                            action_id=action_id, status="applied") == result
    with pytest.raises(ValueError, match="key conflict"):
        await h.coordinator.acknowledge(graph_mode="handoff", thread_id="p1-coordinator", user_id=None,
                                       action_id=action_id, status="failed")
    with pytest.raises(ContinuityConflict):
        await h.coordinator.acknowledge(graph_mode="handoff", thread_id="p1-coordinator", user_id="foreign",
                                       action_id=action_id, status="applied")


def identity(h):
    from app.agent.foundation.long_horizon.models import ContinuityIdentity
    session = h.coordinator.service.open_conversation("handoff:v1:isolated")
    return ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="test", graph_mode="handoff", run_id="run:isolated")


def test_fencing_survives_store_restart_and_blocks_stale_owner(setup):
    h = setup
    one = h.coordinator.locks.acquire("lease", "scope")
    locks = ExecutionLocks(SQLiteContinuityStore(h.store.path))
    with pytest.raises(ContinuityConflict):
        locks.acquire("lease", "scope")
    one.advance()
    stale = replace(one, revision=0)
    with pytest.raises(ContinuityConflict, match="revision changed"):
        stale.check()
    one.release()
    two = locks.acquire("lease", "scope")
    assert two.fence > one.fence and two.revision == 1
    with pytest.raises(ContinuityConflict):
        one.check()
    two.release()


@pytest.mark.asyncio
async def test_lost_model_reservation_and_retry_are_reconciled_without_reset(setup):
    h = setup
    ident = identity(h)
    lease = h.coordinator.locks.acquire("accounting", "scope")
    accounting = DurableAccounting(h.coordinator.writer, ident, lease)
    token = active_accounting.set(accounting)
    bounds = RunLimits()
    ledger = RequestLedger(tuple(AgentUsage(agent=agent) for agent in AgentKey), AgentKey.CURATOR, bounds,
                           deadline=30, run_id=ident.run_id)
    accounting.reserve(ledger, 123)  # Process dies before SDK settlement.
    assert ledger.retry()
    active_accounting.reset(token)
    lease.release()
    restarted = ContinuityCoordinator(LongHorizonContextService(SQLiteContinuityStore(h.store.path)))
    next_lease = restarted.locks.acquire("accounting", "scope")
    restored = DurableAccounting(restarted.writer, ident, next_lease)
    totals = restored.totals(ident.run_id, [row.model_dump(mode="json") for row in (AgentUsage(agent=agent) for agent in AgentKey)])
    curator = next(row for row in totals if row["agent"] == "curator")
    assert curator["tokens"] == 123 and curator["estimated_tokens"] == 123
    assert curator["model_calls"] == 1 and curator["retries"] == 1
    next_lease.release()


@pytest.mark.asyncio
async def test_model_settlement_uses_actual_usage_and_preserves_one_reservation(setup):
    h = setup
    ident = identity(h)
    lease = h.coordinator.locks.acquire("settle", "scope")
    accounting = DurableAccounting(h.coordinator.writer, ident, lease)
    token = active_accounting.set(accounting)
    ledger = RequestLedger(tuple(AgentUsage(agent=agent) for agent in AgentKey), AgentKey.GENERAL_CHAT,
                           RunLimits(), deadline=30, run_id=ident.run_id)
    async def sdk():
        return AIMessage(content="可见结果", usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7})
    with request_scope(ledger):
        await accounted_call(sdk, {"public": "input"})
    totals = accounting.totals(ident.run_id, tuple(ledger.usage.values()))
    assert totals[0]["tokens"] == 7 and totals[0]["model_calls"] == 1
    active_accounting.reset(token)
    lease.release()


@pytest.mark.asyncio
async def test_unknown_write_is_not_reissued_and_success_replay_uses_safe_artifact(setup):
    h = setup
    ident = identity(h)
    lease = h.coordinator.locks.acquire("operation", "scope")
    accounting = DurableAccounting(h.coordinator.writer, ident, lease)
    journal = OperationJournal(h.coordinator.writer, accounting)
    call = SimpleNamespace(tool_call={"id": "a", "name": "set_song_liked_tool", "args": {"song_id": 101, "liked": True}})
    counter = 0
    async def failed(_):
        nonlocal counter
        counter += 1
        raise OSError("connection lost")
    with pytest.raises(OSError):
        await journal.call(call, failed)
    with pytest.raises(ContinuityConflict, match="effect unknown"):
        await journal.call(call, failed)
    assert counter == 1
    assert journal.unknown_writes(ident.run_id)
    second = SimpleNamespace(tool_call={"id": "b", "name": "set_song_liked_tool", "args": {"song_id": 102, "liked": True}})
    async def succeeded(_):
        nonlocal counter
        counter += 1
        return ToolMessage(content='{"song_id":102,"liked":true,"status":"synced","message":"成功"}', name="set_song_liked_tool", tool_call_id="b")
    original = await journal.call(second, succeeded)
    cached = await journal.call(second, succeeded)
    import json
    assert json.loads(original.content) == json.loads(cached.content) and counter == 2
    lease.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("succeeded", [False, True])
async def test_write_receipts_survive_session_change_and_new_tool_call_id(setup, succeeded):
    import json
    from datetime import UTC, datetime

    h = setup
    ident = identity(h)
    lease = h.coordinator.locks.acquire("operation-cross-session", "scope")
    accounting = DurableAccounting(h.coordinator.writer, ident, lease)
    journal = OperationJournal(h.coordinator.writer, accounting)
    counter = 0
    async def write(_):
        nonlocal counter
        counter += 1
        if not succeeded:
            raise OSError("controlled lost response")
        return ToolMessage(content='{"song_id":101,"liked":true,"status":"synced","message":"成功"}',
            name="set_song_liked_tool", tool_call_id="old")
    request = SimpleNamespace(tool_call={"id": "old", "name": "set_song_liked_tool", "args": {"song_id": 101, "liked": True}})
    if succeeded:
        await journal.call(request, write)
    else:
        with pytest.raises(OSError):
            await journal.call(request, write)
    original = h.store.require_session(ident.session_id)
    h.store.close_session(original.session_id, datetime.now(UTC).isoformat())
    h.store.create_session(original.model_copy(update={"session_id": "session:next", "previous_session_id": original.session_id,
        "created_at": datetime.now(UTC), "closed_at": None}))
    accounting.identity = ident.model_copy(update={"session_id": "session:next"})
    request.tool_call["id"] = "new"
    if succeeded:
        replay = await journal.call(request, write)
        assert replay.tool_call_id == "new" and json.loads(replay.content)["status"] == "synced"
    else:
        with pytest.raises(ContinuityConflict, match="effect unknown"):
            await journal.call(request, write)
    assert counter == 1
    lease.release()


@pytest.mark.asyncio
async def test_namespace_migration_refuses_to_discard_persisted_child_execution():
    from unittest.mock import AsyncMock
    from app.agent.foundation.long_horizon.migration import NamespaceMigrator

    source = SimpleNamespace(next_nodes=("explorer_agent",), ref=SimpleNamespace(checkpoint_id="root-pending"))
    coordinator = SimpleNamespace(store=SimpleNamespace(supports_atomic_rollover=True), snapshot=AsyncMock(return_value=source))
    async def children(*args, **kwargs):
        yield SimpleNamespace(config={"configurable": {"session_id": "original", "checkpoint_ns": "query:child"}},
            metadata={"parents": {"": "root-pending"}})
    saver = SimpleNamespace(aget_tuple=AsyncMock(return_value=SimpleNamespace(pending_writes=[])), alist=children)
    call = SimpleNamespace(lease=SimpleNamespace(check=lambda: None), graph=SimpleNamespace(checkpointer=saver), config={},
        identity=SimpleNamespace(conversation_id="conversation", session_id="original"))
    with pytest.raises(ContinuityConflict, match="child execution"):
        await NamespaceMigrator(coordinator).migrate(call, migration_id="move")


def test_budgeted_pointers_artifacts_and_cursor_archives_have_hard_limits(setup):
    h = setup
    ident = identity(h)
    writer = h.coordinator.writer
    rows = [writer.append_public_event(ident, str(index), HistoryEventType.USER_MESSAGE, PublicText(text="事实")) for index in range(45)]
    history = HistoryDrilldown(writer)
    pack = history.read(ident, count=3, tokens=200, bytes_limit=2000)
    assert len(pack.items) <= 3 and pack.tokens <= 200 and pack.bytes <= 2000 and pack.truncated
    pack2 = history.read(ident, cursor=pack.next_cursor, count=3, tokens=200, bytes_limit=2000)
    assert pack2.next_cursor > pack.next_cursor
    with pytest.raises(ValueError):
        history.read(ident, pointers=tuple(HistoryPointer(kind="event", target_id=row.event_id) for row in rows[:21]))
    large = history.public_text(ident, "large", HistoryEventType.ASSISTANT_MESSAGE, "可见文本" * 4000)
    ref = HistoryPointer.model_validate(large.payload["artifact_ref"])
    lazy = history.read(ident, pointers=(ref,))
    assert len(lazy.items) == 1 and "可见文本" in str(lazy.items[0])
    expanded = history.read(ident, pointers=(ref,), expand_artifacts=True)
    assert not expanded.items and expanded.truncated
    cursor = history.archive(ident, cursor=0)
    next_cursor = history.archive(ident, cursor=cursor)
    assert next_cursor > cursor
    assert history.archive(ident) >= next_cursor


def test_decision_revocation_is_append_only_and_whitelist_refuses_private_payload(setup):
    h = setup
    ident = identity(h)
    first = h.coordinator.writer.append_public_event(ident, "d1", HistoryEventType.USER_DECISION,
                                                     PublicDecision(decision_id="one", text="只读探索"))
    h.coordinator.writer.append_public_event(ident, "d2", HistoryEventType.USER_DECISION,
        PublicDecision(decision_id="two", text="撤销旧偏好", effect="revoke", supersedes=(HistoryPointer(kind="event", target_id=first.event_id),)))
    assert not h.coordinator.history.effective_decisions(ident)
    assert h.store.get_history_event(first.event_id).payload["text"] == "只读探索"
    with pytest.raises(ValueError):
        h.coordinator.writer.append_public_event(ident, "bad", HistoryEventType.USER_MESSAGE, {"text": "公开", "scratchpad": "隐私"})
    with pytest.raises(ValueError):
        h.coordinator.writer.append_public_event(ident, "secret", HistoryEventType.USER_MESSAGE, PublicText(text="api_key=private"))
    with pytest.raises(ValueError):
        PublicTool(run_id="x", operation_id="x", name="create_playlist_tool", parameters={"name": "正常", "Cookie": "secret"}, status="proposed", effect="write")


def test_resume_requires_empty_input_and_cannot_silently_start_new_session():
    assert ChatRequest(message="", thread_id="existing", resume=True).resume
    for values in ({"message": ""}, {"message": "new", "thread_id": "existing", "resume": True},
                   {"message": "", "resume": True}, {"message": "", "thread_id": "existing", "resume": True, "new_session": True}):
        with pytest.raises(ValueError):
            ChatRequest(**values)


def test_public_decisions_reach_api_options_and_revoke_inherited_constraints(setup):
    h = setup
    ident = identity(h)
    request = ChatRequest(message="继续", decisions=[{"decision_id": "new", "text": "只读"}])
    assert request.continuity_options()["decisions"][0].text == "只读"
    event = h.coordinator.writer.append_public_event(ident, "original", HistoryEventType.USER_DECISION,
        PublicDecision(decision_id="original", text="只读"))
    ref = HistoryPointer(kind="event", target_id=event.event_id)
    from app.agent.foundation.long_horizon.models import HandoffDraftV2
    original = HandoffDraftV2(goal="继续", current_stage="执行中", constraints=["只读"], constraint_refs={"只读": [ref]},
        decisions=["只读"], decision_refs={"只读": [ref]})
    h.coordinator.writer.append_public_event(ident, "revoke", HistoryEventType.USER_DECISION,
        PublicDecision(decision_id="revoke", text="撤销", effect="revoke", supersedes=(ref,)))
    filtered = h.coordinator.service.apply_revocations(original, ident.continuity_task_id)
    assert not filtered.constraints and not filtered.decisions
    assert original.constraints == ["只读"]
    active = h.coordinator.writer.append_public_event(ident, "current", HistoryEventType.USER_DECISION,
        PublicDecision(decision_id="current", text="禁止写入", kind="constraint"))
    session = h.store.require_session(ident.session_id)
    draft = h.coordinator.draft(session, None, None, "继续")
    assert draft.constraints == ["禁止写入"]
    assert draft.constraint_refs["禁止写入"][0].target_id == active.event_id
    with pytest.raises(ValueError):
        ChatRequest(message="", thread_id="x", resume=True, decisions=request.decisions)


@pytest.mark.asyncio
async def test_read_refresh_rechecks_source_then_revalidates_without_granting_authority(tmp_path):
    from tests.context_loop_helpers import harness, checkpoint
    from tests.test_context_loop_recovery import project
    from app.agent.foundation.long_horizon.models import LiveSnapshot, StateObservation
    from app.agent.foundation.long_horizon.recovery_executor import RecoveryExecutor
    from datetime import UTC, datetime
    h = harness(tmp_path / "refresh.db")
    saved = checkpoint(h)
    projection = project(h, revalidate_fields=["index_version"],
        last_known_state={"index_version": {"value": "v1", "revision": "r1"}})
    reads = []
    async def refresh(fields, snapshot, live):
        reads.append(fields)
        return LiveSnapshot(observations=(StateObservation(field="index_version", value="v1",
            source="retrieval_manifest", scope=h.identity.conversation_id, observed_at=datetime.now(UTC), revision="r1"),))
    executor = RecoveryExecutor(h.writer, h.journal, read_facts=refresh)
    plan, report, live = await executor.execute(projection, LiveSnapshot(), saved)
    assert reads == [("index_version",)] and plan.action == "resume_source"
    assert report.field_results["index_version"] == "valid"
    changed = live.model_copy(update={"observations": (live.observations[0].model_copy(update={"value": "v2"}),)})
    async def still_changed(*args):
        return changed
    executor.read_facts = still_changed
    plan, report, _ = await executor.execute(projection, changed, saved)
    assert plan.action == "needs_input" and report.field_results["index_version"] == "changed"


def test_action_ack_http_validation_success_conflict_and_scope(setup, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.agent.foundation.long_horizon.public_events import PublicAction
    from app.agent.foundation.long_horizon.history_writer import digest
    ident = identity(setup)
    monkeypatch.setattr("app.agent.foundation.long_horizon.coordinator.get_continuity_coordinator", lambda: setup.coordinator)
    lease = setup.coordinator.locks.acquire(ident.conversation_id, digest(["handoff", None]))
    lease.release()
    setup.coordinator.writer.append_public_event(ident, "action:test", HistoryEventType.CLIENT_ACTION,
        PublicAction(action_id="test", name="pause_player", arguments_digest=digest({})))
    client = TestClient(app)
    body = {"thread_id": "isolated", "graph_mode": "handoff", "action_id": "test", "status": "applied"}
    assert client.post("/api/ai/actions/ack", json={**body, "status": "unknown"}).status_code == 422
    assert client.post("/api/ai/actions/ack", json={**body, "action_id": "missing"}).status_code == 409
    assert client.post("/api/ai/actions/ack", json=body).status_code == 200
    assert client.post("/api/ai/actions/ack", json=body).status_code == 200
    assert client.post("/api/ai/actions/ack", json={**body, "status": "failed"}).status_code == 409


@pytest.mark.asyncio
async def test_sqlite_rollover_stays_disabled_and_request_conflicts_do_not_prepare(setup):
    values = {key: value for key, value in options(setup).items() if key != "coordinator"}
    with pytest.raises(ContinuityConflict, match="SQLite"):
        await setup.coordinator.begin_call(**values, new_session=True)
    with pytest.raises(ContinuityConflict, match="unavailable"):
        await setup.coordinator.begin_call(**{**values, "message": "", "resume": True})


def test_child_namespace_isolation_and_versioned_json_without_serde_expansion():
    from langgraph.checkpoint.base import empty_checkpoint
    from app.agent.foundation.long_horizon.checkpoint_models import encode, decode
    from app.rag.query_understanding.models import CatalogReference
    saver = create_memory_checkpointer()
    for namespace, text in (("", "root"), ("worldline:child", "child")):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"text": text}
        checkpoint["channel_versions"] = {"text": 1}
        config = {"configurable": {"thread_id": "tree", "session_id": "session", "checkpoint_ns": namespace}}
        saver.put(config, checkpoint, {"source": "loop", "step": 0}, {"text": 1})
        assert saver.get_tuple(config).checkpoint["channel_values"]["text"] == text
        assert saver.get_tuple(config).config["configurable"]["checkpoint_ns"] == namespace
    value = CatalogReference(provider="netease", entity_id="21", entity_type="artist", name="歌手")
    assert decode(encode(value)) == value
    with pytest.raises(ValueError):
        decode({"continuity_typed_state": 1, "type_id": "arbitrary.module.Class", "value": {}})


@pytest.mark.asyncio
async def test_sse_action_is_visible_only_after_checkpoint_receipt_and_ack_can_arrive_during_run(setup):
    from app.schemas.chat import ClientActionStreamEvent
    received = []
    async for event in stream(**options(setup)):
        if isinstance(event, ClientActionStreamEvent):
            task = setup.store.active_task("handoff:v1:p1-coordinator")
            rows = setup.store.query_task_history(task.task_id, event_type=HistoryEventType.CLIENT_ACTION)
            assert any(row.payload["action_id"] == event.action.action_id for row in rows)
            ack = await setup.coordinator.acknowledge(graph_mode="handoff", thread_id="p1-coordinator",
                user_id=None, action_id=event.action.action_id, status="applied")
            assert ack["status"] == "applied"
            received.append(event.action.action_id)
    assert len(received) == 1


@pytest.mark.asyncio
async def test_read_tool_results_use_whitelisted_artifact_and_lazy_drilldown(setup):
    ident = identity(setup)
    lease = setup.coordinator.locks.acquire("read", "scope")
    accounting = DurableAccounting(setup.coordinator.writer, ident, lease)
    journal = OperationJournal(setup.coordinator.writer, accounting)
    from app.schemas.music import MusicSearchResult
    result = MusicSearchResult(keyword="测试", limit=10, songs=[])
    async def search(_):
        return ToolMessage(content=result.model_dump_json(), name="search_music", tool_call_id="search")
    await journal.call(SimpleNamespace(tool_call={"id": "search", "name": "search_music",
        "args": {"keyword": "测试", "limit": 10}}), search)
    rows = setup.store.query_task_history(ident.continuity_task_id, event_type=HistoryEventType.TOOL_RESULT)
    ref = HistoryPointer.model_validate(rows[0].payload["result_ref"])
    pack = setup.coordinator.history.read(ident, pointers=(ref,))
    assert pack.items[0]["projection"]["name"] == "search_music"
    lease.release()


def test_active_decisions_cannot_be_silently_dropped_when_model_budget_is_tight():
    from app.agent.foundation.context_engineering import build_context, ContextPolicy, POLICIES
    decisions = [{"decision_id": "hard", "text": "不能执行任何写操作", "kind": "constraint"}]
    pack = build_context(RouteKey.GENERAL_CHAT, query="继续", system="按约束回答",
        continuity_context={"effective_decisions": decisions})
    selected = next(item for item in pack.items if item.id == "effective_decisions")
    assert selected.required and not selected.compressible
    policy = ContextPolicy(node=RouteKey.GENERAL_CHAT, total_input_budget=15, reserved_output_tokens=0,
        safety_margin=0, allowed_types=POLICIES[RouteKey.GENERAL_CHAT].allowed_types)
    with pytest.raises(ValueError, match="required context"):
        build_context(RouteKey.GENERAL_CHAT, query="继续", system="按约束回答", policy=policy,
            continuity_context={"effective_decisions": decisions})

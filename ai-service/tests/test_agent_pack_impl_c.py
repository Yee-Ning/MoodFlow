"""Real C model boundaries, scoped history and unchanged A/B budgets."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.foundation.agent_history_source import AgentHistorySource, ScopedHistoryMemorySource, bind_produced_message
from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs, AgentPackInvocation
from app.agent.foundation.business_pack import build_business_pack
from app.agent.foundation.context_contracts import PackManifest
from app.agent.foundation.context_sources import SourceRequest
from app.agent.foundation.long_horizon.models import HistoryEvent, HistoryEventType
from app.agent.foundation.routing import RouteKey, RouteTask
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.foundation.subgraphs.general_chat.assistant import answer_general_chat
from app.agent.foundation.subgraphs.current_song.assistant import answer_current_song_task
from app.agent.foundation.subgraphs.tool_calling import create_tool_calling_subgraph
from app.agent.foundation.subgraphs.tool_calling.assistant import answer_tool_calling_task
from app.agent.foundation.user_profile import ProfileDocument, ProfileEntry
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.agents import execute_owned_task
from app.agent.handoff.hop_context import hop_source_scope
from app.agent.handoff.state import AgentUsage, HandoffRun, RunLimits, RunStatus
from app.agent.handoff.state import record_task_result
from app.agent.handoff.registry import AgentKey
from app.llm.runtime import RequestLedger, RuntimeLimitError, request_scope
from tests.context_loop_helpers import checkpoint, harness, transfer
from tests.test_handoff_runtime import Router, call, input_state, player_context, song


NOW = datetime(2026, 10, 8, tzinfo=UTC)


class BoundaryModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        return self.responses.pop(0).model_copy(update={
            "usage_metadata": {"input_tokens": 80, "output_tokens": 20, "total_tokens": 100}})


def read_call(call_id="read:one"):
    return AIMessage(content="", tool_calls=[{
        "id": call_id, "name": "get_current_song_context", "args": {}, "type": "tool_call"}])


def invocation(owner="general_chat", **changes):
    route, phase, refs = (RouteKey.GENERAL_CHAT, "answer", ("general-chat-base/v1",)) if owner == "general_chat" else (
        RouteKey.TOOL_CALLING, "tool_step", ("curator-tools-base/v1",))
    return AgentPackInvocation(**{**dict(owner_agent=owner, run_id="run:shared", phase=phase,
        task=RouteTask(task_id="task:shared", route_key=route, input_text="解释音乐"),
        recipe_id=owner, recipe_version="v1", prompt_refs=refs,
        inputs=AgentPackInputs(continuity_context={"user_id": "alice"})), **changes})


def text(pack):
    return "\n".join(str(message.content) for message in pack.messages)


def references(manifest, kind):
    return [json.loads(ref.split(":", 1)[1]) for row in manifest["agent_context"]["source_selection"]
            for ref in row["references"] if ref.startswith(kind + ":")]


def call_manifests(caplog):
    prefix = "context_engineering.call_manifest "
    return [json.loads(row.getMessage()[len(prefix):]) for row in caplog.records
            if row.name == "app.llm.runtime" and row.getMessage().startswith(prefix)
            and "agent_context" in json.loads(row.getMessage()[len(prefix):])]


@pytest.mark.asyncio
@pytest.mark.parametrize("route,phases,prompts", [
    (RouteKey.GENERAL_CHAT, ["answer", "read_observation"],
     ["general-chat-base", "general-chat-handoff-read", "general-chat-result-evidence", "general-chat-preferences"]),
    (RouteKey.TOOL_CALLING, ["tool_step", "tool_observation"], ["curator-tools-base"]),
    (RouteKey.CURRENT_SONG, ["song_analysis"], ["curator-current-song"]),
])
async def test_real_handoff_business_calls_use_packs_and_versioned_prompts(monkeypatch, caplog, route, phases, prompts):
    caplog.set_level(logging.INFO, logger="app.llm.runtime")
    built = []
    original = AgentPackBuilder.build

    def record(self, value, **kwargs):
        if value.phase != "relay":
            built.append(value)
        return original(self, value, **kwargs)

    monkeypatch.setattr(AgentPackBuilder, "build", record)
    model = BoundaryModel(*([read_call(), AIMessage(content="歌曲信息已核实。")] if len(phases) == 2 else [AIMessage(content="歌曲分析。")]))
    provider = SimpleNamespace(get_song=AsyncMock(return_value=song()))
    graph = build_handoff_graph(intent_router=Router(route), transfer_model=BoundaryModel(call()),
        general_model=model, curator_model=model, song_model=model, provider=provider,
        tool_tools=[]).compile()
    query = "聊聊音乐里的表达" if route == RouteKey.GENERAL_CHAT else "看看当前歌曲信息"
    state = input_state(query)
    state["continuity_context"] = {"user_id": "alice"}
    output = await graph.ainvoke(state)
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.COMPLETED
    assert [value.phase for value in built] == phases
    assert len(model.calls) == len(phases)
    from app.agent.foundation.context_contracts import CallManifest
    manifests = [row for row in call_manifests(caplog) if row["agent_context"]["phase"] != "relay"]
    for phase, manifest, messages in zip(phases, manifests, model.calls, strict=True):
        audit = CallManifest.model_validate(manifest).agent_context
        assert audit.phase == phase and audit.recipe_version == "v1"
        assert audit.recipe_id == ("general_chat" if route == RouteKey.GENERAL_CHAT else "curator")
        assert [prompt.prompt_id for prompt in audit.prompts] == prompts
        assert all(prompt.version == "v1" and prompt.content_digest.startswith("sha256:") for prompt in audit.prompts)
        assert messages[-1].content == query or any(isinstance(message, ToolMessage) for message in messages)
        quotas = {row["source"]: row for row in references(manifest, "quota")}
        assert quotas["session_view"]["candidate_limit"] == 8 and quotas["session_view"]["tokens"] == 600
        assert quotas["history_memory"] == {"source": "history_memory", "candidate_limit": 24, "tokens": 600, "excerpt_characters": 400}
    if len(phases) == 2:
        observed = model.calls[-1]
        assert any(isinstance(message, ToolMessage) and message.tool_call_id == "read:one" for message in observed)
        assert any(isinstance(message, AIMessage) and message.tool_calls[0]["id"] == "read:one"
                   for message in observed if isinstance(message, AIMessage) and message.tool_calls)
        scopes = references(manifests[-1], "source_scope")
        assert any(row["agent"] == run.current_agent.value and row["user"] == "alice"
                   and row["run_id"] == run.run_id and row["route_task_id"] == "task-0" for row in scopes)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [RouteKey.GENERAL_CHAT, RouteKey.CURRENT_SONG])
async def test_foundation_model_boundaries_also_have_agent_context(caplog, route):
    caplog.set_level(logging.INFO, logger="app.llm.runtime")
    model = BoundaryModel(AIMessage(content="有依据的回答。"))
    if route == RouteKey.GENERAL_CHAT:
        await answer_general_chat([], "你好", player_context(), [], model=model)
    else:
        await answer_current_song_task([], "分析歌曲", song(), player_context(), model=model)
    assert call_manifests(caplog)[0]["agent_context"]["recipe_id"] == ("general_chat" if route == RouteKey.GENERAL_CHAT else "curator")


@pytest.mark.asyncio
async def test_foundation_preferences_keep_owned_tool_pairs_and_pack_audit(caplog):
    from app.agent.foundation.subgraphs.general_chat.session import SessionStore, session_for

    caplog.set_level(logging.INFO, logger="app.llm.runtime")
    context = {"user_id": "alice", "run_id": "run:shared", "conversation_id": "combined", "session_id": "combined"}
    value = invocation(inputs=AgentPackInputs(continuity_context=context))
    store = SessionStore()
    session_for(context, store=store).confirmed = ["古典音乐"]
    messages = [
        HumanMessage(content="我喜欢电子乐，尚未确认"),
        bind_produced_message(read_call(), value),
        bind_produced_message(ToolMessage(content="只读结果：喜欢不能拆开工具协议", tool_call_id="read:one"), value),
        bind_produced_message(AIMessage(content="FOREIGN_PRIVATE"), invocation("curator")),
    ]
    model = BoundaryModel(AIMessage(content="有依据的回答。"))
    await answer_general_chat(messages, "聊聊音乐里的表达", player_context(), [], model=model,
        continuity_context=context, route_task_id=value.task.task_id, session_store=store)
    rendered = str(model.calls[0])
    assert "尚未确认" not in rendered and "FOREIGN_PRIVATE" not in rendered
    assert "古典音乐" in rendered and "confirmed_session_preferences" in rendered
    assert any(isinstance(item, ToolMessage) and item.tool_call_id == "read:one" for item in model.calls[0])
    assert any(isinstance(item, AIMessage) and item.tool_calls for item in model.calls[0])
    audit = call_manifests(caplog)[0]["agent_context"]
    assert [row["prompt_id"] for row in audit["prompts"]] == [
        "general-chat-base", "general-chat-result-evidence", "general-chat-preferences"]
    assert references(call_manifests(caplog)[0], "source_scope")


@pytest.mark.asyncio
async def test_foundation_curator_keeps_local_tool_pair_without_hop_or_persistent_scope(caplog):
    caplog.set_level(logging.INFO, logger="app.llm.runtime")
    from app.agent.handoff.song_context import create_song_context_tool
    model = BoundaryModel(read_call(), AIMessage(content="观察完成。"))
    context_tool = create_song_context_tool(player_context(), [], provider=SimpleNamespace(get_song=AsyncMock(return_value=song())))
    graph = create_tool_calling_subgraph(model=model, tools=[context_tool])
    output = await graph.ainvoke({"messages": [HumanMessage(content="读取歌曲")], "current_input": "读取歌曲",
        "routing_intents": [], "task_id": "foundation:one", "routing_context": player_context(),
        "prior_entities": [], "tool_calls": [], "tool_results": [], "client_actions": [], "tool_call_count": 0})
    assert output["task_result"].content == "观察完成。"
    assert [manifest["agent_context"]["phase"] for manifest in call_manifests(caplog)] == ["tool_step", "tool_observation"]
    assert any(isinstance(message, ToolMessage) for message in model.calls[-1])


@pytest.mark.parametrize("owner", ["general_chat", "curator"])
def test_same_user_private_histories_are_isolated_in_actual_packs(owner):
    value = invocation(owner)
    other = invocation("curator" if owner == "general_chat" else "general_chat")
    own = bind_produced_message(AIMessage(content="本任务私有已验证状态"), value)
    foreign = bind_produced_message(AIMessage(content="FOREIGN_AGENT_PRIVATE"), other)
    unknown = AIMessage(content="UNKNOWN_PRIVATE", additional_kwargs={"private_history": True})
    history = [HumanMessage(content="公开会话"), foreign, unknown, own]
    pack = build_business_pack(value.model_copy(update={"inputs": value.inputs.model_copy(update={"messages": history})}))
    assert "本任务私有已验证状态" in text(pack) and "公开会话" in text(pack)
    assert "FOREIGN_AGENT_PRIVATE" not in text(pack) and "UNKNOWN_PRIVATE" not in text(pack)
    with pytest.raises(SourceIntegrityError):
        AgentHistorySource(value).read_private(foreign)
    scopes = references(pack.manifest, "source_scope")
    assert any(row["agent"] == owner and row["user"] == "alice" for row in scopes)
    assert "本任务私有已验证状态" not in json.dumps(pack.manifest, ensure_ascii=False)
    assert history[1].additional_kwargs["owner_agent"] == other.owner_agent


@pytest.mark.parametrize("change", [
    {"run_id": "run:other"}, {"route_task_id": "task:other"}, {"owner_agent": "curator"},
    {"user_id": "bob"}, {"route_task_id": None}, {"owner_agent": None}, {},
])
def test_source_denies_wrong_or_missing_private_scope(change):
    value = invocation()
    valid = bind_produced_message(AIMessage(content="private state"), value)
    metadata = {**valid.additional_kwargs, **change} if change else {"private_history": True}
    invalid = valid.model_copy(update={"additional_kwargs": metadata})
    source = AgentHistorySource(value)
    assert source.collect([invalid]) == []
    with pytest.raises(SourceIntegrityError):
        source.read_private(invalid)


@pytest.mark.parametrize("kind", ["unscoped", "foreign_result", "orphaned"])
def test_source_excludes_invalid_tool_protocol_as_whole_unit(kind):
    value = invocation()
    ai = bind_produced_message(read_call(), value)
    tool = bind_produced_message(ToolMessage(content="可见只读结果", tool_call_id="read:one"), value)
    if kind == "unscoped":
        ai, tool = read_call(), ToolMessage(content="UNKNOWN_RESULT", tool_call_id="read:one")
    elif kind == "foreign_result":
        tool = bind_produced_message(tool, invocation("curator"))
    else:
        tool = tool.model_copy(update={"tool_call_id": "another:id"})
    assert AgentHistorySource(value).collect([ai, tool]) == []


def history_event(index, *, metadata=None, text_value=None):
    return HistoryEvent(event_id=f"event:{index}", task_id="long:one", session_id="session:one",
        event_type=HistoryEventType.ASSISTANT_MESSAGE, timestamp=NOW, node="unreliable-node", source="public",
        payload={"text": text_value or f"音乐记忆 {index}"}, metadata=metadata or {})


def store_for(events):
    return SimpleNamespace(require_session=Mock(return_value=SimpleNamespace(conversation_id="conversation:one", task_id="long:one")),
        tasks_for_conversation=Mock(return_value=[SimpleNamespace(task_id="long:one")]),
        query_task_history=Mock(return_value=events))


@pytest.mark.parametrize("owner", ["general_chat", "curator"])
def test_store_without_agent_filter_is_still_scoped_at_source(owner):
    context = {"user_id": "alice", "conversation_id": "conversation:one", "session_id": "session:one", "task_id": "long:one"}
    value = invocation(owner, inputs=AgentPackInputs(continuity_context=context))
    own = bind_produced_message(AIMessage(content=""), value).additional_kwargs
    other = bind_produced_message(AIMessage(content=""), invocation("curator" if owner == "general_chat" else "general_chat")).additional_kwargs
    store = store_for([history_event(0), history_event(1, metadata=own), history_event(2, metadata=other),
                       history_event(3, metadata={"private_history": True})])
    request = SourceRequest("音乐", context, history_store=store)
    rows = ScopedHistoryMemorySource(value).collect(request, limit=24)
    assert {row.id for row in rows} == {"recall:event:0", "recall:event:1"}
    assert any(ref.startswith("source_scope:") for row in rows if row.id == "recall:event:1" for ref in row.references)
    assert "owner_agent" not in store.query_task_history.call_args.kwargs


@pytest.mark.asyncio
async def test_hop_profile_and_history_limits_use_original_drop_audit(tmp_path, monkeypatch):
    h = harness(tmp_path / "quota.db", routes=(RouteKey.GENERAL_CHAT,))
    doc = ProfileDocument(user_id="alice", version=3, updated_at=NOW, entries=tuple(
        ProfileEntry(key=f"preference:{i}", value=f"音乐交流偏好 {i} " + "简洁解释音乐。" * 20,
            source="user:alice", updated_at=NOW, confidence=.9, intents=(RouteKey.GENERAL_CHAT,)) for i in range(20)))
    events = [history_event(i, text_value=f"以前的音乐问答 {i} " + "介绍音乐主题。" * 90) for i in range(35)]
    store = store_for(events)
    original = ScopedHistoryMemorySource.collect
    monkeypatch.setattr(ScopedHistoryMemorySource, "collect", lambda self, request, *, limit:
        original(self, replace(request, history_store=store), limit=limit))
    inputs = AgentPackInputs(continuity_context={"user_id": "alice", "user_profile": doc,
        "conversation_id": "conversation:one", "session_id": "session:one", "task_id": "long:one"})
    task = h.run.pending_tasks[0]
    value = invocation(task=task, run_id=h.run.run_id, inputs=inputs)
    async with hop_source_scope({"messages": []}, h.run):
        pack = build_business_pack(value)
    for source in ("user_profile", "history_memory"):
        assert pack.manifest["sources"][source]["final_tokens"] <= (240 if source == "user_profile" else 600)
        assert any(row["reason"] == f"{source}_budget_exceeded" for row in pack.manifest["sources"][source]["events"])
    assert len([row for row in pack.trace.entries if row.stage == "collected" and row.item_id.startswith("profile:")]) == 16
    assert len([row for row in pack.trace.entries if row.stage == "collected" and row.item_id.startswith("recall:")]) == 24
    assert pack.dropped_items
    assert any(row.profile_version == 3 for row in PackManifest.model_validate(pack.manifest).agent_context.source_selection)


def test_stopped_or_exhausted_budget_never_reads_new_sources(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("unexpected Source read"))
    monkeypatch.setattr("app.agent.foundation.context_sources.UserProfileSource.collect", forbidden)
    value = invocation()
    limits = RunLimits()
    for exhausted in (False, True):
        book = RequestLedger(tuple(AgentUsage(agent=agent) for agent in AgentKey), AgentKey.GENERAL_CHAT, limits,
            deadline=30, run_id=value.run_id)
        if exhausted:
            book.usage["general_chat"]["tokens"] = book.agent_limit
        else:
            book.source_stops.add("general_chat")
        with request_scope(book):
            if exhausted:
                with pytest.raises(RuntimeLimitError):
                    build_business_pack(value)
            else:
                assert not [row for row in build_business_pack(value).items if row.source == "user_profile"]
    assert not forbidden.called


@pytest.mark.asyncio
async def test_recovery_shortcut_still_skips_pack_and_model(monkeypatch):
    monkeypatch.setattr("app.agent.foundation.long_horizon.recovery_receipts.answer_recovery_query", AsyncMock(return_value="已恢复。"))
    forbidden = Mock(side_effect=AssertionError("shortcut invoked pack/model"))
    monkeypatch.setattr("app.agent.foundation.business_pack.build_business_pack", forbidden)
    response = await answer_general_chat([], "恢复了吗", player_context(), [], model=SimpleNamespace(ainvoke=forbidden))
    assert response.text == "已恢复。" and not forbidden.called


@pytest.mark.asyncio
@pytest.mark.parametrize("edge", [("general_chat", "curator"), ("curator", "general_chat")])
async def test_committed_business_boundary_consumes_b_sources_without_writes(tmp_path, monkeypatch, caplog, edge):
    caplog.set_level(logging.INFO, logger="app.llm.runtime")
    h = harness(tmp_path / "committed.db", routes=(RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT))
    await transfer(h, "curator", summary="已提交的公开参考材料")
    checkpoint(h)
    if edge == ("curator", "general_chat"):
        from app.agent.foundation.models import TaskResult, TaskStatus
        task = h.run.pending_tasks[0]
        h.run = record_task_result(h.run, TaskResult(task_id=task.task_id, route_key=task.route_key,
            status=TaskStatus.SUCCESS, content="目录任务已完成。"))
        h.envelope = None
        checkpoint(h)
        await transfer(h, "general_chat", call_id="return:one", summary="已提交的公开参考材料")
        checkpoint(h)
    context = {"user_id": "alice", "conversation_id": h.session.conversation_id,
        "task_id": h.session.task_id, "session_id": h.session.session_id, "request_id": h.identity.request_id}
    state = {"messages": h.messages, "transfer_envelope": h.envelope,
        "continuity_context": context, "routing_context": player_context(), "task_results": []}
    from app.agent.foundation import business_pack
    original, packs = business_pack.build_hop_pack, []

    def record(value, **kwargs):
        result = original(value, **kwargs)
        packs.append(result.pack)
        return result

    monkeypatch.setattr(business_pack, "build_hop_pack", record)
    model = BoundaryModel(AIMessage(content="承接已提交的参考材料。"))
    before = h.store.query_task_history(h.session.task_id, limit=500)
    book = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=30, run_id=h.run.run_id)
    with request_scope(book):
        async with hop_source_scope(state, h.run, journal=h.journal, identity=h.identity):
            result = await execute_owned_task(state, h.run, SimpleNamespace(context=None),
                general_model=model, curator_model=model)
    assert result.content == "承接已提交的参考材料。" and len(packs) == 1
    assert "已提交的公开参考材料" in "".join(str(message.content) for message in model.calls[0])
    manifest = call_manifests(caplog)[0]
    assert manifest["agent_context"]["envelope"]["commit_ref"] is not None
    assert manifest["agent_context"]["envelope"]["selected_fields"]
    assert any(row["source"] == "session_view" for row in manifest["agent_context"]["source_selection"])
    assert book.usage[edge[1]]["model_calls"] == 1
    assert h.store.query_task_history(h.session.task_id, limit=500) == before


@pytest.mark.parametrize("key", ["user_id", "conversation_id", "session_id", "task_id"])
def test_memory_source_rejects_cross_scope_request_before_store_read(key):
    context = {"user_id": "alice", "conversation_id": "conversation:one", "session_id": "session:one", "task_id": "long:one"}
    value = invocation(inputs=AgentPackInputs(continuity_context=context))
    store = store_for([])
    with pytest.raises(SourceIntegrityError):
        ScopedHistoryMemorySource(value).collect(SourceRequest("音乐", {**context, key: "other"}, history_store=store), limit=24)
    assert not store.require_session.called and not store.query_task_history.called


@pytest.mark.asyncio
async def test_curator_routes_share_existing_l2_and_only_actual_calls_charge_it(caplog):
    caplog.set_level(logging.INFO, logger="app.llm.runtime")
    book = RequestLedger(tuple(AgentUsage(agent=agent) for agent in AgentKey), AgentKey.CURATOR,
        RunLimits(), deadline=30, run_id="run:curator")
    model = BoundaryModel(AIMessage(content="歌曲分析。"), AIMessage(content="目录说明。"))
    with request_scope(book):
        await answer_current_song_task([], "理解歌词", song(), player_context(), model=model, route_task_id="song:one")
        await answer_tool_calling_task([], "查询目录", player_context(), [], model=model, route_task_id="tool:one", search_only=True)
    assert book.usage["curator"]["tokens"] == 200 and book.usage["curator"]["model_calls"] == 2
    assert book.usage["general_chat"]["tokens"] == 0
    manifests = call_manifests(caplog)
    assert {manifest["agent_context"]["recipe_id"] for manifest in manifests} == {"curator"}
    assert manifests[1]["agent_context"]["prompts"][-1]["prompt_id"] == "search-only"

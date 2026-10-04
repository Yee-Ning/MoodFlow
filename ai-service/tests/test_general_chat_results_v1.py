"""GeneralChat v1 acceptance with real SQLite receipts and controlled models."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.long_horizon.execution import ContinuityConflict
from app.agent.foundation.long_horizon.history_writer import digest
from app.agent.foundation.long_horizon.models import HistoryEventType, HistoryPointer
from app.agent.foundation.long_horizon.operations import OperationJournal, active_operations
from app.agent.foundation.long_horizon.public_events import PublicAction, PublicActionAck
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask, RoutingContext
from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
from app.agent.foundation.subgraphs.general_chat.assistant import answer_general_chat
from app.agent.foundation.subgraphs.general_chat.results import ResultState, read_result_receipt
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.progress import with_combination_progress
from app.agent.handoff.state import HandoffRun, RunStatus, new_run
from app.tools.music import MusicServiceError
from tests.test_general_chat_subgraph import chat_state
from tests.test_handoff_runtime import Router, ToolModel, call, input_state, song
from tests import test_write_reconciliation as write_helpers
from tests.test_write_reconciliation import reads, request

journal = write_helpers.journal


def projected_result(*, actions=True, steps=(), extra_tasks=(), status=TaskStatus.SUCCESS):
    result = TaskResult(task_id="play-task", route_key=RouteKey.TOOL_CALLING,
                        status=status, content="已经全部完成，已经在播了。",
                        error="服务不可用。" if status is TaskStatus.FAILED else None,
                        data={"client_actions": [{"name": "pause_player", "action_id": "action:test"}]
                              if actions else [], "tool_call_details": list(steps)})
    run = new_run("test-run", "暂停一下", RouteDecision(is_complete=True, tasks=[
        RouteTask(task_id=result.task_id, route_key=RouteKey.TOOL_CALLING, input_text="暂停一下"),
        *extra_tasks,
    ]), started_at=datetime.now(UTC))
    return with_combination_progress(run, result)


def action_receipt(journal, *, status="applied", reference=None, name="pause_player", other_session=False):
    proposal = journal.writer.append_public_event(journal.identity, "action:test", HistoryEventType.CLIENT_ACTION,
        PublicAction(action_id="action:test", name=name, arguments_digest=digest({})))
    if reference:
        other = journal.writer.append_public_event(journal.identity, "action:other", HistoryEventType.CLIENT_ACTION,
            PublicAction(action_id="action:other", name=name, arguments_digest=digest({})))
        reference = other.event_id
    if other_session:
        store = journal.writer.store
        session = store.require_session(journal.identity.session_id)
        store.create_session(session.model_copy(update={"session_id": "foreign-session"}))
    identity = journal.identity.model_copy(update={"session_id": "foreign-session"}) if other_session else journal.identity
    return journal.writer.append_public_event(identity, "ack:test", HistoryEventType.ACTION_ACK,
        PublicActionAck(action_id="action:test", status=status,
                        action_ref=HistoryPointer(kind="event", target_id=reference or proposal.event_id)))


@pytest.fixture(autouse=True)
def isolated_active_operations():
    token = active_operations.set(None)
    yield
    active_operations.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", list(ResultState))
async def test_four_states_are_read_from_existing_receipts_and_never_promoted(state, journal):
    result = projected_result()
    operations = journal
    if state is ResultState.EXECUTED:
        action_receipt(journal)
    if state in {ResultState.CONFIRMED, ResultState.UNKNOWN}:
        operations = OperationJournal(journal.writer, journal.accounting,
                                      read_client=reads(present=state is ResultState.CONFIRMED))
        execute = AsyncMock(side_effect=MusicServiceError("http_502", "回执丢失。"))
        if state is ResultState.UNKNOWN:
            with pytest.raises(MusicServiceError):
                await operations.call(request(), execute)
        else:
            await operations.call(request(), execute)
        result = projected_result(actions=False, steps=[{"name": "add_song_to_playlist", "status": "error"}])
    token = active_operations.set(operations)
    try:
        receipt = await read_result_receipt([result])
        assert any(fact.state is state for fact in receipt.facts)
        source = "operation_journal" if state in {ResultState.CONFIRMED, ResultState.UNKNOWN} else (
            "action_ack" if state is ResultState.EXECUTED else "combination_projection")
        assert any(fact.source == source for fact in receipt.facts)
        events_before = operations.writer.store.query_task_history(operations.identity.continuity_task_id)
        model = ToolModel(responses=[AIMessage(content="已经全部完成，已经在播了。")])
        response = await answer_general_chat([HumanMessage(content="刚才任务完成了吗？")],
            "刚才任务完成了吗？", RoutingContext(), [result], model=model)
        assert response.text == receipt.wording()
        assert "已经全部完成" not in response.text and "已经在播了" not in response.text
        assert len(operations.writer.store.query_task_history(operations.identity.continuity_task_id)) == len(events_before)
        if state is ResultState.GENERATED:
            assert "已生成" in response.text and "待客户端执行/确认" in response.text
        elif state is ResultState.EXECUTED:
            assert "已执行" in response.text and "ACK" in response.text and "此刻仍在播放" in response.text
        elif state is ResultState.CONFIRMED:
            assert "只读核对已确认" in response.text
        else:
            assert "效果未知" in response.text
    finally:
        active_operations.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_ack", ["missing", "failed", "wrong_ref", "wrong_name", "other_session", "fake_field"])
async def test_only_applied_ack_for_the_original_action_can_prove_execution(bad_ack, journal):
    result = projected_result()
    if bad_ack == "failed":
        action_receipt(journal, status="failed")
    elif bad_ack == "wrong_ref":
        action_receipt(journal, reference="not-this-action")
    elif bad_ack == "wrong_name":
        action_receipt(journal, name="resume_player")
    elif bad_ack == "other_session":
        action_receipt(journal, other_session=True)
    elif bad_ack == "fake_field":
        result.data["client_actions"][0]["ack"] = "applied"
        result.data["result_state"] = "confirmed"
    token = active_operations.set(journal)
    try:
        receipt = await read_result_receipt([result])
        assert all(fact.state not in {ResultState.EXECUTED, ResultState.CONFIRMED} for fact in receipt.facts)
        assert "已执行：" not in receipt.wording()
        if bad_ack == "failed":
            assert receipt.facts[0].state is ResultState.UNKNOWN
    finally:
        active_operations.reset(token)


@pytest.mark.asyncio
async def test_backend_response_success_does_not_become_readback_or_client_ack(journal):
    await journal.call(request(), AsyncMock(return_value=ToolMessage(content=
        '{"playlist_id":18445771229,"song_id":2652820720,"status":"added","message":"成功"}',
        tool_call_id="one")))
    token = active_operations.set(journal)
    try:
        receipt = await read_result_receipt([projected_result(actions=False)])
        assert "后端成功回执" in receipt.wording() and "未另行只读核对" in receipt.wording()
        assert "已确认：" not in receipt.wording() and "已执行：" not in receipt.wording()
        assert receipt.facts[-1].state is None
    finally:
        active_operations.reset(token)


@pytest.mark.asyncio
async def test_definite_backend_failure_remains_failure_rather_than_unknown(journal):
    with pytest.raises(MusicServiceError):
        await journal.call(request(), AsyncMock(side_effect=MusicServiceError("http_403", "拒绝。")))
    token = active_operations.set(journal)
    try:
        receipt = await read_result_receipt([projected_result(actions=False)])
        assert receipt.facts[-1].state is None
        assert "明确拒绝" in receipt.wording() and "效果未知" not in receipt.wording()
    finally:
        active_operations.reset(token)


@pytest.mark.asyncio
async def test_ack_keeps_its_original_session_after_current_session_migration(journal, monkeypatch):
    action_receipt(journal)
    migrated_identity = journal.identity.model_copy(update={"session_id": "new-session"})
    session = journal.writer.store.require_session(journal.identity.session_id)
    journal.writer.store.create_session(session.model_copy(update={
        "session_id": migrated_identity.session_id, "previous_session_id": session.session_id,
    }))
    monkeypatch.setattr(journal.accounting, "identity", migrated_identity)
    token = active_operations.set(journal)
    try:
        receipt = await read_result_receipt([projected_result()])
        assert receipt.facts[0].state is ResultState.EXECUTED
    finally:
        active_operations.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["我帮你播放了十年。", "音乐已经响起来了。", "都办妥了。", "已经完成了。", "搞定了！"])
async def test_spontaneous_model_completion_claim_is_replaced_by_generated_fact(claim):
    model = ToolModel(responses=[AIMessage(content=claim)])
    response = await answer_general_chat([HumanMessage(content="陪我聊聊天")], "陪我聊聊天",
        RoutingContext(), [projected_result()], model=model)
    assert "已生成" in response.text and "待客户端执行/确认" in response.text
    assert claim not in response.text


@pytest.mark.asyncio
async def test_general_chat_rejects_invalid_tool_calls_as_well_as_valid_ones():
    model = ToolModel(responses=[AIMessage(content="已执行。", invalid_tool_calls=[
        {"name": "pause_player", "args": "bad-json", "id": "bad", "error": "invalid"}])])
    with pytest.raises(RuntimeError, match="attempted to call a tool"):
        await answer_general_chat([HumanMessage(content="聊聊天")], "聊聊天", RoutingContext(), [], model=model)


@pytest.mark.asyncio
async def test_journal_conflict_is_unknown_and_cannot_be_overridden_by_projection(journal, monkeypatch):
    with pytest.raises(MusicServiceError):
        await journal.call(request(), AsyncMock(side_effect=MusicServiceError("http_502", "未知。")))
    monkeypatch.setattr(journal, "state", lambda _: (_ for _ in ()).throw(ContinuityConflict("conflict")))
    token = active_operations.set(journal)
    try:
        receipt = await read_result_receipt([projected_result(actions=False)])
        assert receipt.facts[-1].state is ResultState.UNKNOWN
        assert receipt.facts[-1].source == "operation_journal_unavailable"
    finally:
        active_operations.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["刚才完成了吗？", "好了没？", "现在在播吗？", "Is it done?"])
async def test_missing_record_never_uses_model_or_player_snapshot_as_execution_proof(query):
    result = TaskResult(task_id="fake", route_key=RouteKey.TOOL_CALLING,
                        status=TaskStatus.SUCCESS, content="播放成功，已确认。",
                        data={"result_state": "confirmed", "confirmation_source": "readback", "ack": "applied"})
    model = ToolModel(responses=[AIMessage(content="Already playing; completed!")])
    response = await answer_general_chat([HumanMessage(content=query)], query,
        RoutingContext(has_current_song=True, current_song_id=11), [result], model=model)
    assert "效果未知" in response.text and "completed" not in response.text
    assert "已确认：" not in response.text and "已执行：" not in response.text
    assert "result_facts" in model.seen_messages[0][0].content


@pytest.mark.asyncio
async def test_handoff_enforces_projection_even_when_model_skips_the_status_tool():
    model = ToolModel(responses=[AIMessage(content="已经全部完成，已经在播了。")])
    graph = build_handoff_graph(intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model).compile()
    state = input_state("刚才任务完成了吗？")
    state["task_results"] = [projected_result()]
    output = await graph.ainvoke(state)
    assert "已生成" in output["final_response"] and "待客户端执行/确认" in output["final_response"]
    assert "已经全部完成" not in output["final_response"]
    assert not output["handoff_failed"]


@pytest.mark.asyncio
async def test_partial_combination_tool_query_cannot_become_all_success():
    pending = RouteTask(task_id="pending", route_key=RouteKey.WORLDLINE, input_text="暂停一下")
    result = projected_result(extra_tasks=[pending], status=TaskStatus.FAILED)
    model = ToolModel(responses=[call("get_combination_task_status", args={}, call_id="status"),
                                AIMessage(content="全部成功，已经完成了。")])
    graph = build_handoff_graph(intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model).compile()
    state = input_state("刚才哪些步骤成功，卡在哪里？")
    state["task_results"] = [result]
    output = await graph.ainvoke(state)
    assert "失败" in output["final_response"] and "待办，尚未执行" in output["final_response"]
    assert "已生成" in output["final_response"]
    assert "全部成功" not in output["final_response"]
    assert any(isinstance(message, ToolMessage) for message in model.seen_messages[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["帮我弄一下", "处理一下", "帮我播放", "收藏它", "删除那个"])
async def test_ambiguous_inputs_clarify_without_model_execution(text):
    model = ToolModel(responses=[call("pause_player", args={})])
    graph = build_handoff_graph(intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model).compile()
    output = await graph.ainvoke(input_state(text))
    run = HandoffRun.model_validate(output["handoff_run"])
    assert run.status is RunStatus.NEEDS_INPUT
    assert "？" in output["final_response"]
    assert not model.seen_messages and not run.results[0].data.get("client_actions")
    assert run.handoff_count == 0


@pytest.mark.asyncio
async def test_clarification_followup_preserves_original_request_in_real_checkpoint():
    model = ToolModel(responses=[AIMessage(content="你希望一起聊聊工作压力。今天发生了什么？")])
    graph = build_handoff_graph(intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model).compile(
        checkpointer=create_memory_checkpointer())
    config = {"configurable": {"thread_id": "clarification-followup"}}
    first = await graph.ainvoke(input_state("帮我弄一下"), config)
    assert first["handoff_needs_input"]
    second = await graph.ainvoke(input_state("我想聊聊工作压力"), config)
    assert not second["handoff_needs_input"] and not second["handoff_failed"]
    sent = "\n".join(message.text for message in model.seen_messages[-1])
    assert "帮我弄一下" in sent and "你想让我帮你做什么" in sent and "我想聊聊工作压力" in sent
    assert "工作压力" in second["final_response"]


@pytest.mark.asyncio
async def test_explicit_supplement_continues_original_music_task_without_changing_router():
    class FollowupRouter:
        async def route(self, text, context):
            route = RouteKey.GENERAL_CHAT if text == "帮我弄一下" else RouteKey.TOOL_CALLING
            return await Router(route).route(text, context)

    graph = build_handoff_graph(intent_router=FollowupRouter(),
        general_model=ToolModel(responses=[AIMessage(content="不应调用")]),
        transfer_model=ToolModel(responses=[call()])).compile(checkpointer=create_memory_checkpointer())
    config = {"configurable": {"thread_id": "clarify-then-pause"}}
    first = await graph.ainvoke(input_state("帮我弄一下"), config)
    assert first["handoff_needs_input"]
    second = await graph.ainvoke(input_state("暂停一下"), config)
    run = HandoffRun.model_validate(second["handoff_run"])
    assert run.status is RunStatus.COMPLETED
    assert run.results[0].data["client_actions"][0]["name"] == "pause_player"
    assert any("你想让我帮你做什么" in message.text for message in second["messages"])


@pytest.mark.asyncio
async def test_emotional_support_and_current_song_light_chat_remain_read_only():
    provider = AsyncMock()
    provider.get_song.return_value = song()
    reply = "这首歌的怀念感很适合慢慢聊。我在这里，今天有什么让你难过的事？"
    model = ToolModel(responses=[call("get_current_song_context", args={}), AIMessage(content=reply)])
    graph = build_handoff_graph(intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model,
                                provider=provider).compile()
    output = await graph.ainvoke(input_state("今天有点难过，陪我聊聊这首歌"))
    assert output["final_response"] == reply
    assert not output["handoff_needs_input"] and not output["handoff_failed"]
    assert model.bound_names == [["get_current_song_context"]]
    provider.get_song.assert_awaited_once_with(11)
    assert not HandoffRun.model_validate(output["handoff_run"]).results[0].data.get("client_actions")


@pytest.mark.asyncio
async def test_foundation_clarification_contract_is_needs_input():
    assistant = AsyncMock(return_value=AIMessage(content="不应调用"))
    output = await create_general_chat_subgraph(assistant=assistant).ainvoke(
        chat_state(current_input="帮我弄一下", messages=[HumanMessage(content="帮我弄一下")]))
    assert output["task_result"].status is TaskStatus.NEEDS_INPUT
    assistant.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["我刚刚完成了工作，感觉很累，陪我聊聊", "刚刚成功解决问题，想聊聊当前这首歌"])
async def test_personal_progress_is_emotional_conversation_not_an_action_status_query(text):
    reply = "辛苦了，我们可以慢慢聊。今天最让你在意的是什么？"
    model = ToolModel(responses=[AIMessage(content=reply)])
    response = await answer_general_chat([HumanMessage(content=text)], text, RoutingContext(), [], model=model)
    assert response.text == reply

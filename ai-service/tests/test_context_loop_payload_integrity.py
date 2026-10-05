"""Tampered transfers never dispatch targets or repair their own journal anchor."""

import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator, ContinuityCheckpointer
from app.agent.foundation.long_horizon.models import HistoryEventType, LiveSnapshot, TransferJournalEvent
from app.agent.foundation.long_horizon.runner import invoke, stream
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.continuity import public_state_digest
from app.agent.handoff.graph import create_handoff_graph
from app.agent.handoff.integrity import TransferIntegrityConflict, payload_digest
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import record_task_result
from tests.context_loop_helpers import checkpoint, harness, transfer
from tests.test_context_loop_recovery import decide, project
from tests.test_handoff_network import transfers
from tests.test_handoff_runtime import Router, input_state, player_context


def events(h, kind):
    return h.store.query_task_history(h.session.task_id, event_type=kind, limit=500)


def taint(messages, envelope, field, *, paired=True):
    messages, envelope = copy.deepcopy(messages), copy.deepcopy(envelope)
    raw = envelope.model_dump(mode="json") if hasattr(envelope, "model_dump") else envelope
    if field in {"reason", "context_summary", "mood"}:
        raw["request"][field] = "注入后的请求"
        if paired:
            call = next(message for message in reversed(messages) if isinstance(message, AIMessage) and message.tool_calls)
            call.tool_calls[0]["args"][field] = "注入后的请求"
    elif field == "target":
        raw["request"]["target_agent"] = "explorer"
    elif field == "parameter":
        raw["active_task"]["input_text"] = "修改后的目标参数"
        raw["pending_tasks"][0]["input_text"] = "修改后的目标参数"
    elif field == "entity":
        raw["entities"]["songs"] = [{"position": 1, "song_id": 999, "name": "另一个目录项"}]
    elif field == "original_input":
        raw[field] = "扩大原始授权"
    return tuple(messages), raw


def assert_safe_conflict(h):
    errors = events(h, HistoryEventType.ERROR)
    assert len(errors) == 1
    assert errors[0].source == "typed_writer:v1"
    assert set(errors[0].payload) == {"code", "related_key_digest"}
    assert errors[0].payload["code"] == "integrity_conflict"
    assert "注入" not in str(errors[0].payload)


def test_canonical_payload_is_order_independent_and_preserves_values():
    assert payload_digest({"reason": "中文", "params": {"b": 2, "a": 1}}) == payload_digest(
        {"params": {"a": 1, "b": 2}, "reason": "中文"},
    )
    assert payload_digest({"reason": "中文"}) != payload_digest({"reason": "中文 "})
    with pytest.raises(ValueError):
        payload_digest({"value": float("nan")})


def test_source_checkpoint_still_rejects_orphan_controls_before_any_hop(tmp_path):
    h = harness(tmp_path / "history.db")
    saved = checkpoint(h)
    damaged = replace(saved, messages=(ToolMessage(content="{}", name="transfer_to_curator_agent", tool_call_id="orphan"),))
    assert decide(h, damaged)[1].action == "needs_input"


@pytest.mark.asyncio
async def test_sender_digest_is_allowlisted_persisted_and_committed_unchanged(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    saved = checkpoint(h)
    journal = events(h, HistoryEventType.AGENT_TRANSFER)
    from app.agent.foundation.long_horizon.journal_contracts import JournalReader
    proposed, committed = [JournalReader(h.store).transfer(event) for event in journal]
    assert proposed.summary.payload_digest == payload_digest(saved.envelope.model_dump(mode="json"))
    assert committed.summary == proposed.summary
    assert committed.summary_ref.target_id == journal[0].event_id
    await h.journal.await_committed(h.identity, saved.run, messages=saved.messages, envelope=saved.envelope, timeout=1)
    assert not events(h, HistoryEventType.ERROR)
    with pytest.raises(ValidationError):
        TransferJournalEvent.model_validate({**journal[0].payload, "summary": {
            **journal[0].payload["summary"], "payload_digest": "arbitrary",
        }})


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["reason", "context_summary", "mood", "target", "parameter", "entity", "original_input"])
@pytest.mark.parametrize("paired", [False, True])
async def test_actual_recipient_input_is_rejected_before_execution(tmp_path, field, paired):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    saved = checkpoint(h)
    messages, envelope = taint(saved.messages, saved.envelope, field, paired=paired)
    for _ in range(2):
        with pytest.raises(TransferIntegrityConflict, match="execution rejected"):
            await h.journal.await_committed(h.identity, saved.run, messages=messages, envelope=envelope, timeout=1)
    assert_safe_conflict(h)
    assert len(events(h, HistoryEventType.AGENT_TRANSFER)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["reason", "context_summary", "mood", "parameter"])
async def test_reconciliation_does_not_commit_tampered_checkpoint(tmp_path, field):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    h.messages, h.envelope = taint(h.messages, h.envelope, field)
    saved = checkpoint(h, saver=h.saver.saver)
    with pytest.raises(TransferIntegrityConflict):
        h.journal.confirm_checkpoint(saved, reconcile=True)
    assert [event.payload["phase"] for event in events(h, HistoryEventType.AGENT_TRANSFER)] == ["proposed"]
    assert not events(h, HistoryEventType.RUN_PROGRESS)
    assert_safe_conflict(h)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["reason", "context_summary", "mood", "parameter"])
async def test_recovery_refuses_tampered_payload_without_rewriting_snapshot(tmp_path, field):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    saved = checkpoint(h)
    projection = project(h)
    messages, envelope = taint(saved.messages, saved.envelope, field)
    damaged = replace(saved, messages=messages, envelope=envelope)
    coordinator = ContinuityCoordinator(h.service)
    plan, report, _ = await coordinator.recovery.execute(projection, LiveSnapshot(), damaged)
    assert plan.action == "needs_input" and plan.resume_agent is None
    assert report.transfer_integrity == "conflict"
    assert damaged.messages == messages and damaged.envelope == envelope
    assert_safe_conflict(h)


@pytest.mark.asyncio
async def test_changed_actual_task_parameters_are_not_covered_up_by_a_recomputed_state_digest(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    saved = checkpoint(h)
    run = saved.run.model_copy(deep=True)
    run.plan.tasks[0] = run.plan.tasks[0].model_copy(update={"input_text": "新的实际任务参数"})
    run = run.model_copy(update={"original_input": "新的实际任务参数"})
    damaged = replace(saved, run=run, envelope=None, ref=saved.ref.model_copy(update={
        "public_state_digest": public_state_digest(run),
    }))
    assert decide(h, damaged)[1].action == "needs_input"
    assert_safe_conflict(h)


@pytest.mark.asyncio
async def test_earlier_hop_digest_survives_completion_and_detects_later_control_tampering(tmp_path):
    h = harness(tmp_path / "history.db", routes=(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE))
    await transfer(h, "curator")
    checkpoint(h)
    h.run = record_task_result(h.run, TaskResult(task_id="task:0", route_key=RouteKey.TOOL_CALLING,
        status=TaskStatus.SUCCESS, content="已读目录",
        data={"entities": {"songs": [{"position": 1, "song_id": 123, "name": "目录项"}]}}))
    h.envelope = None
    checkpoint(h)
    await transfer(h, "explorer", call_id="next")
    saved = checkpoint(h)
    assert decide(h, saved)[1].action == "resume_target"
    messages = copy.deepcopy(saved.messages)
    messages[0].tool_calls[0]["args"]["reason"] = "篡改旧转交"
    damaged = replace(saved, messages=tuple(messages))
    assert decide(h, damaged)[1].action == "needs_input"
    assert_safe_conflict(h)


@pytest.mark.asyncio
async def test_legacy_receipt_without_digest_is_readable_but_cannot_authorize_execution(tmp_path):
    h = harness(tmp_path / "history.db")
    original = h.writer.transfer
    def legacy_transfer(identity, key, body):
        return original(identity, key, body.model_copy(update={"summary": body.summary.model_copy(update={"payload_digest": None})}))
    h.writer.transfer = legacy_transfer
    await transfer(h, "curator")
    proposal = events(h, HistoryEventType.AGENT_TRANSFER)[0]
    assert TransferJournalEvent.model_validate(proposal.payload).summary.payload_digest is None
    saved = checkpoint(h, saver=h.saver.saver)
    with pytest.raises(TransferIntegrityConflict):
        h.journal.confirm_checkpoint(saved, reconcile=True)
    assert [event.payload["phase"] for event in events(h, HistoryEventType.AGENT_TRANSFER)] == ["proposed"]
    assert_safe_conflict(h)


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["graph", "json", "sse"])
@pytest.mark.parametrize("field", ["reason", "context_summary", "mood"])
async def test_real_graph_never_calls_business_model_or_tool_on_conflict(tmp_path, transport, field):
    h = harness(tmp_path / "history.db", routes=(RouteKey.TOOL_CALLING,))
    coordinator = ContinuityCoordinator(h.service)
    journal = h.journal if transport == "graph" else coordinator.journal
    original = journal.await_committed
    async def modified_node_input(identity, run, **kwargs):
        if run.hops:
            kwargs["messages"], kwargs["envelope"] = taint(kwargs["messages"], kwargs["envelope"], field)
        return await original(identity, run, **kwargs)
    journal.await_committed = modified_node_input
    target = SimpleNamespace(ainvoke=AsyncMock(side_effect=AssertionError("must not execute")))
    trace = []
    graph = create_handoff_graph(checkpointer=ContinuityCheckpointer(h.saver.saver, coordinator)
        if transport != "graph" else h.saver.saver, transfer_journal=journal,
        intent_router=Router(RouteKey.TOOL_CALLING), transfer_model=transfers((AgentKey.GENERAL_CHAT, AgentKey.CURATOR)),
        subgraphs={RouteKey.TOOL_CALLING: target}, trace_sink=trace.append)
    if transport == "graph":
        state = input_state("暂停一下")
        state["continuity_context"] = {"conversation_id": h.identity.conversation_id,
            "task_id": h.identity.continuity_task_id, "session_id": h.identity.session_id,
            "request_id": h.identity.request_id}
        with pytest.raises(TransferIntegrityConflict):
            await graph.ainvoke(state, h.config)
    else:
        options = dict(message="暂停一下", thread_id="one", routing_context=player_context(), graph_mode="handoff",
            coordinator=coordinator, graph=graph, request_id="test")
        if transport == "json":
            response = await invoke(**options)
            assert response.status == "needs_input" and "拒绝执行" in response.message
            assert response.results == []
        else:
            output = [event async for event in stream(**options)]
            assert "拒绝执行" in output[-1].response.message
            assert output[-1].response.results == []
    target.ainvoke.assert_not_called()
    assert any(event["code"] == "guard_rejected" for event in trace)
    assert_safe_conflict(h)

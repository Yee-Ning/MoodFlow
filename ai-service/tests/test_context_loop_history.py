from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.agent.foundation.long_horizon.models import (
    HistoryEvent, HistoryEventType, PublicTaskResult, TransferJournalEvent,
)
from app.agent.handoff.continuity import JournalCheckpointer
from app.agent.handoff.graph import create_handoff_graph
from app.agent.handoff.state import HandoffRun, RunLimits
from app.agent.foundation.routing import RouteKey
from tests.context_loop_helpers import checkpoint, harness, transfer
from tests.test_handoff_network import transfers, worldline
from tests.test_handoff_runtime import Router, input_state
from app.agent.handoff.registry import AgentKey


def events(h, kind=HistoryEventType.AGENT_TRANSFER):
    return h.store.query_task_history(h.identity.continuity_task_id, event_type=kind.value)


@pytest.mark.asyncio
async def test_command_is_only_proposed_then_confirmed_checkpoint_commits(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    assert [e.payload["phase"] for e in events(h)] == ["proposed"]
    assert "private intermediate" not in str(events(h))
    snapshot = checkpoint(h)
    assert [e.payload["phase"] for e in events(h)] == ["proposed", "committed"]
    h.journal.confirm_checkpoint(snapshot)
    assert len(events(h)) == 2
    assert events(h)[1].payload["checkpoint_ref"]["checkpoint_id"] == snapshot.ref.checkpoint_id


@pytest.mark.asyncio
async def test_guard_rejection_is_audited_without_accepted_progress(tmp_path):
    h = harness(tmp_path / "history.db")
    guard = SimpleNamespace(review=lambda envelope: _denied())
    await transfer(h, "curator", guard=guard)
    checkpoint(h)
    assert [e.payload["phase"] for e in events(h)] == ["proposed", "rejected"]
    assert h.run.handoff_count == 0
    progress = events(h, HistoryEventType.RUN_PROGRESS)[-1].payload
    assert progress["run"]["chain"] == ["general_chat"]


async def _denied():
    return SimpleNamespace(allowed=False, code="guard_rejected", score=0.1)


@pytest.mark.asyncio
async def test_failed_saver_never_writes_committed(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    failing = Mock(wraps=h.saver.saver)
    failing.serde = h.saver.serde
    failing.put.side_effect = RuntimeError("save failed")
    with pytest.raises(RuntimeError, match="save failed"):
        checkpoint(h, saver=JournalCheckpointer(failing, h.journal))
    assert [e.payload["phase"] for e in events(h)] == ["proposed"]


@pytest.mark.asyncio
async def test_save_succeeded_missing_receipt_can_be_reconciled_once(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    # Deliberately use the unwrapped saver: crash between checkpoint and receipt.
    snapshot = checkpoint(h, saver=h.saver.saver)
    h.journal.confirm_checkpoint(snapshot, reconcile=True)
    h.journal.confirm_checkpoint(snapshot, reconcile=True)
    assert [e.payload["phase"] for e in events(h)] == ["proposed", "reconciled"]


@pytest.mark.asyncio
async def test_async_real_graph_adapter_records_both_hops_and_results(tmp_path):
    h = harness(tmp_path / "history.db")
    graph = create_handoff_graph(
        intent_router=Router(RouteKey.WORLDLINE),
        transfer_model=transfers((AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.EXPLORER)),
        worldline_subgraph=worldline(), transfer_journal=h.journal,
        checkpointer=h.saver.saver,
    )
    state = input_state("探索歌手《周杰伦》的世界线")
    state["continuity_context"] = {
        "conversation_id": h.identity.conversation_id, "task_id": h.identity.continuity_task_id,
        "session_id": h.identity.session_id, "request_id": h.identity.request_id,
    }
    result = await graph.ainvoke(state, h.config)
    run = HandoffRun.model_validate(result["handoff_run"])
    assert run.handoff_count == 2 and run.status.value == "completed"
    assert [e.payload["phase"] for e in events(h)].count("committed") == 2
    assert len(events(h, HistoryEventType.STRUCTURED_OUTPUT)) == 1


@pytest.mark.asyncio
async def test_actual_async_checkpoint_failure_blocks_target_business_before_dispatch(tmp_path):
    h = harness(tmp_path / "history.db", routes=(RouteKey.TOOL_CALLING,))
    delegated = h.saver.saver
    original_put = delegated.aput
    async def fail_accepted(config, row, metadata, versions):
        run = row["channel_values"].get("handoff_run") or {}
        if run.get("hops"):
            raise RuntimeError("accepted checkpoint unavailable")
        return await original_put(config, row, metadata, versions)
    delegated.aput = fail_accepted
    business = SimpleNamespace(ainvoke=AsyncMock())
    graph = create_handoff_graph(intent_router=Router(RouteKey.TOOL_CALLING),
                                 transfer_model=transfers((AgentKey.GENERAL_CHAT, AgentKey.CURATOR)),
                                 subgraphs={RouteKey.TOOL_CALLING: business}, transfer_journal=h.journal,
                                 limits=RunLimits(agent_timeout_seconds=1),
                                 checkpointer=delegated)
    state = input_state("暂停播放")
    state["continuity_context"] = {"conversation_id": h.identity.conversation_id, "task_id": h.identity.continuity_task_id,
                                   "session_id": h.identity.session_id, "request_id": h.identity.request_id}
    with pytest.raises(RuntimeError, match="accepted checkpoint unavailable"):
        await graph.ainvoke(state, h.config)
    business.ainvoke.assert_not_called()
    assert not any(e.payload["phase"] == "committed" for e in events(h))


@pytest.mark.asyncio
async def test_target_phase_hooks_are_idempotent_and_finish_needs_saved_task_result(tmp_path):
    from app.agent.foundation.models import TaskResult, TaskStatus
    from app.agent.handoff.state import record_task_result

    h = harness(tmp_path / "history.db", routes=(RouteKey.TOOL_CALLING,))
    await transfer(h, "curator")
    saved = checkpoint(h)
    first = h.journal.observe_target(saved, phase="target_started")
    assert h.journal.observe_target(saved, phase="target_started").event_id == first.event_id
    with pytest.raises(ValueError, match="task result"):
        h.journal.observe_target(saved, phase="target_finished")
    h.run = record_task_result(h.run, TaskResult(task_id="task:0", route_key=RouteKey.TOOL_CALLING, status=TaskStatus.SUCCESS, content="目录只读查询完成"))
    h.envelope = None
    saved = checkpoint(h)
    finished = h.journal.observe_target(saved, phase="target_finished")
    assert finished.payload["result_refs"]
    assert h.journal.observe_target(saved, phase="target_finished").event_id == finished.event_id


@pytest.mark.parametrize("memory", [False, True])
def test_writer_concurrent_replays_use_one_immutable_event_and_stable_cursor(tmp_path, memory):
    h = harness(":memory:" if memory else tmp_path / "history.db")
    result = PublicTaskResult(
        run_id=h.run.run_id, route_task_id="task:0", route_key="worldline", status="success", content="公开结果",
    )
    with ThreadPoolExecutor(max_workers=6) as executor:
        receipts = list(executor.map(lambda _: h.writer.task_result(h.identity, result), range(12)))
    assert len({e.event_id for e in receipts}) == 1
    assert len({e.event_seq for e in receipts}) == 1
    assert h.writer.task_result(h.identity.model_copy(update={"request_id": "request:retry"}), result).event_id == receipts[0].event_id
    assert h.store.query_task_history(h.session.task_id, after_seq=receipts[0].event_seq) == []
    changed = result.model_copy(update={"content": "不同结果"})
    with pytest.raises(ValueError, match="key conflict"):
        h.writer.task_result(h.identity, changed)
    assert len(events(h, HistoryEventType.ERROR)) == 1
    assert h.store.get_history_event(receipts[0].event_id).payload["content"] == "公开结果"


@pytest.mark.parametrize("field", ["scratchpad", "api_key", "raw_prompt", "metadata", "data"])
def test_writer_rejects_unknown_private_fields(tmp_path, field):
    h = harness(tmp_path / "history.db")
    with pytest.raises(ValidationError):
        h.writer.append_public_event(h.identity, "bad", HistoryEventType.STRUCTURED_OUTPUT, {
            "run_id": h.run.run_id, "route_task_id": "task:0", "route_key": "worldline", "status": "success",
            field: "private",
        })
    assert not events(h, HistoryEventType.STRUCTURED_OUTPUT)


@pytest.mark.parametrize("text", ["scratchpad: hidden", "api_key=not-a-real-key", '"api_key":"not-a-real-key"', "rawPrompt: private", "Bearer fake", "<reasoning>private</reasoning>"])
def test_writer_rejects_private_text_in_allowlisted_field(tmp_path, text):
    h = harness(tmp_path / "history.db")
    with pytest.raises(ValueError, match="private"):
        h.writer.task_result(h.identity, PublicTaskResult(
            run_id=h.run.run_id, route_task_id="task:0", route_key="worldline", status="success", content=text,
        ))


def test_writer_bounds_sizes_scope_and_type(tmp_path):
    h = harness(tmp_path / "history.db")
    result = PublicTaskResult(run_id=h.run.run_id, route_task_id="task:0", route_key="worldline", status="success")
    h.writer.max_bytes = 10
    with pytest.raises(ValueError, match="byte budget"):
        h.writer.task_result(h.identity, result)
    with pytest.raises(ValueError, match="scope mismatch"):
        h.writer.task_result(h.identity.model_copy(update={"conversation_id": "other"}), result)
    with pytest.raises(ValueError, match="allowlisted"):
        h.writer.append_public_event(h.identity, "bad", HistoryEventType.HANDOFF, {})


def test_legacy_handoff_is_session_semantics_only_for_rollover():
    from datetime import UTC, datetime

    base = dict(event_id="evt:one", task_id="task:one", session_id="session:one", timestamp=datetime.now(UTC),
                event_type=HistoryEventType.HANDOFF, node="rollover", source="test", payload={})
    assert HistoryEvent(**base).semantic_type is HistoryEventType.SESSION_HANDOFF
    base["node"] = "other"
    assert HistoryEvent(**base).semantic_type is HistoryEventType.HANDOFF


def test_committed_dto_without_checkpoint_is_rejected():
    with pytest.raises(ValidationError, match="checkpoint"):
        TransferJournalEvent.model_validate({
            "phase": "committed", "transfer_id": "transfer:" + "0" * 64,
            "summary": {"transfer_id": "transfer:" + "0" * 64, "run_id": "run:1", "hop_index": 0,
                        "source_agent": "general_chat", "target_agent": "curator", "route_task_id": "t",
                        "reason_code": "capability_owner"},
        })


def test_writer_rejects_cross_task_and_missing_nested_references(tmp_path):
    from app.agent.foundation.long_horizon.models import HistoryPointer

    h = harness(tmp_path / "history.db")
    other = harness(tmp_path / "history.db", suffix="other")
    result = PublicTaskResult(run_id=other.run.run_id, route_task_id="task:0", route_key="worldline", status="success")
    foreign = other.writer.task_result(other.identity, result)
    for target in (foreign.event_id, "evt:missing"):
        with pytest.raises(ValueError, match="unresolvable"):
            h.writer.append_public_event(h.identity, "bad-ref", HistoryEventType.STRUCTURED_OUTPUT,
                                         result.model_copy(update={"run_id": h.run.run_id}),
                                         refs=(HistoryPointer(kind="event", target_id=target),))

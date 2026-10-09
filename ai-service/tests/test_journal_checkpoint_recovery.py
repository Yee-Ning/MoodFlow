"""Scheme A never fabricates an executable run from public journal evidence."""

from datetime import UTC, datetime, timedelta
import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from langchain_core.messages import AIMessage, ToolMessage
import pytest

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.journal_contracts import JournalReader
from app.agent.foundation.long_horizon.journal_rebuild import JournalCheckpointRebuilder, RECOVERY_NAMESPACE
from app.agent.foundation.long_horizon.models import (
    HandoffDraftV2, HistoryEvent, HistoryEventType, LiveSnapshot, StateObservation,
)
from app.agent.foundation.long_horizon.public_events import PublicUsage
from app.agent.foundation.long_horizon.recovery import RecoveryPlanner
from app.agent.foundation.long_horizon.recovery_checkpoint import RecoveryCheckpoint
from app.agent.foundation.long_horizon.recovery_executor import RecoveryExecutor
from app.agent.foundation.long_horizon.revalidation import RealityRevalidator
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.continuity import JournalCheckpointer
from app.agent.handoff.integrity import TransferIntegrityConflict
from app.agent.handoff.state import finish_run, record_task_result
from tests.context_loop_helpers import checkpoint, harness, transfer


def rebuilder(h, saver=None, store=None):
    return JournalCheckpointRebuilder(HistoryWriter(store or h.store), saver or h.saver,
                                      task_id=h.session.task_id)


def events(h, kind=None):
    return h.store.query_task_history(h.session.task_id, event_type=kind, limit=100)


def projection(h):
    return h.service.build_handoff(h.session.session_id, HandoffDraftV2(goal="继续原任务", current_stage="恢复"))


def audit(h, target, call_id, rejected=False):
    message = AIMessage(content="", tool_calls=[{"id": call_id, "name": f"transfer_to_{target}_agent",
        "args": {"reason": "转交能力", "context_summary": "候选进度"}}])
    command = {"messages": [message]}
    proposed = h.journal.observe_transfer(h.identity, h.run, command)
    if rejected:
        command["messages"].append(ToolMessage(content='{"accepted":false,"code":"wrong_target"}',
                                               tool_call_id=call_id))
        h.journal.observe_transfer(h.identity, h.run, command, phase="rejected")
    return proposed


@pytest.mark.asyncio
async def test_interleaved_multihop_exact_recovery_projection(tmp_path):
    h = harness(tmp_path / "history.db", routes=(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING))
    checkpoint(h)
    await transfer(h, "curator")
    saved = checkpoint(h)
    h.journal.observe_target(saved, phase="target_started")
    audit(h, "general_chat", "denied", rejected=True)
    await transfer(h, "explorer", call_id="explore")
    saved = checkpoint(h)
    h.journal.observe_target(saved, phase="target_started")
    h.run = record_task_result(h.run, TaskResult(task_id="task:0", route_key=RouteKey.WORLDLINE,
                                                status=TaskStatus.SUCCESS, content="已探索"))
    h.envelope = None
    saved = checkpoint(h)
    h.journal.observe_target(saved, phase="target_finished")
    await transfer(h, "curator", call_id="return")
    checkpoint(h)
    pending = audit(h, "general_chat", "pending")
    usage = h.writer.append_public_event(h.identity, "usage:reserve:reserved", HistoryEventType.MODEL_USAGE,
        PublicUsage(run_id=h.run.run_id, agent="curator", call_id="reserve", phase="reserved", tokens=20))
    journal_before = events(h)
    receipts = [event for event in journal_before if event.payload.get("phase") == "committed"]
    output = rebuilder(h).rebuild(session_id=h.session.session_id, page_size=2)
    expected_hops = [{"from_agent": source, "to_agent": target,
                      "transfer_id": event.payload["transfer_id"], "event_seq": event.event_seq}
                     for source, target, event in zip(("general_chat", "curator", "explorer"),
                                                       ("curator", "explorer", "curator"), receipts, strict=True)]
    assert output.checkpoint.model_dump(mode="json") == {
        "schema_version": 1, "run_id": h.run.run_id, "session_id": h.session.session_id,
        "conversation_id": h.session.conversation_id,
        "handoff_chain": expected_hops, "accepted_hop_count": 3, "last_agent": "curator",
        "last_run_id": h.run.run_id, "run_status": "running", "transfer_phase": "proposed",
        "recent_run_chain": ["general_chat", "curator", "explorer", "curator"],
        "model_usage_refs": [{"kind": "event", "target_id": usage.event_id}],
        "budget_ref": {"continuity_task_id": h.session.task_id, "run_id": h.run.run_id, "event_type": "model_usage"},
        "missing": [
            {"field": "original_input", "reason": "not_recorded_in_public_journal"},
            {"field": "plan", "reason": "public_plan_digest_is_not_an_execution_plan"},
            {"field": "hops", "reason": "original_tool_call_ids_not_recorded"},
            {"field": "usage", "reason": "existing_ledger_reference_only"},
            {"field": "messages", "reason": "not_recorded_in_public_journal"},
            {"field": "transfer_envelope", "reason": "original_payload_not_recorded"},
            {"field": "execution_checkpoint", "reason": "recovery_evidence_cannot_execute_graph"},
            {"field": "payload_digest", "reason": "original_payload_unavailable_for_sha256_recomputation"},
        ],
        "rebuilt_from": {"journal_head_event_seq": usage.event_seq, "digest_verified": False},
    }
    assert output.events_consumed + output.events_skipped == len(journal_before)
    assert output.missing_markers == 8 and pending.event_id not in [hop.transfer_id for hop in output.checkpoint.handoff_chain]
    assert events(h) == journal_before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["proposed", "rejected", "target_started", "target_finished"])
async def test_only_committed_counts_and_observation_never_creates_a_hop(tmp_path, phase):
    h = harness(tmp_path / "history.db")
    checkpoint(h)
    if phase in {"proposed", "rejected"}:
        audit(h, "curator", "audit", rejected=phase == "rejected")
        expected = 0
    else:
        await transfer(h, "curator")
        saved = checkpoint(h)
        if phase == "target_finished":
            # Curator is a relay here, so it has no task result. Build a real
            # owned task instead for the explicit target-finished observation.
            h = harness(tmp_path / "finished.db", routes=(RouteKey.TOOL_CALLING,))
            await transfer(h, "curator")
            checkpoint(h)
            h.run = record_task_result(h.run, TaskResult(task_id="task:0", route_key=RouteKey.TOOL_CALLING,
                                                        status=TaskStatus.SUCCESS, content="已查询"))
            h.envelope = None
            saved = checkpoint(h)
        h.journal.observe_target(saved, phase=phase)
        expected = 1
    result = rebuilder(h).rebuild(h.run.run_id)
    assert result.checkpoint.accepted_hop_count == expected
    assert result.checkpoint.transfer_phase == phase


@pytest.mark.asyncio
async def test_repeated_prefix_is_byte_identical_and_does_not_touch_live_graph_or_receipts(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    native = h.saver.get_tuple(h.config)
    journal_before = events(h)
    builder = rebuilder(h)
    first = builder.rebuild(h.run.run_id)
    first_row = h.saver.get_tuple(first.checkpoint_config)
    second = builder.rebuild(h.run.run_id, through_seq=first.checkpoint.rebuilt_from.journal_head_event_seq)
    second_row = h.saver.get_tuple(second.checkpoint_config)
    assert first.checkpoint.model_dump_json().encode() == second.checkpoint.model_dump_json().encode()
    assert json.dumps(first_row.checkpoint, sort_keys=True).encode() == json.dumps(second_row.checkpoint, sort_keys=True).encode()
    assert first.checkpoint_config == second.checkpoint_config
    assert first_row.parent_config is None and second_row.parent_config is None
    assert len(list(h.saver.list(first.checkpoint_config))) == 1
    assert h.saver.get_tuple(h.config) == native
    assert events(h) == journal_before
    assert first.checkpoint_config["configurable"]["checkpoint_ns"] == RECOVERY_NAMESPACE
    assert set(first_row.checkpoint["channel_values"]) == {"recovery_checkpoint"}


@pytest.mark.asyncio
async def test_whole_checkpoint_storage_loss_uses_journal_only(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    await transfer(h, "explorer", call_id="second")
    checkpoint(h)
    empty_saver = JournalCheckpointer(create_memory_checkpointer(), h.journal)
    assert empty_saver.get_tuple(h.config) is None
    result = rebuilder(h, empty_saver).rebuild(h.run.run_id)
    assert result.checkpoint.recent_run_chain == ("general_chat", "curator", "explorer")
    assert empty_saver.get_tuple(result.checkpoint_config).checkpoint["channel_values"]["recovery_checkpoint"] == result.checkpoint.model_dump(mode="json")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_digest", ["sha256:" + "0" * 64, "invalid-digest"])
async def test_legacy_receipt_digest_conflict_aborts_before_put_and_audit_is_idempotent(tmp_path, bad_digest):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    receipt = next(event for event in events(h) if event.payload.get("phase") == "committed")
    view = JournalReader(h.store).transfer(receipt).model_dump(mode="json")
    view["summary"]["payload_digest"] = bad_digest
    h.store.append_event_once(receipt.model_copy(update={"event_id": "evt:legacy-conflict", "event_key": "legacy-conflict", "payload": view}))
    head = events(h)[-1].event_seq
    spy = Mock(wraps=h.saver.put)
    h.saver.put = spy
    for _ in range(2):
        with pytest.raises(TransferIntegrityConflict):
            rebuilder(h).rebuild(h.run.run_id, through_seq=head)
    spy.assert_not_called()
    errors = events(h, HistoryEventType.ERROR)
    assert len(errors) == 1 and errors[0].payload["code"] == "integrity_conflict"


def test_empty_journal_discloses_all_execution_gaps_without_phase_guess(tmp_path):
    h = harness(tmp_path / "empty.db")
    result = rebuilder(h).rebuild(session_id=h.session.session_id)
    state = result.checkpoint
    assert state.run_id is None and state.last_run_id is None and state.last_agent is None
    assert state.run_status is None and state.transfer_phase is None and state.budget_ref is None
    assert state.accepted_hop_count == 0 and state.recent_run_chain == ()
    assert {item.field for item in state.missing} >= {
        "original_input", "plan", "hops", "usage", "messages", "transfer_envelope", "execution_checkpoint",
        "run_id", "last_run_id", "last_agent", "run_status", "transfer_phase", "budget_ref",
    }
    assert result.events_consumed == result.events_skipped == 0


class MaskedStore:
    def __init__(self, store, hidden):
        self.store, self.hidden = store, hidden

    def __getattr__(self, name):
        return getattr(self.store, name)

    def get_history_event(self, event_id):
        return None if event_id in self.hidden else self.store.get_history_event(event_id)

    def query_task_history(self, *args, **kwargs):
        return [event for event in self.store.query_task_history(*args, **kwargs) if event.event_id not in self.hidden]


@pytest.mark.asyncio
async def test_missing_proposal_does_not_guess_accepted_transfer(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    proposed = next(event for event in events(h) if event.payload.get("phase") == "proposed")
    masked = MaskedStore(h.store, {proposed.event_id})
    result = rebuilder(h, store=masked).rebuild(h.run.run_id)
    assert result.checkpoint.accepted_hop_count == 0
    assert result.checkpoint.last_agent is None
    assert {item.field for item in result.checkpoint.missing} >= {"handoff_chain", "last_agent"}


def append_unknown(h):
    body = HistoryEvent(event_id="evt:future", task_id=h.session.task_id, session_id=h.session.session_id,
        event_type=HistoryEventType.ERROR, timestamp=datetime.now(UTC), node="future", source="test",
        payload={"run_id": h.run.run_id}).model_dump(mode="json")
    body["event_type"] = "future_journal_v9"
    # Simulate an event written by a future producer without widening today's
    # public Writer or modifying/deleting any existing immutable event.
    connection = h.store._connect()
    try:
        seq = connection.execute("SELECT COALESCE(MAX(event_seq),0)+1 FROM history_events").fetchone()[0]
        body["event_seq"] = seq
        connection.execute("INSERT INTO history_events(event_id,task_id,session_id,event_type,timestamp,node,source,body,event_key,event_seq) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)", (body["event_id"], h.session.task_id, h.session.session_id, body["event_type"],
                body["timestamp"], "future", "test", json.dumps(body), "future", seq))
        connection.commit()
    finally:
        connection.close()
    return seq


@pytest.mark.asyncio
async def test_unknown_persisted_event_is_opaque_missing_even_when_it_breaks_typed_store_reads(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    seq = append_unknown(h)
    result = rebuilder(h).rebuild(h.run.run_id, page_size=1)
    assert result.checkpoint.accepted_hop_count == 1
    assert result.checkpoint.rebuilt_from.journal_head_event_seq == seq
    assert any(item.field == "journal" and item.reason == "unknown_event_type_or_invalid_envelope" for item in result.checkpoint.missing)
    assert result.events_skipped == 1


@pytest.mark.asyncio
async def test_prefix_bounds_exclude_future_receipts_and_detect_missing_earlier_hops(tmp_path):
    h = harness(tmp_path / "history.db")
    checkpoint(h)
    await transfer(h, "curator")
    proposal_seq = events(h)[-1].event_seq
    checkpoint(h)
    first_end = events(h)[-1].event_seq
    await transfer(h, "explorer", call_id="next")
    checkpoint(h)
    builder = rebuilder(h)
    prior = builder.rebuild(h.run.run_id, through_seq=proposal_seq, page_size=1).checkpoint
    assert prior.accepted_hop_count == 0 and prior.last_agent == "general_chat" and prior.transfer_phase == "proposed"
    full = builder.rebuild(h.run.run_id, through_seq=first_end).checkpoint
    assert full.accepted_hop_count == 1 and full.recent_run_chain == ("general_chat", "curator")
    partial = builder.rebuild(h.run.run_id, after_seq=first_end, page_size=1).checkpoint
    assert partial.accepted_hop_count == 1 and partial.last_agent == "explorer" and partial.recent_run_chain == ()
    assert any(item.reason == "committed_chain_gap" for item in partial.missing)


@pytest.mark.parametrize("kwargs", [{}, {"session_id": "test", "page_size": 0},
    {"session_id": "test", "after_seq": -1}, {"session_id": "test", "after_seq": 3, "through_seq": 2},
    {"session_id": "test", "page_size": True}])
def test_invalid_request_and_bounds_fail_before_checkpoint_write(tmp_path, kwargs):
    h = harness(tmp_path / "history.db")
    spy = Mock(wraps=h.saver.put)
    builder = rebuilder(h, SimpleNamespace(put=spy))
    with pytest.raises(ValueError):
        builder.rebuild(**kwargs)
    spy.assert_not_called()


def test_run_only_requires_task_scope_and_cross_task_session_is_rejected(tmp_path):
    h = harness(tmp_path / "history.db")
    builder = JournalCheckpointRebuilder(h.writer, h.saver)
    with pytest.raises(ValueError, match="scope"):
        builder.rebuild(h.run.run_id)
    with pytest.raises(ValueError, match="scope"):
        JournalCheckpointRebuilder(h.writer, h.saver, task_id="other").rebuild(session_id=h.session.session_id)


def test_schema_cannot_claim_complete_or_encode_unknown_as_a_phase(tmp_path):
    h = harness(tmp_path / "history.db")
    state = rebuilder(h).rebuild(session_id=h.session.session_id).checkpoint.model_dump(mode="json")
    for updates in ({"transfer_phase": "unknown"}, {"accepted_hop_count": 1}, {"missing": []},
                    {"rebuilt_from": {"journal_head_event_seq": 0, "digest_verified": True}}, {"original_input": "伪造"}):
        with pytest.raises(ValueError):
            RecoveryCheckpoint.model_validate({**state, **updates})


def test_usage_is_bounded_references_and_never_a_second_budget_ledger(tmp_path):
    h = harness(tmp_path / "history.db")
    for index in range(67):
        h.writer.append_public_event(h.identity, f"usage:call:{index}:reserved", HistoryEventType.MODEL_USAGE,
            PublicUsage(run_id=h.run.run_id, agent="curator", call_id=f"call:{index}", phase="reserved", tokens=index))
    spy = Mock(wraps=h.store.query_task_history)
    h.store.query_task_history = spy
    result = rebuilder(h).rebuild(h.run.run_id, page_size=3)
    assert len(result.checkpoint.model_usage_refs) == 64
    assert any(item.reason == "reference_window_truncated_use_existing_ledger" for item in result.checkpoint.missing)
    assert result.checkpoint.budget_ref.run_id == h.run.run_id
    assert "tokens" not in result.checkpoint.model_dump_json()
    assert max(call.kwargs["limit"] for call in spy.call_args_list) <= 3


@pytest.mark.asyncio
async def test_planner_and_executor_request_server_facts_and_honestly_degrade(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    state = rebuilder(h).rebuild(h.run.run_id).checkpoint
    validator = RealityRevalidator(h.store)
    report = validator.revalidate(projection(h), LiveSnapshot(), state)
    plan = RecoveryPlanner().decide(report, state)
    assert plan.action == "refresh_facts" and set(plan.required_reads) >= {"original_input", "plan", "hops", "usage", "execution_checkpoint", "payload_digest"}
    assert plan.resume_agent is None and plan.resume_checkpoint_ref is None
    calls = []
    async def read(fields, context, live):
        calls.append(fields)
        assert context.identity.run_id == h.run.run_id and context.recovery_checkpoint == state
        return LiveSnapshot(observations=(StateObservation(field="original_input", value="服务端可信输入",
            source="server_execution", observed_at=datetime.now(UTC), scope=h.session.conversation_id),))
    executor = RecoveryExecutor(h.writer, h.journal, read_facts=read)
    before = state.model_dump_json()
    plan, report, live = await executor.execute(projection(h), LiveSnapshot(), state,
        operations=SimpleNamespace(accounting=SimpleNamespace(totals=Mock(side_effect=AssertionError("no budget recomputation")))))
    assert calls and "original_input" in calls[0] and "original_input" not in calls[-1]
    assert report.field_results["original_input"] == "valid"
    assert plan.action == "needs_input" and plan.blocked_reason == "recovery_facts_unavailable"
    assert plan.resume_agent is None and plan.resume_checkpoint_ref is None and state.model_dump_json() == before
    assert len([event for event in events(h) if event.payload.get("phase") == "committed"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["absent", "timeout", "no_progress"])
async def test_unavailable_provider_is_a_legal_needs_input_plan(tmp_path, provider):
    h = harness(tmp_path / "history.db")
    state = rebuilder(h).rebuild(session_id=h.session.session_id).checkpoint
    async def read(fields, context, live):
        if provider == "timeout":
            raise TimeoutError("server unavailable")
        return live
    plan, report, _ = await RecoveryExecutor(h.writer, h.journal, read_facts=None if provider == "absent" else read).execute(
        projection(h), LiveSnapshot(), state)
    assert plan.action == "needs_input" and plan.required_reads
    assert report.field_results["execution_checkpoint"] == "unknown"


@pytest.mark.parametrize("source,scope,age", [("client_player", "correct", 0), ("server_execution", "other", 0),
                                          ("server_execution", "correct", 301)])
def test_live_recovery_facts_require_server_source_scope_and_freshness(tmp_path, source, scope, age):
    h = harness(tmp_path / "history.db")
    state = rebuilder(h).rebuild(session_id=h.session.session_id).checkpoint
    now = datetime.now(UTC)
    live = LiveSnapshot(observations=(StateObservation(field="original_input", value="旧输入", source=source,
        observed_at=now - timedelta(seconds=age), scope=h.session.conversation_id if scope == "correct" else scope),))
    report = RealityRevalidator(h.store, now=lambda: now).revalidate(projection(h), live, state)
    assert report.field_results["original_input"] == "unknown"


@pytest.mark.asyncio
async def test_cross_scope_recovery_never_calls_provider_or_writes_audit(tmp_path):
    h = harness(tmp_path / "history.db")
    state = rebuilder(h).rebuild(session_id=h.session.session_id).checkpoint
    invalid = state.model_copy(update={"conversation_id": "other"})
    async def fail(*args):
        raise AssertionError("must not call cross-scope provider")
    plan, report, _ = await RecoveryExecutor(h.writer, h.journal, read_facts=fail).execute(projection(h), LiveSnapshot(), invalid)
    assert report.checkpoint_integrity == "conflict" and plan.action == "needs_input"
    assert events(h) == []


@pytest.mark.asyncio
async def test_reconciled_without_committed_is_a_missing_marker_not_an_accepted_hop(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    saved = checkpoint(h, saver=h.saver.saver)
    h.journal.confirm_checkpoint(saved, reconcile=True)
    state = rebuilder(h).rebuild(h.run.run_id).checkpoint
    assert state.accepted_hop_count == 0 and state.last_agent is None
    assert state.transfer_phase == "reconciled"
    assert any(item.reason == "observation_without_committed_event" for item in state.missing)


@pytest.mark.asyncio
async def test_duplicate_legacy_committed_receipt_is_folded_once(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    receipt = next(event for event in events(h) if event.payload.get("phase") == "committed")
    body = JournalReader(h.store).transfer(receipt).model_dump(mode="json")
    h.store.append_event_once(receipt.model_copy(update={"event_id": "evt:legacy-copy", "event_key": "legacy-copy", "payload": body}))
    result = rebuilder(h).rebuild(h.run.run_id)
    assert result.checkpoint.accepted_hop_count == 1 and result.events_skipped == 1


@pytest.mark.asyncio
async def test_identity_mismatch_with_matching_digest_is_still_an_integrity_conflict(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    receipt = next(event for event in events(h) if event.payload.get("phase") == "committed")
    body = JournalReader(h.store).transfer(receipt).model_dump(mode="json")
    body["summary"]["target_agent"] = "explorer"
    h.store.append_event_once(receipt.model_copy(update={"event_id": "evt:legacy-changed", "event_key": "legacy-changed", "payload": body}))
    spy = Mock(wraps=h.saver.put)
    with pytest.raises(TransferIntegrityConflict):
        rebuilder(h, SimpleNamespace(put=spy)).rebuild(h.run.run_id)
    spy.assert_not_called()


@pytest.mark.asyncio
async def test_completed_run_remains_terminal_and_never_requests_reexecution(tmp_path):
    h = harness(tmp_path / "history.db", routes=(RouteKey.TOOL_CALLING,))
    await transfer(h, "curator")
    checkpoint(h)
    h.run = record_task_result(h.run, TaskResult(task_id="task:0", route_key=RouteKey.TOOL_CALLING,
        status=TaskStatus.SUCCESS, content="只读查询已完成"))
    h.run = finish_run(h.run, "已完成")
    h.envelope = None
    checkpoint(h)
    state = rebuilder(h).rebuild(h.run.run_id).checkpoint
    report = RealityRevalidator(h.store).revalidate(projection(h), LiveSnapshot(), state)
    plan = RecoveryPlanner().decide(report, state)
    assert state.run_status == "completed" and plan.action == "stop"
    assert plan.resume_agent is None and plan.resume_checkpoint_ref is None


@pytest.mark.asyncio
async def test_frozen_head_does_not_consume_concurrent_appends(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    head = events(h)[-1].event_seq
    original = h.store.query_task_history
    appended = []
    def query(*args, **kwargs):
        rows = original(*args, **kwargs)
        if kwargs.get("descending") and not appended:
            appended.append(True)
            audit(h, "explorer", "concurrent")
        return rows
    h.store.query_task_history = query
    result = rebuilder(h).rebuild(h.run.run_id)
    assert result.checkpoint.rebuilt_from.journal_head_event_seq == head
    assert result.checkpoint.transfer_phase == "committed"
    assert original(h.session.task_id, limit=100)[-1].event_seq > head


@pytest.mark.asyncio
async def test_later_prefix_never_overwrites_prior_checkpoint_channel_blob(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    builder = rebuilder(h)
    first = builder.rebuild(h.run.run_id)
    first_row = h.saver.get_tuple(first.checkpoint_config)
    await transfer(h, "explorer", call_id="later")
    checkpoint(h)
    later = builder.rebuild(h.run.run_id)
    assert later.checkpoint.accepted_hop_count == 2
    assert h.saver.get_tuple(first.checkpoint_config).checkpoint == first_row.checkpoint
    assert h.saver.get_tuple(later.checkpoint_config).checkpoint["channel_values"]["recovery_checkpoint"] == later.checkpoint.model_dump(mode="json")


def test_validation_process_and_child_preserve_unicode_stdout():
    child = subprocess.run([sys.executable, "-c", "print('持久消息')"], capture_output=True, text=True, check=True)
    assert child.stdout.strip() == "持久消息"

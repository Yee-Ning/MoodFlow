"""Recovery bookkeeping and query contracts; A remains the sole write repairer."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.agent.foundation.long_horizon.accounting import DurableAccounting
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
from app.agent.foundation.long_horizon.execution import ContinuityConflict, ExecutionLocks
from app.agent.foundation.long_horizon.history_writer import digest
from app.agent.foundation.long_horizon.models import HistoryEventType, HistoryPointer, LiveSnapshot, RecoveryGap, SessionRecord
from app.agent.foundation.long_horizon.operations import OperationJournal, active_operations
from app.agent.foundation.long_horizon.public_events import PublicAction, PublicActionAck, PublicTool, PublicUsage
from app.agent.foundation.long_horizon.recovery_executor import RecoveryExecutor
from app.agent.foundation.long_horizon.recovery_receipts import RecoveryReceiptRepair, answer_recovery_query, latest_recovery_status
from app.agent.foundation.subgraphs.general_chat.assistant import answer_general_chat
from app.agent.foundation.routing import RoutingContext
from app.agent.handoff.state import RunStatus, StopReason
from app.main import app
from tests.context_loop_helpers import checkpoint, harness, transfer
from tests.test_context_loop_recovery import project
from tests.test_write_reconciliation import reads


def operation(h, client=None):
    lease = ExecutionLocks(h.store).acquire(h.identity.conversation_id, digest(["handoff", "test-user"]))
    accounting = DurableAccounting(h.writer, h.identity, lease)
    return OperationJournal(h.writer, accounting, read_client=client)


def propose(h, operations):
    oid = "op:" + "a" * 64
    h.writer.append_public_event(h.identity, "operation:" + oid + ":proposed", HistoryEventType.TOOL_CALL,
        PublicTool(run_id=h.run.run_id, operation_id=oid, name="add_song_to_playlist",
            parameters={"playlist_id": 18445771229, "song_id": 2652820720}, status="proposed", effect="write",
            auth_scope=operations.accounting.lease.scope))
    return oid


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,reason", [("success", "readback_confirmed"), ("absent", "effect_not_observed"),
    ("unavailable", "readback_client_unavailable"), ("read_error", "http_502"),
    ("scope", "write_evidence_or_scope_conflict")])
async def test_write_gap_repair_only_calls_a_reconcile_and_reports_block(tmp_path, outcome, reason, monkeypatch):
    h = harness(tmp_path / "history.db")
    client = reads(present=outcome != "absent") if outcome != "unavailable" else None
    if outcome == "read_error":
        from app.tools.music import MusicServiceError
        client.get_playlist_detail.side_effect = MusicServiceError("http_502", "读回失败")
    ops = operation(h, client)
    oid = propose(h, ops)
    if outcome == "scope":
        ops.accounting.lease.scope = "wrong-scope"
    saved = checkpoint(h)
    original = ops.reconcile
    spy = AsyncMock(wraps=original)
    monkeypatch.setattr(ops, "reconcile", spy)
    repair = RecoveryReceiptRepair(h.writer, h.journal)
    gaps = await repair.repair(saved, ops)
    assert gaps[0].subject_id == oid and gaps[0].reason == reason
    assert gaps[0].status == ("repaired" if outcome == "success" else "blocked")
    spy.assert_awaited_once_with(oid)
    repair.record(saved, gaps)
    coordinator = ContinuityCoordinator(h.service)
    view = coordinator.recovery_status(graph_mode="handoff", thread_id="one", user_id="test-user")
    assert view["receipt_gaps"][0]["reason"] == reason
    assert not any(row.payload["status"] == "success" for row in ops.write_events()) or outcome == "success"
    ops.accounting.lease.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["deadline", "tokens", "cancelled"])
async def test_missing_transfer_receipt_repairs_before_budget_or_cancel_stop(tmp_path, condition):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    if condition == "deadline":
        h.run = h.run.model_copy(update={"started_at": datetime.now(UTC) - timedelta(seconds=121)})
    if condition == "cancelled":
        h.run = h.run.model_copy(update={"status": RunStatus.FAILED, "stop_reason": StopReason.CANCELLED,
            "final_response": "用户已取消原运行。"})
    saved = checkpoint(h, saver=h.saver.saver)
    ops = operation(h)
    if condition == "tokens":
        h.writer.append_public_event(h.identity, "baseline", HistoryEventType.MODEL_USAGE,
            PublicUsage(run_id=h.run.run_id, agent="curator", call_id="base", phase="baseline", tokens=0))
        h.writer.append_public_event(h.identity, "reserved", HistoryEventType.MODEL_USAGE,
            PublicUsage(run_id=h.run.run_id, agent="curator", call_id="unknown-dispatch", phase="reserved",
                tokens=h.run.limits.agent_token_budget))
    before = saved.run.model_dump(mode="json")
    gaps = await RecoveryReceiptRepair(h.writer, h.journal).repair(saved, ops)
    assert all(g.status == "repaired" for g in gaps)
    plan, report, _ = await RecoveryExecutor(h.writer, h.journal).execute(project(h), LiveSnapshot(), saved,
        operations=ops, receipt_gaps=gaps)
    assert plan.action == "stop"
    assert plan.blocked_reason == ("original_run_cancelled" if condition == "cancelled" else "original_run_limit_exhausted")
    assert saved.run.model_dump(mode="json") == before
    assert report.receipt_gaps == gaps
    assert len([row for row in h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)
        if row.payload["phase"] == "reconciled"]) == 1
    ops.accounting.lease.release()


@pytest.mark.asyncio
async def test_transfer_conflict_is_structured_and_never_repaired(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    saved = checkpoint(h, saver=h.saver.saver)
    saved = replace(saved, ref=saved.ref.model_copy(update={"public_state_digest": "sha256:" + "0" * 64}))
    ops = operation(h)
    gaps = await RecoveryReceiptRepair(h.writer, h.journal).repair(saved, ops)
    assert gaps[0].status == "blocked" and gaps[0].reason == "checkpoint_evidence_conflict"
    assert not any(row.payload["phase"] == "reconciled" for row in h.store.query_task_history(h.session.task_id,
        event_type=HistoryEventType.AGENT_TRANSFER))
    ops.accounting.lease.release()


@pytest.mark.asyncio
async def test_ack_gap_is_not_fabricated_and_generalchat_explains_it(tmp_path):
    from app.agent.foundation.models import TaskResult, TaskStatus
    from app.agent.foundation.routing import RouteKey
    from app.agent.handoff.state import record_task_result
    h = harness(tmp_path / "history.db", routes=(RouteKey.TOOL_CALLING,))
    await transfer(h, "curator")
    h.run = record_task_result(h.run, TaskResult(task_id="task:0", route_key=RouteKey.TOOL_CALLING,
        status=TaskStatus.SUCCESS, content="播放器动作已发出", data={"client_actions": [{"action_id": "pause-one"}]}))
    h.envelope = None
    h.writer.append_public_event(h.identity, "action:pause-one", HistoryEventType.CLIENT_ACTION,
        PublicAction(action_id="pause-one", name="pause_player", arguments_digest=digest({})))
    saved = checkpoint(h)
    ops = operation(h)
    repair = RecoveryReceiptRepair(h.writer, h.journal)
    gaps = await repair.repair(saved, ops)
    assert gaps[0].kind == "action_ack" and gaps[0].reason == "action_ack_missing"
    repair.record(saved, gaps)
    token = active_operations.set(ops)
    model = AsyncMock(side_effect=AssertionError("must answer from recovery ledger"))
    try:
        answer = await answer_recovery_query("恢复卡在哪一步")
        chat = await answer_general_chat([], "恢复卡在哪一步", RoutingContext(), [], model=model)
    finally:
        active_operations.reset(token)
    assert "缺少播放器 ACK" in answer and "缺少播放器 ACK" in chat.text
    assert not h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.ACTION_ACK)
    ops.accounting.lease.release()


def test_recovery_query_api_is_scoped_validated_and_read_only(tmp_path, monkeypatch):
    h = harness(tmp_path / "history.db")
    ops = operation(h)
    ops.accounting.lease.release()
    coordinator = ContinuityCoordinator(h.service)
    monkeypatch.setattr("app.agent.foundation.long_horizon.coordinator.get_continuity_coordinator", lambda: coordinator)
    async def resolve(*args):
        return "test-user"
    monkeypatch.setattr("app.api.chat.UserIdentityProvider.resolve", resolve)
    saved = checkpoint(h)
    gap = RecoveryGap(
        kind="action_ack", subject_id="original-action", status="blocked", reason="action_ack_missing")
    coordinator.receipt_repair.record(saved, (gap,))
    count = len(h.store.query_task_history(h.session.task_id, limit=100))
    client = TestClient(app)
    dto = {"thread_id": "one", "graph_mode": "handoff"}
    response = client.post("/api/ai/continuity/recovery/status", json=dto)
    assert response.status_code == 200
    assert response.json()["receipt_gaps"][0]["reason"] == "action_ack_missing"
    assert client.post("/api/ai/continuity/recovery/status", json={"thread_id": "one"}).status_code == 422
    mismatch = client.post("/api/ai/continuity/recovery/status", json={**dto, "graph_mode": "foundation"})
    assert mismatch.status_code == 409 and mismatch.json()["detail"]["code"] == "recovery_scope_mismatch"
    assert len(h.store.query_task_history(h.session.task_id, limit=100)) == count
    with pytest.raises(ContinuityConflict):
        coordinator.recovery_status(**dto, user_id="different-user")


@pytest.mark.asyncio
async def test_cancelled_readback_propagates_without_failed_receipt(tmp_path):
    h = harness(tmp_path / "history.db")
    client = reads()
    client.get_playlist_detail.side_effect = asyncio.CancelledError()
    ops = operation(h, client)
    oid = propose(h, ops)
    saved = checkpoint(h)
    try:
        with pytest.raises(asyncio.CancelledError):
            await RecoveryReceiptRepair(h.writer, h.journal).repair(saved, ops)
        assert ops.state(oid)["state"] == "unknown"
        assert not any(row.payload["status"] == "failed" for row in ops.write_events())
    finally:
        ops.accounting.lease.release()


def test_status_query_ignores_legacy_cold_start_schema(tmp_path):
    h = harness(tmp_path / "history.db")
    repair = RecoveryReceiptRepair(h.writer, h.journal)
    repair.record(checkpoint(h), (RecoveryGap(kind="action_ack", subject_id="one", status="blocked",
        reason="action_ack_missing"),))
    h.service.record_event(task_id=h.session.task_id, session_id=h.session.session_id,
        event_type=HistoryEventType.REALITY_REVALIDATION, node="cold_start", source="live_state",
        payload={"historical": {}, "current": {}, "changed_fields": []})
    view = latest_recovery_status(h.store, h.session.task_id)
    assert view["receipt_gaps"][0]["reason"] == "action_ack_missing"


@pytest.mark.asyncio
async def test_original_session_ack_remains_visible_to_migrated_task_recovery(tmp_path):
    from app.agent.foundation.models import TaskResult, TaskStatus
    from app.agent.foundation.routing import RouteKey
    from app.agent.handoff.state import record_task_result
    h = harness(tmp_path / "history.db", routes=(RouteKey.TOOL_CALLING,))
    await transfer(h, "curator")
    h.run = record_task_result(h.run, TaskResult(task_id="task:0", route_key=RouteKey.TOOL_CALLING,
        status=TaskStatus.SUCCESS, content="播放器动作已确认", data={"client_actions": [{"action_id": "original"}]}))
    h.envelope = None
    action = h.writer.append_public_event(h.identity, "action:original", HistoryEventType.CLIENT_ACTION,
        PublicAction(action_id="original", name="pause_player", arguments_digest=digest({})))
    h.writer.append_public_event(h.identity, "ack:original", HistoryEventType.ACTION_ACK,
        PublicActionAck(action_id="original", status="applied", action_ref=HistoryPointer(kind="event", target_id=action.event_id)))
    saved = checkpoint(h)
    h.store.close_session(h.session.session_id, datetime.now(UTC).isoformat())
    target = SessionRecord(task_id=h.session.task_id, conversation_id=h.session.conversation_id,
        session_id="migrated-session", previous_session_id=h.session.session_id, created_at=datetime.now(UTC))
    h.store.create_session(target)
    saved = replace(saved, identity=h.identity.model_copy(update={"session_id": target.session_id}))
    ops = operation(h)
    try:
        gaps = RecoveryReceiptRepair(h.writer, h.journal).scan(saved, ops)
        assert not any(g.kind == "action_ack" for g in gaps)
    finally:
        ops.accounting.lease.release()

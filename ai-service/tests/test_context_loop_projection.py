from datetime import UTC, datetime

import pytest

from app.agent.checkpointing import create_checkpoint_serializer
from app.agent.foundation.long_horizon.models import (
    HandoffDraft, HandoffDraftV2, HandoffProjectionV2, HistoryEventType,
    RolloverReason, read_handoff_projection,
)
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.agent.handoff.continuity import ProgressProjector
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import new_run, record_task_result
from tests.context_loop_helpers import checkpoint, harness, transfer


def draft():
    return HandoffDraft(goal="探索音乐并完成音乐待办", constraints=["不更改原始授权"], current_stage="进行中")


@pytest.mark.asyncio
async def test_proposals_and_rejections_cannot_invent_owner_or_accepted_hop(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    projection = ProgressProjector(h.service).project_progress(h.session, draft=draft())
    assert projection.agent_progress.last_agent is None
    assert projection.agent_progress.transfer_phase == "proposed"
    assert projection.agent_progress.accepted_hop_count == 0
    assert not projection.completed_steps


@pytest.mark.asyncio
async def test_real_chain_and_validated_results_aggregate_without_scratchpad(tmp_path):
    h = harness(tmp_path / "history.db", routes=(RouteKey.WORLDLINE, RouteKey.TOOL_CALLING))
    await transfer(h, "curator", call_id="first")
    checkpoint(h)
    await transfer(h, "explorer", call_id="second")
    checkpoint(h)
    h.run = record_task_result(h.run, TaskResult(
        task_id="task:0", route_key=RouteKey.WORLDLINE, status=TaskStatus.SUCCESS,
        content="探索已完成，证据已核对", data={"scratchpad": "private reasoning"},
    ))
    h.envelope = None
    checkpoint(h)
    await transfer(h, "curator", call_id="third")
    snapshot = checkpoint(h)
    projected = ProgressProjector(h.service).project_progress(h.session, draft=draft())
    assert projected.agent_progress.last_agent == "curator"
    assert projected.agent_progress.recent_run_chain == ("general_chat", "curator", "explorer", "curator")
    assert projected.agent_progress.accepted_hop_count == 3
    assert len(projected.completed_steps) == 1
    assert projected.pending_items == ["task:1"]
    assert projected.source_checkpoint_ref == snapshot.ref
    assert "scratchpad" not in projected.model_dump_json()
    assert "private reasoning" not in str(h.store.query_task_history(h.session.task_id))
    envelope = h.service.build_handoff(h.session.session_id, projected)
    assert envelope.token_count == h.service.counter.count(envelope.model_dump_json(exclude_none=True))
    assert envelope.token_count <= envelope.token_budget


@pytest.mark.asyncio
async def test_result_failure_is_not_completed_and_snapshot_usage_survives(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    await transfer(h, "explorer", call_id="two")
    checkpoint(h)
    h.run = record_task_result(h.run, TaskResult(
        task_id="task:0", route_key=RouteKey.WORLDLINE, status=TaskStatus.FAILED,
        content="资料暂时不可用", error="dependency_unavailable",
    ))
    h.envelope = None
    checkpoint(h)
    projected = ProgressProjector(h.service).project_progress(h.session, draft=draft())
    assert projected.completed_steps == []
    event = h.store.get_history_event(projected.agent_progress.run_state_ref.target_id)
    assert event.payload["run"]["started_at"] == h.run.model_dump(mode="json")["started_at"]
    assert event.payload["run"]["usage"] == h.run.model_dump(mode="json")["usage"]


@pytest.mark.asyncio
async def test_multiple_runs_retain_earlier_chain_refs_and_distinct_result_ids(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    snapshot = checkpoint(h)
    first = ProgressProjector(h.service).project_progress(h.session, draft=draft())
    h.run = new_run("run:two", "暂停播放", RouteDecision(is_complete=True, tasks=[
        RouteTask(task_id="task:0", route_key=RouteKey.TOOL_CALLING, input_text="暂停播放"),
    ]), started_at=datetime.now(UTC), entry_agent=AgentKey.GENERAL_CHAT)
    h.identity = h.identity.model_copy(update={"run_id": "run:two", "request_id": "request:two"})
    h.envelope = None
    await transfer(h, "curator", call_id="run-two")
    checkpoint(h)
    projected = ProgressProjector(h.service).project_progress(h.session, draft=draft())
    assert projected.agent_progress.last_run_id == "run:two"
    assert first.agent_progress.run_state_ref in projected.agent_progress.earlier_chain_refs
    assert projected.source_checkpoint_ref != snapshot.ref


@pytest.mark.asyncio
async def test_incremental_projection_preserves_confirmed_prefix(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    first = ProgressProjector(h.service).project_progress(h.session, draft=draft())
    await transfer(h, "explorer", call_id="next")
    checkpoint(h)
    projected = ProgressProjector(h.service).project_progress(h.session, first.source_event_cursor, draft=first)
    assert projected.agent_progress.accepted_hop_count == 2
    assert projected.agent_progress.last_agent == "explorer"


@pytest.mark.asyncio
async def test_v2_storage_and_json_checkpoint_roundtrip_do_not_require_new_modules(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    checkpoint(h)
    projected = ProgressProjector(h.service).project_progress(h.session, draft=draft())
    envelope = h.service.build_handoff(h.session.session_id, projected)
    h.store.put_handoff(envelope)
    loaded = h.store.get_handoff(envelope.handoff_id)
    assert isinstance(loaded, HandoffProjectionV2)
    assert loaded.agent_progress == projected.agent_progress
    serde = create_checkpoint_serializer()
    primitives = projected.model_dump(mode="json")
    assert serde.loads_typed(serde.dumps_typed(primitives)) == primitives


def test_v1_reader_explicitly_reports_unknown_transfer_dimension(tmp_path):
    h = harness(tmp_path / "history.db")
    v1 = h.service.build_handoff(h.session.session_id, draft())
    loaded = read_handoff_projection(v1.model_dump(mode="json"))
    assert loaded.agent_progress.last_agent is None
    assert loaded.agent_progress.transfer_phase == "unknown"
    assert not loaded.agent_progress.recent_run_chain


def test_mandatory_projection_overflow_and_sqlite_auto_rollover_leave_session_open(tmp_path):
    h = harness(tmp_path / "history.db")
    h.service.policy = h.service.policy.model_copy(update={"handoff_token_budget": 30})
    with pytest.raises(ValueError, match="token budget"):
        h.service.build_handoff(h.session.session_id, HandoffDraftV2(goal="目标", current_stage="进行中"))
    with pytest.raises(ValueError, match="atomic"):
        h.service.rollover(h.session.session_id, draft(), RolloverReason.TOKEN_THRESHOLD, automatic=True)
    assert h.store.active_session(h.session.task_id).session_id == h.session.session_id
    assert not h.service.query_task_history(h.session.task_id, event_type=HistoryEventType.SESSION_HANDOFF)


@pytest.mark.asyncio
async def test_new_session_v2_projection_retains_previous_confirmed_chain(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    snapshot = checkpoint(h)
    prior = ProgressProjector(h.service).project_progress(h.session, draft=draft())
    rolled = h.service.rollover(h.session.session_id, prior, RolloverReason.EXPLICIT_CHECKPOINT)
    next_draft = ProgressProjector(h.service).project_progress(rolled.new_session, draft=HandoffDraftV2(goal="继续任务", current_stage="恢复前"))
    assert next_draft.agent_progress.last_agent == "curator"
    assert next_draft.agent_progress.recent_run_chain == ("general_chat", "curator")
    assert next_draft.source_checkpoint_ref == snapshot.ref
    h.service.build_handoff(rolled.new_session.session_id, next_draft)


@pytest.mark.asyncio
async def test_control_call_ids_are_scoped_by_run_when_session_history_contains_two_runs(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator", call_id="shared-call")
    checkpoint(h)
    h.run = new_run("run:later", h.run.original_input, h.run.plan, started_at=datetime.now(UTC), entry_agent=AgentKey.GENERAL_CHAT)
    h.identity = h.identity.model_copy(update={"run_id": h.run.run_id, "request_id": "request:later"})
    h.envelope = None
    await transfer(h, "curator", call_id="shared-call")
    checkpoint(h)
    projected = ProgressProjector(h.service).project_progress(h.session, draft=draft())
    assert projected.agent_progress.last_run_id == "run:later"
    assert projected.agent_progress.accepted_hop_count == 1

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from app.agent.foundation.long_horizon.models import HandoffDraftV2, LiveSnapshot, StateObservation
from app.agent.foundation.long_horizon.recovery import RecoveryPlanner
from app.agent.foundation.long_horizon.revalidation import RealityRevalidator
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.handoff.continuity import ProgressProjector
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import record_task_result
from app.agent.foundation.routing import RouteKey
from tests.context_loop_helpers import checkpoint, harness, transfer


def project(h, **fields):
    draft = HandoffDraftV2(goal="继续完成音乐请求", current_stage="执行中", **fields)
    return h.service.build_handoff(h.session.session_id, ProgressProjector(h.service).project_progress(h.session, draft=draft))


def decide(h, snapshot, projection=None, observations=(), now=None):
    projection = projection or project(h)
    report = RealityRevalidator(h.store, now=now).revalidate(projection, LiveSnapshot(observations=observations), snapshot)
    return report, RecoveryPlanner().decide(report, snapshot)


@pytest.mark.asyncio
async def test_each_disconnect_boundary_preserves_source_or_requires_receipt_reconciliation(tmp_path):
    h = harness(tmp_path / "history.db")
    initial = checkpoint(h)
    assert decide(h, initial)[1].action == "resume_source"
    await transfer(h, "curator")
    committed_source = checkpoint(h)
    projection = project(h)
    await transfer(h, "explorer", call_id="next")
    # The accepted Command has not been persisted: keep the actual source.
    report, plan = decide(h, committed_source, projection)
    assert report.transfer_integrity == "uncommitted"
    assert plan.action == "resume_source" and plan.resume_agent == "curator"
    assert plan.resume_checkpoint_ref == committed_source.ref
    target = checkpoint(h, saver=h.saver.saver)
    report, plan = decide(h, target, projection)
    assert report.transfer_integrity == "missing_receipt"
    assert plan.action == "needs_input" and plan.required_reads == ("reconcile_committed_receipt",)
    assert plan.evidence_refs
    h.journal.confirm_checkpoint(target, reconcile=True)
    before = target.run.model_dump(mode="json")
    report, plan = decide(h, target)
    assert report.transfer_integrity == "valid"
    assert plan.action == "resume_target" and plan.resume_agent == "explorer"
    assert plan.run_id == before["run_id"] and plan.resume_checkpoint_ref == target.ref
    assert 0 < plan.remaining_deadline <= before["limits"]["run_timeout_seconds"]
    assert target.run.model_dump(mode="json") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["owner", "digest", "tool_pair", "orphan", "next_node", "lineage", "authority"])
async def test_transfer_integrity_faults_never_guess_owner(tmp_path, fault):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    saved = checkpoint(h)
    if fault == "owner":
        saved = replace(saved, run=saved.run.model_copy(update={"current_agent": AgentKey.EXPLORER}))
    elif fault == "digest":
        saved = replace(saved, ref=saved.ref.model_copy(update={"public_state_digest": "sha256:" + "0" * 64}))
    elif fault == "tool_pair":
        saved = replace(saved, messages=saved.messages[:-1])
    elif fault == "orphan":
        saved = replace(saved, messages=(*saved.messages, ToolMessage(content="{}", name="transfer_to_curator_agent", tool_call_id="orphan")))
    elif fault == "next_node":
        saved = replace(saved, next_nodes=("explorer_agent",))
    elif fault == "lineage":
        saved = replace(saved, ref=saved.ref.model_copy(update={"checkpoint_id": "other"}), ancestor_checkpoint_ids=frozenset())
    else:
        saved = replace(saved, envelope=saved.envelope.model_copy(update={"original_input": "扩大原始授权"}))
    report, plan = decide(h, saved)
    assert "conflict" in {report.checkpoint_integrity, report.transfer_integrity}
    assert plan.action == "needs_input" and plan.resume_agent is None


@pytest.mark.asyncio
@pytest.mark.parametrize("condition", ["deadline", "agent_tokens", "run_tokens", "limits_changed"])
async def test_original_limits_stop_without_budget_retry_or_start_reset(tmp_path, condition):
    h = harness(tmp_path / "history.db")
    now = datetime.now(UTC)
    if condition == "deadline":
        h.run = h.run.model_copy(update={"started_at": now - timedelta(seconds=121)})
    elif condition in {"agent_tokens", "run_tokens"}:
        tokens = h.run.limits.agent_token_budget if condition == "agent_tokens" else h.run.limits.run_token_budget
        h.run = h.run.model_copy(update={"usage": tuple(item.model_copy(update={"tokens": tokens, "estimated_tokens": tokens, "retries": 1}) if item.agent == AgentKey.CURATOR else item for item in h.run.usage)})
    saved = checkpoint(h)
    before = saved.run.model_dump(mode="json")
    report, plan = decide(h, saved, now=lambda: now)
    if condition == "limits_changed":
        plan = RecoveryPlanner().decide(report, saved, limits=saved.run.limits.model_copy(update={"run_timeout_seconds": 240}))
    assert plan.action == "stop"
    assert saved.run.model_dump(mode="json") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("source,age,revision,value,status", [
    ("retrieval_manifest", 0, "r1", "v1", "valid"),
    ("client_player", 0, "r1", "v1", "unknown"),
    ("retrieval_manifest", 301, "r1", "v1", "unknown"),
    ("retrieval_manifest", 0, None, "v1", "unknown"),
    ("retrieval_manifest", 0, "r2", "v1", "changed"),
    ("retrieval_manifest", 0, "r1", "v2", "changed"),
])
async def test_source_age_and_revision_control_fact_refresh(tmp_path, source, age, revision, value, status):
    h = harness(tmp_path / "history.db")
    saved = checkpoint(h)
    now = datetime.now(UTC)
    projection = project(h, revalidate_fields=["index_version"], last_known_state={"index_version": {"value": "v1", "revision": "r1"}})
    observation = StateObservation(field="index_version", value=value, source=source, observed_at=now - timedelta(seconds=age), revision=revision, scope=h.identity.conversation_id)
    report, plan = decide(h, saved, projection, [observation], now=lambda: now)
    assert report.field_results == {"index_version": status}
    assert plan.action == ("resume_source" if status == "valid" else "refresh_facts")
    if status != "valid":
        assert plan.required_reads == ("index_version",)


@pytest.mark.asyncio
async def test_unknown_auth_and_cross_scope_observation_require_input(tmp_path):
    h = harness(tmp_path / "history.db")
    saved = checkpoint(h)
    projection = project(h, revalidate_fields=["logged_in"], last_known_state={"logged_in": True})
    observation = StateObservation(field="logged_in", value=True, source="server_auth", observed_at=datetime.now(UTC), scope="other")
    assert decide(h, saved, projection, [observation])[1].blocked_reason == "current_authority_unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,historical,current", [("logged_in", True, False), ("write_permission", True, False), ("user_id", "user:original", "user:other")])
async def test_lost_authority_or_changed_user_requires_input_without_dispatch(tmp_path, field, historical, current):
    h = harness(tmp_path / "history.db")
    saved = checkpoint(h)
    projection = project(h, revalidate_fields=[field], last_known_state={field: historical})
    observation = StateObservation(field=field, value=current, source="server_auth", observed_at=datetime.now(UTC), scope=h.identity.conversation_id)
    report, plan = decide(h, saved, projection, [observation])
    assert report.field_results[field] == "changed"
    assert plan.action == "needs_input" and plan.blocked_reason == "current_authority_incompatible"


@pytest.mark.asyncio
async def test_client_write_ack_is_required_and_completed_task_is_not_replayed(tmp_path):
    h = harness(tmp_path / "history.db", routes=(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE))
    await transfer(h, "curator")
    checkpoint(h)
    h.run = record_task_result(h.run, TaskResult(task_id="task:0", route_key=RouteKey.TOOL_CALLING, status=TaskStatus.SUCCESS, content="暂停指令已发出", data={"client_actions": [{"action_id": "act:1", "action": "pause"}]}))
    h.envelope = None
    saved = checkpoint(h)
    before = saved.run.model_dump(mode="json")
    assert decide(h, saved)[1].blocked_reason == "write_effect_unknown"
    ack = StateObservation(field="action_ack:act:1", value="applied", source="client_action_ack", observed_at=datetime.now(UTC), scope=h.identity.conversation_id)
    report, plan = decide(h, saved, observations=[ack])
    assert report.write_status == "confirmed" and plan.action == "resume_target"
    assert tuple(result.task_id for result in saved.run.results) == ("task:0",)
    assert saved.run.model_dump(mode="json") == before
    assert decide(h, replace(saved, pending_writes=True), observations=[ack])[1].action == "needs_input"


@pytest.mark.asyncio
async def test_guard_rejection_is_terminal_and_superseded_retry_is_only_audit(tmp_path):
    h = harness(tmp_path / "history.db")
    async def reject():
        return SimpleNamespace(allowed=False, code="guard_rejected", score=0.1)
    await transfer(h, "curator", guard=SimpleNamespace(review=lambda _: reject()))
    saved = checkpoint(h)
    assert decide(h, saved)[1].action == "stop"
    # Use a separate run with the actual retry ledger; terminal runs stay shut.
    h = harness(tmp_path / "retry.db", suffix="retry")
    from app.llm.runtime import RequestLedger, request_scope
    ledger = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=45)
    with request_scope(ledger):
        await transfer(h, "curator", guard=SimpleNamespace(review=lambda _: reject()))
    saved = checkpoint(h)
    assert decide(h, saved)[1].action == "resume_source"
    await transfer(h, "curator", call_id="later")
    saved = checkpoint(h)
    assert decide(h, saved)[1].action == "resume_target"


@pytest.mark.asyncio
async def test_saved_result_without_history_receipt_is_reconciled_before_final_node(tmp_path):
    h = harness(tmp_path / "history.db", routes=(RouteKey.TOOL_CALLING,))
    await transfer(h, "curator")
    checkpoint(h)
    projection = project(h)
    h.run = record_task_result(h.run, TaskResult(task_id="task:0", route_key=RouteKey.TOOL_CALLING, status=TaskStatus.SUCCESS, content="只读目录查询完成"))
    h.envelope = None
    saved = checkpoint(h, saver=h.saver.saver)
    report, plan = decide(h, saved, projection)
    assert "result_receipt_reconciliation_required" in report.reasons
    assert plan.blocked_reason == "journal_reconciliation_required"
    h.journal.confirm_checkpoint(saved, reconcile=True)
    saved = replace(saved, next_nodes=("final_response",))
    report, plan = decide(h, saved)
    assert report.run_status == "finalizing" and plan.action == "resume_target"
    assert plan.resume_checkpoint_ref == saved.ref


@pytest.mark.asyncio
async def test_guard_retry_deadline_and_usage_are_preserved_on_resume(tmp_path):
    h = harness(tmp_path / "history.db")
    saved = checkpoint(h)
    expired = replace(saved, guard_retry_deadline=datetime.now(UTC) - timedelta(seconds=1))
    report, plan = decide(h, expired)
    assert report.remaining_deadline == 0 and plan.action == "stop"
    assert expired.run.started_at == saved.run.started_at and expired.run.usage == saved.run.usage
    assert decide(h, None)[1].action == "needs_input"


@pytest.mark.asyncio
async def test_superseded_uncommitted_proposal_is_audit_and_committed_journal_cannot_override_source(tmp_path):
    h = harness(tmp_path / "history.db")
    await transfer(h, "curator")
    source = checkpoint(h)
    projection = project(h)
    await transfer(h, "explorer", call_id="lost-command")
    assert decide(h, source, projection)[1].action == "resume_source"
    # Resume from the reliable saved source without deleting the first proposal.
    h.run, h.messages, h.envelope = source.run, list(source.messages), source.envelope
    await transfer(h, "explorer", call_id="confirmed-command")
    target = checkpoint(h)
    assert decide(h, target)[1].action == "resume_target"
    report, plan = decide(h, source, projection)
    assert report.transfer_integrity == "conflict" and plan.action == "needs_input"

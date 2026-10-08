"""Strict contracts, pointer ownership, single storage, and compatible views."""

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.foundation.context_contracts import CallManifest, PackManifest
from app.agent.foundation.long_horizon.journal_contracts import JournalReader, resolve_projection
from app.agent.foundation.long_horizon.models import (
    EventPointer, HandoffDraft, HandoffDraftV2, HistoryEventType,
    RunProgressRecord, TransferJournalReference,
    TransferProposalReference, HistoryPointer, RolloverReason,
)
from app.agent.foundation.long_horizon.public_events import PublicText, PublicUsage
from app.agent.handoff.contracts import HandoffEnvelope
from app.llm.runtime import active_manifest, request_scope
from tests.context_loop_helpers import checkpoint, harness, transfer


@pytest.mark.asyncio
async def test_envelope_required_and_extra_fields_and_digest_shape(tmp_path):
    h = harness(tmp_path / "contracts.db")
    await transfer(h, "curator")
    saved = checkpoint(h)
    data = saved.envelope.model_dump(mode="json")
    for name, field in HandoffEnvelope.model_fields.items():
        if field.is_required():
            with pytest.raises(ValidationError):
                HandoffEnvelope.model_validate({key: value for key, value in data.items() if key != name})
    for name in ("mood", "narration", "key_ids", "l3_tokens", "raw_prompt"):
        with pytest.raises(ValidationError):
            HandoffEnvelope.model_validate({**data, name: "extra"})
    assert set(data["request"]) == {"target_agent", "reason", "context_summary"}
    assert "usage" not in data


@pytest.mark.asyncio
async def test_transfer_receipts_store_only_canonical_proposal_pointer(tmp_path):
    h = harness(tmp_path / "contracts.db")
    await transfer(h, "curator")
    saved = checkpoint(h)
    h.journal.observe_target(saved, phase="target_started")
    events = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)
    canonical = JournalReader(h.store).transfer(events[0])
    for event in events[1:]:
        raw = TransferJournalReference.model_validate(event.payload)
        assert raw.summary_ref.target_id == events[0].event_id
        assert "summary" not in event.payload and "payload_digest" not in event.payload
        hydrated = JournalReader(h.store).transfer(event)
        assert hydrated.summary == canonical.summary
        assert hydrated.summary.payload_digest is not None
        with pytest.raises(ValidationError):
            TransferJournalReference.model_validate({**event.payload, "summary": events[0].payload["summary"]})
        for field in ("phase", "transfer_id", "summary_ref", "checkpoint_ref"):
            with pytest.raises(ValidationError):
                TransferJournalReference.model_validate({key: value for key, value in event.payload.items() if key != field})
    legacy = events[1].model_copy(update={"payload": JournalReader(h.store).transfer(events[1]).model_dump(mode="json")})
    assert JournalReader(h.store).transfer(legacy).summary == canonical.summary
    count = len(events)
    h.journal.confirm_checkpoint(saved)
    assert len(h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)) == count


@pytest.mark.asyncio
async def test_rejection_references_proposal_and_preserves_verdict_and_retry(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    h = harness(tmp_path / "contracts.db")
    guard = SimpleNamespace(review=AsyncMock(return_value=SimpleNamespace(allowed=False, code="guard_rejected", score=0.1)))
    await transfer(h, "curator", guard=guard)
    events = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)
    proposal, rejected = [JournalReader(h.store).transfer(event) for event in events]
    assert rejected.phase == "rejected" and rejected.summary.reason_code == "guard_rejected"
    assert rejected.summary.candidate_summary == proposal.summary.candidate_summary
    assert "summary" not in events[1].payload
    assert events[1].payload["summary_ref"]["target_id"] == events[0].event_id
    assert rejected.retry_allowed is False and rejected.checkpoint_ref is None
    retriable = h.writer.transfer(h.identity, "rejection:retry-observation",
        rejected.model_copy(update={"retry_allowed": True}))
    assert JournalReader(h.store).transfer(retriable).retry_allowed is True
    assert "summary" not in retriable.payload
    with pytest.raises(ValidationError):
        TransferJournalReference.model_validate({key: value for key, value in events[1].payload.items() if key != "reason_code"})


@pytest.mark.asyncio
async def test_migrated_proposal_references_original_summary_with_lineage_check(tmp_path):
    h = harness(tmp_path / "contracts.db")
    await transfer(h, "curator")
    checkpoint(h)
    proposal = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)[0]
    rollover = h.service.rollover(h.session.session_id, HandoffDraft(goal="音乐", current_stage="探索"),
                                 RolloverReason.EXPLICIT_CHECKPOINT)
    identity = h.identity.model_copy(update={"session_id": rollover.new_session.session_id})
    canonical = JournalReader(h.store).transfer(proposal)
    alias = h.writer.append_public_event(identity, canonical.transfer_id + ":proposed", HistoryEventType.AGENT_TRANSFER,
        TransferProposalReference(transfer_id=canonical.transfer_id,
            summary_ref=HistoryPointer(kind="event", target_id=proposal.event_id)))
    assert "summary" not in alias.payload and alias.payload["schema_version"] == 3
    assert JournalReader(h.store).transfer(alias).summary == canonical.summary
    assert h.writer.append_public_event(identity, canonical.transfer_id + ":proposed", HistoryEventType.AGENT_TRANSFER,
        alias.payload).event_id == alias.event_id
    with pytest.raises(ValueError, match="lineage"):
        h.writer.append_public_event(identity, "bad-lineage", HistoryEventType.AGENT_TRANSFER,
            {**alias.payload, "summary_ref": {"kind": "event", "target_id": alias.event_id}})


@pytest.mark.asyncio
async def test_reference_receipt_rejects_wrong_scope_type_run_and_missing_target(tmp_path):
    h = harness(tmp_path / "contracts.db")
    other = harness(tmp_path / "contracts.db", suffix="other")
    await transfer(h, "curator")
    saved = checkpoint(h)
    events = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)
    text = h.writer.append_public_event(h.identity, "text", HistoryEventType.USER_MESSAGE, PublicText(text="公开"))
    foreign = other.writer.append_public_event(other.identity, "text", HistoryEventType.USER_MESSAGE, PublicText(text="公开"))
    for target in ("evt:missing", foreign.event_id, text.event_id, events[1].event_id):
        with pytest.raises(ValueError):
            h.writer.append_public_event(h.identity, "invalid-ref", HistoryEventType.AGENT_TRANSFER,
                {**events[1].payload, "summary_ref": {"kind": "event", "target_id": target}})
    with pytest.raises(ValueError, match="run identity"):
        h.writer.append_public_event(h.identity.model_copy(update={"run_id": "wrong-run"}), "invalid-run",
            HistoryEventType.AGENT_TRANSFER, events[1].payload)
    assert saved.run.run_id == h.identity.run_id
    assert len(h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)) == 2


@pytest.mark.parametrize("version", [1, 2])
def test_projection_index_contains_only_pointer_and_roundtrips_exact_view(tmp_path, version):
    h = harness(tmp_path / "contracts.db")
    draft_type = HandoffDraftV2 if version == 2 else HandoffDraft
    projection = h.service.build_handoff(h.session.session_id,
        draft_type(goal="音乐探索", summary="有界摘要", current_stage="待执行"))
    h.store.put_handoff(projection)
    raw = json.loads(h.store._one("SELECT body FROM handoffs WHERE handoff_id=?", (projection.handoff_id,))["body"])
    assert set(raw) == {"storage_schema", "handoff_id", "task_id", "session_id", "event_ref"}
    assert "summary" not in json.dumps(raw) and "goal" not in raw
    restored = h.store.get_handoff(projection.handoff_id)
    assert type(restored) is type(projection)
    assert restored.model_dump(mode="json") == projection.model_dump(mode="json")
    assert len(h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.SESSION_PROJECTION)) == 1
    # Old serialized projection bodies remain readable without any rewrite.
    assert resolve_projection(h.store, projection.model_dump(mode="json")) == projection
    with pytest.raises(ValueError, match="unresolvable"):
        resolve_projection(h.store, {**raw, "event_ref": {"kind": "event", "target_id": "evt:missing"}})


def test_explicit_pointer_drilldown_and_time_query_cross_pages(tmp_path):
    h = harness(tmp_path / "contracts.db")
    base = datetime(2026, 10, 5, tzinfo=UTC)
    for index in range(105):
        h.writer.now = lambda index=index: base + timedelta(seconds=index)
        last = h.writer.append_public_event(h.identity, f"text:{index}", HistoryEventType.USER_MESSAGE,
                                           PublicText(text=f"公开事件 {index}"))
    reader = JournalReader(h.store)
    pointer = EventPointer(task_id=h.session.task_id, event_id=last.event_id, offset=1)
    assert reader.resolve(pointer) == last
    assert [event.payload["text"] for event in reader.query(h.session.task_id,
        start=base + timedelta(seconds=102), end=base + timedelta(seconds=103))] == ["公开事件 102", "公开事件 103"]
    assert reader.query(h.session.task_id, after_seq=last.event_seq) == []
    with pytest.raises(ValueError, match="unresolvable"):
        reader.resolve(pointer.model_copy(update={"task_id": "foreign"}))
    with pytest.raises(ValidationError):
        EventPointer(task_id=h.session.task_id, event_id=last.event_id, offset=-1)
    with pytest.raises(ValueError, match="timezone"):
        reader.query(h.session.task_id, start=datetime(2026, 10, 5))
    with pytest.raises(ValueError, match="reversed"):
        reader.query(h.session.task_id, start=base + timedelta(seconds=1), end=base)


@pytest.mark.asyncio
async def test_progress_references_budget_without_changing_checkpoint_or_legacy(tmp_path):
    from app.agent.handoff.continuity import public_run
    h = harness(tmp_path / "contracts.db")
    saved = checkpoint(h)
    raw = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.RUN_PROGRESS)[0].payload
    assert raw["schema_version"] == raw["run"]["schema_version"] == 2
    assert "usage" not in raw["run"]
    assert RunProgressRecord.model_validate(raw).run.run_id == h.run.run_id
    legacy = RunProgressRecord(run=public_run(h.run), checkpoint_ref=saved.ref)
    assert RunProgressRecord.model_validate(legacy.model_dump(mode="json")).run.usage == public_run(h.run).usage
    assert saved.run.usage == h.run.usage
    receipt = PublicUsage(run_id=h.run.run_id, agent="general_chat", call_id="one", phase="settled",
                          tokens=7, input_tokens=5, output_tokens=2)
    first = h.writer.append_public_event(h.identity, "usage:one:settled", HistoryEventType.MODEL_USAGE, receipt)
    assert h.writer.append_public_event(h.identity, "usage:one:settled", HistoryEventType.MODEL_USAGE, receipt).event_id == first.event_id
    with pytest.raises(ValidationError):
        h.writer.append_public_event(h.identity, "bad", HistoryEventType.MODEL_USAGE,
                                     {**receipt.model_dump(), "input_tokens": 6})


def test_manifest_contracts_reject_missing_extra_and_inconsistent_fields():
    pack = {"tokens": 20, "budget": 100, "sources": {"history_memory": {
        "candidate_tokens": 15, "final_tokens": 10, "events": [], "dedup_dropped": []}}}
    assert PackManifest.model_validate(pack).tokens == 20
    for name in pack:
        with pytest.raises(ValidationError):
            PackManifest.model_validate({key: value for key, value in pack.items() if key != name})
    with pytest.raises(ValidationError):
        PackManifest.model_validate({**pack, "prompt": "extra"})
    call = {**pack, "call_id": "one", "trace_id": "run:one", "task_id": "run:one",
        "agent": "jev", "model": "jev-latest", "input_tokens": 8, "output_tokens": 2,
        "total_tokens": 10, "estimated": False, "usage_basis": "provider", "max_output_tokens": 5,
        "output_limit_exceeded": False, "status": "completed", "budget_events": []}
    assert CallManifest.model_validate(call).total_tokens == 10
    for updates in ({"total_tokens": 11}, {"output_limit_exceeded": True}, {"task_id": "long-term-task"}, {"prompt": "extra"}):
        with pytest.raises(ValidationError):
            CallManifest.model_validate({**call, **updates})


def test_outer_request_discards_stale_pack_but_nested_scope_restores_parent():
    from types import SimpleNamespace
    active_manifest.set({"stale": True})
    with request_scope(SimpleNamespace()):
        assert active_manifest.get() is None
        active_manifest.set({"parent": True})
        with request_scope(SimpleNamespace()):
            assert active_manifest.get() is None
            active_manifest.set({"child": True})
        assert active_manifest.get() == {"parent": True}
    assert active_manifest.get() is None


def test_new_contract_paths_cannot_call_sdk_or_write_around_history_writer():
    root = Path(__file__).resolve().parents[1] / "app/agent/foundation"
    for path in (root / "context_contracts.py", root / "context_pointers.py",
                 root / "long_horizon/journal_contracts.py", root / "long_horizon/migration.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                assert name.split(".")[-1] not in {"ainvoke", "invoke", "system_one", "append_event_once", "append_event"}
                assert "responses.create" not in name


@pytest.mark.asyncio
async def test_call_manifest_pointer_resolves_settlement_in_long_term_scope(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.agent.foundation.long_horizon.accounting import DurableAccounting, active_accounting
    from app.llm.runtime import RequestLedger, accounted_call
    h = harness(tmp_path / "contracts.db")
    durable = DurableAccounting(h.writer, h.identity, SimpleNamespace(check=lambda: None))
    token = active_accounting.set(durable)
    try:
        ledger = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=30, run_id=h.run.run_id)
        with request_scope(ledger):
            await accounted_call(AsyncMock(return_value=SimpleNamespace(
                usage={"input_tokens": 3, "output_tokens": 2}, output_text="公开回答")),
                {"input": "公开输入"}, output_tokens=10, model="deepseek-chat")
        manifest = CallManifest.model_validate(ledger.call_manifests[0])
        assert manifest.task_id == h.run.run_id
        assert manifest.usage_ref.task_id == h.session.task_id
        event = JournalReader(h.store).resolve(manifest.usage_ref)
        assert event.event_type is HistoryEventType.MODEL_USAGE and event.payload["phase"] == "settled"
        assert event.payload["call_id"] == manifest.call_id
        assert (event.payload["input_tokens"], event.payload["output_tokens"]) == (3, 2)
    finally:
        active_accounting.reset(token)


def test_archive_and_progress_writer_reject_scope_mismatch_and_extra_fields(tmp_path):
    from app.agent.handoff.continuity import public_run
    h = harness(tmp_path / "contracts.db")
    projection = h.service.build_handoff(h.session.session_id, HandoffDraftV2(goal="音乐", current_stage="探索"))
    body = {"projection": projection.model_dump(mode="json")}
    with pytest.raises(ValidationError):
        h.writer.append_public_event(h.identity, "bad-extra", HistoryEventType.SESSION_PROJECTION, {**body, "extra": "x"})
    with pytest.raises(ValueError, match="scope mismatch"):
        h.writer.append_public_event(h.identity, "bad-scope", HistoryEventType.SESSION_PROJECTION,
            {"projection": {**body["projection"], "task_id": "foreign"}})
    saved = checkpoint(h)
    progress = RunProgressRecord(run=public_run(h.run),
        checkpoint_ref=saved.ref.model_copy(update={"checkpoint_ns": "foreign"}))
    with pytest.raises(ValueError, match="scope mismatch"):
        h.writer.append_public_event(h.identity, "bad-progress", HistoryEventType.RUN_PROGRESS, progress)
    assert h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.SESSION_PROJECTION) == []


@pytest.mark.asyncio
async def test_legacy_receipt_and_progress_replays_keep_original_immutable_content(tmp_path):
    from app.agent.foundation.long_horizon.history_writer import digest
    from app.agent.handoff.continuity import public_run
    h = harness(tmp_path / "contracts.db")
    await transfer(h, "curator")
    saved = checkpoint(h)
    current = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)[1]
    legacy_body = JournalReader(h.store).transfer(current)
    key = "legacy:target"
    identity = "public:" + digest([h.session.task_id, h.session.session_id, key])[7:]
    # An old-version fixture, never an application write path.
    old = h.store.append_event_once(current.model_copy(update={"event_id": "evt:" + identity[7:],
        "event_key": identity, "event_seq": None, "payload": legacy_body.model_dump(mode="json", exclude_none=True)}))
    assert h.writer.transfer(h.identity, key, legacy_body) == old
    assert h.store.get_history_event(old.event_id).payload == old.payload
    legacy_progress = RunProgressRecord(run=public_run(h.run), checkpoint_ref=saved.ref)
    progress = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.RUN_PROGRESS)[0]
    key = "legacy:progress"
    identity = "public:" + digest([h.session.task_id, h.session.session_id, key])[7:]
    old_progress = h.store.append_event_once(progress.model_copy(update={"event_id": "evt:" + identity[7:],
        "event_key": identity, "event_seq": None, "payload": legacy_progress.model_dump(mode="json", exclude_none=True)}))
    assert h.writer.append_public_event(h.identity, key, HistoryEventType.RUN_PROGRESS, legacy_progress) == old_progress
    assert "usage" in h.store.get_history_event(old_progress.event_id).payload["run"]

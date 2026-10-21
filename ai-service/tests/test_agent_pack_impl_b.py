"""Committed current-hop sources, four edges, relay isolation and scope expiry."""

import ast
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs, AgentPackInvocation
from app.agent.foundation.context_contracts import PackManifest
from app.agent.foundation.context_sources import SourceRegistry, SourceRequest
from app.agent.foundation.hop_sources import (
    ReceivedEnvelopeSource, SessionViewSource, active_source_call, build_hop_pack,
    invocation_source_scope, prepare_hop_sources,
)
from app.agent.foundation.long_horizon.journal_contracts import JournalReader
from app.agent.foundation.long_horizon.models import HandoffDraft, HandoffDraftV2, HistoryEventType
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.handoff.continuity import transfer_identity
from app.agent.handoff.hop_context import active_hop, hop_source_scope
from app.agent.handoff.registry import AgentKey, CAPABILITY_OWNERS
from app.agent.handoff.state import record_task_result
from app.llm.runtime import RequestLedger, RuntimeLimitError, request_scope
from tests.context_loop_helpers import checkpoint, harness, transfer

ROOT = Path(__file__).resolve().parents[1]
ROUTES = (RouteKey.TOOL_CALLING, RouteKey.WORLDLINE, RouteKey.GENERAL_CHAT, RouteKey.TOOL_CALLING)


def state(h):
    return {"messages": h.messages, "transfer_envelope": getattr(h, "envelope", None)}


def invocation(h, *, inputs=None, **changes):
    task = h.run.pending_tasks[0]
    owner = h.run.current_agent.value
    relay = CAPABILITY_OWNERS[task.route_key] != h.run.current_agent
    phase, refs = {
        "general_chat": ("answer", ("general-chat-base/v1",)),
        "curator": ("tool_step", ("curator-tools-base/v1",)),
        "explorer": ("narrative", ("explorer-narrative/v2",)),
    }[owner]
    if relay:
        phase, refs = "relay", ("handoff-transfer/v1",)
    return AgentPackInvocation(**{**dict(owner_agent=owner, run_id=h.run.run_id, task=task, phase=phase,
        recipe_id=owner, recipe_version="v1", prompt_refs=refs, inputs=inputs or AgentPackInputs()), **changes})


def result(h):
    task = h.run.pending_tasks[0]
    return TaskResult(task_id=task.task_id, route_key=task.route_key, status=TaskStatus.SUCCESS,
        content="有出处的公开完成结果 [catalog:11]",
        data={"entities": {"songs": [{"position": 1, "song_id": 11, "name": "夜曲", "artists": ["歌手"],
                                   "album": "专辑", "cover_url": "https://catalog.invalid/cover", "duration": 100}],
            "artists": [{"position": 1, "id": 21, "name": "歌手"}],
            "albums": [{"position": 1, "id": 31, "name": "专辑"}],
            "playlists": [{"position": 1, "playlist_id": 41, "name": "歌单"}]}} if task.route_key == RouteKey.TOOL_CALLING else {})


async def edge_harness(path, edge, *, summary="公开参考摘要"):
    h = harness(path, routes=ROUTES)
    for target, finish in (("curator", True), ("explorer", True), ("curator", False), ("general_chat", True), ("curator", False)):
        source = h.run.current_agent.value
        await transfer(h, target, call_id=f"call:{h.run.handoff_count}", summary=summary)
        checkpoint(h)
        if (source, target) == edge and not (edge == ("general_chat", "curator") and h.run.handoff_count == 1):
            return h
        if finish:
            h.run = record_task_result(h.run, result(h))
            h.envelope = None
            checkpoint(h)
    raise AssertionError("test edge not reached")


@pytest.mark.asyncio
@pytest.mark.parametrize("edge", [("general_chat", "curator"), ("curator", "general_chat"), ("curator", "explorer")])
async def test_three_business_edges_select_safe_fields_and_audit_one_proposal(tmp_path, edge):
    h = await edge_harness(tmp_path / "edge.db", edge)
    before = h.store.query_task_history(h.session.task_id, limit=500)
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        output = build_hop_pack(invocation(h))
    pack = output.pack
    audit = pack.manifest["agent_context"]["envelope"]
    assert output.source_status == {"received_envelope": "available", "session_view": "available"}
    assert audit["transfer_id"] == transfer_identity(h.run.run_id, h.run.hops[-1].tool_call_id)
    proposal = JournalReader(h.store).resolve(SimpleNamespace(**audit["summary_ref"]))
    assert proposal.payload["phase"] == "proposed"
    chunks = [item for item in pack.items if item.source == "received_envelope"]
    fields = [item.metadata["field_path"] for item in chunks]
    assert "request.context_summary" in fields
    assert any(field.startswith("completed_results.") for field in fields)
    assert any(field.startswith("entities.songs.") for field in fields)
    assert audit["selected_fields"] == fields
    for item in chunks:
        assert not item.required and item.type == "business_state" and item.token_count <= 256
        assert item.metadata["proposal_ref"] == audit["summary_ref"] and item.references
        assert item.metadata["payload_digest"] == audit["payload_digest"]
        payload = json.loads(item.content)
        if "entity" in payload:
            assert not set(payload["entity"]) & {"playable", "queueable", "cover_url", "duration", "position"}
            if edge[1] == "general_chat":
                assert set(payload["entity"]) <= {"id", "type", "name", "source"}
    if edge[1] == "explorer":
        assert not any(field.startswith("entities.playlists") for field in fields)
    assert not any(field in {"original_input", "active_task", "pending_tasks", "reason"} for field in fields)
    assert h.store.query_task_history(h.session.task_id, limit=500) == before  # Source path has no writes.


@pytest.mark.asyncio
async def test_explorer_to_curator_relay_reads_only_minimal_path(tmp_path):
    h = await edge_harness(tmp_path / "relay.db", ("explorer", "curator"))
    forbidden = Mock(side_effect=AssertionError("relay read profile/RAG/tool data"))
    registry = SourceRegistry([ReceivedEnvelopeSource(), SessionViewSource(),
        SimpleNamespace(name="user_profile", collect=forbidden), SimpleNamespace(name="history_memory", collect=forbidden)])
    inputs = AgentPackInputs(business_state={"play": "untrusted"}, tool_results=[{"result": "untrusted"}],
        messages=[], rag_pack=SimpleNamespace(items=[{"context": "untrusted"}]),
        continuity_context={"user_profile": "untrusted", "entry_candidates": [], "handoff": {"goal": "untrusted"}})
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        output = build_hop_pack(invocation(h, inputs=inputs), registry=registry)
    envelope_items = [item for item in output.pack.items if item.source == "received_envelope"]
    assert len(envelope_items) == 1 and envelope_items[0].metadata["field_path"] == "route_path"
    assert json.loads(envelope_items[0].content)["next_agent"] == "general_chat"
    assert output.source_status == {"received_envelope": "available", "session_view": "empty"}
    assert {item.type for item in output.pack.items} == {"system", "user_query", "business_state"}
    assert "untrusted" not in "".join(str(message.content) for message in output.pack.messages)
    assert not forbidden.called


@pytest.mark.asyncio
async def test_explorer_to_curator_business_edge_excludes_irrelevant_results_and_playlist(tmp_path):
    h = harness(tmp_path / "explorer.db", routes=(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE, RouteKey.TOOL_CALLING))
    await transfer(h, "curator")
    checkpoint(h)
    h.run = record_task_result(h.run, result(h))
    h.envelope = None
    checkpoint(h)
    await transfer(h, "explorer", call_id="call:explorer")
    checkpoint(h)
    h.run = record_task_result(h.run, result(h))
    h.envelope = None
    checkpoint(h)
    await transfer(h, "curator", call_id="call:back")
    checkpoint(h)
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        output = build_hop_pack(invocation(h))
    fields = output.pack.manifest["agent_context"]["envelope"]["selected_fields"]
    assert "completed_results.task:1" in fields and "completed_results.task:0" not in fields
    assert "entities.songs.11" in fields and not any("playlists" in field for field in fields)


@pytest.mark.asyncio
async def test_first_hop_empty_is_legal_and_client_continuity_cannot_forge_snapshot(tmp_path):
    h = harness(tmp_path / "first.db", routes=(RouteKey.GENERAL_CHAT,))
    value = invocation(h, inputs=AgentPackInputs(continuity_context={"received_envelope": {"run_id": "forged"}}))
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        output = build_hop_pack(value)
    assert output.source_status == {"received_envelope": "empty", "session_view": "empty"}
    assert output.pack.manifest["agent_context"]["envelope"] is None
    assert "forged" not in json.dumps(output.pack.manifest)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["uncommitted", "missing", "summary", "owner", "run", "task"])
async def test_invalid_hop_hard_rejects_before_collect_or_model(tmp_path, damage):
    h = harness(tmp_path / "integrity.db")
    await transfer(h, "curator")
    if damage != "uncommitted":
        checkpoint(h)
    damaged = deepcopy(state(h))
    value = invocation(h)
    if damage == "missing":
        damaged["transfer_envelope"] = None
    elif damage == "summary":
        damaged["transfer_envelope"]["request"]["context_summary"] = "改写"
    elif damage == "owner":
        value = value.model_copy(update={"owner_agent": "explorer"})
    elif damage == "run":
        value = value.model_copy(update={"run_id": "wrong-run"})
    elif damage == "task":
        value = value.model_copy(update={"task": value.task.model_copy(update={"input_text": "越权"})})
    reader = Mock(side_effect=AssertionError("must fail before collect"))
    registry = SourceRegistry([SimpleNamespace(name="received_envelope", collect=reader)])
    before = h.store.query_task_history(h.session.task_id, limit=500)
    with pytest.raises(SourceIntegrityError):
        async with hop_source_scope(damaged, h.run, journal=h.journal, identity=h.identity):
            prepare_hop_sources(value, registry=registry)
    assert not reader.called and h.store.query_task_history(h.session.task_id, limit=500) == before


def test_registry_propagates_integrity_but_preserves_normal_unavailable():
    error = SimpleNamespace(name="received_envelope", collect=Mock(side_effect=SourceIntegrityError()))
    with pytest.raises(SourceIntegrityError):
        SourceRegistry([error]).collect(SourceRequest("query", {}))
    ordinary = SimpleNamespace(name="session_view", collect=Mock(side_effect=OSError("private diagnostics")))
    items, statuses = SourceRegistry([ordinary]).collect(SourceRequest("query", {}))
    assert items == [] and statuses == {"session_view": "unavailable:OSError"}
    assert "private" not in str(statuses)


@pytest.mark.asyncio
async def test_text_excerpt_bound_and_source_budget_use_original_dropped_items(tmp_path):
    h = await edge_harness(tmp_path / "quota.db", ("curator", "general_chat"), summary="有界公开文字" * 300)
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        prepared = prepare_hop_sources(invocation(h))
        rows = [item for item in prepared.invocation.inputs.registered_items if item.source == "received_envelope"]
        summary = next(item for item in rows if item.metadata["field_path"] == "request.context_summary")
        assert summary.token_count <= 256 and summary.metadata["truncated"]
        assert json.loads(summary.content)["truncated"] is True
        builder = AgentPackBuilder()
        from app.agent.foundation.context_engineering import POLICIES
        policy = POLICIES[h.run.pending_tasks[0].route_key].model_copy(update={"source_budgets": {"received_envelope": 1}})
        pack = builder.build(prepared.invocation, policy=policy, intent={"routes": [h.run.pending_tasks[0].route_key.value]})
    assert all(item.id in pack.dropped_items for item in rows)
    events = pack.manifest["sources"]["received_envelope"]["events"]
    assert {event["item_id"] for event in events if event["stage"] == "dropped"} == {item.id for item in rows}
    assert all(event["reason"] == "received_envelope_budget_exceeded" for event in events if event["stage"] == "dropped")
    assert any(event["stage"] == "compressed" and event["reason"] == "received_envelope_excerpt_truncated" for event in events)


@pytest.mark.asyncio
async def test_session_view_has_committed_progress_refs_limits_and_no_duplicate_results(tmp_path):
    h = await edge_harness(tmp_path / "progress.db", ("curator", "general_chat"))
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        prepared = prepare_hop_sources(invocation(h))
        rows = [item for item in prepared.invocation.inputs.registered_items if item.source == "session_view"]
        assert 0 < len(rows) <= 8 and all(item.references and item.token_count <= 256 for item in rows)
        assert not any(item.metadata["field_path"] == "completed_results" for item in rows)
        pack = build_hop_pack(invocation(h)).pack
        assert pack.manifest["sources"]["session_view"]["final_tokens"] <= 600
        session = pack.manifest["agent_context"]["session"]
        assert session["source_event_cursor"] > 0 and session["checkpoint_ref"]["session_id"] == h.session.session_id
        assert session["projection_version"] is None and session["state_revision"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("draft_type", [HandoffDraft, HandoffDraftV2])
async def test_required_inherited_projection_is_single_provider_and_old_views_stay_readable(tmp_path, draft_type):
    h = await edge_harness(tmp_path / "overlap.db", ("curator", "general_chat"))
    projection = h.service.build_handoff(h.session.session_id, draft_type(goal="保留会话目标", current_stage="执行中",
        completed_steps=["公开步骤"], pending_items=["task:2"], next_actions=["继续"])).model_dump(mode="json")
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        output = build_hop_pack(invocation(h, inputs=AgentPackInputs(handoff_projection=projection)))
    inherited = [item for item in output.pack.items if item.type == "handoff_projection"]
    assert len(inherited) == 1 and inherited[0].required
    assert not any(item.source == "session_view" and item.metadata["field_path"] in {"pending_tasks", "completed_results"}
                   for item in output.pack.items)
    assert PackManifest.model_validate(output.pack.manifest).agent_context is not None


@pytest.mark.asyncio
async def test_snapshot_is_frozen_and_child_context_cannot_read_after_hop_end(tmp_path):
    h = await edge_harness(tmp_path / "lease.db", ("curator", "explorer"))
    release = asyncio.Event()
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity) as snapshot:
        original = snapshot.envelope_json
        h.envelope["request"]["context_summary"] = "原状态后来改动"
        assert snapshot.envelope_json == original
        assert snapshot.envelope().request.context_summary != "原状态后来改动"
        async def late_child():
            await release.wait()
            with pytest.raises(SourceIntegrityError):
                prepare_hop_sources(invocation(h))
        child = asyncio.create_task(late_child())
    assert active_hop.get() is None and active_source_call.get() is None
    release.set()
    await child


@pytest.mark.asyncio
async def test_no_journal_path_is_preserved_but_formal_envelope_source_requires_proof(tmp_path):
    h = harness(tmp_path / "compat.db")
    await transfer(h, "curator")
    async with hop_source_scope(state(h), h.run) as snapshot:
        assert snapshot.envelope_audit is None
        with pytest.raises(SourceIntegrityError):
            build_hop_pack(invocation(h))
    assert active_hop.get() is None


@pytest.mark.asyncio
async def test_budget_stop_cannot_hide_a_tampered_snapshot(tmp_path):
    h = await edge_harness(tmp_path / "budget.db", ("curator", "explorer"))
    book = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=30, run_id=h.run.run_id)
    book.source_stops.add(h.run.current_agent.value)
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity) as snapshot:
        with request_scope(book):
            output = build_hop_pack(invocation(h))
            assert output.source_status == {"received_envelope": "budget_stopped", "session_view": "budget_stopped"}
            assert not any(item.source in output.source_status for item in output.pack.items)
        damaged = replace(snapshot, envelope_digest="sha256:" + "0" * 64)
        token = active_hop.set(damaged)
        try:
            with request_scope(book), pytest.raises(SourceIntegrityError):
                prepare_hop_sources(invocation(h))
        finally:
            active_hop.reset(token)


@pytest.mark.asyncio
async def test_spent_l2_stops_reads_before_collect(tmp_path):
    h = await edge_harness(tmp_path / "spent.db", ("curator", "explorer"))
    book = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=30, run_id=h.run.run_id)
    book.usage[h.run.current_agent.value]["tokens"] = book.agent_limit
    collect = Mock(side_effect=AssertionError("fetch after exhausted budget"))
    registry = SourceRegistry([SimpleNamespace(name="session_view", collect=collect)])
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        with request_scope(book), pytest.raises(RuntimeLimitError):
            prepare_hop_sources(invocation(h), registry=registry)
    assert not collect.called


def test_new_sources_cannot_call_models_tools_or_write_journal():
    tree = ast.parse((ROOT / "app/agent/foundation/hop_sources.py").read_text(encoding="utf-8"))
    banned = {"invoke", "ainvoke", "generate", "append_event", "append_public_event", "transfer", "execute_transfer"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert ast.unparse(node.func).split(".")[-1] not in banned


@pytest.mark.asyncio
async def test_reconciled_receipt_is_accepted_and_uses_original_proposal(tmp_path):
    h = harness(tmp_path / "reconciled.db", routes=(RouteKey.TOOL_CALLING,))
    await transfer(h, "curator")
    from app.agent.checkpointing import create_memory_checkpointer
    h.saver = create_memory_checkpointer()
    saved = checkpoint(h)  # Saver read exists, no committed receipt yet.
    h.journal.confirm_checkpoint(saved, reconcile=True)
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        output = build_hop_pack(invocation(h))
    ref = output.pack.manifest["agent_context"]["envelope"]["commit_ref"]
    assert h.store.get_history_event(ref["event_id"]).payload["phase"] == "reconciled"


@pytest.mark.asyncio
async def test_session_query_outage_is_unavailable_without_losing_envelope(tmp_path, monkeypatch):
    h = await edge_harness(tmp_path / "outage.db", ("curator", "general_chat"))
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        monkeypatch.setattr(h.store, "query_task_history", Mock(side_effect=OSError("private error")))
        output = build_hop_pack(invocation(h))
    assert output.source_status == {"received_envelope": "available", "session_view": "unavailable:OSError"}
    assert any(item.source == "received_envelope" for item in output.pack.items)
    assert "private error" not in str(output.source_status)


@pytest.mark.asyncio
async def test_invalid_session_record_hard_rejects_through_registry(tmp_path, monkeypatch):
    h = await edge_harness(tmp_path / "invalid-session.db", ("curator", "general_chat"))
    rows = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.RUN_PROGRESS, descending=True)
    row = rows[0]
    payload = deepcopy(row.payload)
    payload["transfer_refs"] = []
    damaged = row.model_copy(update={"payload": payload})
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        monkeypatch.setattr(h.store, "query_task_history", Mock(return_value=[damaged]))
        with pytest.raises(SourceIntegrityError):
            prepare_hop_sources(invocation(h))


@pytest.mark.asyncio
async def test_previous_hop_candidates_cannot_be_reinjected(tmp_path):
    h = await edge_harness(tmp_path / "prior.db", ("curator", "explorer"))
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        prepared = prepare_hop_sources(invocation(h))
        with pytest.raises(SourceIntegrityError):
            prepare_hop_sources(invocation(h, inputs=prepared.invocation.inputs))


@pytest.mark.asyncio
async def test_evidence_reference_collection_has_no_automatic_drilldown(tmp_path, monkeypatch):
    h = await edge_harness(tmp_path / "references.db", ("curator", "explorer"))
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity) as snapshot:
        original = snapshot.envelope()
        from app.agent.foundation.long_horizon.models import HistoryPointer
        # Exercise the reference projector after the separately tested integrity
        # boundary; mock does not make this fixture into a committed payload.
        projected = original.model_copy(update={"evidence_refs": (HistoryPointer(kind="event", target_id="evidence:one"),)})
        monkeypatch.setattr(type(snapshot), "envelope", lambda self: projected)
        getter = h.store.get_history_event
        def guarded_get(event_id):
            assert event_id != "evidence:one", "must not resolve evidence body"
            return getter(event_id)
        monkeypatch.setattr(h.store, "get_history_event", guarded_get)
        with invocation_source_scope(invocation(h)):
            items = ReceivedEnvelopeSource().collect(SourceRequest("query", {}), limit=24)
    reference = next(item for item in items if item.metadata["field_path"] == "evidence_refs.0")
    assert "event:evidence:one" in reference.references
    assert json.loads(reference.content)["evidence_ref"] == {"kind": "event", "target_id": "evidence:one"}


@pytest.mark.asyncio
async def test_graph_consumption_hook_is_after_commit_and_hop_state_clears(tmp_path, monkeypatch):
    from app.agent.handoff import graph as graph_module
    from tests.test_handoff_runtime import Router, input_state, player_context
    from tests.test_handoff_network import transfers
    h = harness(tmp_path / "graph.db")
    seen, snapshots = [], []

    async def consume(_state, run, _runtime, **_kwargs):
        snapshot = active_hop.get()
        assert snapshot is not None and snapshot.committed
        # B verifies the consumption boundary using a controlled business
        # producer. Production per-agent LLM wiring stays in C/D.
        local = SimpleNamespace(run=run)
        output = build_hop_pack(invocation(local))
        seen.append(output)
        snapshots.append(snapshot)
        task = run.pending_tasks[0]
        return TaskResult(task_id=task.task_id, route_key=task.route_key, status=TaskStatus.SUCCESS, content="已完成")

    monkeypatch.setattr(graph_module, "execute_owned_task", consume)
    graph = graph_module.build_handoff_graph(intent_router=Router(RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT),
        transfer_model=transfers((AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.GENERAL_CHAT)),
        transfer_journal=h.journal).compile(checkpointer=h.saver)
    values = input_state("暂停一下；聊聊音乐")
    values["continuity_context"] = {"conversation_id": h.identity.conversation_id, "task_id": h.session.task_id,
        "session_id": h.session.session_id, "request_id": h.identity.request_id, "run_id": h.run.run_id}
    updates = [chunk async for chunk in graph.astream(values, h.config, context=player_context(), stream_mode="updates")]
    assert len(seen) == 2 and all(result.pack.manifest["agent_context"]["envelope"] for result in seen)
    assert all(not snapshot.lease.active for snapshot in snapshots) and active_hop.get() is None
    task_updates = [update["transfer_envelope"] for chunk in updates for update in chunk.values()
                    if isinstance(update, dict) and update.get("task_results")]
    assert task_updates == [None, None]
    final = await graph.aget_state(h.config)
    assert final.values["transfer_envelope"] is None
    assert "CurrentHopSnapshot" not in str(final.values) and "agent_pack_current_hop" not in str(final.values)


@pytest.mark.asyncio
async def test_original_guard_retry_can_retire_consumed_envelope_only_with_receipt(tmp_path):
    from unittest.mock import AsyncMock
    h = await edge_harness(tmp_path / "retry.db", ("explorer", "curator"))
    book = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=30, run_id=h.run.run_id)
    guard = SimpleNamespace(review=AsyncMock(return_value=SimpleNamespace(allowed=False, code="guard_rejected", score=0.1)))
    with request_scope(book):
        command = await transfer(h, "general_chat", call_id="call:guard-retry", guard=guard)
    assert command.update["guard_retry"] and h.envelope is None
    checkpoint(h)
    raw = {**state(h), "guard_retry": True}
    async with hop_source_scope(raw, h.run, journal=h.journal, identity=h.identity):
        output = build_hop_pack(invocation(h))
    assert output.source_status == {"received_envelope": "empty", "session_view": "empty"}
    assert output.pack.manifest["agent_context"]["envelope"] is None
    raw["messages"] = raw["messages"][:-2]  # A flag alone is not consumption evidence.
    with pytest.raises(SourceIntegrityError):
        async with hop_source_scope(raw, h.run, journal=h.journal, identity=h.identity):
            pass

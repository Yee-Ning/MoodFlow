"""Production choose_transfer uses a minimal Pack at the real model boundary."""

from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.agent.foundation.agent_pack import AgentPackBuilder
from app.agent.foundation.context_contracts import CallManifest
from app.agent.foundation.context_sources import HistoryMemorySource, UserProfileSource
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.rag_source import RagSource
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.handoff import agents
from app.agent.handoff.hop_context import active_hop, hop_source_scope
from app.agent.handoff.prompts import HANDOFF_TRANSFER_PROMPT, TRANSFER_RETRY_PROMPT
from app.agent.handoff.registry import AgentKey, next_agent_for_task
from app.agent.handoff.state import new_run, record_task_result
from app.llm.runtime import RequestLedger, RuntimeLimitError, request_scope
from tests.context_loop_helpers import checkpoint, harness, transfer
from tests.test_agent_pack_impl_b import state
from tests.test_handoff_runtime import Router, ToolModel, call


class UnreadableState(dict):
    def get(self, *args, **kwargs):
        raise AssertionError("relay read business/continuity state")


@pytest.fixture
def pack_calls(monkeypatch):
    result = SimpleNamespace(pack=None)
    original_hop = agents.build_hop_pack
    builder = AgentPackBuilder()
    original_build = builder.build

    def hop(invocation, **kwargs):
        output = original_hop(invocation, **kwargs)
        result.pack = output.pack
        result.status = output.source_status
        return output

    def build(invocation, **kwargs):
        result.pack = original_build(invocation, **kwargs)
        return result.pack

    result.hop = Mock(side_effect=hop)
    result.build = Mock(side_effect=build)
    builder.build = result.build
    monkeypatch.setattr(agents, "build_hop_pack", result.hop)
    monkeypatch.setattr(agents, "AgentPackBuilder", lambda: builder)
    return result


async def current_run(path, scenario):
    h = harness(path, routes=(RouteKey.WORLDLINE, RouteKey.GENERAL_CHAT))
    text = "探索音乐世界线；陪我聊聊"
    plan = await Router(RouteKey.WORLDLINE, RouteKey.GENERAL_CHAT).route(text, None)
    h.run = new_run(h.run.run_id, text, plan, started_at=h.run.started_at, entry_agent=AgentKey.GENERAL_CHAT)
    if scenario != "first":
        await transfer(h, "curator", summary="ENVELOPE_SUMMARY_CANARY")
        checkpoint(h)
    if scenario in {"after_result", "return"}:
        await transfer(h, "explorer", call_id="call:explorer", summary="ENVELOPE_SUMMARY_CANARY")
        checkpoint(h)
        task = h.run.pending_tasks[0]
        h.run = record_task_result(h.run, TaskResult(task_id=task.task_id, route_key=task.route_key,
            status=TaskStatus.SUCCESS, content="BUSINESS_RESULT_CANARY"))
        h.envelope = None
        checkpoint(h)
    if scenario == "return":
        await transfer(h, "curator", call_id="call:return", summary="ENVELOPE_SUMMARY_CANARY")
        checkpoint(h)
    return h


def no_business_reads(monkeypatch, h):
    forbidden = Mock(side_effect=AssertionError("relay read profile/history/RAG/progress"))
    for source in (UserProfileSource, HistoryMemorySource, RagSource):
        monkeypatch.setattr(source, "collect", forbidden)
    monkeypatch.setattr(h.store, "query_task_history", forbidden)
    return forbidden


def book_and_model(h):
    target = next_agent_for_task(h.run.current_agent, h.run.pending_tasks[0].route_key)
    message = call(f"transfer_to_{target.value}_agent")
    ledger = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=30, run_id=h.run.run_id)
    return ledger, ToolModel(responses=[message]), message


def assert_call(h, pack, ledger, model, *, feedback, formal):
    assert len(ledger.call_manifests) == len(model.seen_messages) == 1
    manifest = CallManifest.model_validate(ledger.call_manifests[0])
    audit = manifest.agent_context
    assert audit is not None
    assert (audit.recipe_id, audit.recipe_version, audit.phase) == ("handoff", "v1", "relay")
    task = h.run.pending_tasks[0]
    assert (audit.owner_agent, audit.route_key) == (h.run.current_agent.value, task.route_key.value)
    assert manifest.agent == h.run.current_agent.value and manifest.task_id == h.run.run_id
    expected = HANDOFF_TRANSFER_PROMPT + (TRANSFER_RETRY_PROMPT if feedback else "")
    assert next(item.content for item in pack.items if item.type == "system") == expected
    path = {"task_id": task.task_id, "route_key": task.route_key.value,
            "current_agent": h.run.current_agent.value,
            "next_agent": next_agent_for_task(h.run.current_agent, task.route_key).value}
    path_frame = "" if formal and getattr(h, "envelope", None) is None else (
        '\n<business_state>' + json.dumps(path, ensure_ascii=False, separators=(",", ":")) + '</business_state>')
    framing = '\n<current_intent>{"routes":["' + task.route_key.value + '"]}</current_intent>'
    assert model.seen_messages[0][0].content == expected + path_frame + framing
    assert [prompt.content_digest for prompt in audit.prompts] == [
        "sha256:" + sha256(text.encode("utf-8")).hexdigest()
        for text in ([HANDOFF_TRANSFER_PROMPT, TRANSFER_RETRY_PROMPT] if feedback else [HANDOFF_TRANSFER_PROMPT])]
    assert {item.type for item in pack.items} <= {"system", "user_query", "business_state"}
    assert next(item.content for item in pack.items if item.type == "user_query") == task.input_text
    assert {row.item_id for row in audit.source_selection} == {item.id for item in pack.items}
    visible = json.dumps([message.content for message in model.seen_messages[0]], ensure_ascii=False)
    assert h.run.original_input not in visible
    assert all(canary not in visible for canary in (
        "ORIGINAL_INPUT_CANARY", "BUSINESS_RESULT_CANARY", "ENVELOPE_SUMMARY_CANARY"))
    paths = [item for item in pack.items if item.type == "business_state"]
    if formal and getattr(h, "envelope", None) is not None:
        assert len(paths) == 1 and paths[0].source == "received_envelope"
        assert audit.envelope.selected_fields == ("route_path",)
        assert manifest.sources["received_envelope"].final_tokens <= 256
    elif formal:
        assert paths == [] and audit.envelope is None
    else:
        assert len(paths) == 1 and paths[0].source == "business_state"
        assert audit.envelope is audit.session is None
    for item in paths:
        assert json.loads(item.content) == path
    assert all(name.startswith("transfer_to_") for name in model.bound_names[0])
    assert ledger.usage[h.run.current_agent.value]["tokens"] == 100 and ledger.reserved == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["first", "inbound", "after_result", "return"])
@pytest.mark.parametrize("feedback", [False, True])
async def test_choose_transfer_uses_b_relay_pack_without_business_reads(tmp_path, monkeypatch, pack_calls, scenario, feedback):
    h = await current_run(tmp_path / "formal.db", scenario)
    ledger, model, expected = book_and_model(h)
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        forbidden = no_business_reads(monkeypatch, h)
        with request_scope(ledger):
            response = await agents.choose_transfer(UnreadableState(), h.run, model=model, feedback=feedback)
    assert response.tool_calls == expected.tool_calls and response.content == expected.content
    assert pack_calls.hop.call_count == pack_calls.build.call_count == 1 and not forbidden.called
    assert_call(h, pack_calls.pack, ledger, model, feedback=feedback, formal=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("with_hop", [False, True])
@pytest.mark.parametrize("feedback", [False, True])
async def test_without_journal_uses_a_runtime_path_and_no_commit_observation(tmp_path, monkeypatch, pack_calls, with_hop, feedback):
    h = await current_run(tmp_path / "compat.db", "inbound")
    ledger, model, expected = book_and_model(h)
    forbidden = no_business_reads(monkeypatch, h)
    with request_scope(ledger):
        if with_hop:
            async with hop_source_scope(state(h), h.run):
                response = await agents.choose_transfer(UnreadableState(), h.run, model=model, feedback=feedback)
        else:
            response = await agents.choose_transfer(UnreadableState(), h.run, model=model, feedback=feedback)
    assert response.tool_calls == expected.tool_calls and response.content == expected.content
    assert pack_calls.build.call_count == 1 and not pack_calls.hop.called and not forbidden.called
    assert_call(h, pack_calls.pack, ledger, model, feedback=feedback, formal=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["run", "owner", "task", "digest", "lease", "uncommitted_proof"])
async def test_invalid_scope_cannot_fall_back_or_call_model(tmp_path, monkeypatch, pack_calls, damage):
    h = await current_run(tmp_path / "invalid.db", "inbound")
    ledger, model, _ = book_and_model(h)
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity) as snapshot:
        changes = {
            "run": {"run_id": "wrong-run"}, "owner": {"owner_agent": "explorer"},
            "task": {"task_json": snapshot.task_json.replace("探索音乐", "篡改音乐")},
            "digest": {"envelope_digest": "sha256:" + "0" * 64},
            "uncommitted_proof": {"committed": False},
        }
        if damage == "lease":
            snapshot.lease.active = False
        token = active_hop.set(replace(snapshot, **changes.get(damage, {})))
        try:
            with request_scope(ledger), pytest.raises(SourceIntegrityError):
                await agents.choose_transfer(UnreadableState(), h.run, model=model)
        finally:
            active_hop.reset(token)
    assert model.seen_messages == [] and not pack_calls.build.called
    assert ledger.call_manifests == [] and ledger.reserved == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("formal", [False, True])
async def test_exhausted_owner_stops_before_source_or_model(tmp_path, monkeypatch, pack_calls, formal):
    h = await current_run(tmp_path / "spent.db", "inbound")
    ledger, model, _ = book_and_model(h)
    ledger.usage[h.run.current_agent.value]["tokens"] = ledger.agent_limit
    forbidden = no_business_reads(monkeypatch, h)
    from app.agent.foundation.hop_sources import ReceivedEnvelopeSource
    monkeypatch.setattr(ReceivedEnvelopeSource, "collect", forbidden)
    async with hop_source_scope(state(h), h.run, **({"journal": h.journal, "identity": h.identity} if formal else {})):
        with request_scope(ledger), pytest.raises(RuntimeLimitError, match="token_limit"):
            await agents.choose_transfer(UnreadableState(), h.run, model=model)
    assert not forbidden.called and model.seen_messages == [] and ledger.call_manifests == []


@pytest.mark.asyncio
async def test_source_stop_cannot_import_ambient_business_continuity(tmp_path, pack_calls):
    from app.agent.foundation.context_engineering import POLICIES
    from app.agent.foundation.long_horizon.coordinator import active_call

    h = await current_run(tmp_path / "degraded.db", "inbound")
    ledger, model, _ = book_and_model(h)
    remaining = POLICIES[h.run.pending_tasks[0].route_key].reserved_output_tokens + 500
    ledger.usage[h.run.current_agent.value]["tokens"] = ledger.agent_limit - remaining
    continuity = SimpleNamespace(
        revalidation={"reason": "AMBIENT_REVALIDATION_CANARY"},
        lease=SimpleNamespace(check=Mock()),
        coordinator=SimpleNamespace(writer=SimpleNamespace(append_public_event=Mock())),
        accounting=SimpleNamespace(identity=h.identity),
    )
    token = active_call.set(continuity)
    try:
        async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
            with request_scope(ledger):
                try:
                    await agents.choose_transfer({}, h.run, model=model)
                except RuntimeLimitError:
                    # Final provider framing may refuse a nearly exhausted owner.
                    assert ledger.blocked == "token_limit"
    finally:
        active_call.reset(token)
    assert any(row["stage"] == "stop_sources" for row in ledger.budget_trace)
    assert pack_calls.pack is not None
    continuity.lease.check.assert_called_once()
    continuity.coordinator.writer.append_public_event.assert_called_once()
    assert pack_calls.pack.manifest["sources"].get("business_state", {}).get("candidate_tokens", 0) == 0
    assert "AMBIENT_REVALIDATION_CANARY" not in str(pack_calls.pack.messages)


@pytest.mark.asyncio
async def test_invalid_model_message_keeps_original_error_contract(tmp_path, pack_calls):
    h = await current_run(tmp_path / "bad-message.db", "first")

    class InvalidModel:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages, **kwargs):
            return "invalid response"

    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        with pytest.raises(ValueError, match="transfer model returned an invalid message"):
            await agents.choose_transfer({}, h.run, model=InvalidModel())
    assert pack_calls.hop.call_count == 1

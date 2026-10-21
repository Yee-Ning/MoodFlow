"""Adapter, immutable prompt anchors and strict observation compatibility."""

import ast
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs, AgentPackInvocation
from app.agent.foundation.context_contracts import (
    AgentContextManifest,
    BudgetSnapshot,
    CallManifest,
    CheckpointManifest,
    EnvelopeManifest,
    PackManifest,
    PromptManifest,
    SessionManifest,
    SourceSelectionManifest,
)
from app.agent.foundation.context_engineering import POLICIES, ContextCollector, ContextSelector, build_context
from app.agent.foundation.context_sources import SourceRegistry, source_item
from app.agent.foundation.long_horizon.models import CheckpointRef
from app.agent.foundation.prompt_bindings import PROMPT_BINDINGS, bind_prompts
from app.agent.foundation.routing import RouteKey, RouteTask
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import AgentUsage, RunLimits
from app.llm.runtime import RequestLedger, RuntimeLimitError, accounted_call, active_manifest, agent_scope, request_scope
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter

ROOT = Path(__file__).resolve().parents[1]

# Literal UTF-8 hashes, independent of the runtime registry. Narrative v2 includes
# the source-citation and abstention rules added after the d1ff818c baseline.
BASELINE_DIGESTS = {
    "general-chat-base/v1": "4626a71fee1d1d7adaa979cd27837dbed5c93da9b4e5ab2a28da5b11dc6e38e9",
    "curator-tools-base/v1": "25fb024bf98caa16700f61f37ca03aaaf08e351e83c2e2cddcd57bef81fdac98",
    "search-only/v1": "84f6fe5f58f712e2a94e629f64c5bbc7c1dc566e7251ea091b021f9c31d2a4a2",
    "song-playback/v1": "39b683662e3195ed4a85e7f31e4e5c673419c175d4a3dc6ef1214c5191ae721c",
    "curator-current-song/v1": "ce45e29ec6e7cefd2326f22843d51389f358ed849bd26875aebf35cc1db167c4",
    "query-analysis/v1": "89c717bd39166cebec481128cd4c27fad5fc6a08394729fcbb612e6bbb7b01f0",
    "explorer-narrative/v2": "05099b6cd6b9873067c169817a5c089ece5d1558dac351e90a077f443d7c599d",
    "explorer-branches/v1": "c2e3594834e8a9215eff64b6e81e6c246792bb708c73146583f3463e08f34e20",
    "handoff-transfer/v1": "c439d5c31d52b568a5feab831cba235cabbc0713a9b8218aa5993b0df0462cd4",
    "transfer-retry/v1": "e9ad532d821af713caac997680bc729267396c0d26bb14ef05e68b855cc675cb",
    "general-chat-handoff-read/v1": "7c7333a129204eb84836849b6e6d4faa795c64d083329271b525bdc4e0dae750",
}


def invocation(**changes):
    data = dict(owner_agent="general_chat", run_id="run:adapter", phase="answer",
                recipe_id="general-chat", recipe_version="v1", prompt_refs=("general-chat-base/v1",),
                task=RouteTask(task_id="route:chat", route_key=RouteKey.GENERAL_CHAT, input_text="你好"))
    return AgentPackInvocation(**{**data, **changes})


def ledger(*, l2=16000, l3=32000):
    return RequestLedger(tuple(AgentUsage(agent=agent) for agent in AgentKey), AgentKey.GENERAL_CHAT,
                         RunLimits(agent_token_budget=l2, run_token_budget=l3), deadline=30, run_id="run:adapter")


@pytest.mark.parametrize("reference,digest", BASELINE_DIGESTS.items())
def test_prompt_utf8_digests_pin_the_baseline_text(reference, digest):
    binding = PROMPT_BINDINGS[reference]
    assert sha256(binding.text.encode("utf-8")).hexdigest() == digest
    assert binding.manifest.content_digest == "sha256:" + digest
    assert set(binding.manifest.model_dump()) == {"prompt_id", "version", "variant", "content_digest"}
    with pytest.raises(TypeError):
        PROMPT_BINDINGS[reference] = binding


def test_preferences_prompt_has_its_own_immutable_version():
    binding = PROMPT_BINDINGS["general-chat-preferences/v1"]
    assert binding.manifest.version == "v1"
    assert binding.manifest.variant == "preferences"
    assert binding.manifest.content_digest == "sha256:d077ede03d1941f9c41c81cd4ee4b2d70d5045096c64a26b7dc289dd523b9bc8"


@pytest.mark.parametrize("owner,phase,refs", [
    ("general_chat", "answer", ("general-chat-base/v1", "general-chat-handoff-read/v1")),
    ("curator", "tool_step", ("curator-tools-base/v1", "search-only/v1")),
    ("curator", "tool_step", ("curator-tools-base/v1", "song-playback/v1")),
    ("explorer", "relay", ("handoff-transfer/v1", "transfer-retry/v1")),
])
def test_static_variants_preserve_existing_concatenation(owner, phase, refs):
    text, audit = bind_prompts(refs, owner=owner, phase=phase)
    base, variant = (PROMPT_BINDINGS[ref] for ref in refs)
    separator = "\n" if variant.prompt_id in {"search-only", "song-playback"} else ""
    assert text == base.text + separator + variant.text
    assert tuple(row.prompt_id for row in audit) == (base.prompt_id, variant.prompt_id)


@pytest.mark.parametrize("refs,owner,phase", [
    ((), "general_chat", "answer"),
    (("general-chat-base/v2",), "general_chat", "answer"),
    (("general-chat-base/v1",) * 2, "general_chat", "answer"),
    (("general-chat-handoff-read/v1",), "general_chat", "answer"),
    (("general-chat-base/v1",), "curator", "answer"),
    (("general-chat-base/v1",), "general_chat", "relay"),
    (("explorer-narrative/v2", "explorer-branches/v1"), "explorer", "narrative"),
    (("curator-tools-base/v1", "search-only/v1", "song-playback/v1"), "curator", "tool_step"),
])
def test_invalid_static_bindings_fail_before_collect(refs, owner, phase):
    with pytest.raises(ValueError):
        bind_prompts(refs, owner=owner, phase=phase)


def test_static_binding_uses_existing_pipeline_without_source_reads(monkeypatch):
    monkeypatch.setattr(SourceRegistry, "collect", Mock(side_effect=AssertionError("dynamic Source read")))
    collector = ContextCollector.collect
    selector = ContextSelector.build
    seen = []

    def collect(self, **sources):
        assert sources["system"] == ""
        items = collector(self, **sources)
        assert not any(item.type == "system" for item in items)
        seen.append("collect")
        return items

    def select(self, items, policy, **kwargs):
        systems = [item for item in items if item.type == "system"]
        assert len(systems) == 1 and systems[0].required and not systems[0].compressible
        seen.append("select")
        return selector(self, items, policy, **kwargs)

    monkeypatch.setattr(ContextCollector, "collect", collect)
    monkeypatch.setattr(ContextSelector, "build", select)
    monkeypatch.setenv("JEV_ENABLED", "true")
    pack = AgentPackBuilder().build(invocation())
    assert seen == ["collect", "select"]
    assert pack.messages[0].content == PROMPT_BINDINGS["general-chat-base/v1"].text
    assert not SourceRegistry.collect.called


def test_static_binding_source_code_cannot_fetch_dynamic_text_or_call_models():
    forbidden = {"open", "read_text", "read_bytes", "getenv", "load_context_policy", "get_settings",
                 "collect", "invoke", "ainvoke", "generate", "create", "SourceRegistry"}
    tree = ast.parse((ROOT / "app/agent/foundation/prompt_bindings.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            assert ast.unparse(node.func).split(".")[-1] not in forbidden
    adapter = ast.parse((ROOT / "app/agent/foundation/agent_pack.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1]
                   in {"invoke", "ainvoke", "generate", "reserve", "settle", "select_context"}
                   for node in ast.walk(adapter))


def test_system_prompt_counts_once_at_the_l1_boundary():
    value = invocation()
    counter = UnicodeHeuristicTokenCounter()
    text = PROMPT_BINDINGS[value.prompt_refs[0]].text
    tokens = counter.count(text) + counter.count(value.task.input_text)
    policy = POLICIES[value.task.route_key].model_copy(update={"total_input_budget": tokens + 64,
        "reserved_output_tokens": 32, "safety_margin": 32})
    pack = AgentPackBuilder(counter).build(value, policy=policy)
    assert pack.total_tokens == pack.budget == tokens
    assert pack.manifest["sources"]["system"]["candidate_tokens"] == counter.count(text)
    assert pack.manifest["sources"]["system"]["final_tokens"] == counter.count(text)
    assert pack.messages[0].content.count(text) == 1
    with pytest.raises(ValueError, match="required context exceeds"):
        AgentPackBuilder(counter).build(value, policy=policy.model_copy(update={"total_input_budget": tokens + 63}))


def test_adapter_preserves_existing_selection_and_legacy_mode():
    inputs = AgentPackInputs(messages=[HumanMessage(content="聊聊音乐"), AIMessage(content="你最近听什么？")],
                             business_state={"read_only": True})
    value = invocation(inputs=inputs)
    expected = build_context(value.task.route_key, query=value.task.input_text,
        system=PROMPT_BINDINGS[value.prompt_refs[0]].text, messages=inputs.messages, business_state=inputs.business_state)
    actual = AgentPackBuilder().build(value)
    assert PackManifest.model_validate(actual.manifest).legacy_projection() == expected.manifest
    assert actual.items == expected.items and actual.messages == expected.messages
    assert actual.trace == expected.trace
    legacy = AgentPackBuilder().build(value, include_agent_context=False)
    assert legacy == expected and "agent_context" not in legacy.manifest


def test_final_selection_audit_keeps_refs_versions_and_original_drop_statistics():
    first = source_item("profile:first", "user_profile", {"taste": "民谣"}, source="user_profile",
                        intents=["general_chat"], metadata={"version": 7}, references=["profile:synthetic:7"])
    duplicate = first.model_copy(update={"id": "profile:duplicate"})
    oversized = source_item("profile:large", "user_profile", {"taste": "喜爱安静音乐" * 1000},
                            source="user_profile", intents=["general_chat"], metadata={"version": 7})
    value = invocation(inputs=AgentPackInputs(registered_items=[first, duplicate, oversized]))
    before = value.model_dump()
    pack = AgentPackBuilder().build(value, intent={"routes": ["general_chat"]})
    assert value.model_dump() == before
    audit = AgentContextManifest.model_validate(pack.manifest["agent_context"])
    assert [row.item_id for row in audit.source_selection] == [item.id for item in pack.items]
    profile = next(row for row in audit.source_selection if row.item_id == first.id)
    assert profile.references == ("profile:synthetic:7",) and profile.profile_version == 7
    assert profile.final_tokens == next(item.token_count for item in pack.items if item.id == first.id)
    assert duplicate.id in pack.dropped_items and oversized.id in pack.dropped_items
    events = pack.manifest["sources"]["user_profile"]["events"]
    assert any(row["item_id"] == duplicate.id and row["stage"] == "deduplicated" for row in events)
    assert any(row["item_id"] == oversized.id and row["stage"] == "dropped" for row in events)
    assert "喜爱安静音乐" not in json.dumps(pack.manifest, ensure_ascii=False)


def test_native_tool_messages_keep_paired_protocol():
    call = AIMessage(content="查找", tool_calls=[{"name": "search_music", "args": {"keyword": "民谣"}, "id": "call:one"}])
    result = ToolMessage(content='{"songs":[{"id":"song:one","name":"民谣"}]}', tool_call_id="call:one", name="search_music")
    value = invocation(owner_agent="curator", phase="tool_step", prompt_refs=("curator-tools-base/v1",),
        task=RouteTask(task_id="route:tool", route_key=RouteKey.TOOL_CALLING, input_text="找民谣"),
        inputs=AgentPackInputs(messages=[call, result]))
    pack = AgentPackBuilder().build(value)
    assistant = next(message for message in pack.messages if isinstance(message, AIMessage))
    tool = next(message for message in pack.messages if isinstance(message, ToolMessage))
    assert assistant.tool_calls == call.tool_calls and tool.tool_call_id == "call:one"
    assert tool.content == result.content


@pytest.mark.parametrize("field", ["system", "query", "source_registry", "profile_store"])
def test_inputs_reject_dynamic_prompt_and_provider_overrides(field):
    with pytest.raises(ValidationError):
        AgentPackInputs.model_validate({field: "untrusted"})


def test_assembly_observes_existing_ledger_without_reserving_or_settling():
    book = ledger()
    book.usage["general_chat"]["tokens"] = 200
    book.usage["explorer"]["tokens"] = 300
    book.reserved = 50
    before = deepcopy(book.usage)
    with request_scope(book):
        pack = AgentPackBuilder().build(invocation())
        assert active_manifest.get() == pack.manifest
        snapshot = pack.manifest["agent_context"]["budget_snapshot"]
        assert snapshot == {"owner_remaining": 15750, "run_remaining": 31450,
                            "reserved_tokens": 50, "degradation_stages": []}
        assert book.usage == before and book.reserved == 50 and book.call_manifests == []
    assert active_manifest.get() is None


def test_assembly_reuses_the_existing_l2_degradation_order():
    book = ledger(l2=100, l3=32000)
    builder = AgentPackBuilder()
    builder.collector.collect = Mock(side_effect=AssertionError("collect after refusal"))
    with request_scope(book), pytest.raises(RuntimeLimitError):
        builder.build(invocation())
    assert [row["stage"] for row in book.budget_trace] == [
        "truncate_history", "summarize_history", "stop_sources", "honest_degradation"]
    assert book.source_stops == {"general_chat"} and book.reserved == 0


@pytest.mark.parametrize("change", [{"run_id": "run:other"}, {"owner_agent": "curator", "phase": "song_analysis",
                                                          "prompt_refs": ("curator-current-song/v1",)}])
def test_active_run_owner_mismatch_fails_before_collect(change):
    builder = AgentPackBuilder()
    builder.collector.collect = Mock(side_effect=AssertionError("wrong scope"))
    with request_scope(ledger()), pytest.raises(ValueError, match="active run and owner"):
        builder.build(invocation(**change))


def test_owner_scope_uses_curators_balance_and_rejects_wrong_policy():
    book = ledger()
    book.usage["curator"]["tokens"] = 500
    value = invocation(owner_agent="curator", phase="song_analysis", prompt_refs=("curator-current-song/v1",),
        task=RouteTask(task_id="route:song", route_key=RouteKey.CURRENT_SONG, input_text="介绍这首歌"))
    with request_scope(book), agent_scope("curator"):
        pack = AgentPackBuilder().build(value)
        assert pack.manifest["agent_context"]["budget_snapshot"]["owner_remaining"] == 15500
    with pytest.raises(ValueError, match="policy must match"):
        AgentPackBuilder().build(value, policy=POLICIES[RouteKey.GENERAL_CHAT])


def test_old_pack_and_phase2_call_documents_read_and_serialize_compatibly():
    legacy_call = json.loads((Path(__file__).with_name("fixtures") / "legacy_call_manifest.json")
                             .read_text(encoding="utf-8"))["manifest"]
    call = CallManifest.model_validate(legacy_call)
    assert call.agent_context is None and "agent_context" not in call.model_dump()
    assert {key: call.model_dump(mode="json")[key] for key in legacy_call} == legacy_call
    legacy_pack = {key: legacy_call[key] for key in ("tokens", "budget", "sources")}
    assert PackManifest.model_validate(legacy_pack).model_dump(mode="json") == legacy_pack
    assert PackManifest.model_validate({**legacy_pack, "agent_context": None}).model_dump() == legacy_pack
    standalone = {key: value for key, value in legacy_call.items() if key not in legacy_pack}
    assert CallManifest.model_validate(standalone).agent_context is None


def test_strict_extension_roundtrip_and_explicit_v1_projection():
    pack = AgentPackBuilder().build(invocation())
    document = deepcopy(pack.manifest)
    projection = PackManifest.model_validate_json(json.dumps(document))
    assert projection.model_dump(mode="json") == document
    legacy = projection.legacy_projection()
    assert set(legacy) == {"tokens", "budget", "sources"}
    assert "agent_context" in document
    with pytest.raises(ValidationError):
        PackManifest.model_validate({**document, "unknown": "x"})
    with pytest.raises(ValidationError):
        PackManifest.model_validate({**document, "agent_context": {**document["agent_context"], "scratchpad": "private"}})


@pytest.mark.parametrize("kind,data", [
    (PromptManifest, {"prompt_id": "base", "version": "v1", "content_digest": "sha256:" + "1" * 64}),
    (EnvelopeManifest, {"transfer_id": "transfer:" + "1" * 64, "run_id": "run:adapter", "tool_call_id": "call:one",
                        "schema_version": 1, "payload_digest": "sha256:" + "2" * 64}),
    (SessionManifest, {"session_id": "session:one"}),
    (SourceSelectionManifest, {"item_id": "item:one", "source": "conversation", "type": "conversation", "final_tokens": 2}),
    (BudgetSnapshot, {"owner_remaining": -1, "run_remaining": 20, "reserved_tokens": 0}),
])
def test_nested_observation_contracts_forbid_text_and_unknown_fields(kind, data):
    assert kind.model_config["extra"] == "forbid" and kind.model_config["frozen"]
    kind.model_validate(data)
    with pytest.raises(ValidationError):
        kind.model_validate({**data, "content": "private raw text"})


def test_checkpoint_and_event_refs_keep_the_original_shapes_without_consumption():
    checkpoint = CheckpointRef(execution_thread_id="thread:one", session_id="session:one", checkpoint_ns="",
        checkpoint_id="checkpoint:one", public_state_digest="sha256:" + "1" * 64)
    data = checkpoint.model_dump(mode="json")
    assert CheckpointManifest.model_validate(data).model_dump(mode="json") == data
    assert set(CheckpointManifest.model_fields) == set(CheckpointRef.model_fields)
    session = SessionManifest(session_id="session:one", checkpoint_ref=data)
    envelope = EnvelopeManifest(transfer_id="transfer:" + "1" * 64, run_id="run:adapter", tool_call_id="call:one",
        schema_version=1, payload_digest="sha256:" + "2" * 64,
        summary_ref={"task_id": "long-task:one", "event_id": "event:summary"}, selected_fields=("route_task_id",))
    pack = AgentPackBuilder().build(invocation(session=session, envelope=envelope))
    assert pack.manifest["agent_context"]["session"]["projection_version"] is None
    assert pack.manifest["agent_context"]["envelope"]["summary_ref"]["task_id"] == "long-task:one"
    with pytest.raises(ValidationError, match="match the session"):
        SessionManifest(session_id="session:other", checkpoint_ref=data)
    with pytest.raises(ValueError, match="match the invocation run"):
        AgentPackBuilder().build(invocation(envelope=envelope.model_copy(update={"run_id": "run:other"})))


@pytest.mark.asyncio
async def test_existing_call_boundary_preserves_extension_with_distinct_billing_dimension():
    book = ledger()
    async def operation():
        return AIMessage(content="完成", usage_metadata={"input_tokens": 30, "output_tokens": 4, "total_tokens": 34})

    with request_scope(book):
        pack = AgentPackBuilder().build(invocation())
        await accounted_call(operation, pack.messages, output_tokens=100, model="jev-latest", billing_agent="jev")
    call = CallManifest.model_validate(book.call_manifests[0])
    assert call.agent == "jev" and call.agent_context.owner_agent == "general_chat"
    assert call.total_tokens == 34 and call.agent_context == PackManifest.model_validate(pack.manifest).agent_context
    assert book.usage["general_chat"]["tokens"] == 34
    assert "agent_context" not in call.legacy_projection() and call.legacy_projection()["total_tokens"] == 34

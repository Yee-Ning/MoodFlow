"""Budget receipts, recovery, provider caps and honest cumulative refusal."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import SecretStr, ValidationError
from typesafe_sdk import Choice

from app.agent.decision.jev.client import JevClient
from app.agent.foundation.context_engineering import build_context_async
from app.agent.foundation.context_sources import SourceRegistry, SourceRequest, source_item
from app.agent.foundation.long_horizon.accounting import DurableAccounting, active_accounting
from app.agent.foundation.long_horizon.public_events import PublicUsage
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import AgentUsage, RunLimits
from app.llm.budget import TokenUsage, output_limit
from app.llm.budget_strategy import prepare_sources
from app.llm.runtime import (
    RequestLedger, RuntimeLimitError, accounted_call, active_manifest, agent_scope,
    instrument_chat_model, measure_usage, request_scope,
)
from tests.context_loop_helpers import harness
from tests.test_jev_decision_layer import settings


def book(*, l2=16000, l3=32000, run_id=None, usage=None):
    return RequestLedger(usage or tuple(AgentUsage(agent=key) for key in AgentKey),
        AgentKey.GENERAL_CHAT, RunLimits(agent_token_budget=l2, run_token_budget=l3),
        deadline=30, run_id=run_id)


def response(left, right):
    return AIMessage(content="已完成", usage_metadata={"input_tokens": left,
        "output_tokens": right, "total_tokens": left + right})


def accounting(h):
    return DurableAccounting(h.writer, h.identity, SimpleNamespace(check=lambda: None))


@pytest.mark.asyncio
async def test_existing_receipts_have_split_models_and_same_request_after_resume(tmp_path):
    h = harness(tmp_path / "budget.db")
    durable = accounting(h)
    token = active_accounting.set(durable)
    try:
        ledger = book(run_id=h.run.run_id)
        with request_scope(ledger):
            await accounted_call(AsyncMock(return_value=response(100, 10)), "hello",
                                 output_tokens=100, model="deepseek-chat")
            with agent_scope(AgentKey.EXPLORER):
                await accounted_call(AsyncMock(return_value=response(200, 20)), "explore",
                                     output_tokens=100, model="deepseek-reasoner")
        assert ledger.budget_ledger.get_task_total(h.run.run_id) == TokenUsage(300, 30, 0, 2)
        assert ledger.budget_ledger.get_agent_total(h.run.run_id, "explorer") == TokenUsage(200, 20, 0, 1)
        assert set(ledger.budget_ledger.get_model_breakdown(h.run.run_id)) == {"deepseek-chat", "deepseek-reasoner"}
        # Even an older empty checkpoint restores committed usage, including all owners.
        restored = book(run_id=h.run.run_id)
        assert restored.usage["general_chat"]["tokens"] == 110
        assert restored.usage["explorer"]["tokens"] == 220
        assert restored.remaining == ledger.remaining
        assert restored.budget_ledger.get_task_total(h.run.run_id).total_tokens == 330
        other_id = h.run.run_id + "-next"
        fresh = book(run_id=other_id)
        assert sum(row["tokens"] for row in fresh.usage.values()) == 0
        assert fresh.budget_ledger.get_task_total(other_id).total_tokens == 0
        with pytest.raises(ValueError, match="scope mismatch"):
            fresh.budget_ledger.get_task_total(h.run.run_id)
    finally:
        active_accounting.reset(token)


def test_add_is_a_view_of_existing_accounting_and_cost_uses_config(tmp_path, monkeypatch):
    h = harness(tmp_path / "cost.db")
    adapter = accounting(h).budget_ledger
    adapter.add(h.run.run_id, "general_chat", 1000, 100, "deepseek-chat")
    adapter.add(h.run.run_id, "jev", 200, 20, "jev-latest", estimated=True)
    assert adapter.get_task_total(h.run.run_id) == TokenUsage(1200, 120, 220, 2)
    assert adapter.get_agent_total(h.run.run_id, "jev") == TokenUsage(200, 20, 220, 1)
    # Synthetic rates, deliberately unrelated to provider pricing.
    configured = {"currency": "test-unit", "prices": {
        "deepseek-chat": {"input_per_million": "2.5", "output_per_million": "10"},
        "jev-latest": {"input_per_million": "1", "output_per_million": "2"}}}
    monkeypatch.setattr("app.llm.budget.policy", lambda: configured)
    assert adapter.get_cost(h.run.run_id) == {"currency": "test-unit", "priced_cost": "0.00374",
        "complete": True, "unpriced_models": []}
    configured["prices"].pop("jev-latest")
    assert adapter.get_cost(h.run.run_id)["complete"] is False
    assert adapter.get_cost(h.run.run_id)["unpriced_models"] == ["jev-latest"]
    with pytest.raises(ValidationError):
        adapter.add(h.run.run_id, "general_chat", -1, 2, "deepseek-chat")
    with pytest.raises(ValueError, match="unknown budget agent"):
        adapter.add(h.run.run_id, "unregistered", 1, 1, "deepseek-chat")


def test_unsettled_dispatch_remains_estimated_then_settlement_replaces_it_once(tmp_path):
    h = harness(tmp_path / "unsettled.db")
    durable = accounting(h)
    token = active_accounting.set(durable)
    try:
        ledger = book(run_id=h.run.run_id)
        call_id = durable.reserve(ledger, 120, input_tokens=20, output_tokens=100,
                                  model="deepseek-chat", billing_agent="general_chat")
        recovered = book(run_id=h.run.run_id)
        assert recovered.usage["general_chat"]["tokens"] == 120
        assert recovered.budget_ledger.get_task_total(h.run.run_id) == TokenUsage(20, 100, 120, 1)
        durable.settle(ledger, call_id, 120, response(20, 7), usage=measure_usage(response(20, 7), "hi", 100),
                       model="deepseek-chat", billing_agent="general_chat", max_output_tokens=100)
        assert recovered.budget_ledger.get_task_total(h.run.run_id) == TokenUsage(20, 7, 0, 1)
        # Saved conservative usage is never reduced on recovery.
        assert book(run_id=h.run.run_id, usage=tuple(AgentUsage.model_validate(row)
                    for row in recovered.usage.values())).usage["general_chat"]["tokens"] == 120
    finally:
        active_accounting.reset(token)


def test_later_session_baseline_does_not_double_bill_existing_usage(tmp_path):
    h = harness(tmp_path / "baseline.db")
    durable = accounting(h)
    durable.write_usage(PublicUsage(run_id=h.run.run_id, agent="general_chat", call_id="original",
        phase="baseline", tokens=500, model_calls=1))
    adapter = durable.budget_ledger
    adapter.add(h.run.run_id, "general_chat", 100, 0, "deepseek-chat")
    durable.write_usage(PublicUsage(run_id=h.run.run_id, agent="general_chat", call_id="later-session",
        phase="baseline", tokens=600, model_calls=2))
    assert adapter.get_task_total(h.run.run_id) == TokenUsage(600, 0, 500, 2)
    assert adapter.get_model_breakdown(h.run.run_id) == {
        "unknown": TokenUsage(500, 0, 500, 1), "deepseek-chat": TokenUsage(100, 0, 0, 1)}


@pytest.mark.parametrize("usage,basis,estimated,expected", [
    ({"input_tokens": 11, "output_tokens": 2}, "provider_usage", False, (11, 2)),
    ({"prompt_tokens": 9, "completion_tokens": 0}, "provider_usage", False, (9, 0)),
    ({"total_tokens": 17}, "provider_total_estimated_split", True, None),
    (None, "token_counter", True, None),
    ({"input_tokens": True, "output_tokens": -1}, "token_counter", True, None),
])
def test_usage_formats_authoritative_counts_or_explicit_estimates(usage, basis, estimated, expected):
    measured = measure_usage(SimpleNamespace(usage=usage, output_text="你好"), {"input": "hi"}, 100)
    assert measured.basis == basis and measured.estimated is estimated
    assert measured.input_tokens >= 0 and measured.output_tokens >= 0
    if expected:
        assert (measured.input_tokens, measured.output_tokens) == expected
    if basis == "provider_total_estimated_split":
        assert measured.total_tokens == 17


@pytest.mark.asyncio
async def test_manifest_combines_source_counts_with_actual_call_usage():
    ledger = book()
    item = source_item("p", "user_profile", "喜欢爵士", source="user_profile", intents=["general_chat"])
    with request_scope(ledger):
        pack = await build_context_async(RouteKey.GENERAL_CHAT, query="hello", system="rules", registered_items=[item],
                                         intent={"routes": ["general_chat"]})
        await instrument_chat_model(SimpleNamespace(ainvoke=AsyncMock(return_value=response(80, 5)),
                                                   model_name="deepseek-chat")).ainvoke(pack.messages)
    manifest = ledger.call_manifests[0]
    assert manifest["sources"]["user_profile"]["final_tokens"] > 0
    assert "history_memory" in manifest["sources"]
    assert (manifest["input_tokens"], manifest["output_tokens"], manifest["total_tokens"]) == (80, 5, 85)
    assert manifest["max_output_tokens"] == output_limit("deepseek-chat")
    assert manifest["estimated"] is False and manifest["trace_id"] == ledger.run_id
    assert active_manifest.get() is None


@pytest.mark.asyncio
async def test_l2_history_summary_stop_sources_and_refusal_are_ordered():
    ledger = book(l2=1200)
    old = [HumanMessage(content="旧内容" * 400) for _ in range(10)]
    protocol = [AIMessage(content="", tool_calls=[{"id": "t", "name": "search", "args": {}}]),
                ToolMessage(content="可信结果", tool_call_id="t")]
    with request_scope(ledger):
        reduced = prepare_sources({"query": "hi", "system": "rules", "messages": old + protocol,
                                   "registered_items": ["画像" * 2000]}, reserved_output=100)
        assert reduced["registered_items"] == []
        assert all(message in reduced["messages"] for message in protocol)
        assert "历史摘录" in reduced["messages"][0].content
        assert [event["stage"] for event in ledger.budget_trace] == ["truncate_history", "summarize_history", "stop_sources"]
        source = SimpleNamespace(name="user_profile", collect=AsyncMock())
        _, status = SourceRegistry([source]).collect(SourceRequest("hi", {}, allow_new_sources=False))
        assert status == {"user_profile": "budget_stopped"}
        source.collect.assert_not_called()
        with pytest.raises(RuntimeLimitError, match="token_limit"):
            prepare_sources({"query": "必需输入" * 2000, "system": "rules"}, reserved_output=100)
    assert [event["stage"] for event in ledger.budget_trace][-1] == "honest_degradation"
    assert all(event["trace_id"] == ledger.run_id and event["level"] == "l2" for event in ledger.budget_trace)
    assert "预算已用完" in ledger.limit_message


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["l2", "l3"])
async def test_observed_overshoot_keeps_bill_and_prevents_next_dispatch(level):
    ledger = book(l2=600, l3=600 if level == "l3" else 2000)
    blocked = AsyncMock(return_value=response(1, 1))
    with request_scope(ledger):
        if level == "l3":
            await accounted_call(AsyncMock(return_value=response(349, 1)), "hi", output_tokens=50)
            with agent_scope(AgentKey.EXPLORER), pytest.raises(RuntimeLimitError, match="token_limit"):
                await accounted_call(AsyncMock(return_value=response(349, 1)), "hi", output_tokens=50)
            assert sum(row["tokens"] for row in ledger.usage.values()) == 700
        else:
            with pytest.raises(RuntimeLimitError, match="token_limit"):
                await accounted_call(AsyncMock(return_value=response(649, 1)), "hi", output_tokens=50)
            assert ledger.usage["general_chat"]["tokens"] == 650
        with pytest.raises(RuntimeLimitError, match="token_limit"):
            await accounted_call(blocked, "hi", output_tokens=50)
    blocked.assert_not_called()
    stages = [row["stage"] for row in ledger.budget_trace]
    assert stages == (["truncate_history", "summarize_history", "stop_sources", "honest_degradation"]
                      if level == "l2" else ["task_degradation", "refuse"])


def test_l3_prepack_refusal_does_not_change_hop_limit():
    usage = tuple(AgentUsage(agent=key, tokens=300) for key in AgentKey)
    ledger = book(l2=600, l3=900, usage=usage)
    assert ledger.limits.max_handoffs == 5
    with request_scope(ledger), pytest.raises(RuntimeLimitError, match="token_limit"):
        prepare_sources({"query": "hi", "system": "rules"}, reserved_output=50)
    assert [row["stage"] for row in ledger.budget_trace] == ["task_degradation", "refuse"]


def test_jev_preflight_budget_rejection_records_ordered_trace_without_dispatch():
    from app.agent.foundation.decision_input import prepare_decision_input
    ledger = book(l2=300)
    with request_scope(ledger), pytest.raises(RuntimeLimitError, match="token_limit"):
        prepare_decision_input("hi", {"route": Choice(instructions="choose", criteria={"chat": None})})
    assert [row["stage"] for row in ledger.budget_trace] == [
        "truncate_history", "summarize_history", "stop_sources", "honest_degradation"]
    assert not ledger.call_manifests and ledger.reserved == 0


@pytest.mark.asyncio
async def test_provider_cap_and_ignoring_cap_cannot_return_oversized_result(monkeypatch):
    monkeypatch.setattr("app.llm.budget.output_limit", lambda _model="default": 32)
    called = []
    async def compliant(messages, **kwargs):
        called.append(kwargs)
        return AIMessage(content="长" * kwargs["max_tokens"], usage_metadata={"input_tokens": 40,
            "output_tokens": kwargs["max_tokens"], "total_tokens": 40 + kwargs["max_tokens"]})
    ledger = book()
    with request_scope(ledger):
        output = await instrument_chat_model(SimpleNamespace(ainvoke=compliant, model_name="deepseek-chat")).ainvoke([HumanMessage(content="hi")])
        assert len(output.content) == 32
        with pytest.raises(RuntimeLimitError, match="token_limit"):
            await instrument_chat_model(SimpleNamespace(ainvoke=AsyncMock(return_value=response(40, 90)),
                model_name="deepseek-chat")).ainvoke([HumanMessage(content="hi")])
    assert called == [{"max_tokens": 32}]
    assert ledger.call_manifests[1]["output_limit_exceeded"] is True
    assert ledger.usage["general_chat"]["tokens"] == 202


@pytest.mark.asyncio
async def test_caller_smaller_output_cap_is_the_actual_reservation_and_manifest_cap():
    ledger = book()
    model = SimpleNamespace(model_name="deepseek-chat", ainvoke=AsyncMock(return_value=response(40, 17)))
    with request_scope(ledger):
        await instrument_chat_model(model).ainvoke([HumanMessage(content="hi")], max_tokens=17)
    assert model.ainvoke.await_args.kwargs["max_tokens"] == 17
    assert ledger.call_manifests[0]["max_output_tokens"] == 17


@pytest.mark.asyncio
@pytest.mark.parametrize("reported", [True, False])
async def test_jev_sdk_usage_or_estimate_enters_same_l3_and_model_dimension(tmp_path, reported):
    sent = []
    class SDK:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def system_one(self, **kwargs):
            sent.append(kwargs)
            return SimpleNamespace(model="jev-version", usage=SimpleNamespace(model_dump=lambda: {
                "input_tokens": 21, "output_tokens": 2}) if reported else None,
                answers={"route": SimpleNamespace(type="choice", choice="chat", confidence=1, probabilities={"chat": 1})})
    h = harness(tmp_path / "jev.db")
    token = active_accounting.set(accounting(h))
    try:
        ledger = book(run_id=h.run.run_id)
        configured = settings().model_copy(update={"typesafe_api_key": SecretStr("offline")})
        with patch("app.agent.decision.jev.client.AsyncTypeSafeClient", SDK), request_scope(ledger):
            await JevClient(configured).decide("hi", {"route": Choice(instructions="choose", criteria={"chat": None})})
        assert "extra_body" not in sent[0]
        assert ledger.call_manifests[0]["max_output_tokens"] == output_limit("jev-latest")
        usage = ledger.budget_ledger.get_agent_total(h.run.run_id, "jev")
        assert usage == ledger.budget_ledger.get_task_total(h.run.run_id)
        assert ledger.budget_ledger.get_model_breakdown(h.run.run_id) == {"jev-latest": usage}
        assert ledger.call_manifests[0]["estimated"] is not reported
        assert ledger.call_manifests[0]["model"] == "jev-latest"
        if reported:
            assert usage == TokenUsage(21, 2, 0, 1)
        else:
            assert usage.estimated_tokens == usage.total_tokens > 0
    finally:
        active_accounting.reset(token)


def test_stop_source_budget_event_survives_resume_without_billing_again(tmp_path):
    h = harness(tmp_path / "stop.db")
    token = active_accounting.set(accounting(h))
    try:
        ledger = book(run_id=h.run.run_id)
        ledger.budget_event("l2", "stop_sources")
        resumed = book(run_id=h.run.run_id)
        assert "general_chat" in resumed.source_stops
        assert resumed.budget_ledger.get_task_total(h.run.run_id).total_tokens == 0
        assert any(event.phase == "budget" and event.budget_stage == "stop_sources"
                   for event in resumed.accounting.events(h.run.run_id))
    finally:
        active_accounting.reset(token)


@pytest.mark.asyncio
async def test_new_source_cannot_bypass_final_token_gate():
    operation = AsyncMock(return_value=response(1, 1))
    with pytest.raises(RuntimeLimitError, match="token_limit"):
        await accounted_call(operation, {"user_profile": "画像" * 30000}, output_tokens=100)
    operation.assert_not_called()


def test_sdk_dispatch_lint_requires_accounted_call_boundary():
    root = Path(__file__).resolve().parents[1] / "app"
    files = [root / "llm/client.py", root / "llm/runtime.py", root / "agent/decision/jev/client.py"]
    dispatches = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            if name not in {"client.responses.create", "client.system_one", "self.model.ainvoke"}:
                continue
            dispatches += 1
            parent = parents[node]
            assert isinstance(parent, ast.Lambda), (path.name, node.lineno)
            boundary = parents[parent]
            assert isinstance(boundary, ast.Call) and ast.unparse(boundary.func) == "accounted_call"
    assert dispatches == 3


def test_receipts_reject_mismatched_splits_and_unknown_fields():
    base = {"run_id": "r", "agent": "general_chat", "call_id": "c", "phase": "settled", "tokens": 9}
    with pytest.raises(ValidationError):
        PublicUsage(**base, input_tokens=5, output_tokens=5)
    with pytest.raises(ValidationError):
        PublicUsage(**base, raw_prompt="private")
    # Legacy receipts remain readable with explicit unknown model/split.
    parsed = PublicUsage(**base)
    assert parsed.model == "unknown" and parsed.input_tokens is None

"""Entry triage observes or selects a plan without widening business permissions."""

import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.agent.decision.jev.client import DecisionAnswer, JevDecisionError
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.handoff.routing import HandoffEntryRouter, configured_entry_router
from app.llm.runtime import RuntimeLimitError
from tests.test_handoff_runtime import Router
from tests.test_jev_decision_layer import response, settings


def client(confidence=0.99):
    probe = AsyncMock()
    probe.decide.return_value = response(
        {
            "route": DecisionAnswer(
                "choice",
                choice="tool_calling",
                confidence=confidence,
                probabilities={
                    item.value: confidence
                    if item is RouteKey.TOOL_CALLING
                    else (1 - confidence) / 3
                    for item in RouteKey
                },
            )
        }
    )
    return probe


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shadow,expected", [(True, RouteKey.GENERAL_CHAT), (False, RouteKey.TOOL_CALLING)]
)
async def test_shadow_preserves_baseline_and_active_selects_candidate(
    shadow, expected, caplog
):
    baseline = AsyncMock(wraps=Router(RouteKey.GENERAL_CHAT))
    probe = client()
    with caplog.at_level(logging.INFO):
        plan = await HandoffEntryRouter(baseline, probe, shadow=shadow).route(
            "暂停一下", RoutingContext()
        )
    assert plan.tasks[0].route_key is expected
    assert baseline.route.await_count == int(shadow)
    assert probe.decide.await_count == 1
    event = json.loads(
        next(
            record.message.split("jev.handoff_entry ")[1]
            for record in caplog.records
            if record.message.startswith("jev.handoff_entry ")
        )
    )
    assert event["plan_agreement"] is (False if shadow else None)
    assert "暂停一下" not in caplog.text and "candidate_summary" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [True, False])
@pytest.mark.parametrize(
    "failure",
    [
        JevDecisionError("timeout"),
        JevDecisionError("schema_failure"),
        KeyError("private"),
    ],
)
async def test_api_and_schema_failures_use_baseline_once(shadow, failure):
    baseline = AsyncMock(wraps=Router(RouteKey.TOOL_CALLING))
    probe = client()
    probe.decide.side_effect = failure
    plan = await HandoffEntryRouter(baseline, probe, shadow=shadow).route(
        "暂停一下", RoutingContext()
    )
    assert plan.tasks[0].route_key is RouteKey.TOOL_CALLING
    assert baseline.route.await_count == probe.decide.await_count == 1


@pytest.mark.asyncio
async def test_low_confidence_falls_back_and_budget_failure_propagates():
    baseline = AsyncMock(wraps=Router(RouteKey.GENERAL_CHAT))
    probe = client(0.79)
    adapter = HandoffEntryRouter(baseline, probe, shadow=False)
    assert (await adapter.route("暂停一下", RoutingContext())).tasks[
        0
    ].route_key is RouteKey.GENERAL_CHAT
    probe.decide.side_effect = RuntimeLimitError("token_limit")
    with pytest.raises(RuntimeLimitError):
        await adapter.route("暂停一下", RoutingContext())
    assert baseline.route.await_count == 1


def test_independent_factory_gates(monkeypatch):
    baseline = object()
    assert configured_entry_router(baseline) is baseline
    monkeypatch.setenv("JEV_ENABLED", "true")
    config = settings(jev_handoff_router_enabled=False)
    monkeypatch.setattr("app.agent.handoff.routing.get_settings", lambda: config)
    assert configured_entry_router(baseline) is baseline
    config = config.model_copy(
        update={
            "jev_handoff_router_enabled": True,
            "jev_handoff_router_shadow_mode": True,
            "jev_shadow_mode": False,
        }
    )
    adapter = configured_entry_router(baseline)
    assert adapter.shadow and adapter.baseline is baseline


@pytest.mark.asyncio
async def test_entry_candidate_reaches_existing_handoff_graph_without_protocol_change():
    from app.agent.handoff.graph import build_handoff_graph
    from tests.test_handoff_runtime import ToolModel, call, input_state
    from langchain_core.messages import AIMessage

    baseline = AsyncMock(wraps=Router(RouteKey.GENERAL_CHAT))
    adapter = HandoffEntryRouter(baseline, client(), shadow=False)
    model = ToolModel(responses=[AIMessage(content="已完成。")])
    out = (
        await build_handoff_graph(
            intent_router=adapter,
            transfer_model=ToolModel(responses=[call()]),
            curator_model=model,
        )
        .compile()
        .ainvoke(input_state())
    )
    assert out["handoff_run"]["status"] == "completed"
    assert out["handoff_run"]["results"][0]["route_key"] == "tool_calling"
    assert "task_fidelity" not in out["final_response"]

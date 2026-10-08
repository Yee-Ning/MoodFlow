"""The JEV score cannot widen deterministic transfer permissions."""

import json
from unittest.mock import AsyncMock

import pytest

from app.agent.decision.jev.client import DecisionAnswer, JevDecisionError
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.guard import JevHandoffGuard, configured_guard
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.state import HandoffRun, RunStatus, StopReason
from tests.test_jev_decision_layer import response, settings
from tests.test_handoff_runtime import Router, ToolModel, call, input_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "false")


def guard(*scores, shadow=False):
    client = AsyncMock()
    client.decide.side_effect = [
        response(
            {
                "task_fidelity": DecisionAnswer("noul", noul=score),
                "authority_override": DecisionAnswer("noul", noul=0),
            }
        )
        for score in scores
    ]
    return JevHandoffGuard(client, shadow=shadow), client


def graph(model, review):
    return build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=model,
        handoff_guard=review,
    ).compile()


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [0.8, 1.0])
async def test_threshold_includes_equality_and_keeps_assigned_task(score):
    review, client = guard(score)
    out = await graph(ToolModel(responses=[call()]), review).ainvoke(input_state())
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.status is RunStatus.COMPLETED and run.handoff_count == 1
    assert client.decide.await_count == 1
    payload = client.decide.call_args.args[0]
    assert (
        payload["original_input"] == "暂停一下"
        and payload["assigned_task"]["task_id"] == "task-0"
    )
    assert any(item["guard_score"] == score for item in out["handoff_trace"])
    assert payload["task_owner"] == "curator"
    assert "tool_calling" in payload["agent_roles"]["curator"]["capabilities"]
    assert set(client.decide.call_args.args[1]) == {
        "task_fidelity",
        "authority_override",
    }


@pytest.mark.asyncio
async def test_low_score_pairs_error_then_one_redecision_without_consuming_hop():
    review, client = guard(0.79, 0.9)
    model = ToolModel(responses=[call(call_id="first"), call(call_id="second")])
    out = await graph(model, review).ainvoke(input_state())
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.status is RunStatus.COMPLETED and run.handoff_count == 1
    assert run.hops[0].tool_call_id == "second" and run.usage[0].retries == 1
    assert client.decide.await_count == 2 and len(model.seen_messages) == 2
    assert (
        len([row for row in out["handoff_trace"] if row["code"] == "guard_rejected"])
        == 1
    )
    assert "上次转交被护栏拒绝" in str(model.seen_messages[1])
    observations = [
        json.loads(item.content)
        for item in out["messages"]
        if getattr(item, "type", None) == "tool"
    ]
    assert observations == [
        {"accepted": False, "code": "guard_rejected"},
        {"accepted": True, "code": "accepted"},
    ]


@pytest.mark.asyncio
async def test_two_low_scores_stop_with_zero_hops_and_no_action():
    review, client = guard(0.1, 0.2)
    out = await graph(
        ToolModel(responses=[call(call_id="first"), call(call_id="second")]), review
    ).ainvoke(input_state())
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.status is RunStatus.FAILED and run.stop_reason is StopReason.RETRY_LIMIT
    assert run.results == () and run.handoff_count == 0 and run.usage[0].retries == 1
    assert client.decide.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        JevDecisionError("unavailable"),
        TimeoutError(),
        ValueError("private"),
        response({"transfer": DecisionAnswer("choice", choice="bad")}),
        response({"transfer": DecisionAnswer("noul", noul=float("nan"))}),
        response(
            {
                "task_fidelity": DecisionAnswer("noul", noul=1),
                "authority_override": DecisionAnswer("noul", noul=float("nan")),
            }
        ),
        response(
            {
                "task_fidelity": DecisionAnswer("noul", noul=1),
                "authority_override": DecisionAnswer("choice", choice="bad"),
            }
        ),
    ],
)
async def test_unavailable_or_malformed_guard_uses_local_contract_once(bad):
    client = AsyncMock()
    client.decide.side_effect = bad if isinstance(bad, Exception) else None
    if not isinstance(bad, Exception):
        client.decide.return_value = bad
    out = await graph(ToolModel(responses=[call()]), JevHandoffGuard(client)).ainvoke(
        input_state()
    )
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.status is RunStatus.COMPLETED and run.handoff_count == 1
    assert client.decide.await_count == 1 and run.usage[0].retries == 0
    assert "guard_unavailable" in {row["code"] for row in out["handoff_trace"]}
    assert "private" not in json.dumps(out["handoff_trace"])


@pytest.mark.asyncio
async def test_shadow_scores_observe_without_changing_business_outcome():
    review, client = guard(0, shadow=True)
    out = await graph(ToolModel(responses=[call()]), review).ainvoke(input_state())
    assert (
        out["handoff_run"]["status"] == "completed" and client.decide.await_count == 1
    )
    assert any(
        row["code"] == "guard_shadow" and row["guard_score"] == 0
        for row in out["handoff_trace"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    [
        call("transfer_to_explorer_agent"),
        call(args={"reason": "bad", "context_summary": "bad", "authorized": True}),
    ],
)
async def test_invalid_edge_or_forged_authority_is_denied_before_jev(message):
    review, client = guard(1)
    out = await graph(ToolModel(responses=[message]), review).ainvoke(input_state())
    assert out["handoff_run"]["status"] == "failed"
    client.decide.assert_not_called()


def test_existing_enable_and_shadow_settings_control_default_guard(monkeypatch):
    assert configured_guard() is None
    monkeypatch.setenv("JEV_ENABLED", "true")
    monkeypatch.setattr(
        "app.agent.handoff.guard.get_settings", lambda: settings(jev_shadow_mode=True)
    )
    probe = configured_guard()
    assert probe.shadow and probe.threshold == 0.8
    assert probe.client.settings.jev_model == "jev-latest"


@pytest.mark.asyncio
@pytest.mark.parametrize("risk,allowed", [(0.2, True), (0.2001, False), (1, False)])
async def test_authority_override_has_independent_inclusive_boundary(risk, allowed):
    from app.agent.handoff.contracts import HandoffEnvelope, HandoffRequest

    task = (await Router(RouteKey.TOOL_CALLING).route("暂停一下", None)).tasks[0]
    from app.agent.handoff.registry import AgentKey

    envelope = HandoffEnvelope(
        run_id="test",
        tool_call_id="call",
        source_agent=AgentKey.GENERAL_CHAT,
        request=HandoffRequest(
            target_agent=AgentKey.CURATOR, reason="音乐操作", context_summary="暂停"
        ),
        original_input="暂停一下",
        active_task=task,
        pending_tasks=(task,),
    )
    client = AsyncMock()
    client.decide.return_value = response(
        {
            "task_fidelity": DecisionAnswer("noul", noul=0.99),
            "authority_override": DecisionAnswer("noul", noul=risk),
        }
    )
    result = await JevHandoffGuard(client).review(envelope)
    assert result.allowed is allowed and result.would_allow is allowed
    assert result.scores["authority_override"] == risk


def test_guard_and_router_controls_are_independent(monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "true")
    config = settings(
        jev_shadow_mode=False,
        jev_handoff_guard_shadow_mode=True,
        jev_router_confidence_threshold=0.8,
        jev_handoff_guard_threshold=0.9,
        jev_handoff_injection_threshold=0.1,
    )
    monkeypatch.setattr("app.agent.handoff.guard.get_settings", lambda: config)
    probe = configured_guard()
    assert probe.shadow and probe.threshold == 0.9 and probe.injection_threshold == 0.1
    monkeypatch.setattr(
        "app.agent.handoff.guard.get_settings",
        lambda: config.model_copy(update={"jev_handoff_guard_enabled": False}),
    )
    assert configured_guard() is None


@pytest.mark.parametrize(
    "threshold,risk",
    [(-1, 0.2), (1.1, 0.2), (0.8, -1), (0.8, 1.1), (float("nan"), 0.2)],
)
def test_invalid_thresholds_are_rejected(threshold, risk):
    with pytest.raises(ValueError):
        JevHandoffGuard(None, threshold=threshold, injection_threshold=risk)

"""Candidate/cohort metadata, Guard bins and observational failure isolation."""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.decision.jev.client import JevDecisionError
from app.agent.foundation.routing import RouteKey, RoutingContext
from app.agent.handoff import observability as obs
from app.agent.handoff.routing import HandoffEntryRouter
from app.agent.handoff.trace import TraceRecorder
from app.agent.handoff.registry import AgentKey
from app.schemas.chat import ClientActionStreamEvent, FoundationResponse
from app.schemas.client_actions import ClientAction
from tests.test_handoff_contracts import make_run, transfer
from tests.test_handoff_guard import guard
from tests.test_handoff_runtime import Router
from tests.test_jev_router_v5 import Client


def events(caplog, marker):
    return [
        json.loads(record.message.split(marker + " ", 1)[1])
        for record in caplog.records
        if record.message.startswith(marker + " ")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "routes,query,classification",
    [
        (["general_chat"], "老唱片为什么会有底噪", "Single"),
        (
            ["general_chat", "general_chat"],
            "老唱片为什么会有底噪；爵士乐的摇摆感从何而来",
            "Multi",
        ),
    ],
)
async def test_real_candidate_classification_preserves_shadow_baseline(
    caplog, routes, query, classification
):
    baseline = Router(*([RouteKey.GENERAL_CHAT] * len(routes)))
    expected = await baseline.route(query, RoutingContext())
    context, token = obs.begin_observation(
        "handoff", "private-session", "acceptance", entrypoint="chat"
    )
    try:
        with caplog.at_level(logging.INFO):
            decision = await HandoffEntryRouter(
                baseline, Client(routes), shadow=True
            ).route(query, RoutingContext())
        assert decision == expected
        (event,) = events(caplog, "jev.handoff_entry")
        assert (event["decision_class"], event["attempt_class"]) == (
            classification,
            classification,
        )
        assert event["cohort"] == "canary" and event["sample_kind"] == "acceptance"
        assert (
            event["request_key"] == context.request_key and event["candidate_complete"]
        )
        assert event["api_calls"] == (1 if classification == "Single" else 2)
        assert event["candidate_task_count"] == len(routes) and event["v5_attempted"]
        assert query not in caplog.text and "private-session" not in caplog.text
    finally:
        obs.end_observation(token)


@pytest.mark.asyncio
async def test_rules_are_not_counted_as_v5_and_fallback_emits_alert(caplog):
    baseline = AsyncMock(wraps=Router(RouteKey.GENERAL_CHAT))
    client = AsyncMock()
    client.decide.side_effect = JevDecisionError("timeout")
    _, token = obs.begin_observation("handoff", "one", "synthetic", entrypoint="chat")
    try:
        with caplog.at_level(logging.INFO):
            await HandoffEntryRouter(baseline, client).route(
                "暂停播放", RoutingContext()
            )
            decision = await HandoffEntryRouter(baseline, client).route(
                "老唱片为什么会有底噪", RoutingContext()
            )
        (rule,) = events(caplog, "jev.handoff_entry_local")
        assert rule["decision_class"] == "rule" and not rule["v5_attempted"]
        (event,) = events(caplog, "jev.handoff_entry")
        assert (
            event["decision_class"] == "fallback"
            and event["fallback_reason"] == "timeout"
        )
        assert (
            event["attempt_class"] == "Single" and event["sample_kind"] == "synthetic"
        )
        (fallback,) = events(caplog, "handoff.fallback")
        (alert,) = events(caplog, "handoff.alert")
        assert alert["fallback_event_id"] == fallback["event_id"]
        assert decision.tasks[0].route_key is RouteKey.GENERAL_CHAT
        assert baseline.route.await_count == client.decide.await_count == 1
    finally:
        obs.end_observation(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "score,expected",
    [
        (0, "[0,.7)"),
        (0.6999, "[0,.7)"),
        (0.7, "[.7,.8)"),
        (0.7999, "[.7,.8)"),
        (0.8, "[.8,.9)"),
        (0.8999, "[.8,.9)"),
        (0.9, "[.9,1]"),
        (1, "[.9,1]"),
    ],
)
async def test_guard_histogram_boundaries_preserve_threshold_and_shadow(
    caplog, score, expected
):
    _, result = transfer(
        make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT), AgentKey.CURATOR
    )
    review, _ = guard(score, shadow=True)
    with caplog.at_level(logging.INFO):
        verdict = await review.review(result.envelope)
    (event,) = events(caplog, "jev.handoff_guard")
    assert (
        event["score"] == score
        and event["score_bucket"] == expected
        and event["threshold"] == 0.8
    )
    assert event["scores"] == {"task_fidelity": score, "authority_override": 0}
    assert verdict.allowed and verdict.would_allow == (score >= 0.8)


@pytest.mark.asyncio
async def test_guard_missing_score_is_explicit(caplog):
    _, result = transfer(
        make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT), AgentKey.CURATOR
    )
    review, client = guard(1)
    client.decide.side_effect = JevDecisionError("private upstream text")
    with caplog.at_level(logging.INFO):
        verdict = await review.review(result.envelope)
    (event,) = events(caplog, "jev.handoff_guard")
    assert verdict.allowed and verdict.code == "guard_unavailable"
    assert (
        event["score"] is None
        and event["score_bucket"] == "missing"
        and event["status"] == "guard_unavailable"
    )
    assert "private upstream text" not in caplog.text


def test_trace_keeps_durable_contract_and_logs_edge_metadata(caplog):
    trace = TraceRecorder("run-fixture", AgentKey.GENERAL_CHAT)
    with caplog.at_level(logging.INFO):
        trace.emit(
            code="accepted",
            target=AgentKey.CURATOR,
            tokens=12,
            duration_ms=34,
            call_id="private-call",
        )
    (event,) = events(caplog, "handoff.trace")
    assert (
        event["source_agent"],
        event["target_agent"],
        event["reason_code"],
        event["tokens"],
        event["duration_ms"],
    ) == ("general_chat", "curator", "accepted", 12, 34)
    assert "cohort" not in trace.events[0] and "reason_code" not in trace.events[0]
    assert "private-call" not in caplog.text


@pytest.mark.asyncio
async def test_worker_context_is_concurrent_and_does_not_change_actions_or_results(
    caplog,
):
    action = ClientAction.model_validate(
        {
            "action_id": "private-action",
            "task_id": "task-one",
            "name": "pause_player",
            "arguments": {},
        }
    )
    response = FoundationResponse(status="success", message="private reply")
    received = []

    @obs.observe_execution
    async def worker(**kwargs):
        await asyncio.sleep(0)
        kwargs["emit"](ClientActionStreamEvent(action=action))
        return response

    async def run(mode, thread, source):
        _, token = obs.begin_observation(mode, thread, source, entrypoint="chat")
        try:
            return await asyncio.create_task(worker(emit=received.append))
        finally:
            obs.end_observation(token)

    with caplog.at_level(logging.INFO):
        results = await asyncio.gather(
            run("handoff", "secret-thread-a", "production"),
            run("foundation", "secret-thread-b", "synthetic"),
        )
    assert all(result is response for result in results)
    assert all(event.action is action for event in received)
    generated = events(caplog, "handoff.action")
    assert {(row["cohort"], row["sample_kind"]) for row in generated} == {
        ("canary", "production"),
        ("non_canary", "synthetic"),
    }
    assert len({row["request_key"] for row in generated}) == 2
    assert len(events(caplog, "handoff.request")) == 4
    assert (
        "secret-thread" not in caplog.text
        and "private-action" not in caplog.text
        and "private reply" not in caplog.text
    )


def test_ack_keys_join_original_namespace_without_rollout_or_user_data(caplog):
    identity = SimpleNamespace(
        graph_mode="handoff", conversation_id="handoff:v1:private-thread"
    )
    with caplog.at_level(logging.INFO):
        obs.record_ack(identity, "private-action", "applied")
        obs.record_ack(identity, "private-action", "failed")
    one, two = events(caplog, "handoff.ack")
    assert (
        one["action_key"]
        == two["action_key"]
        == obs.action_key("handoff", obs.digest("private-thread"), "private-action")
    )
    assert (one["status"], two["status"]) == ("applied", "rejected")
    assert "private-thread" not in caplog.text and "private-action" not in caplog.text


@pytest.mark.asyncio
async def test_log_failure_cannot_change_business_or_hide_loss_count(monkeypatch):
    response = FoundationResponse(status="success", message="unchanged")
    monkeypatch.setattr(obs, "_logging_errors", 0)
    monkeypatch.setattr(
        obs.logger, "info", lambda *args: (_ for _ in ()).throw(OSError())
    )

    @obs.observe_execution
    async def worker(**kwargs):
        return response

    assert await worker(thread_id="one") is response
    assert obs._logging_errors == 2

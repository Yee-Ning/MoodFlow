"""Sampling and offline replay cannot manufacture agreement."""

from types import SimpleNamespace
import json

import pytest

from app.agent.decision.jev.client import DecisionAnswer, DecisionResponse
from app.agent.decision.jev.router_v5 import JevRouterV5
from evals.rule_layer_r2 import (
    CONTEXT, REPORT, CapturingClient, ReplayClient, comparison, query_for, rule_plan,
    select_sample,
)


def test_sample_has_fifty_distinct_source_backed_fast_path_hits():
    samples = select_sample()
    assert len(samples) == len({sample["query_sha256"] for sample in samples}) == 50
    assert all(rule_plan(query_for(sample)) is not None for sample in samples)


@pytest.mark.asyncio
async def test_capture_replay_requires_matching_request_and_preserves_answer():
    class Provider:
        async def decide(self, state, questions):
            return DecisionResponse(
                requested_model="jev-latest", response_model="fixture-only",
                evaluation_timestamp="2026-10-03T00:00:00Z", latency_ms=1, usage={},
                answers={"route": DecisionAnswer("choice", choice="general_chat", confidence=.91)},
            )

    capture = CapturingClient(Provider())
    expected = await capture.decide({"query": "fixture"}, {"route": object()})
    replay = ReplayClient(capture.calls)
    with pytest.raises(RuntimeError, match="missing_captured_answer"):
        await replay.decide({"query": "different"}, {"route": object()})
    assert await replay.decide({"query": "fixture"}, {"route": object()}) == expected
    with pytest.raises(RuntimeError, match="missing_captured_answer"):
        await replay.decide({"query": "fixture"}, {"route": object()})


def test_fallback_is_excluded_from_agreement_even_when_baseline_is_reused():
    baseline = rule_plan("暂停一下")
    trace = SimpleNamespace(
        router_source="legacy_hybrid_fallback", decision=baseline,
        fallback_reason="low_confidence",
    )
    assert comparison(baseline, trace) == {
        "comparable": False, "agrees": None, "attribution": "low_confidence",
    }


@pytest.mark.asyncio
async def test_fifty_archived_provider_cases_replay_through_v5_without_network():
    capture = json.loads((REPORT / "provider-capture.json").read_text(encoding="utf-8"))
    assert len(capture["samples"]) == len(capture["cases"]) == 50
    for sample, case in zip(capture["samples"], capture["cases"], strict=True):
        text = query_for(sample)
        baseline = rule_plan(text)
        client = ReplayClient(case["calls"])
        trace = await JevRouterV5(client).route_with_trace(
            text, CONTEXT, fallback_decision=baseline,
        )
        actual = comparison(baseline, trace)
        assert actual == {key: case["comparison"][key] for key in actual}
        assert len(client.used) == len(client.calls)

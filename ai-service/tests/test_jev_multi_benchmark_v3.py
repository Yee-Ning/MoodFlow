import pytest

from app.agent.decision.jev.client import DecisionAnswer, DecisionResponse
from evals.jev_multi_benchmark_v3 import (
    ROUTES, call_jev_multi, candidate_configs, multi_questions, operational_metrics, route_metrics,
    select,
)


def test_independent_heads_cover_each_route_with_contract_criteria():
    from app.agent.decision.jev.capability_criteria import router_criteria

    for mode in ("noul", "score"):
        questions = multi_questions(mode)
        assert list(questions) == list(ROUTES)
        for route, question in questions.items():
            assert router_criteria()[route] in str(question.criteria)


def test_selection_families_and_hard_negative_guard():
    scores = {"current_song": .72, "tool_calling": .08,
              "worldline": .03, "general_chat": .61}
    assert select(scores, {"family": "global_threshold", "threshold": .6}) == [
        "current_song", "general_chat"]
    assert select(scores, {"family": "per_route_threshold", "thresholds": {
        "current_song": .8, "tool_calling": .05, "worldline": .5, "general_chat": .7}}) == ["tool_calling"]
    assert select(scores, {"family": "top_k_min_score", "k": 2, "minimum_score": .6}) == [
        "current_song", "general_chat"]
    assert select(scores, {"family": "native_noul"}) == ["current_song", "general_chat"]
    assert select(None, {"family": "global_threshold", "threshold": .5}) == []
    with pytest.raises(ValueError):
        select({"current_song": 1.0}, {"family": "global_threshold", "threshold": .5})


def test_multi_metrics_count_over_under_and_route_count_error_separately():
    rows = [{"gold_routes": ["current_song", "tool_calling"]},
            {"gold_routes": ["general_chat"]},
            {"gold_routes": ["worldline", "general_chat"]}]
    metrics = route_metrics(rows, [["current_song"], ["general_chat", "worldline"],
                                   ["worldline", "tool_calling"]])
    assert metrics["exact_count"] == 0
    assert metrics["tp"] == 3
    assert metrics["fp"] == 2
    assert metrics["fn"] == 2
    assert metrics["over_routing_count"] == 2
    assert metrics["under_routing_count"] == 2
    assert metrics["route_count_error_count"] == 2
    assert metrics["micro_f1"] == .6


@pytest.mark.asyncio
async def test_score_and_noul_evidence_validation_preserves_each_route():
    class FakeClient:
        async def decide(self, state, questions):
            mode = next(iter(questions.values())).type
            answers = {route: (
                DecisionAnswer("noul", noul=.8 if route == "current_song" else .1)
                if mode == "noul" else
                DecisionAnswer("score", score=1.5, confidence=.5,
                               probabilities={"0": .1, "1": .3, "2": .6}))
                for route in questions}
            return DecisionResponse("jev-latest", "test", "2026-09-29T00:00:00Z",
                                    5.0, {"input_tokens": 4}, answers)

    noul = await call_jev_multi(FakeClient(), "分析当前这首歌", "noul")
    score = await call_jev_multi(FakeClient(), "分析当前这首歌", "score")
    assert noul["scores"] == {route: .8 if route == "current_song" else .1 for route in ROUTES}
    assert score["scores"] == {route: .6 for route in ROUTES}
    assert score["evidence"]["worldline"]["probabilities"]["2"] == .6
    assert score["error_category"] is None


@pytest.mark.asyncio
async def test_bad_score_schema_is_counted_as_parse_failure():
    class BadClient:
        async def decide(self, state, questions):
            return DecisionResponse("jev-latest", "test", "2026-09-29T00:00:00Z",
                                    5.0, None,
                                    {route: DecisionAnswer("score", score=1.0,
                                                           probabilities={"0": .5, "2": .5})
                                     for route in questions})

    result = await call_jev_multi(BadClient(), "query", "score")
    assert result["scores"] is None
    assert result["error_category"] == "parse_schema_failure"


def test_dev_candidates_include_all_four_selection_families():
    rows = []
    for index, gold in enumerate(ROUTES):
        scores = {route: .8 if route == gold else .1 for route in ROUTES}
        rows.append({"case_id": str(index), "gold_routes": [gold],
                     "score": {"scores": scores}, "noul": {"scores": scores}})
    families = {item["config"]["family"] for item in candidate_configs(rows)}
    assert families == {"global_threshold", "per_route_threshold",
                        "top_k_min_score", "native_noul"}


def test_operations_distinguish_api_timeout_from_schema_and_clarification():
    outputs = [
        {"wall_latency_ms": 5, "api_latency_ms": None, "api_calls": 1,
         "local_model_calls": 2, "usage": {}, "error_category": "api_failure",
         "error_type": "TimeoutError", "fallback_reason": "needs_clarification",
         "is_complete": False},
        {"wall_latency_ms": 7, "api_latency_ms": None, "api_calls": 0,
         "local_model_calls": 1, "usage": {}, "error_category": "parse_schema_failure",
         "error_type": "ValueError", "fallback_reason": "llm_escalation",
         "is_complete": True},
    ]
    metrics = operational_metrics(outputs, jev=False)
    assert metrics["api_failures"] == 1
    assert metrics["timeout_failures"] == 1
    assert metrics["parse_schema_failures"] == 1
    assert metrics["clarification_count"] == 1
    assert metrics["llm_escalation_count"] == 1
    assert metrics["incomplete_count"] == 1

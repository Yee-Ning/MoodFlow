"""V5 route-set and task-cardinality separation, safety and fallback tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.decision.jev.client import DecisionAnswer, DecisionResponse, JevDecisionError
from app.agent.decision.jev.router_v5 import JevRouterV5
from app.agent.decision.jev.shadow_v5 import snapshot
from app.agent.decision.jev.task_orchestrator_v5 import parse_task_clauses
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask, RoutingContext
from app.agent.foundation.state.main import create_main_state


def answer_response(answers):
    return DecisionResponse(
        requested_model="jev-latest", response_model="jev-test",
        evaluation_timestamp="2026-09-29T00:00:00+00:00", latency_ms=10,
        usage={"input_tokens": 10, "output_tokens": 2}, answers=answers,
    )


class Client:
    def __init__(self, fragment_routes, selected=None, *, confidence=.95):
        self.fragment_routes = fragment_routes
        self.selected = selected if selected is not None else set(fragment_routes)
        self.confidence = confidence
        self.calls = []

    async def decide(self, state, questions):
        self.calls.append((state, questions))
        if set(questions) == {"route"}:
            return answer_response({"route": DecisionAnswer(
                "choice", choice=self.fragment_routes[0], confidence=self.confidence,
            )})
        if set(questions) == {"current_song", "tool_calling", "worldline", "general_chat"}:
            return answer_response({route: DecisionAnswer(
                "noul", noul=.9 if route in self.selected else .05,
            ) for route in questions})
        return answer_response({name: DecisionAnswer(
            "choice", choice=self.fragment_routes[int(name.split("_")[-1])], confidence=.9,
        ) for name in questions})


@pytest.mark.asyncio
async def test_normal_single_has_one_frozen_choice_and_zero_classifier_calls():
    client = Client(["general_chat"])
    trace = await JevRouterV5(client).route_with_trace("为什么老唱片会有底噪？")
    assert trace.router_source == "jev_single"
    assert trace.classifier_hot_path_calls == 0
    assert len(client.calls) == 1
    assert trace.decision.tasks[0].route_key is RouteKey.GENERAL_CHAT
    assert RouteDecision.model_validate(trace.decision.model_dump()) == trace.decision


@pytest.mark.asyncio
@pytest.mark.parametrize("query,count", [
    ("先搜索《Creep》，然后播放它。", 2),
    ("先静音，再暂停，最后恢复播放。", 3),
    ("查看当前歌词并把音量调低。", 2),
])
async def test_same_route_actions_are_distinct_tasks(query, count):
    clauses = parse_task_clauses(query)
    assert len(clauses) == count
    client = Client(["tool_calling"] * count, selected={"tool_calling", "current_song"})
    trace = await JevRouterV5(client).route_with_trace(
        query, RoutingContext(has_current_song=True, current_song_id=21),
    )
    assert trace.router_source == "jev_multi"
    assert len(trace.decision.tasks) == count
    assert all(task.route_key is RouteKey.TOOL_CALLING for task in trace.decision.tasks)
    assert trace.classifier_hot_path_calls == 0
    assert trace.classifier_fallback_calls == 0
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_same_route_parallel_general_tasks_have_no_dependency():
    query = "分别解释节拍和拍号。"
    clauses = parse_task_clauses(query)
    assert len(clauses) == 2
    trace = await JevRouterV5(Client(["general_chat"] * 2)).route_with_trace(query)
    assert len(trace.decision.tasks) == 2
    assert all(not task.depends_on for task in trace.decision.tasks)


@pytest.mark.asyncio
async def test_multi_cross_route_order_and_current_song_context():
    trace = await JevRouterV5(Client(["tool_calling", "current_song"])).route_with_trace(
        "先播放《后来》，然后分析播出的歌。"
    )
    assert trace.decision.is_complete
    assert trace.decision.tasks[1].depends_on == [trace.decision.tasks[0].task_id]
    missing = await JevRouterV5(Client(["current_song"])).route_with_trace("分析当前这首歌。")
    assert missing.decision.needs_clarification and not missing.decision.tasks


@pytest.mark.asyncio
async def test_timeout_low_confidence_schema_failure_and_route_conflict_fallback():
    baseline = RouteDecision(is_complete=True, tasks=[RouteTask(
        task_id="legacy", route_key=RouteKey.GENERAL_CHAT, input_text="fallback",
    )])
    legacy = SimpleNamespace(route=AsyncMock(return_value=baseline))
    timeout = SimpleNamespace(decide=AsyncMock(side_effect=JevDecisionError("TypeSafeAPITimeoutError")))
    trace = await JevRouterV5(timeout, fallback_router=legacy).route_with_trace("解释旋律。")
    assert trace.router_source == "legacy_hybrid_fallback"
    assert trace.fallback_reason == "timeout"
    low = await JevRouterV5(Client(["general_chat"], confidence=.79), fallback_router=legacy).route_with_trace(
        "解释旋律。"
    )
    assert low.fallback_reason == "low_confidence"
    invalid = SimpleNamespace(decide=AsyncMock(return_value=answer_response({})))
    bad = await JevRouterV5(invalid, fallback_router=legacy).route_with_trace("解释旋律。")
    assert bad.fallback_reason == "schema_failure"
    conflict = await JevRouterV5(
        Client(["current_song", "tool_calling"], selected={"tool_calling"}),
        fallback_router=legacy,
    ).route_with_trace("分析这首歌，然后暂停。", RoutingContext(has_current_song=True))
    assert conflict.fallback_reason == "task_conflict"
    assert legacy.route.await_count == 4


@pytest.mark.asyncio
async def test_shadow_is_nonblocking_private_and_never_dispatches_candidate(caplog):
    gate = asyncio.Event()
    baseline = RouteDecision(needs_clarification=True, clarification_question="legacy answer")
    baseline_router = SimpleNamespace(route=AsyncMock(return_value=baseline))

    async def delayed(*args, **kwargs):
        await gate.wait()
        return await JevRouterV5(Client(["tool_calling"])).route_with_trace("播放秘密歌曲。")

    candidate = SimpleNamespace(route_with_trace=AsyncMock(side_effect=delayed))
    settings = SimpleNamespace(jev_enabled=True, jev_shadow_mode=True,
                               jev_router_v5_shadow_enabled=True,
                               jev_router_v4_shadow_enabled=False,
                               jev_router_confidence_threshold=.8)
    with patch("app.config.settings.get_settings", return_value=settings):
        graph = build_main_graph(intent_router=baseline_router, jev_v5_shadow_router=candidate).compile()
        result = await asyncio.wait_for(graph.ainvoke(create_main_state("播放秘密歌曲。")), timeout=2)
    assert result["final_response"] == "legacy answer"
    assert snapshot()["pending"] >= 1
    gate.set()
    await asyncio.sleep(.05)
    assert "秘密歌曲" not in caplog.text


def test_v5_gold_is_frozen_and_new():
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "evals/datasets"
    path = root / "router_e2e_blind_v5.jsonl"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "24d190d630614a611f19ac2ea1b7fc61cbf529b4222dfe826ec1b2b64f5e6daf"
    )
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 30 and len({row["case_id"] for row in rows}) == 30


@pytest.mark.asyncio
async def test_v5_flag_rolls_back_to_legacy_without_candidate():
    settings = SimpleNamespace(jev_enabled=True, jev_shadow_mode=True,
                               jev_router_v5_shadow_enabled=False,
                               jev_router_v4_shadow_enabled=False,
                               jev_router_enabled=False)
    baseline = RouteDecision(needs_clarification=True, clarification_question="legacy only")
    legacy = SimpleNamespace(route=AsyncMock(return_value=baseline))
    candidate = SimpleNamespace(route_with_trace=AsyncMock())
    with patch("app.config.settings.get_settings", return_value=settings):
        graph = build_main_graph(intent_router=legacy, jev_v5_shadow_router=candidate).compile()
        result = await graph.ainvoke(create_main_state("解释后摇。"))
    assert result["final_response"] == "legacy only"
    candidate.route_with_trace.assert_not_awaited()

"""Decision construction and safety boundaries for the opt-in V4 candidate."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.decision.jev.capability_criteria import ROUTER_QUESTION_INSTRUCTIONS, router_criteria
from app.agent.decision.jev.client import DecisionAnswer, DecisionResponse, JevDecisionError
from app.agent.decision.jev.router_v4 import JevRouterCandidate, frozen_thresholds, split_clauses
from app.agent.decision.jev.telemetry import record_v4_shadow, record_v4_shadow_failure
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask, RoutingContext
from app.agent.foundation.state.main import create_main_state


def response(answers: dict[str, DecisionAnswer]) -> DecisionResponse:
    return DecisionResponse(
        requested_model="jev-latest", response_model="jev-test",
        evaluation_timestamp="2026-09-29T00:00:00+00:00", latency_ms=12,
        usage={"input_tokens": 50, "output_tokens": 5}, answers=answers,
    )


class FakeClient:
    def __init__(self, labels: list[str], selected: set[str] | None = None) -> None:
        self.labels = labels
        self.selected = selected or set(labels)
        self.calls = []

    async def decide(self, state, questions):
        self.calls.append((state, questions))
        if set(questions) == {"route"}:
            return response({"route": DecisionAnswer("choice", choice=self.labels[0], confidence=.93)})
        if set(questions) == set(frozen_thresholds()):
            return response({name: DecisionAnswer("noul", noul=.9 if name in self.selected else .05)
                             for name in questions})
        return response({name: DecisionAnswer("choice", choice=self.labels[int(name.rsplit("_", 1)[1])], confidence=.88)
                         for name in questions})


@pytest.mark.asyncio
async def test_single_preserves_frozen_choice_question_and_main_graph_schema():
    client = FakeClient(["general_chat"])
    trace = await JevRouterCandidate(client).route_with_trace("为什么现场声音有空间感？")
    state, questions = client.calls[0]
    assert state == {"query": "为什么现场声音有空间感？"}
    assert questions["route"].instructions == ROUTER_QUESTION_INSTRUCTIONS
    assert questions["route"].criteria == router_criteria()
    assert trace.router_source == "jev_single"
    assert trace.api_calls == 1
    assert trace.decision.is_complete
    assert trace.decision.tasks[0].input_text == "为什么现场声音有空间感"
    assert RouteDecision.model_validate(trace.decision.model_dump()) == trace.decision


@pytest.mark.asyncio
async def test_multi_uses_frozen_noul_and_constructs_ordered_tasks():
    client = FakeClient(["current_song", "tool_calling"])
    trace = await JevRouterCandidate(client).route_with_trace(
        "先解读正在播放的歌，然后收藏它。",
        RoutingContext(has_current_song=True, current_song_id=42),
    )
    assert trace.router_source == "jev_multi"
    assert trace.api_calls == 2
    assert [task.route_key for task in trace.decision.tasks] == [RouteKey.CURRENT_SONG, RouteKey.TOOL_CALLING]
    assert [task.input_text for task in trace.decision.tasks] == ["先解读正在播放的歌", "收藏它"]
    assert trace.decision.tasks[1].depends_on == [trace.decision.tasks[0].task_id]
    head_call = next(questions for _, questions in client.calls
                     if set(questions) == set(frozen_thresholds()))
    from evals.jev_multi_benchmark_v3 import multi_questions
    frozen_heads = multi_questions("noul")
    assert {name: (head_call[name].instructions, head_call[name].criteria)
            for name in head_call} == {
                name: (frozen_heads[name].instructions, frozen_heads[name].criteria)
                for name in frozen_heads
            }
    assert RouteDecision.model_validate(trace.decision.model_dump()) == trace.decision


@pytest.mark.asyncio
async def test_parallel_clauses_and_same_route_actions():
    parallel = await JevRouterCandidate(FakeClient(["worldline", "general_chat"])).route_with_trace(
        "探索王菲的影响关系；另外解释什么是切分音。"
    )
    assert len(parallel.decision.tasks) == 2
    assert parallel.decision.tasks[1].depends_on == []
    sequence = await JevRouterCandidate(FakeClient(["tool_calling", "tool_calling"])).route_with_trace(
        "先暂停音乐，然后调低音量。"
    )
    assert len(sequence.decision.tasks) == 2
    assert sequence.decision.tasks[1].depends_on == [sequence.decision.tasks[0].task_id]


@pytest.mark.asyncio
async def test_missing_context_negation_and_unsupported_are_safe():
    missing = await JevRouterCandidate(FakeClient(["current_song"])).route_with_trace("这首歌的情绪是什么？")
    assert missing.decision.needs_clarification and not missing.decision.tasks
    negated = await JevRouterCandidate(FakeClient(["general_chat"])).route_with_trace(
        "不要搜索音乐，解释什么是节拍。"
    )
    assert [task.route_key for task in negated.decision.tasks] == [RouteKey.GENERAL_CHAT]
    assert "搜索" not in negated.decision.tasks[0].input_text
    unsupported = await JevRouterCandidate(FakeClient(["worldline"])).route_with_trace(
        "如果某专辑从未发行，音乐史会怎样？"
    )
    assert unsupported.decision.needs_clarification and not unsupported.decision.tasks


@pytest.mark.asyncio
async def test_unanchored_person_reference_clarifies_before_worldline_dispatch():
    trace = await JevRouterCandidate(FakeClient(["worldline"])).route_with_trace(
        "看看他与谁存在有据的音乐关联。"
    )
    assert trace.decision.needs_clarification
    assert trace.decision.tasks == []

    anchored = await JevRouterCandidate(FakeClient(["worldline"])).route_with_trace(
        "探索某位音乐人与其他作品的有据联系。"
    )
    assert anchored.decision.is_complete


def test_comma_plus_temporal_adverb_keeps_order_without_verb_dictionary():
    clauses = split_clauses("搜索某乐队的专辑，再梳理其关系。")
    assert [clause.text for clause in clauses] == ["搜索某乐队的专辑", "梳理其关系"]
    assert clauses[1].sequential


@pytest.mark.asyncio
async def test_named_play_can_supply_song_context_to_later_analysis():
    trace = await JevRouterCandidate(FakeClient(["tool_calling", "current_song"])).route_with_trace(
        "先播放《海阔天空》，然后分析正在播放的歌。"
    )
    assert trace.decision.is_complete
    assert trace.decision.tasks[1].depends_on == [trace.decision.tasks[0].task_id]


@pytest.mark.asyncio
async def test_route_fragment_conflict_and_api_failure_use_hybrid_only_then():
    baseline = RouteDecision(is_complete=True, tasks=[RouteTask(
        task_id="base", route_key=RouteKey.GENERAL_CHAT, input_text="fallback",
    )])
    fallback = SimpleNamespace(route=AsyncMock(return_value=baseline))
    client = FakeClient(["current_song", "tool_calling"], selected={"current_song"})
    trace = await JevRouterCandidate(client, fallback_router=fallback).route_with_trace(
        "分析音乐，然后收藏它。", RoutingContext(has_current_song=True)
    )
    assert trace.router_source == "hybrid_fallback"
    assert trace.fallback_reason == "route_fragment_conflict"
    fallback.route.assert_awaited_once()

    low = SimpleNamespace(decide=AsyncMock(return_value=response({
        "route": DecisionAnswer("choice", choice="general_chat", confidence=.79),
    })))
    fallback.route.reset_mock()
    low_trace = await JevRouterCandidate(low, fallback_router=fallback).route_with_trace("聊聊音乐。")
    assert low_trace.router_source == "hybrid_fallback"
    assert low_trace.fallback_reason == "low_choice_confidence"
    fallback.route.assert_awaited_once()
    assert trace.decision == baseline

    broken = SimpleNamespace(decide=AsyncMock(side_effect=JevDecisionError("TimeoutError")))
    shared_baseline = asyncio.create_task(asyncio.sleep(0, result=baseline))
    fallback.route.reset_mock()
    trace = await JevRouterCandidate(broken, fallback_router=fallback).route_with_trace(
        "说说音乐。", fallback_decision=shared_baseline,
    )
    assert trace.decision == baseline
    fallback.route.assert_not_awaited()

    fallback.route.reset_mock()
    trace = await JevRouterCandidate(broken, fallback_router=fallback).route_with_trace("说说音乐。")
    assert trace.router_source == "hybrid_fallback"
    assert trace.fallback_reason == "TimeoutError"
    fallback.route.assert_awaited_once()


def test_syntax_split_is_not_based_on_route_keywords():
    clauses = split_clauses("甲的解释，并看看乙的关系；最后再聊丙。")
    assert [item.text for item in clauses] == ["甲的解释", "看看乙的关系", "再聊丙"]
    assert [item.sequential for item in clauses] == [False, False, True]


def test_v3_multi_thresholds_are_semantically_frozen():
    assert frozen_thresholds() == {
        "current_song": .30, "tool_calling": .25,
        "worldline": .45, "general_chat": .50,
    }


def test_frozen_v4_gold_has_grounded_tasks_and_no_v3_query_overlap():
    dataset_dir = Path(__file__).resolve().parents[1] / "evals/datasets"
    rows = [json.loads(line) for line in (dataset_dir / "router_e2e_blind_v4.jsonl")
            .read_text(encoding="utf-8").splitlines()]
    earlier = {json.loads(line)["query"]
               for name in ("multi_dev_v3.jsonl", "multi_blind_test_v3.jsonl", "single_blind_v3.jsonl")
               for line in (dataset_dir / name).read_text(encoding="utf-8").splitlines()}
    assert len(rows) == 24 and len({row["case_id"] for row in rows}) == 24
    assert not {row["query"] for row in rows} & earlier
    for row in rows:
        assert set(row["expected_routes"]) == {task["route"] for task in row["expected_tasks"]}
        assert row["expected_clarify"] == (not row["expected_tasks"])
        for index, task in enumerate(row["expected_tasks"]):
            assert task["fragment"] in row["query"]
            assert all(prior < index for prior in task["depends_on"])


def test_shadow_telemetry_omits_query_and_fragments(caplog):
    from app.agent.decision.jev.router_v4 import CandidateTrace

    baseline = RouteDecision(is_complete=True, tasks=[RouteTask(
        task_id="base", route_key=RouteKey.GENERAL_CHAT, input_text="private phrase",
    )])
    trace = CandidateTrace(
        decision=baseline, router_source="jev_single", fallback_reason=None,
        latency_ms=15, api_calls=1, usage={}, response_model="jev-test",
        scores={"general_chat": .9}, confidence=.9,
    )
    with caplog.at_level("INFO"):
        record_v4_shadow(baseline, trace)
    event = json.loads(caplog.records[-1].message.split(" ", 1)[1])
    assert event["candidate_routes"] == ["general_chat"]
    assert "private phrase" not in caplog.text
    record_v4_shadow_failure("TimeoutError")
    assert "TimeoutError" in caplog.text


@pytest.mark.asyncio
async def test_main_graph_shadow_uses_only_baseline_decision():
    baseline = RouteDecision(needs_clarification=True, clarification_question="baseline clarification")
    router = SimpleNamespace(route=AsyncMock(return_value=baseline))
    candidate = SimpleNamespace(route_with_trace=AsyncMock(return_value=SimpleNamespace(
        decision=RouteDecision(is_complete=True, tasks=[RouteTask(
            task_id="danger", route_key=RouteKey.TOOL_CALLING, input_text="播放音乐",
        )]), router_source="jev_single", fallback_reason=None, response_model="jev-test",
        latency_ms=1, api_calls=1, usage={}, scores={}, confidence=.9,
    )))
    settings = SimpleNamespace(jev_enabled=True, jev_shadow_mode=True,
                               jev_router_v4_shadow_enabled=False)
    with patch("app.config.settings.get_settings", return_value=settings):
        graph = build_main_graph(intent_router=router, jev_v4_shadow_router=candidate).compile()
        result = await graph.ainvoke(create_main_state("播放音乐"))
    assert result["final_response"] == "baseline clarification"
    assert result["route_decision"].needs_clarification
    candidate.route_with_trace.assert_awaited_once()

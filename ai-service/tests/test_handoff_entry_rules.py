"""Local entry safety, complete rule plans, and bounded fallback semantics."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage

from app.agent.decision.jev.client import JevDecisionError
from app.agent.foundation.routing import IntentSource, RouteDecision, RouteKey, RoutingContext
from app.agent.foundation.hybrid_routing import HybridIntentRouter
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.routing import HandoffEntryRouter, LazyHybridRouter
from app.agent.handoff.state import new_run
from app.llm.runtime import RuntimeLimitError
from tests.test_handoff_jev_routing import client
from tests.test_handoff_runtime import Router, ToolModel, call, input_state, player_context


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [True, False])
@pytest.mark.parametrize("text,routes", [
    ("暂停一下", [RouteKey.TOOL_CALLING]),
    ("先暂停播放，然后陪我聊聊", [RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT]),
    ("先探索周杰伦的音乐世界线，然后暂停播放", [RouteKey.WORLDLINE, RouteKey.TOOL_CALLING]),
    ("先暂停播放，然后恢复播放", [RouteKey.TOOL_CALLING, RouteKey.TOOL_CALLING]),
    ("不要订机票，暂停播放", [RouteKey.TOOL_CALLING]),
    ("不要暂停播放，然后陪我聊聊", [RouteKey.GENERAL_CHAT]),
])
async def test_complete_rules_short_circuit_models_and_preserve_task_contract(shadow, text, routes):
    baseline, probe = AsyncMock(), client()
    plan = await HandoffEntryRouter(baseline, probe, shadow=shadow).route(text, player_context())
    assert plan.is_complete
    assert [task.route_key for task in plan.tasks] == routes
    assert all(intent.source is IntentSource.RULE for intent in plan.intents)
    for index, task in enumerate(plan.tasks):
        assert task.depends_on == ([plan.tasks[index - 1].task_id] if index else [])
        assert "不要" not in task.input_text
    run = new_run("entry-test", text, plan, started_at=datetime.now(timezone.utc))
    assert run.status.value == "running"
    baseline.route.assert_not_awaited()
    probe.decide.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text,phrase", [
    ("", "告诉我"), ("  ", "告诉我"), (None, "告诉我"),
    ("a" * 4001, "4000"), ("不要暂停播放", "已否定"),
    ("don't play music", "已否定"),
    ("暂停播放，然后订机票", "音乐相关能力"),
    ("如果周杰伦从未出道，探索他的音乐世界线", "未发生"),
    ("分析当前这首歌", "没有正在播放"),
    ("收藏这首歌", "没有可操作"),
])
async def test_preflight_and_rule_context_checks_clarify_without_models(text, phrase):
    baseline, probe = AsyncMock(), client()
    plan = await HandoffEntryRouter(baseline, probe, shadow=False).route(text, RoutingContext())
    assert plan.needs_clarification and not plan.tasks
    assert phrase in plan.clarification_question
    baseline.route.assert_not_awaited()
    probe.decide.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_rule_coverage_preserves_all_fragments_for_v5():
    baseline, probe = AsyncMock(), client()
    adapter = HandoffEntryRouter(baseline, probe, shadow=False)
    candidate = AsyncMock(wraps=adapter.candidate.route_with_trace)
    adapter.candidate.route_with_trace = candidate
    text = "暂停播放；解释什么是转调"
    # The probe intentionally cannot answer Multi: the untouched V5 exception
    # path must delegate the entire original request exactly once to Hybrid.
    baseline.route.side_effect = Router(RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT).route
    plan = await adapter.route(text, player_context())
    assert plan.is_complete
    assert [task.input_text for task in plan.tasks] == ["暂停播放", "解释什么是转调"]
    candidate.assert_awaited_once_with(text, player_context(), fallback_decision=None)
    baseline.route.assert_awaited_once_with(text, player_context())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError(), JevDecisionError("timeout")])
async def test_v5_failures_use_hybrid_once(failure):
    baseline = AsyncMock(wraps=Router(RouteKey.GENERAL_CHAT))
    probe = client()
    probe.decide.side_effect = failure
    plan = await HandoffEntryRouter(baseline, probe, shadow=False).route(
        "老唱片为什么会有底噪", RoutingContext(),
    )
    assert plan.tasks[0].route_key is RouteKey.GENERAL_CHAT
    assert baseline.route.await_count == probe.decide.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [True, False])
@pytest.mark.parametrize("result", [
    RuntimeError("hybrid unavailable"), RouteDecision(), RouteDecision(is_complete=True),
    RouteDecision(needs_clarification=True, clarification_question="请指明歌曲。"),
])
async def test_no_hybrid_plan_clarifies_without_duplicate_fallback(shadow, result):
    baseline, probe = AsyncMock(), client()
    if isinstance(result, Exception):
        baseline.route.side_effect = result
    else:
        baseline.route.return_value = result
    probe.decide.side_effect = JevDecisionError("timeout")
    plan = await HandoffEntryRouter(baseline, probe, shadow=shadow).route(
        "老唱片为什么会有底噪", RoutingContext(),
    )
    assert plan.needs_clarification and plan.clarification_question and not plan.tasks
    assert baseline.route.await_count == probe.decide.await_count == 1
    if isinstance(result, RouteDecision) and result.clarification_question:
        assert plan.clarification_question == result.clarification_question


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeLimitError("token_limit"), asyncio.CancelledError()])
async def test_fallback_respects_parent_budget_and_cancellation(failure):
    baseline, probe = AsyncMock(), client()
    probe.decide.side_effect = JevDecisionError("timeout")
    baseline.route.side_effect = failure
    with pytest.raises(type(failure)):
        await HandoffEntryRouter(baseline, probe, shadow=False).route(
            "老唱片为什么会有底噪", RoutingContext(),
        )
    baseline.route.assert_awaited_once()


@pytest.mark.asyncio
async def test_rules_do_not_load_hybrid_when_v5_disabled():
    factory = Mock(return_value=Router(RouteKey.GENERAL_CHAT))
    adapter = HandoffEntryRouter(LazyHybridRouter(factory))
    assert (await adapter.route("暂停一下", RoutingContext())).is_complete
    factory.assert_not_called()
    assert (await adapter.route("老唱片为什么会有底噪", RoutingContext())).is_complete
    await adapter.route("节拍有什么作用", RoutingContext())
    factory.assert_called_once()


@pytest.mark.asyncio
async def test_hybrid_cannot_reintroduce_negated_fragment():
    text = "请勿暂停播放，然后陪我聊聊天好吗"
    baseline = AsyncMock()
    # This is the legacy failure shape: both task spans belong to the original
    # input, but the first belongs to a clause explicitly excluded by preflight.
    baseline.route.return_value = await Router(RouteKey.TOOL_CALLING).route(text, RoutingContext())
    probe = client()
    probe.decide.side_effect = JevDecisionError("timeout")
    adapter = HandoffEntryRouter(baseline, probe, shadow=False)
    adapter.rules = Mock(wraps=adapter.rules)
    adapter.rules.route.return_value = RouteDecision()
    plan = await adapter.route(text, RoutingContext())
    assert plan.needs_clarification and not plan.tasks
    baseline.route.assert_awaited_once()


@pytest.mark.asyncio
async def test_v5_failure_reaches_real_legacy_hybrid_rule_path():
    model, llm = Mock(), AsyncMock()
    hybrid = HybridIntentRouter(model_router=model, llm_router=llm)
    probe = client()
    probe.decide.side_effect = JevDecisionError("timeout")
    adapter = HandoffEntryRouter(hybrid, probe, shadow=False)
    # Force a local fast-path miss to exercise the real Hybrid implementation,
    # while retaining its own RuleRouter and task builder without substitutes.
    adapter.rules = Mock()
    adapter.rules.route.return_value = RouteDecision()
    plan = await adapter.route("暂停一下", RoutingContext())
    assert plan.is_complete and plan.tasks[0].route_key is RouteKey.TOOL_CALLING
    model.route.assert_not_called()
    llm.route.assert_not_awaited()
    probe.decide.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [None, "", " " * 5, "a" * 4001, "不要播放", "播放音乐然后订机票"])
async def test_preflight_ends_graph_without_transfer_or_agent_execution(text):
    baseline, probe = AsyncMock(), client()
    models = AsyncMock()
    adapter = HandoffEntryRouter(baseline, probe, shadow=False)
    state = input_state()
    state["current_input"] = text
    out = await build_handoff_graph(
        intent_router=adapter, general_model=models, transfer_model=models,
        curator_model=models, song_model=models,
    ).compile().ainvoke(state)
    assert out["handoff_needs_input"] and not out["handoff_failed"]
    assert out["final_response"] and not out["transfer_envelope"]
    if out["handoff_run"]:
        assert out["handoff_run"]["status"] == "needs_input"
    models.ainvoke.assert_not_awaited()
    models.bind_tools.assert_not_called()
    baseline.route.assert_not_awaited()
    probe.decide.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_handoff_graph_builds_rule_plan_without_loading_classifier(monkeypatch):
    factory = Mock(side_effect=AssertionError("classifier must remain unloaded"))
    monkeypatch.setattr("app.agent.handoff.graph.create_hybrid_router", factory)
    out = await build_handoff_graph(
        transfer_model=ToolModel(responses=[call()]),
        curator_model=ToolModel(responses=[AIMessage(content="已完成。")]),
    ).compile().ainvoke(input_state())
    assert out["handoff_run"]["status"] == "completed"
    plan = out["handoff_run"]["plan"]
    assert plan["intents"][0]["source"] == "rule"
    assert plan["intents"][0]["sub_intent"] == "pause_music"
    assert out["handoff_run"]["results"][0]["task_id"] == plan["tasks"][0]["task_id"]
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_input_at_limit_can_reach_v5():
    probe = client()
    baseline = AsyncMock()
    plan = await HandoffEntryRouter(baseline, probe, shadow=False).route("a" * 4000, RoutingContext())
    assert plan.is_complete and plan.tasks[0].input_text == "a" * 4000
    probe.decide.assert_awaited_once()
    baseline.route.assert_not_awaited()

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langgraph.graph import END, START, StateGraph

from app.agent.foundation import create_main_graph, create_main_state, RouteKey, RoutingContext
from app.agent.foundation.hybrid_routing import HybridIntentRouter, EscalationPolicy
from app.agent.foundation.llm_routing import LLMRouter
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import IntentType
from app.agent.foundation.subgraphs.general_chat import GeneralChatState
from app.agent.foundation.subgraphs.base import SubGraphOutput
from tests.test_intent_merger import model_result
from tests.test_llm_router import TAXONOMY, output_for


def fixture_router(model=None, classify=None):
    backend = model or Mock(side_effect=lambda text: model_result(text))
    runtime = SimpleNamespace(classify=classify or AsyncMock(side_effect=AssertionError("LLM should not be called")))
    router = HybridIntentRouter(SimpleNamespace(route=backend), LLMRouter(TAXONOMY, runtime=runtime))
    return router, backend, runtime


class HybridGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_graph_uses_dynamic_router_factory(self):
        router, _, _ = fixture_router()
        with patch("app.agent.foundation.main_graph.create_hybrid_router", return_value=router) as factory:
            graph = build_main_graph().compile()
        self.assertEqual(factory.call_count, 1)
        self.assertIn("intent_router", graph.get_graph().nodes)
        self.assertNotIn("development_route", graph.get_graph().nodes)
        result = await graph.ainvoke(create_main_state("暂停一下"))
        self.assertTrue(result["route_decision"].is_complete)

    async def test_rule_complete_skips_model_and_llm(self):
        router, model, runtime = fixture_router()
        decision = await router.route("暂停一下")
        model.assert_not_called()
        runtime.classify.assert_not_awaited()
        self.assertEqual(decision.tasks[0].route_key, RouteKey.TOOL_CALLING)

    async def test_failed_task_stops_later_dispatch_and_reports_one_error(self):
        def fail(state):
            return {"task_result": TaskResult(
                task_id=state["task_id"], route_key=RouteKey.TOOL_CALLING,
                status=TaskStatus.FAILED, error="fixture failure",
            )}
        sub = StateGraph(GeneralChatState, output_schema=SubGraphOutput)
        sub.add_node("fail", fail)
        sub.add_edge(START, "fail")
        sub.add_edge("fail", END)
        router, _, _ = fixture_router()
        result = await build_main_graph(intent_router=router, tool_calling_subgraph=sub.compile()).compile().ainvoke(
            create_main_state("暂停一下，然后讲讲这首歌"))
        self.assertEqual(len(result["task_results"]), 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("执行失败", result["final_response"])

    async def test_earlier_thread_results_do_not_leak_into_clarification(self):
        async def classify(request, **kwargs):
            return output_for(request, (), clarification=True)
        router, _, _ = fixture_router(
            model=Mock(side_effect=lambda text: model_result(text, ())),
            classify=AsyncMock(side_effect=classify),
        )
        graph = create_main_graph(intent_router=router)
        config = {"configurable": {"thread_id": "stale"}}
        await graph.ainvoke(create_main_state("暂停一下"), config)
        result = await graph.ainvoke(create_main_state("阿巴阿巴"), config)
        self.assertEqual(result["route_decision"].tasks, [])
        self.assertIn("补充", result["final_response"])
        self.assertNotIn("ToolCalling", result["final_response"])

    async def test_rule_model_graph_dispatches_in_query_order_and_preserves_context(self):
        router, model, runtime = fixture_router()
        graph = create_main_graph(intent_router=router)
        state = create_main_state("暂停一下，然后讲讲这首歌", routing_context=RoutingContext(has_current_song=True, current_song_id=1))
        result = await graph.ainvoke(state, {"configurable": {"thread_id": "hybrid"}})
        self.assertEqual([r.route_key for r in result["task_results"]], [RouteKey.TOOL_CALLING, RouteKey.CURRENT_SONG])
        self.assertIn("ToolCalling", result["final_response"])
        self.assertIn("CurrentSong", result["final_response"])
        model.assert_called_once_with("讲讲这首歌")
        runtime.classify.assert_not_awaited()
        self.assertEqual(result["routing_context"], state["routing_context"])

    async def test_rejected_model_llm_multilabel_reaches_main_graph(self):
        async def classify(request, **kwargs):
            return output_for(request, ("current_song", "tool_calling"))
        router, _, runtime = fixture_router(
            model=Mock(side_effect=lambda text: model_result(text, ())),
            classify=AsyncMock(side_effect=classify),
        )
        # No rule match here: the fixture exercises one LLM-owned multi-intent segment.
        result = await build_main_graph(intent_router=router).compile().ainvoke(create_main_state("谈谈配器并把它纳入我的歌单"))
        self.assertEqual(len(result["route_decision"].intents), 2)
        self.assertEqual(len(result["task_results"]), 2)
        self.assertTrue(all(i.source.value == "llm" for i in result["route_decision"].intents))
        runtime.classify.assert_awaited_once()

    async def test_unclear_segment_blocks_even_recognized_rule_tasks(self):
        async def classify(request, **kwargs):
            return output_for(request, (), clarification=True)
        router, _, _ = fixture_router(
            model=Mock(side_effect=lambda text: model_result(text, ())),
            classify=AsyncMock(side_effect=classify),
        )
        result = await build_main_graph(intent_router=router).compile().ainvoke(create_main_state("暂停一下，然后阿巴阿巴"))
        self.assertTrue(result["route_decision"].needs_clarification)
        self.assertEqual(result["route_decision"].intents[0].intent, IntentType.TOOL_CALLING)
        self.assertEqual(result["task_results"], [])
        self.assertIn("补充", result["final_response"])

    async def test_general_chat_has_real_graph_destination(self):
        router, _, _ = fixture_router(model=Mock(side_effect=lambda text: model_result(text, (IntentType.GENERAL_CHAT,))))
        result = await build_main_graph(intent_router=router).compile().ainvoke(create_main_state("什么叫概念专辑"))
        self.assertEqual(result["task_results"][0].route_key, RouteKey.GENERAL_CHAT)
        self.assertIn("GeneralChat", result["final_response"])

    async def test_network_error_propagates_without_fake_clarification(self):
        router, _, _ = fixture_router(
            model=Mock(side_effect=lambda text: model_result(text, ())),
            classify=AsyncMock(side_effect=TimeoutError("fixture")),
        )
        with self.assertRaises(TimeoutError):
            await build_main_graph(intent_router=router).compile().ainvoke(create_main_state("阿巴阿巴"))

    async def test_same_thread_repeated_requests_do_not_skip_or_reuse_tasks(self):
        router, _, _ = fixture_router()
        graph = create_main_graph(intent_router=router)
        config = {"configurable": {"thread_id": "repeat"}}
        first = await graph.ainvoke(create_main_state("暂停一下"), config)
        second = await graph.ainvoke(create_main_state("暂停一下"), config)
        self.assertNotEqual(first["route_decision"].tasks[0].task_id, second["route_decision"].tasks[0].task_id)
        self.assertEqual(len(second["task_results"]), 2)
        self.assertEqual(second["final_response"].count("ToolCalling"), 1)

    async def test_subgraph_receives_only_its_assigned_evidence(self):
        seen = []
        def run(state):
            seen.append(state)
            return {"task_result": TaskResult(task_id=state["task_id"], route_key=RouteKey.CURRENT_SONG,
                                             status=TaskStatus.SUCCESS, content="fixture")}
        sub = StateGraph(GeneralChatState, output_schema=SubGraphOutput)
        sub.add_node("capture", run)
        sub.add_edge(START, "capture")
        sub.add_edge("capture", END)
        router, _, _ = fixture_router()
        result = await build_main_graph(intent_router=router, current_song_subgraph=sub.compile()).compile().ainvoke(
            create_main_state("暂停一下，然后讲讲这首歌"))
        self.assertEqual(seen[0]["current_input"], "讲讲这首歌")
        self.assertEqual(seen[0]["routing_intents"][0].intent, IntentType.CURRENT_SONG)
        self.assertNotIn("routing_intents", result)

    async def test_configurable_boundary_escalation_does_not_change_thresholds(self):
        model = model_result("说说它", (IntentType.CURRENT_SONG,))
        thresholds = {label: 0.79 for label in model.scores}
        policy = EscalationPolicy(boundary_margin=0.02)
        self.assertEqual(policy.reason(model, thresholds), "near_threshold")
        self.assertIsNone(EscalationPolicy().reason(model, thresholds))
        self.assertEqual(thresholds["CURRENT_SONG"], 0.79)

    async def test_invalid_input_never_calls_models(self):
        router, model, _ = fixture_router()
        for text in ("  ", "字" * 4001):
            with self.assertRaises(ValueError):
                await router.route(text)
        model.assert_not_called()

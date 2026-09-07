import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.agent.foundation import create_main_graph, create_main_state, RouteKey, RoutingContext
from app.agent.foundation.hybrid_routing import HybridIntentRouter, EscalationPolicy
from app.agent.foundation.llm_routing import LLMRouter
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    RouteDecision,
    RouteTask,
)
from app.agent.foundation.subgraphs.general_chat import (
    GeneralChatState,
    create_general_chat_subgraph,
)
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState
from app.agent.checkpointing import create_memory_checkpointer
from tests.test_intent_merger import model_result
from tests.test_llm_router import TAXONOMY, output_for


def fixture_router(model=None, classify=None):
    backend = model or Mock(side_effect=lambda text: model_result(text))
    runtime = SimpleNamespace(classify=classify or AsyncMock(side_effect=AssertionError("LLM should not be called")))
    router = HybridIntentRouter(SimpleNamespace(route=backend), LLMRouter(TAXONOMY, runtime=runtime))
    return router, backend, runtime


def successful_subgraph(route: RouteKey, content: str = "fixture"):
    def complete(state):
        return {"task_result": TaskResult(
            task_id=state["task_id"],
            route_key=route,
            status=TaskStatus.SUCCESS,
            content=content,
        )}
    sub = StateGraph(GeneralChatState, output_schema=SubGraphOutput)
    sub.add_node("complete", complete)
    sub.add_edge(START, "complete")
    sub.add_edge("complete", END)
    return sub.compile()


class HybridGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_previous_tool_entities_reach_next_turn(self):
        seen_prior_entities = []
        route_count = 0

        class ToolRouter:
            async def route(self, text, context):
                nonlocal route_count
                route_count += 1
                intent = IntentItem(
                    intent=IntentType.TOOL_CALLING,
                    sub_intent="manage_music",
                    confidence=1.0,
                    source=IntentSource.LLM,
                    evidence=text,
                    start=0,
                    end=len(text),
                    order=0,
                )
                return RouteDecision(
                    intents=[intent],
                    is_complete=True,
                    coverage=1.0,
                    tasks=[
                        RouteTask(
                            task_id=f"tool-{route_count}",
                            route_key=RouteKey.TOOL_CALLING,
                            input_text=text,
                            intent_orders=[0],
                        )
                    ],
                )

        def capture(state):
            seen_prior_entities.append(state["prior_entities"])
            return {
                "task_result": TaskResult(
                    task_id=state["task_id"],
                    route_key=RouteKey.TOOL_CALLING,
                    status=TaskStatus.SUCCESS,
                    content="fixture",
                    data={
                        "entities": {
                            "songs": [
                                {
                                    "position": 1,
                                    "song_id": 101,
                                    "name": "测试歌曲",
                                    "artists": ["测试歌手"],
                                    "album": "",
                                    "cover_url": "",
                                    "duration": 0,
                                }
                            ],
                            "playlists": [],
                        }
                    },
                )
            }

        subgraph = StateGraph(ToolCallingState, output_schema=SubGraphOutput)
        subgraph.add_node("capture", capture)
        subgraph.add_edge(START, "capture")
        subgraph.add_edge("capture", END)
        graph = build_main_graph(
            intent_router=ToolRouter(),
            tool_calling_subgraph=subgraph.compile(),
        ).compile(checkpointer=create_memory_checkpointer())
        config = {"configurable": {"thread_id": "prior-entities"}}

        await graph.ainvoke(create_main_state("先推荐一首"), config)
        await graph.ainvoke(create_main_state("使用刚才第一首"), config)

        self.assertEqual(seen_prior_entities[0], [])
        self.assertEqual(seen_prior_entities[1][0].songs[0].song_id, 101)

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
        self.assertIn("暂停指令", result["final_response"])
        self.assertIn("CurrentSong", result["final_response"])
        model.assert_called_once_with("讲讲这首歌")
        runtime.classify.assert_not_awaited()
        self.assertEqual(result["routing_context"], state["routing_context"])

    async def test_rejected_model_llm_multilabel_reaches_main_graph(self):
        async def classify(request, **kwargs):
            tool_start = request.segment.text.index("把它")
            return json.dumps(
                {
                    "intents": [
                        {
                            "intent": "current_song",
                            "confidence": 0.8,
                            "evidence": "谈谈配器并",
                            "start": 0,
                            "end": 5,
                        },
                        {
                            "intent": "tool_calling",
                            "confidence": 0.8,
                            "evidence": request.segment.text[tool_start:],
                            "start": tool_start,
                            "end": len(request.segment.text),
                        },
                    ],
                    "needs_clarification": False,
                },
                ensure_ascii=False,
            )
        router, _, runtime = fixture_router(
            model=Mock(side_effect=lambda text: model_result(text, ())),
            classify=AsyncMock(side_effect=classify),
        )
        # No rule match here: the fixture exercises one LLM-owned multi-intent segment.
        result = await build_main_graph(
            intent_router=router,
            tool_calling_subgraph=successful_subgraph(
                RouteKey.TOOL_CALLING,
                "tool fixture",
            ),
        ).compile().ainvoke(create_main_state("谈谈配器并把它纳入我的歌单"))
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

        async def assistant(*args):
            return AIMessage(content="概念专辑会围绕统一主题组织歌曲。")

        result = await build_main_graph(
            intent_router=router,
            general_chat_subgraph=create_general_chat_subgraph(
                assistant=assistant
            ),
        ).compile().ainvoke(create_main_state("什么叫概念专辑"))
        self.assertEqual(result["task_results"][0].route_key, RouteKey.GENERAL_CHAT)
        self.assertIn("统一主题", result["final_response"])

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
        context = RoutingContext(has_current_song=True, current_song_id=1)
        first = await graph.ainvoke(
            create_main_state("暂停一下", routing_context=context), config
        )
        second = await graph.ainvoke(
            create_main_state("暂停一下", routing_context=context), config
        )
        self.assertNotEqual(first["route_decision"].tasks[0].task_id, second["route_decision"].tasks[0].task_id)
        self.assertEqual(len(second["task_results"]), 2)
        self.assertEqual(second["final_response"].count("暂停指令"), 1)

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
            create_main_state(
                "暂停一下，然后讲讲这首歌",
                routing_context=RoutingContext(
                    has_current_song=True,
                    current_song_id=1,
                ),
            ))
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

    async def test_multilabel_model_result_escalates_by_default(self):
        model = model_result(
            "暂停、聊天",
            (IntentType.TOOL_CALLING, IntentType.GENERAL_CHAT),
        )

        self.assertEqual(
            EscalationPolicy().reason(model, {}),
            "multi_label_ambiguity",
        )
        self.assertIsNone(
            EscalationPolicy(escalate_multilabel=False).reason(model, {})
        )

    async def test_negation_and_coordination_escalate_single_model_label(self):
        model = model_result(
            "不要暂停，只聊天",
            (IntentType.TOOL_CALLING,),
        )
        policy = EscalationPolicy()

        for text in ("不要暂停", "聊音乐并推荐歌曲", "play it then chat"):
            with self.subTest(text=text):
                self.assertEqual(
                    policy.reason(model, {}, text),
                    "semantic_complexity",
                )

        self.assertIsNone(policy.reason(model, {}, "分析这首歌"))
        self.assertIsNone(
            EscalationPolicy(
                escalate_semantic_complexity=False
            ).reason(model, {}, "不要暂停")
        )

    async def test_invalid_input_never_calls_models(self):
        router, model, _ = fixture_router()
        for text in ("  ", "字" * 4001):
            with self.assertRaises(ValueError):
                await router.route(text)
        model.assert_not_called()

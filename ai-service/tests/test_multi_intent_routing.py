import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.foundation.hybrid_routing import HybridIntentRouter
from app.agent.foundation.llm_routing import LLMRouter
from app.agent.foundation.routing import (
    IntentSource,
    IntentType,
    RouteKey,
    RoutingContext,
)
from app.agent.foundation.rule_routing import RuleRouter
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.state.main import create_main_state
from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
from app.agent.foundation.subgraphs.tool_calling.models import ClientActionName
from tests.test_intent_merger import model_result
from tests.test_llm_router import TAXONOMY


class MultiIntentRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_negated_action_is_reviewed_by_llm_before_dispatch(self) -> None:
        text = "不要暂停"

        async def classify(request, **kwargs):
            return json.dumps(
                {
                    "intents": [
                        {
                            "intent": "general_chat",
                            "confidence": 0.92,
                            "evidence": text,
                            "start": 0,
                            "end": len(text),
                        }
                    ],
                    "needs_clarification": False,
                },
                ensure_ascii=False,
            )

        model = Mock(
            side_effect=lambda value: model_result(
                value,
                (IntentType.TOOL_CALLING,),
            )
        )
        runtime = SimpleNamespace(classify=AsyncMock(side_effect=classify))
        router = HybridIntentRouter(
            SimpleNamespace(route=model),
            LLMRouter(TAXONOMY, runtime=runtime),
        )

        decision = await router.route(text)

        self.assertEqual(
            [task.route_key for task in decision.tasks],
            [RouteKey.GENERAL_CHAT],
        )
        self.assertEqual(decision.intents[0].source, IntentSource.LLM)
        runtime.classify.assert_awaited_once()

    async def test_ambiguous_model_multilabel_uses_llm_grounded_order(self) -> None:
        text = "暂停、聊天"

        async def classify(request, **kwargs):
            return json.dumps(
                {
                    "intents": [
                        {
                            "intent": "tool_calling",
                            "confidence": 0.91,
                            "evidence": "暂停",
                            "start": 0,
                            "end": 2,
                        },
                        {
                            "intent": "general_chat",
                            "confidence": 0.88,
                            "evidence": "聊天",
                            "start": 3,
                            "end": 5,
                        },
                    ],
                    "needs_clarification": False,
                },
                ensure_ascii=False,
            )

        model = Mock(
            side_effect=lambda value: model_result(
                value,
                (IntentType.GENERAL_CHAT, IntentType.TOOL_CALLING),
            )
        )
        runtime = SimpleNamespace(classify=AsyncMock(side_effect=classify))
        router = HybridIntentRouter(
            SimpleNamespace(route=model),
            LLMRouter(TAXONOMY, runtime=runtime),
            rule_router=RuleRouter(rules=()),
        )

        decision = await router.route(text)

        self.assertEqual(
            [item.intent for item in decision.intents],
            [IntentType.TOOL_CALLING, IntentType.GENERAL_CHAT],
        )
        self.assertTrue(
            all(item.source is IntentSource.LLM for item in decision.intents)
        )
        self.assertEqual(
            [task.route_key for task in decision.tasks],
            [RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT],
        )
        self.assertEqual(
            [task.input_text for task in decision.tasks],
            ["暂停", "聊天"],
        )
        runtime.classify.assert_awaited_once()

    async def test_tool_chat_tool_order_is_preserved_and_executed(self) -> None:
        query = "暂停一下，然后聊聊音乐，再继续播放"
        model = Mock(
            side_effect=lambda text: model_result(
                text,
                (IntentType.GENERAL_CHAT,),
            )
        )
        runtime = SimpleNamespace(
            classify=AsyncMock(
                side_effect=AssertionError("LLM should not be called")
            )
        )
        router = HybridIntentRouter(
            SimpleNamespace(route=model),
            LLMRouter(TAXONOMY, runtime=runtime),
        )
        chat_calls = []

        async def chat_assistant(messages, current_input, context, prior_results):
            chat_calls.append((current_input, prior_results))
            return AIMessage(content="当然可以，我们继续聊音乐。")

        graph = build_main_graph(
            intent_router=router,
            general_chat_subgraph=create_general_chat_subgraph(
                assistant=chat_assistant
            ),
        ).compile()
        result = await graph.ainvoke(
            create_main_state(
                query,
                messages=[HumanMessage(content=query)],
                routing_context=RoutingContext(
                    has_current_song=True,
                    current_song_id=185809,
                    queue_size=1,
                    current_index=0,
                ),
            )
        )

        tasks = result["route_decision"].tasks
        self.assertEqual(
            [task.route_key for task in tasks],
            [
                RouteKey.TOOL_CALLING,
                RouteKey.GENERAL_CHAT,
                RouteKey.TOOL_CALLING,
            ],
        )
        self.assertEqual(
            [task.input_text for task in tasks],
            ["暂停一下", "聊聊音乐", "继续播放"],
        )
        self.assertEqual(tasks[1].depends_on, [tasks[0].task_id])
        self.assertEqual(tasks[2].depends_on, [tasks[1].task_id])
        self.assertEqual(
            [item.route_key for item in result["task_results"]],
            [
                RouteKey.TOOL_CALLING,
                RouteKey.GENERAL_CHAT,
                RouteKey.TOOL_CALLING,
            ],
        )
        self.assertEqual(chat_calls[0][0], "聊聊音乐")
        self.assertEqual(len(chat_calls[0][1]), 1)
        self.assertEqual(
            result["task_results"][0].data["client_actions"][0]["name"],
            ClientActionName.PAUSE_PLAYER,
        )
        self.assertEqual(
            result["task_results"][2].data["client_actions"][0]["name"],
            ClientActionName.RESUME_PLAYER,
        )
        model.assert_called_once_with("聊聊音乐")
        runtime.classify.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

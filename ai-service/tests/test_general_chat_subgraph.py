import unittest
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    RouteDecision,
    RouteKey,
    RouteTask,
    RoutingContext,
)
from app.agent.foundation.state.main import create_main_state
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.general_chat import (
    GeneralChatState,
    answer_general_chat,
    create_general_chat_subgraph,
)


def chat_state(**overrides):
    state = {
        "messages": [HumanMessage(content="你好")],
        "current_input": "你好",
        "routing_intents": [],
        "task_id": "chat-task",
        "routing_context": RoutingContext(),
        "prior_task_results": [],
        "answer": None,
        "task_result": None,
    }
    state.update(overrides)
    return state


class GeneralChatSubGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_subgraph_returns_stable_success_contract(self) -> None:
        captured = {}

        async def assistant(messages, current_input, routing_context, prior_results):
            captured.update(
                messages=messages,
                current_input=current_input,
                routing_context=routing_context,
                prior_results=prior_results,
            )
            return AIMessage(content="你好，我在这里。")

        context = RoutingContext(volume=0.35)
        prior = TaskResult(
            task_id="tool-task",
            route_key=RouteKey.TOOL_CALLING,
            status=TaskStatus.SUCCESS,
            content="已发送暂停指令。",
        )
        result = await create_general_chat_subgraph(
            assistant=assistant
        ).ainvoke(
            chat_state(
                current_input="聊聊天",
                routing_context=context,
                prior_task_results=[prior],
            )
        )

        self.assertEqual(set(result), {"task_result"})
        task_result = result["task_result"]
        self.assertEqual(task_result.task_id, "chat-task")
        self.assertEqual(task_result.route_key, RouteKey.GENERAL_CHAT)
        self.assertEqual(task_result.status, TaskStatus.SUCCESS)
        self.assertEqual(task_result.content, "你好，我在这里。")
        self.assertEqual(task_result.data, {"response_kind": "general_chat"})
        self.assertEqual(captured["current_input"], "聊聊天")
        self.assertEqual(captured["routing_context"], context)
        self.assertEqual(captured["prior_results"], [prior])

    async def test_default_assistant_bounds_history_and_sanitizes_prior_results(self) -> None:
        model = SimpleNamespace()
        captured = {}

        async def ainvoke(messages, **kwargs):
            captured["messages"] = messages
            captured["max_tokens"] = kwargs["max_tokens"]
            return AIMessage(content="可以，我们继续聊。")

        model.ainvoke = ainvoke
        history = []
        for index in range(20):
            history.extend(
                [
                    HumanMessage(content=f"问题 {index}"),
                    AIMessage(content=f"回答 {index}"),
                ]
            )
        history.append(HumanMessage(content="当前完整请求"))
        prior = TaskResult(
            task_id="tool-task",
            route_key=RouteKey.TOOL_CALLING,
            status=TaskStatus.SUCCESS,
            content="已完成前置操作。",
            data={"secret": "must-not-reach-chat-model"},
        )

        response = await answer_general_chat(
            history,
            "分配后的聊天片段",
            RoutingContext(volume=0.4),
            [prior],
            model=model,
        )

        self.assertEqual(response.text, "可以，我们继续聊。")
        from app.llm.budget import output_limit
        self.assertEqual(captured["max_tokens"], output_limit())
        sent = captured["messages"]
        self.assertIsInstance(sent[0], SystemMessage)
        self.assertIn('"volume":0.4', sent[0].content)
        self.assertIn("已完成前置操作。", sent[0].content)
        self.assertNotIn("must-not-reach-chat-model", sent[0].content)
        self.assertLessEqual(len(sent) - 2, 12)
        self.assertEqual(sent[-1].content, "分配后的聊天片段")
        self.assertNotIn("当前完整请求", [message.content for message in sent])

    async def test_tool_call_or_empty_response_is_rejected(self) -> None:
        async def tool_calling_assistant(*args):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "pause_player",
                        "args": {},
                        "type": "tool_call",
                    }
                ],
            )

        async def empty_assistant(*args):
            return AIMessage(content="   ")

        for assistant in (tool_calling_assistant, empty_assistant):
            with self.subTest(assistant=assistant.__name__):
                graph = create_general_chat_subgraph(assistant=assistant)
                with self.assertRaises(RuntimeError):
                    await graph.ainvoke(chat_state())

    async def test_visible_response_is_bounded(self) -> None:
        async def assistant(*args):
            return AIMessage(content="聊" * 2_100)

        result = await create_general_chat_subgraph(
            assistant=assistant
        ).ainvoke(chat_state())

        content = result["task_result"].content
        self.assertEqual(len(content), 2_000)
        self.assertTrue(content.endswith("…"))

    async def test_main_graph_supplies_prior_task_result_to_chat(self) -> None:
        text = "暂停一下，然后聊聊天"
        chat_start = text.index("聊聊天")
        intents = [
            IntentItem(
                intent=IntentType.TOOL_CALLING,
                sub_intent="pause_music",
                confidence=1.0,
                source=IntentSource.RULE,
                evidence="暂停一下",
                start=0,
                end=4,
                order=0,
            ),
            IntentItem(
                intent=IntentType.GENERAL_CHAT,
                sub_intent="conversation",
                confidence=0.9,
                source=IntentSource.LLM,
                evidence="聊聊天",
                start=chat_start,
                end=chat_start + 3,
                order=1,
            ),
        ]

        class Router:
            async def route(self, query, context):
                return RouteDecision(
                    intents=intents,
                    is_complete=True,
                    coverage=1.0,
                    tasks=[
                        RouteTask(
                            task_id="tool-task",
                            route_key=RouteKey.TOOL_CALLING,
                            input_text="暂停一下",
                            intent_orders=[0],
                        ),
                        RouteTask(
                            task_id="chat-task",
                            route_key=RouteKey.GENERAL_CHAT,
                            input_text="聊聊天",
                            intent_orders=[1],
                            depends_on=["tool-task"],
                        ),
                    ],
                )

        def complete_tool(state):
            return {
                "task_result": TaskResult(
                    task_id=state["task_id"],
                    route_key=RouteKey.TOOL_CALLING,
                    status=TaskStatus.SUCCESS,
                    content="已发送暂停指令。",
                )
            }

        tool_builder = StateGraph(GeneralChatState, output_schema=SubGraphOutput)
        tool_builder.add_node("complete", complete_tool)
        tool_builder.add_edge(START, "complete")
        tool_builder.add_edge("complete", END)
        captured = {}

        async def chat_assistant(messages, current_input, context, prior_results):
            captured["input"] = current_input
            captured["prior"] = prior_results
            return AIMessage(content="好，我们聊聊音乐。")

        graph = build_main_graph(
            intent_router=Router(),
            tool_calling_subgraph=tool_builder.compile(),
            general_chat_subgraph=create_general_chat_subgraph(
                assistant=chat_assistant
            ),
        ).compile()
        result = await graph.ainvoke(create_main_state(text))

        self.assertEqual(captured["input"], "聊聊天")
        self.assertEqual(len(captured["prior"]), 1)
        self.assertEqual(captured["prior"][0].task_id, "tool-task")
        self.assertEqual(
            [item.route_key for item in result["task_results"]],
            [RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT],
        )

    async def test_final_answer_becomes_next_turn_chat_history(self) -> None:
        calls = 0

        class Router:
            async def route(self, text, context):
                nonlocal calls
                calls += 1
                intent = IntentItem(
                    intent=IntentType.GENERAL_CHAT,
                    sub_intent="conversation",
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
                            task_id=f"chat-{calls}",
                            route_key=RouteKey.GENERAL_CHAT,
                            input_text=text,
                            intent_orders=[0],
                        )
                    ],
                )

        seen_messages = []

        async def assistant(messages, *args):
            seen_messages.append([message.content for message in messages])
            return AIMessage(content=f"聊天回复 {len(seen_messages)}")

        graph = build_main_graph(
            intent_router=Router(),
            general_chat_subgraph=create_general_chat_subgraph(
                assistant=assistant
            ),
        ).compile(checkpointer=create_memory_checkpointer())
        config = {"configurable": {"thread_id": "chat-history"}}

        await graph.ainvoke(
            create_main_state(
                "第一轮",
                messages=[HumanMessage(content="第一轮")],
            ),
            config,
        )
        await graph.ainvoke(
            create_main_state(
                "第二轮",
                messages=[HumanMessage(content="第二轮")],
            ),
            config,
        )

        self.assertEqual(seen_messages[0], ["第一轮"])
        self.assertEqual(
            seen_messages[1],
            ["第一轮", "聊天回复 1", "第二轮"],
        )


if __name__ == "__main__":
    unittest.main()

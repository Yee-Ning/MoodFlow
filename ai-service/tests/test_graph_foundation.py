import asyncio
import operator
import unittest
from typing import get_args, get_type_hints

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from app.agent.foundation import (
    BaseAgentState,
    MainState,
    RouteDecision,
    RouteKey,
    RouteTask,
    RoutingContext,
    TaskResult,
    TaskStatus,
    build_main_graph,
    create_main_graph,
    create_main_state,
)
from app.agent.foundation.subgraphs import (
    create_current_song_subgraph,
    create_tool_calling_subgraph,
    create_worldline_subgraph,
)


class GraphFoundationStateTests(unittest.TestCase):
    def test_main_state_can_be_initialized(self) -> None:
        state = create_main_state(
            "分析这首歌",
            messages=[HumanMessage(content="分析这首歌")],
        )

        self.assertEqual(state["current_input"], "分析这首歌")
        self.assertEqual(len(state["messages"]), 1)
        self.assertIsNone(state["route_decision"])
        self.assertEqual(state["task_results"], [])
        self.assertIsNone(state["final_response"])
        self.assertEqual(state["errors"], [])

    def test_messages_use_langgraph_add_messages_reducer(self) -> None:
        annotation = get_type_hints(BaseAgentState, include_extras=True)["messages"]
        reducer = get_args(annotation)[1]

        merged = reducer(
            [HumanMessage(content="first")],
            [AIMessage(content="second")],
        )

        self.assertEqual([message.content for message in merged], ["first", "second"])
        self.assertTrue(all(message.id for message in merged))

    def test_task_results_reducer_appends_multiple_results(self) -> None:
        annotation = get_type_hints(MainState, include_extras=True)["task_results"]
        reducer = get_args(annotation)[1]
        first = TaskResult(
            task_id="task-1",
            route_key=RouteKey.CURRENT_SONG,
            status=TaskStatus.SUCCESS,
            content="first",
        )
        second = TaskResult(
            task_id="task-2",
            route_key=RouteKey.TOOL_CALLING,
            status=TaskStatus.SUCCESS,
            content="second",
        )

        self.assertIs(reducer, operator.add)
        self.assertEqual(reducer([first], [second]), [first, second])

    def test_task_result_rejects_inconsistent_failure_payloads(self) -> None:
        with self.assertRaises(ValidationError):
            TaskResult(
                task_id="failed-without-error",
                route_key=RouteKey.WORLDLINE,
                status=TaskStatus.FAILED,
            )

        with self.assertRaises(ValidationError):
            TaskResult(
                task_id="success-with-error",
                route_key=RouteKey.WORLDLINE,
                status=TaskStatus.SUCCESS,
                error="unexpected",
            )

        with self.assertRaises(ValidationError):
            TaskResult(
                task_id="input-without-question",
                route_key=RouteKey.TOOL_CALLING,
                status=TaskStatus.NEEDS_INPUT,
            )

    def test_route_decision_supports_multiple_tasks(self) -> None:
        decision = RouteDecision(
            tasks=[
                RouteTask(task_id="task-1", route_key=RouteKey.CURRENT_SONG),
                RouteTask(task_id="task-2", route_key=RouteKey.TOOL_CALLING),
            ]
        )

        self.assertEqual(len(decision.tasks), 2)

    def test_thread_id_is_not_business_state(self) -> None:
        annotations = get_type_hints(MainState, include_extras=True)

        self.assertNotIn("thread_id", annotations)
        self.assertNotIn("request_config", annotations)
        self.assertNotIn("model_config", annotations)


class StubSubGraphTests(unittest.TestCase):
    @staticmethod
    def _invoke(graph, route: RouteKey) -> dict:
        local_fields = {
            RouteKey.CURRENT_SONG: {"song_context": None, "rag_context": []},
            RouteKey.TOOL_CALLING: {
                "routing_context": RoutingContext(),
                "prior_entities": [],
                "tool_calls": [],
                "tool_results": [],
                "client_actions": [],
                "tool_call_count": 0,
                "direct_complete": False,
            },
            RouteKey.WORLDLINE: {"rag_context": [], "worldline_plan": None},
        }
        state = {
            "messages": [HumanMessage(content="stub test")],
            "current_input": "stub test",
            "task_id": f"task-{route.value}",
            "answer": None,
            "task_result": None,
            **local_fields[route],
        }
        return asyncio.run(graph.ainvoke(state))

    def test_current_song_stub_runs_independently(self) -> None:
        result = self._invoke(create_current_song_subgraph(), RouteKey.CURRENT_SONG)

        self.assertEqual(set(result), {"task_result"})
        self.assertEqual(result["task_result"].route_key, RouteKey.CURRENT_SONG)
        self.assertEqual(result["task_result"].status, TaskStatus.SUCCESS)

    def test_tool_calling_stub_runs_independently(self) -> None:
        result = self._invoke(create_tool_calling_subgraph(), RouteKey.TOOL_CALLING)

        self.assertEqual(set(result), {"task_result"})
        self.assertEqual(result["task_result"].route_key, RouteKey.TOOL_CALLING)
        self.assertEqual(result["task_result"].status, TaskStatus.SUCCESS)

    def test_worldline_stub_runs_independently(self) -> None:
        result = self._invoke(create_worldline_subgraph(), RouteKey.WORLDLINE)

        self.assertEqual(set(result), {"task_result"})
        self.assertEqual(result["task_result"].route_key, RouteKey.WORLDLINE)
        self.assertEqual(result["task_result"].status, TaskStatus.SUCCESS)


class MainGraphFoundationTests(unittest.TestCase):
    def test_main_graph_compiles(self) -> None:
        graph = build_main_graph(development_routes=(RouteKey.CURRENT_SONG,)).compile()

        self.assertIsInstance(graph, CompiledStateGraph)
        self.assertIn("development_route", graph.get_graph().nodes)
        self.assertNotIn("intent_router", graph.get_graph().nodes)

    def test_main_graph_runs_multi_subgraph_stub_flow_to_end(self) -> None:
        routes = (
            RouteKey.CURRENT_SONG,
            RouteKey.TOOL_CALLING,
            RouteKey.WORLDLINE,
        )
        graph = create_main_graph(development_routes=routes)

        result = asyncio.run(
            graph.ainvoke(
                create_main_state(
                    "分析然后收藏",
                    messages=[HumanMessage(content="分析然后收藏")],
                ),
                {"configurable": {"thread_id": "foundation-multi-intent"}},
            )
        )

        current_results = [
            item
            for item in result["task_results"]
            if item.task_id in {
                task.task_id for task in result["route_decision"].tasks
            }
        ]
        self.assertEqual(
            [item.route_key for item in current_results],
            [route.value for route in routes],
        )
        self.assertTrue(
            all(item.status is TaskStatus.SUCCESS for item in current_results)
        )
        self.assertIn("CurrentSong SubGraph stub completed.", result["final_response"])
        self.assertIn("ToolCalling SubGraph stub completed.", result["final_response"])
        self.assertIn("WorldLine SubGraph stub completed.", result["final_response"])

    def test_subgraph_private_fields_do_not_pollute_main_state(self) -> None:
        graph = create_main_graph(
            development_routes=(
                RouteKey.CURRENT_SONG,
                RouteKey.TOOL_CALLING,
                RouteKey.WORLDLINE,
            )
        )

        result = asyncio.run(
            graph.ainvoke(
                create_main_state("test"),
                {"configurable": {"thread_id": "foundation-private-state"}},
            )
        )

        private_fields = {
            "song_context",
            "rag_context",
            "tool_calls",
            "tool_results",
            "worldline_plan",
            "answer",
            "task_result",
            "task_id",
        }
        self.assertTrue(private_fields.isdisjoint(result))

    def test_checkpointer_uses_configurable_thread_id(self) -> None:
        graph = create_main_graph(development_routes=(RouteKey.CURRENT_SONG,))

        async def scenario() -> tuple[dict, dict]:
            await graph.ainvoke(
                create_main_state(
                    "first",
                    messages=[HumanMessage(content="first")],
                ),
                {"configurable": {"thread_id": "same-thread"}},
            )
            same_thread = await graph.ainvoke(
                create_main_state(
                    "second",
                    messages=[HumanMessage(content="second")],
                ),
                {"configurable": {"thread_id": "same-thread"}},
            )
            other_thread = await graph.ainvoke(
                create_main_state(
                    "isolated",
                    messages=[HumanMessage(content="isolated")],
                ),
                {"configurable": {"thread_id": "other-thread"}},
            )
            return same_thread, other_thread

        same_thread, other_thread = asyncio.run(scenario())

        self.assertEqual(
            [message.content for message in same_thread["messages"]],
            ["first", "second"],
        )
        self.assertEqual(
            [message.content for message in other_thread["messages"]],
            ["isolated"],
        )
        self.assertNotIn("thread_id", same_thread)


if __name__ == "__main__":
    unittest.main()

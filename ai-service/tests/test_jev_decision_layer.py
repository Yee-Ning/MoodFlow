from __future__ import annotations

import unittest
import os
from datetime import UTC, datetime
from unittest.mock import patch

from langgraph.graph import END, START, StateGraph

from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.streaming import _retrieve_query_memories
from app.agent.foundation.state.main import create_main_state
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.general_chat import GeneralChatState

from app.agent.decision.jev.client import DecisionAnswer, DecisionResponse, JevDecisionError, _safe_state
from app.agent.decision.jev.context_selector import select_context
from app.agent.decision.jev.memory_filter import filter_memories
from app.agent.decision.jev.router import compare_route
from app.agent.foundation.context_engineering import ContextItem, build_context, build_context_async
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.agent.memory.models import (
    MemoryRetrievalRequest, MemoryRetrievalResult, MemoryType, RetrievedMemory,
)
from app.config.settings import Settings


def settings(**values) -> Settings:
    base = Settings(
        _env_file=None, api_key="test", base_url="https://example.com",
        model="test", timeout_seconds=10,
        SPRING_BOOT_BASE_URL="http://127.0.0.1:8080",
        SPRING_BOOT_TIMEOUT_SECONDS=10,
    )
    return base.model_copy(update={
        "jev_enabled": True, "jev_router_enabled": True,
        "jev_memory_filter_enabled": True, "jev_context_select_enabled": True,
        "jev_shadow_mode": False, "jev_model": "jev-latest", **values,
    })


def response(answers: dict[str, DecisionAnswer]) -> DecisionResponse:
    return DecisionResponse("jev-latest", "jev-1.13.0", "2026-09-23T00:00:00+00:00",
                            12.0, {"input_tokens": 1, "output_tokens": 1}, answers)


class FakeClient:
    def __init__(self, decide):
        self.handler = decide
        self.calls = []

    async def decide(self, state, questions):
        self.calls.append((state, questions))
        return self.handler(state, questions)


class JevDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_discards_cross_user_retrieval_result(self):
        class WrongUserRuntime:
            async def retrieve(self, request):
                return MemoryRetrievalResult(user_id="other-user", items=[])

        context = {}
        await _retrieve_query_memories(context=context, user_id="current-user", message="hello",
                                       enabled=True, memory_runtime=WrongUserRuntime())
        self.assertNotIn("long_term_memory", context)

    async def test_main_graph_conditional_edge_uses_approved_jev_route(self):
        class BaselineRouter:
            async def route(self, text, context):
                return RouteDecision(is_complete=True, coverage=1.0, tasks=[
                    RouteTask(task_id="baseline:general_chat", route_key=RouteKey.GENERAL_CHAT,
                              input_text=text)])

        def subgraph(route):
            def complete(state):
                return {"task_result": TaskResult(
                    task_id=state["task_id"], route_key=route,
                    status=TaskStatus.SUCCESS, content=route.value)}

            builder = StateGraph(GeneralChatState, output_schema=SubGraphOutput)
            builder.add_node("complete", complete)
            builder.add_edge(START, "complete")
            builder.add_edge("complete", END)
            return builder.compile()

        client = FakeClient(lambda state, questions: response({"route": DecisionAnswer(
            "choice", choice="tool_calling", confidence=0.95,
            probabilities={key.value: (0.95 if key is RouteKey.TOOL_CALLING else 0.05 / 3)
                           for key in RouteKey})}))
        with patch("app.config.settings.get_settings", return_value=settings()):
            graph = build_main_graph(
                intent_router=BaselineRouter(), jev_client=client,
                current_song_subgraph=subgraph(RouteKey.CURRENT_SONG),
                tool_calling_subgraph=subgraph(RouteKey.TOOL_CALLING),
                worldline_subgraph=subgraph(RouteKey.WORLDLINE),
                general_chat_subgraph=subgraph(RouteKey.GENERAL_CHAT),
            ).compile()
            result = await graph.ainvoke(create_main_state("Play music"))
        self.assertEqual(result["final_response"], RouteKey.TOOL_CALLING.value)
        self.assertEqual(result["route_decision"].tasks[0].route_key, RouteKey.TOOL_CALLING)

    async def test_secret_values_are_removed_before_external_decision(self):
        state = {"query": "token=abc Bearer xyz actual-key", "password": "hidden"}
        sanitized = _safe_state(state, "actual-key")
        self.assertNotIn("abc", str(sanitized))
        self.assertNotIn("xyz", str(sanitized))
        self.assertNotIn("actual-key", str(sanitized))
        self.assertNotIn("hidden", str(sanitized))

    async def test_router_live_shadow_low_confidence_and_failure(self):
        baseline = RouteDecision(is_complete=True, coverage=1.0, tasks=[
            RouteTask(task_id="old:general_chat", route_key=RouteKey.GENERAL_CHAT,
                      input_text="Play music")])

        def choose(confidence):
            return FakeClient(lambda state, questions: response({
                "route": DecisionAnswer("choice", choice="tool_calling",
                                        confidence=confidence, probabilities={
                                            "current_song": 0.0, "tool_calling": confidence,
                                            "worldline": 0.0, "general_chat": 1-confidence,
                                        })}))

        live = await compare_route("Play music", baseline, settings=settings(), client=choose(0.95))
        self.assertEqual(live.tasks[0].route_key, RouteKey.TOOL_CALLING)
        self.assertEqual(live.tasks[0].task_id.split(":")[-1], RouteKey.TOOL_CALLING.value)
        low = await compare_route("Play music", baseline, settings=settings(), client=choose(0.4))
        self.assertEqual(low, baseline)
        shadow = await compare_route("Play music", baseline,
                                     settings=settings(jev_shadow_mode=True), client=choose(0.95))
        self.assertEqual(shadow, baseline)
        for handler in (
            lambda state, questions: (_ for _ in ()).throw(JevDecisionError("timeout")),
            lambda state, questions: response({}),
            lambda state, questions: response({"route": DecisionAnswer("choice", choice="invalid", confidence=1)}),
        ):
            self.assertEqual(await compare_route("Play music", baseline,
                                                 settings=settings(), client=FakeClient(handler)), baseline)

    async def test_router_never_replaces_multi_task_baseline(self):
        baseline = RouteDecision(is_complete=True, coverage=1.0, tasks=[
            RouteTask(task_id="a", route_key=RouteKey.CURRENT_SONG, input_text="song"),
            RouteTask(task_id="b", route_key=RouteKey.GENERAL_CHAT, input_text="chat", depends_on=["a"]),
        ])
        client = FakeClient(lambda state, questions: response({"route": DecisionAnswer(
            "choice", choice="tool_calling", confidence=1, probabilities={key.value: 0.25 for key in RouteKey})}))
        self.assertEqual(await compare_route("song and chat", baseline,
                                             settings=settings(), client=client), baseline)

    async def test_memory_stable_mapping_batch_fail_open_and_isolation(self):
        now = datetime.now(UTC)
        items = [RetrievedMemory(memory_id=f"memory:{i}", memory_type=MemoryType.EPISODIC,
                                 content=f"fact {i}", structured_content={}, confidence=1,
                                 importance=0.5, relevance=0.7, updated_at=now)
                 for i in range(21)]
        result = MemoryRetrievalResult(user_id="u1", items=items)
        request = MemoryRetrievalRequest(user_id="u1", query="fact", top_k=21)

        def answer(state, questions):
            by_id = {item["memory_id"]: item for item in state["memories"]}
            return response({qid: DecisionAnswer("noul", noul=1.0 if memory_id.endswith(":0") else 0.0)
                             for qid, question in reversed(list(questions.items()))
                             for memory_id in by_id if memory_id in question.instructions})

        client = FakeClient(answer)
        selected = await filter_memories(request, result, settings=settings(), client=client)
        self.assertEqual([item.memory_id for item in selected.items], ["memory:0"])
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(all("embedding" not in str(state) for state, _ in client.calls))
        failed = await filter_memories(request, result, settings=settings(),
                                       client=FakeClient(lambda state, q: response({})))
        self.assertEqual(failed, result)
        api_failure = FakeClient(lambda state, q: (_ for _ in ()).throw(JevDecisionError("timeout")))
        self.assertEqual(await filter_memories(request, result, settings=settings(), client=api_failure), result)
        duplicated = result.model_copy(update={"items": [*items, items[0]]})
        self.assertEqual(await filter_memories(request, duplicated, settings=settings(), client=client),
                         duplicated)
        shadow = await filter_memories(request, result, settings=settings(jev_shadow_mode=True),
                                       client=client)
        self.assertEqual(shadow, result)
        with self.assertRaises(ValueError):
            await filter_memories(MemoryRetrievalRequest(user_id="u2", query="fact"),
                                  result, settings=settings(), client=client)

    async def test_context_required_batch_shadow_and_fallback(self):
        items = [ContextItem(id="system", type="system", source="system", content="rules", required=True),
                 ContextItem(id="query", type="user_query", source="user_query", content="ask", required=True)]
        items += [ContextItem(id=f"history:{i}", type="conversation", source="conversation",
                              content=f"old fact {i}") for i in range(21)]
        items.append(ContextItem(id="runtime", type="business_state", source="business_state", content="state"))

        def answer(state, questions):
            return response({qid: DecisionAnswer("noul", noul=1.0 if "history:0" in q.instructions else 0.0)
                             for qid, q in reversed(list(questions.items()))})

        client = FakeClient(answer)
        selected = await select_context(items, "ask", settings=settings(), client=client)
        self.assertEqual({item.id for item in selected}, {"system", "query", "history:0", "runtime"})
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(all(len(questions) <= 20 for _, questions in client.calls))
        self.assertTrue(all("system" not in str(state) for state, _ in client.calls))
        self.assertEqual(await select_context(items, "ask", settings=settings(jev_shadow_mode=True),
                                              client=client), items)
        self.assertEqual(await select_context(items, "ask", settings=settings(),
                                              client=FakeClient(lambda state, q: response({}))), items)
        api_failure = FakeClient(lambda state, q: (_ for _ in ()).throw(JevDecisionError("timeout")))
        self.assertEqual(await select_context(items, "ask", settings=settings(), client=api_failure), items)

    async def test_async_context_failure_preserves_existing_pack(self):
        baseline = build_context(RouteKey.GENERAL_CHAT, query="ask", system="rules",
                                 messages=[])
        with patch("app.config.settings.get_settings", return_value=settings()):
            actual = await build_context_async(RouteKey.GENERAL_CHAT, query="ask", system="rules",
                                               messages=[], jev_client=FakeClient(lambda state, q: response({})))
        self.assertEqual(actual.items, baseline.items)
        self.assertEqual(actual.messages, baseline.messages)
        self.assertEqual(actual.total_tokens, baseline.total_tokens)

    async def test_context_without_jev_needs_no_llm_settings(self):
        with patch.dict(os.environ, {"JEV_ENABLED": "false"}, clear=True), \
             patch("app.config.settings.get_settings", side_effect=AssertionError("unexpected settings")):
            pack = await build_context_async(RouteKey.GENERAL_CHAT, query="ask", system="rules")
        self.assertEqual([item.id for item in pack.items], ["system", "query"])


if __name__ == "__main__":
    unittest.main()

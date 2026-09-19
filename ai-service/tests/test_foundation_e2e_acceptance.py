"""HTTP -> router -> real SubGraph -> MainGraph -> public JSON acceptance cases."""

import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.routing import (
    IntentItem, IntentSource, IntentType, RouteDecision, RouteKey,
    RouteTask, RoutingContext,
)
from app.agent.foundation.subgraphs.current_song.graph import create_current_song_subgraph
from app.agent.foundation.subgraphs.general_chat.graph import create_general_chat_subgraph
from app.agent.foundation.subgraphs.tool_calling.graph import create_tool_calling_subgraph
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import EntityType, MusicKnowledgeDocument
from app.agent.foundation.subgraphs.worldline.retrieval import HybridMusicKnowledgeRetriever
from app.rag.query_understanding.service import QueryUnderstandingService
from app.rag.retrieval import RetrievalResult, RetrievalStatus
from app.main import app
from tests.test_current_song_graph import song
from tests.test_tool_calling_subgraph import search_tool, scripted_assistant, tool_call
from tests.test_worldline_graph import (
    FakeGenerator, FakeMusicService, FailingRetriever, entity,
)
from tests.test_worldline_retrieval_adapter import FakeReranker, candidate, profile


class FixedRouter:
    def __init__(self, route: RouteKey, sub_intent: str | None = None) -> None:
        self.route_key = route
        self.sub_intent = sub_intent

    async def route(self, text: str, context: RoutingContext) -> RouteDecision:
        intent = IntentItem(
            intent=IntentType(self.route_key.value), sub_intent=self.sub_intent,
            confidence=1.0, source=IntentSource.RULE,
            evidence=text, start=0, end=len(text), order=0,
        )
        return RouteDecision(
            intents=[intent], is_complete=True, coverage=1.0,
            tasks=[RouteTask(
                task_id=uuid4().hex, route_key=self.route_key,
                input_text=text, intent_orders=[0],
            )],
        )


class SongProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[int] = []
        self.fail = fail

    async def get_song(self, song_id: int):
        self.calls.append(song_id)
        if self.fail:
            raise TimeoutError("private music backend timeout")
        return song(song_id, f"歌曲{song_id}")


class KnowledgeRetriever:
    def __init__(self) -> None:
        self.plans = []

    async def retrieve(self, root, plan):
        self.plans.append(plan)
        return [MusicKnowledgeDocument(
            id="knowledge-1", title="测试资料", content="测试歌曲背景资料。",
            source="frozen-test-index", url="https://example.test/source",
        )]


class FoundationE2EAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def request(self, route: RouteKey, subgraph, text: str, *,
                song_id: int | None = None, sub_intent: str | None = None,
                graph=None, thread_id: str | None = None):
        key = {
            RouteKey.CURRENT_SONG: "current_song_subgraph",
            RouteKey.TOOL_CALLING: "tool_calling_subgraph",
            RouteKey.GENERAL_CHAT: "general_chat_subgraph",
            RouteKey.WORLDLINE: "worldline_subgraph",
        }[route]
        graph = graph or build_main_graph(
            intent_router=FixedRouter(route, sub_intent), **{key: subgraph},
        ).compile(checkpointer=create_memory_checkpointer())
        with patch("app.agent.foundation.streaming.get_foundation_graph", return_value=graph):
            response = self.client.post("/api/ai/foundation", json={
                "message": text,
                "thread_id": thread_id or uuid4().hex,
                "current_song_id": song_id,
            })
        return response, graph

    def test_current_song_normal_and_context_change(self) -> None:
        provider = SongProvider()
        seen: list[int] = []

        async def assistant(messages, current_input, song_context, routing_context):
            seen.append(song_context.song_id)
            return AIMessage(content=f"从歌词来看，歌曲{song_context.song_id}偏怀旧。")

        subgraph = create_current_song_subgraph(provider=provider, assistant=assistant)
        first, graph = self.request(
            RouteKey.CURRENT_SONG, subgraph, "分析这首歌", song_id=101,
            thread_id="current-song-change",
        )
        second, _ = self.request(
            RouteKey.CURRENT_SONG, subgraph, "现在这首呢", song_id=202,
            graph=graph, thread_id="current-song-change",
        )

        self.assertEqual([first.status_code, second.status_code], [200, 200])
        self.assertEqual(seen, [101, 202])
        self.assertEqual(provider.calls, [101, 202])
        self.assertEqual(second.json()["results"][0]["metadata"]["song"]["song_id"], 202)

    def test_current_song_needs_input_without_player(self) -> None:
        response, _ = self.request(
            RouteKey.CURRENT_SONG, create_current_song_subgraph(), "分析这首歌",
        )
        self.assertEqual(response.json()["status"], "needs_input")
        self.assertEqual(response.json()["results"][0]["subgraph"], "current_song")

    def test_current_song_backend_failure_is_sanitized(self) -> None:
        response, _ = self.request(
            RouteKey.CURRENT_SONG,
            create_current_song_subgraph(provider=SongProvider(fail=True)),
            "分析这首歌", song_id=101,
        )
        self.assertEqual(response.json()["status"], "failed")
        self.assertNotIn("private", response.text)

    def test_tool_calling_executes_tool_and_returns_result(self) -> None:
        calls: list[dict] = []
        subgraph = create_tool_calling_subgraph(
            tools=[search_tool(calls=calls)],
            assistant=scripted_assistant([
                tool_call("search_music", "search-1", {"keyword": "测试歌曲"}),
                AIMessage(content="找到了测试歌曲。"),
            ]),
        )
        response, _ = self.request(RouteKey.TOOL_CALLING, subgraph, "搜索测试歌曲")
        result = response.json()["results"][0]
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(calls, [{"keyword": "测试歌曲", "limit": 10}])
        self.assertEqual(result["tool_calls"][0]["name"], "search_music")

    def test_tool_calling_boundary_needs_song_without_action(self) -> None:
        response, _ = self.request(
            RouteKey.TOOL_CALLING, create_tool_calling_subgraph(),
            "暂停一下", sub_intent="pause_music",
        )
        result = response.json()["results"][0]
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["metadata"]["client_actions"], [])

    def test_tool_calling_timeout_returns_failure(self) -> None:
        response, _ = self.request(
            RouteKey.TOOL_CALLING,
            create_tool_calling_subgraph(
                tools=[search_tool(timeout=True)],
                assistant=scripted_assistant([
                    tool_call("search_music", "timeout-1", {"keyword": "测试"}),
                    AIMessage(content="搜索暂时失败，请稍后再试。"),
                ]),
            ),
            "搜索测试",
        )
        self.assertEqual(response.json()["status"], "failed")
        self.assertNotIn("private", response.text)

    def test_general_chat_normal_and_history(self) -> None:
        seen = []

        async def assistant(messages, current_input, context, prior_results):
            seen.append([str(item.content) for item in messages])
            return AIMessage(content=f"答复：{current_input}")

        subgraph = create_general_chat_subgraph(assistant=assistant)
        first, graph = self.request(
            RouteKey.GENERAL_CHAT, subgraph, "你好", thread_id="chat-history",
        )
        second, _ = self.request(
            RouteKey.GENERAL_CHAT, subgraph, "接着聊", graph=graph,
            thread_id="chat-history",
        )
        self.assertEqual(first.json()["status"], "success")
        self.assertEqual(second.json()["status"], "success")
        self.assertTrue(any("答复：你好" in item for item in seen[1]))

    def test_general_chat_without_tools_completes_directly(self) -> None:
        async def assistant(messages, current_input, context, prior_results):
            return AIMessage(content="概念专辑围绕共同主题组织歌曲。")

        response, _ = self.request(
            RouteKey.GENERAL_CHAT,
            create_general_chat_subgraph(assistant=assistant),
            "什么是概念专辑",
        )
        result = response.json()["results"][0]
        self.assertEqual(result["answer"], "概念专辑围绕共同主题组织歌曲。")
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["sources"], [])

    def test_general_chat_receives_prior_task_summary(self) -> None:
        observed = []

        class TwoTaskRouter:
            async def route(self, text, context):
                return RouteDecision(
                    is_complete=True, coverage=1.0,
                    tasks=[
                        RouteTask(
                            task_id="tool-first", route_key=RouteKey.TOOL_CALLING,
                            input_text=text,
                        ),
                        RouteTask(
                            task_id="chat-second", route_key=RouteKey.GENERAL_CHAT,
                            input_text=text, depends_on=["tool-first"],
                        ),
                    ],
                )

        async def assistant(messages, current_input, context, prior_results):
            observed.extend(prior_results)
            return AIMessage(content="前置操作已经处理。")

        graph = build_main_graph(
            intent_router=TwoTaskRouter(),
            tool_calling_subgraph=create_tool_calling_subgraph(
                assistant=scripted_assistant([AIMessage(content="已完成前置任务。")]),
            ),
            general_chat_subgraph=create_general_chat_subgraph(assistant=assistant),
        ).compile(checkpointer=create_memory_checkpointer())
        with patch("app.agent.foundation.streaming.get_foundation_graph", return_value=graph):
            response = self.client.post("/api/ai/foundation", json={
                "message": "先处理任务再聊聊", "thread_id": "chat-prior-summary",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.content for item in observed], ["已完成前置任务。"])
        self.assertEqual(
            [item["subgraph"] for item in response.json()["results"]],
            ["tool_calling", "general_chat"],
        )

    def test_general_chat_bad_model_output_fails_closed(self) -> None:
        async def assistant(messages, current_input, context, prior_results):
            return AIMessage(content="", tool_calls=[{
                "name": "search_music", "args": {"keyword": "测试"},
                "id": "bad-call", "type": "tool_call",
            }])

        response, _ = self.request(
            RouteKey.GENERAL_CHAT,
            create_general_chat_subgraph(assistant=assistant), "普通聊天",
        )
        self.assertEqual(response.status_code, 502)

    def test_worldline_plan_retrieval_generation_and_sources(self) -> None:
        root = entity(1, EntityType.SONG, "晴天")
        retriever = KnowledgeRetriever()
        subgraph = create_worldline_subgraph(
            music_service=FakeMusicService(root), retriever=retriever,
            generator=FakeGenerator(grounded=True),
        )
        response, _ = self.request(
            RouteKey.WORLDLINE, subgraph, "歌曲《晴天》的音乐世界线",
        )
        result = response.json()["results"][0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(retriever.plans), 1)
        self.assertTrue(retriever.plans[0].queries)
        self.assertTrue(result["sources"])
        self.assertTrue(result["citations"])
        self.assertIn("worldline_result", result["metadata"])

    def test_worldline_frozen_rag_stages_reach_main_response(self) -> None:
        root = entity(1, EntityType.SONG, "晴天")
        reranker = FakeReranker()

        class CandidateService:
            def __init__(self):
                self.calls = 0

            async def retrieve(self, plan):
                self.calls += 1
                return RetrievalResult(
                    status=RetrievalStatus.SUCCESS,
                    plan_id=plan.plan_id,
                    index_profile=plan.index_profile,
                    candidates=[
                        candidate(index).model_copy(
                            update={"entity_ids": [root.entity_id]}
                        )
                        for index in range(1, 21)
                    ],
                )

        pool = CandidateService()
        retriever = HybridMusicKnowledgeRetriever(
            pool, reranker=reranker, rerank_mode="on",
        )
        subgraph = create_worldline_subgraph(
            music_service=FakeMusicService(root),
            retriever=retriever,
            query_understanding=QueryUnderstandingService(index_profile=profile()),
            generator=FakeGenerator(grounded=True),
        )

        response, _ = self.request(
            RouteKey.WORLDLINE, subgraph, "歌曲《晴天》的音乐世界线",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(pool.calls, 1)
        self.assertEqual(reranker.calls, 1)
        self.assertIn(
            "knowledge",
            [item["source_type"] for item in response.json()["results"][0]["sources"]],
        )

    def test_worldline_unknown_entity_needs_input(self) -> None:
        subgraph = create_worldline_subgraph(
            music_service=FakeMusicService(None), retriever=KnowledgeRetriever(),
            generator=FakeGenerator(),
        )
        response, _ = self.request(
            RouteKey.WORLDLINE, subgraph, "歌曲《不存在》的音乐世界线",
        )
        self.assertEqual(response.json()["status"], "needs_input")

    def test_worldline_retrieval_failure_falls_back_to_catalog(self) -> None:
        root = entity(1, EntityType.SONG, "晴天")
        subgraph = create_worldline_subgraph(
            music_service=FakeMusicService(root), retriever=FailingRetriever(),
            generator=FakeGenerator(grounded=True),
        )
        response, _ = self.request(
            RouteKey.WORLDLINE, subgraph, "歌曲《晴天》的音乐世界线",
        )
        self.assertEqual(response.json()["status"], "success")
        self.assertIn("仅基于目录事实", response.json()["message"])


if __name__ == "__main__":
    unittest.main()

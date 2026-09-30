import asyncio
import unittest
from unittest.mock import AsyncMock

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessageChunk

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.main_graph import build_main_graph
from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    RouteDecision,
    RouteKey,
    RouteTask,
    RoutingContext,
)
from app.agent.foundation.streaming import stream_foundation_assistant
from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
from app.agent.foundation.subgraphs.current_song import create_current_song_subgraph
from app.agent.foundation.subgraphs.tool_calling.models import (
    ClientAction,
    ClientActionName,
    EmptyActionArguments,
)
from app.schemas.chat import (
    ClientActionStreamEvent,
    FinalStreamEvent,
    MessageStreamEvent,
    UpdateStreamEvent,
)
from app.schemas.context import SongContext


class CountingFoundationGraph:
    def __init__(self) -> None:
        self.calls = 0
        self.input = None
        self.config = None
        self.context = None
        self.stream_mode = None
        self.version = None

    async def astream(self, graph_input, config, **kwargs):
        self.calls += 1
        self.input = graph_input
        self.config = config
        self.context = kwargs.get("context")
        self.stream_mode = kwargs.get("stream_mode")
        self.version = kwargs.get("version")
        action = ClientAction(
            action_id="task-1:direct:0",
            task_id="task-1",
            name=ClientActionName.PAUSE_PLAYER,
            arguments=EmptyActionArguments(),
        )
        yield {
            "type": "updates",
            "data": {"prepare_context": {"current_input": "暂停一下"}},
        }
        yield {
            "type": "updates",
            "data": {
                "tool_calling_subgraph": {
                    "task_results": [
                        TaskResult(
                            task_id="task-1",
                            route_key=RouteKey.TOOL_CALLING,
                            status=TaskStatus.SUCCESS,
                            content="已发送暂停指令。",
                            data={
                                "client_actions": [
                                    action.model_dump(mode="json"),
                                    action.model_dump(mode="json"),
                                ]
                            },
                        )
                    ]
                }
            },
        }
        yield {
            "type": "updates",
            "data": {"final_response": {"final_response": "已发送暂停指令。"}},
        }


class FoundationStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_nested_chat_streams_tokens_before_subgraph_finishes(self) -> None:
        answer = "真正逐步生成的回答"
        model = FakeListChatModel(responses=[answer], sleep=0.01)
        graph = build_main_graph(
            development_routes=[RouteKey.GENERAL_CHAT],
            general_chat_subgraph=create_general_chat_subgraph(model=model),
        ).compile()
        stream = stream_foundation_assistant(
            "聊聊天", "nested-stream", RoutingContext(), graph=graph,
        )
        events = []
        async with asyncio.timeout(10):
            async for event in stream:
                events.append(event)
                if isinstance(event, MessageStreamEvent):
                    break
        self.assertIsInstance(events[-1], MessageStreamEvent)
        self.assertFalse(any(
            isinstance(event, UpdateStreamEvent) and event.node == "general_chat_subgraph"
            for event in events
        ), "The first token must arrive before the nested subgraph completes")
        events.extend([event async for event in stream])
        deltas = [event.delta for event in events if isinstance(event, MessageStreamEvent)]
        self.assertGreater(len(deltas), 1)
        self.assertEqual("".join(deltas), answer)
        self.assertEqual(events[-1].response.message, answer)

    async def test_nested_current_song_streams_model_deltas(self) -> None:
        answer = "从歌词来看，这首歌表达了怀念。"
        provider = AsyncMock()
        provider.get_song.return_value = SongContext(
            song_id=1, name="测试歌曲", artist="测试歌手", album="测试专辑",
            original_lyrics="测试歌词", translated_lyrics="", lyrics_status="AVAILABLE",
        )
        graph = build_main_graph(
            development_routes=[RouteKey.CURRENT_SONG],
            current_song_subgraph=create_current_song_subgraph(
                provider=provider, model=FakeListChatModel(responses=[answer]),
            ),
        ).compile()
        events = [event async for event in stream_foundation_assistant(
            "分析这首歌", "nested-song-stream",
            RoutingContext(has_current_song=True, current_song_id=1), graph=graph,
        )]
        deltas = [event.delta for event in events if isinstance(event, MessageStreamEvent)]
        self.assertGreater(len(deltas), 1)
        self.assertEqual("".join(deltas), answer)
        self.assertEqual(events[-1].response.message, answer)
        provider.get_song.assert_awaited_once_with(1)

    async def test_nested_private_updates_and_non_answer_tokens_are_not_public(self) -> None:
        class NestedGraph:
            async def astream(self, *args, **kwargs):
                self.subgraphs = kwargs.get("subgraphs")
                yield {"type": "updates", "ns": ("child:1",), "data": {
                    "final_response": {"final_response": "private nested state"},
                    "tool_calling_subgraph": {"task_results": ["private tool data"]},
                }}
                for node, integration, text in (
                    ("intent_router", "langchain_chat_model", "private router JSON"),
                    ("agent", "", "private replay"),
                    ("agent", "langchain_chat_model", "visible answer"),
                ):
                    yield {"type": "messages", "ns": ("general_chat_subgraph:1",), "data": (
                        AIMessageChunk(content=text),
                        {"langgraph_node": node, "ls_integration": integration},
                    )}
                yield {"type": "updates", "ns": (), "data": {
                    "final_response": {"final_response": "visible answer"},
                }}

        graph = NestedGraph()
        events = [event async for event in stream_foundation_assistant(
            "聊天", "private-updates", RoutingContext(), graph=graph,
        )]
        self.assertTrue(graph.subgraphs)
        self.assertEqual(
            [event.delta for event in events if isinstance(event, MessageStreamEvent)],
            ["visible answer"],
        )
        self.assertEqual(
            [event.node for event in events if isinstance(event, UpdateStreamEvent)],
            ["final_response"],
        )
        self.assertNotIn("private", "".join(event.model_dump_json() for event in events))

    async def test_tool_text_waits_for_validated_result_and_task_texts_are_separated(self) -> None:
        class MixedGraph:
            async def astream(self, *args, **kwargs):
                yield {"type": "messages", "ns": ("tool_calling_subgraph:1",), "data": (
                    AIMessageChunk(content="错误地声称云端操作成功"),
                    {"langgraph_node": "agent", "ls_integration": "langchain_chat_model"},
                )}
                yield {"type": "updates", "ns": (), "data": {"tool_calling_subgraph": {
                    "task_results": [TaskResult(
                        task_id="tool-task", route_key=RouteKey.TOOL_CALLING,
                        status=TaskStatus.FAILED, content="云端操作失败。", error="upstream unavailable",
                    )],
                }}}
                yield {"type": "messages", "ns": ("general_chat_subgraph:2",), "data": (
                    AIMessageChunk(content="聊天回答。"),
                    {"langgraph_node": "agent", "ls_integration": "langchain_chat_model"},
                )}
                yield {"type": "updates", "ns": (), "data": {"general_chat_subgraph": {
                    "task_results": [TaskResult(
                        task_id="chat-task", route_key=RouteKey.GENERAL_CHAT,
                        status=TaskStatus.SUCCESS, content="聊天回答。",
                    )],
                }}}
                yield {"type": "updates", "ns": (), "data": {
                    "final_response": {"final_response": "云端操作失败。\n聊天回答。"},
                }}

        events = [event async for event in stream_foundation_assistant(
            "复合任务", "mixed-stream", RoutingContext(), graph=MixedGraph(),
        )]
        deltas = [event.delta for event in events if isinstance(event, MessageStreamEvent)]
        self.assertEqual("".join(deltas), "云端操作失败。\n聊天回答。")
        self.assertNotIn("错误地声称", "".join(event.model_dump_json() for event in events))

    async def test_real_main_graph_direct_action_reaches_stream(self) -> None:
        class PauseRouter:
            async def route(self, text, context):
                intent = IntentItem(
                    intent=IntentType.TOOL_CALLING,
                    sub_intent="pause_music",
                    confidence=1.0,
                    source=IntentSource.RULE,
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
                            task_id="pause-task",
                            route_key=RouteKey.TOOL_CALLING,
                            input_text=text,
                            intent_orders=[0],
                        )
                    ],
                )

        graph = build_main_graph(intent_router=PauseRouter()).compile()
        events = [
            event
            async for event in stream_foundation_assistant(
                "暂停一下",
                "real-main-graph",
                RoutingContext(
                    has_current_song=True,
                    current_song_id=185809,
                    queue_size=1,
                    current_index=0,
                ),
                graph=graph,
            )
        ]

        action = next(
            event.action
            for event in events
            if isinstance(event, ClientActionStreamEvent)
        )
        self.assertEqual(action.name, ClientActionName.PAUSE_PLAYER)
        final = next(event for event in events if isinstance(event, FinalStreamEvent))
        self.assertIn("暂停指令", final.response.message)
        self.assertEqual(final.response.status, TaskStatus.SUCCESS)
        self.assertEqual(final.response.results[0].subgraph, RouteKey.TOOL_CALLING)

    async def test_clarification_reaches_final_event(self) -> None:
        class ClarifyRouter:
            async def route(self, text, context):
                return RouteDecision(
                    needs_clarification=True,
                    clarification_question="请明确想了解哪首歌。",
                )

        graph = build_main_graph(intent_router=ClarifyRouter()).compile()
        events = [
            event async for event in stream_foundation_assistant(
                "这首呢", "clarify-thread", RoutingContext(), graph=graph,
            )
        ]

        final = next(item for item in events if isinstance(item, FinalStreamEvent))
        self.assertEqual(final.response.message, "请明确想了解哪首歌。")
        self.assertEqual(final.response.status, TaskStatus.NEEDS_INPUT)
        self.assertEqual(final.response.results, [])

    async def test_long_foundation_answer_is_not_rejected_by_legacy_limit(self) -> None:
        class LongAnswerGraph:
            async def astream(self, *args, **kwargs):
                yield {"type": "updates", "data": {
                    "final_response": {"final_response": "世" * 6_000},
                }}

        events = [
            event async for event in stream_foundation_assistant(
                "生成世界线", "long-answer", RoutingContext(),
                graph=LongAnswerGraph(),
            )
        ]

        final = next(item for item in events if isinstance(item, FinalStreamEvent))
        self.assertEqual(len(final.response.message), 6_000)

    async def test_streams_safe_updates_deduplicated_actions_and_final(self) -> None:
        graph = CountingFoundationGraph()
        routing_context = RoutingContext(
            has_current_song=True,
            current_song_id=185809,
            queue_size=1,
            current_index=0,
        )

        events = [
            event
            async for event in stream_foundation_assistant(
                "暂停一下",
                "foundation-thread",
                routing_context,
                session_cookie="JSESSIONID=trusted-session",
                graph=graph,
            )
        ]

        self.assertEqual(graph.calls, 1)
        self.assertEqual(graph.input["routing_context"], routing_context)
        self.assertEqual(
            graph.config["configurable"]["thread_id"],
            "foundation-thread",
        )
        self.assertEqual(graph.context.session_cookie, "JSESSIONID=trusted-session")
        self.assertEqual(graph.stream_mode, ["messages", "updates"])
        self.assertEqual(graph.version, "v2")
        self.assertEqual(
            [event.node for event in events if isinstance(event, UpdateStreamEvent)],
            ["prepare_context", "tool_calling_subgraph", "final_response"],
        )
        actions = [
            event.action
            for event in events
            if isinstance(event, ClientActionStreamEvent)
        ]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].name, ClientActionName.PAUSE_PLAYER)
        final = next(event for event in events if isinstance(event, FinalStreamEvent))
        self.assertEqual(final.response.message, "已发送暂停指令。")
        serialized = "".join(event.model_dump_json() for event in events)
        self.assertNotIn("trusted-session", serialized)

    async def test_missing_final_response_fails_closed(self) -> None:
        class IncompleteGraph:
            async def astream(self, *args, **kwargs):
                yield {
                    "type": "updates",
                    "data": {"prepare_context": {}},
                }

        with self.assertRaises(RuntimeError):
            _ = [
                event
                async for event in stream_foundation_assistant(
                    "暂停一下",
                    "incomplete-thread",
                    RoutingContext(),
                    graph=IncompleteGraph(),
                )
            ]


if __name__ == "__main__":
    unittest.main()

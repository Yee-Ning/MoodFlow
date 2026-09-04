import asyncio
import unittest
from unittest.mock import AsyncMock

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agent.graph import create_current_song_graph, stream_current_song_assistant
from app.agent.song_assistant import answer_current_song
from app.schemas.chat import FinalStreamEvent, MessageStreamEvent, UpdateStreamEvent
from app.schemas.context import SongContext


def song() -> SongContext:
    return SongContext(
        song_id=185809,
        name="晴天",
        artist="周杰伦",
        album="叶惠美",
        original_lyrics="故事的小黄花",
        translated_lyrics="",
        lyrics_status="AVAILABLE",
    )


class CountingGraph:
    def __init__(self) -> None:
        self.calls = 0
        self.stream_mode = None
        self.version = None

    async def astream(self, *args, **kwargs):
        self.calls += 1
        self.stream_mode = kwargs.get("stream_mode")
        self.version = kwargs.get("version")
        yield {
            "type": "updates",
            "ns": (),
            "data": {
                "load_song_context": {
                    "song_context": {
                        "original_lyrics": "不能发送到浏览器的歌词"
                    }
                }
            },
        }
        yield {
            "type": "updates",
            "ns": (),
            "data": {
                "song_assistant": {
                    "response": {
                        "type": "chat",
                        "message": "完成",
                        "search_query": None,
                    }
                }
            },
        }


class StreamingTests(unittest.TestCase):
    def test_streams_multiple_message_deltas_and_safe_node_updates(self) -> None:
        model = FakeListChatModel(responses=["分段流式回答"])

        async def assistant(messages, current_song_id, context):
            return await answer_current_song(
                messages,
                current_song_id,
                context,
                model=model,
            )

        provider = AsyncMock()
        provider.get_song.return_value = song()
        graph = create_current_song_graph(provider=provider, assistant=assistant)

        async def collect():
            return [
                event
                async for event in stream_current_song_assistant(
                    "分析这首歌",
                    "thread-stream",
                    185809,
                    graph=graph,
                )
            ]

        events = asyncio.run(collect())
        deltas = [
            event.delta for event in events if isinstance(event, MessageStreamEvent)
        ]
        updates = [event for event in events if isinstance(event, UpdateStreamEvent)]
        final = next(event for event in events if isinstance(event, FinalStreamEvent))

        self.assertGreater(len(deltas), 1)
        self.assertEqual("".join(deltas), "分段流式回答")
        self.assertEqual(
            [(event.node, event.status) for event in updates],
            [
                ("load_song_context", "completed"),
                ("song_assistant", "completed"),
            ],
        )
        self.assertEqual(final.response.message, "分段流式回答")
        for event in updates:
            serialized = event.model_dump_json()
            self.assertEqual(
                set(event.model_dump()),
                {"type", "node", "status"},
            )
            self.assertNotIn("歌词", serialized)
            self.assertNotIn("original_lyrics", serialized)

    def test_each_request_executes_graph_only_once(self) -> None:
        graph = CountingGraph()

        async def collect():
            return [
                event
                async for event in stream_current_song_assistant(
                    "分析这首歌",
                    "thread-once",
                    185809,
                    graph=graph,
                )
            ]

        events = asyncio.run(collect())

        self.assertEqual(graph.calls, 1)
        self.assertEqual(graph.stream_mode, ["messages", "updates"])
        self.assertEqual(graph.version, "v2")
        self.assertIsInstance(events[-1], FinalStreamEvent)


if __name__ == "__main__":
    unittest.main()

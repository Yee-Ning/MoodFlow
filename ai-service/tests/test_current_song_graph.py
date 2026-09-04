import asyncio
import unittest
from unittest.mock import AsyncMock

from langchain_core.messages import HumanMessage
from app.agent.graph import create_current_song_graph, create_memory_checkpointer
from app.context.music import MusicContextProviderError
from app.schemas.chat import ChatOutput
from app.schemas.context import SongContext


def song(song_id: int, name: str) -> SongContext:
    return SongContext(
        song_id=song_id,
        name=name,
        artist="测试歌手",
        album="测试专辑",
        original_lyrics="测试歌词",
        translated_lyrics="",
        lyrics_status="AVAILABLE",
    )


async def invoke(graph, thread_id: str, message: str, song_id: int | None):
    result = None
    async for part in graph.astream(
        {
            "messages": [HumanMessage(content=message)],
            "current_song_id": song_id,
            "song_context": None,
            "response": None,
        },
        {"configurable": {"thread_id": thread_id}},
        stream_mode="values",
        version="v2",
    ):
        result = part["data"]
    if result is None:
        raise AssertionError("Graph did not return a final state")
    return result


class CurrentSongGraphTests(unittest.TestCase):
    def test_graph_contains_only_required_nodes(self) -> None:
        graph = create_current_song_graph()

        self.assertEqual(
            set(graph.get_graph().nodes),
            {"__start__", "load_song_context", "song_assistant", "__end__"},
        )

    def test_same_thread_continues_and_new_thread_is_isolated(self) -> None:
        observed_messages: list[list[str]] = []

        async def assistant(messages, current_song_id, context):
            observed_messages.append([str(message.content) for message in messages])
            return ChatOutput(
                type="chat",
                message=f"回答 {len(observed_messages)}",
                search_query=None,
            )

        provider = AsyncMock()
        provider.get_song.return_value = song(1, "第一首")
        graph = create_current_song_graph(
            provider=provider,
            assistant=assistant,
            checkpointer=create_memory_checkpointer(),
        )

        async def scenario() -> None:
            await invoke(graph, "thread-a", "这首歌表达了什么？", 1)
            await invoke(graph, "thread-a", "那第二段呢？", 1)
            await invoke(graph, "thread-b", "这是新会话", 1)

        asyncio.run(scenario())

        self.assertEqual(observed_messages[0], ["这首歌表达了什么？"])
        self.assertEqual(
            observed_messages[1],
            ["这首歌表达了什么？", "回答 1", "那第二段呢？"],
        )
        self.assertEqual(observed_messages[2], ["这是新会话"])

    def test_same_thread_refreshes_context_after_song_change(self) -> None:
        observed_song_ids: list[int | None] = []

        async def assistant(messages, current_song_id, context):
            observed_song_ids.append(context.song_id if context else None)
            return ChatOutput(type="chat", message="已更新", search_query=None)

        provider = AsyncMock()
        provider.get_song.side_effect = [song(1, "第一首"), song(2, "第二首")]
        graph = create_current_song_graph(provider=provider, assistant=assistant)

        async def scenario() -> None:
            await invoke(graph, "thread-switch", "分析这首", 1)
            await invoke(graph, "thread-switch", "现在这首呢", 2)

        asyncio.run(scenario())

        self.assertEqual(observed_song_ids, [1, 2])
        self.assertEqual(
            [call.args[0] for call in provider.get_song.await_args_list],
            [1, 2],
        )

    def test_explicit_none_clears_previous_song_context(self) -> None:
        observed_contexts: list[SongContext | None] = []

        async def assistant(messages, current_song_id, context):
            observed_contexts.append(context)
            return ChatOutput(type="chat", message="收到", search_query=None)

        provider = AsyncMock()
        provider.get_song.return_value = song(1, "第一首")
        graph = create_current_song_graph(provider=provider, assistant=assistant)

        async def scenario() -> dict:
            await invoke(graph, "thread-stop", "分析这首", 1)
            return await invoke(graph, "thread-stop", "已经停止播放", None)

        result = asyncio.run(scenario())

        self.assertEqual(observed_contexts[0].song_id, 1)
        self.assertIsNone(observed_contexts[1])
        self.assertIsNone(result["current_song_id"])
        self.assertIsNone(result["song_context"])
        provider.get_song.assert_awaited_once_with(1)

    def test_spring_failure_degrades_to_empty_context(self) -> None:
        observed_contexts: list[SongContext | None] = []

        async def assistant(messages, current_song_id, context):
            observed_contexts.append(context)
            return ChatOutput(type="chat", message="暂时不可用", search_query=None)

        provider = AsyncMock()
        provider.get_song.side_effect = MusicContextProviderError("unavailable")
        graph = create_current_song_graph(provider=provider, assistant=assistant)

        asyncio.run(invoke(graph, "thread-failure", "分析这首", 1))

        self.assertEqual(observed_contexts, [None])


if __name__ == "__main__":
    unittest.main()

import asyncio
import json
import unittest
from collections.abc import Sequence
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END

from app.agent.graph import (
    MAX_TOOL_CALLS,
    TOOL_LIMIT_MESSAGE,
    create_current_song_graph,
    create_memory_checkpointer,
    route_after_agent,
)
from app.context.music import MusicContextProviderError
from app.schemas.context import MusicAgentContext, SongContext
from app.schemas.music import (
    AddSongToPlaylistArgs,
    GetSongLyricsArgs,
    GetUserPlaylistsArgs,
    SearchMusicArgs,
    UserPlaylist,
    UserPlaylistsResult,
)
from app.tools.music import MUSIC_AGENT_TOOLS, MusicServiceClient


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


def tool_call(name: str, call_id: str, args: dict | None = None) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args or {},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def scripted_assistant(
    responses: Sequence[AIMessage],
    observed: list[list] | None = None,
):
    queue = list(responses)

    async def assistant(messages, current_song_id, context):
        if observed is not None:
            observed.append(list(messages))
        if not queue:
            raise AssertionError("Scripted assistant ran more times than expected")
        return queue.pop(0)

    return assistant


async def invoke(
    graph,
    thread_id: str,
    message: str,
    song_id: int | None = None,
    session_cookie: str | None = None,
):
    result = None
    async for part in graph.astream(
        {
            "messages": [HumanMessage(content=message)],
            "current_song_id": song_id,
            "song_context": None,
            "response": None,
            "tool_call_count": 0,
        },
        {"configurable": {"thread_id": thread_id}},
        context=MusicAgentContext(session_cookie=session_cookie),
        stream_mode="values",
        version="v2",
    ):
        result = part["data"]
    if result is None:
        raise AssertionError("Graph did not return a final state")
    return result


def search_tool(calls: list[dict], *, fail: bool = False) -> BaseTool:
    @tool("search_music", args_schema=SearchMusicArgs)
    async def fake_search_music(keyword: str, limit: int = 10) -> str:
        """Search test music."""
        calls.append({"keyword": keyword, "limit": limit})
        if fail:
            raise RuntimeError("secret upstream error")
        return json.dumps(
            {
                "keyword": keyword,
                "limit": limit,
                "songs": [
                    {
                        "song_id": 11,
                        "name": "第一首",
                        "artists": ["歌手一"],
                        "album": "专辑一",
                    },
                    {
                        "song_id": 22,
                        "name": "第二首",
                        "artists": ["歌手二"],
                        "album": "专辑二",
                    },
                    {
                        "song_id": 33,
                        "name": "第三首",
                        "artists": ["歌手三"],
                        "album": "专辑三",
                    },
                ],
            },
            ensure_ascii=False,
        )

    return fake_search_music


def lyrics_tool(calls: list[dict]) -> BaseTool:
    @tool("get_song_lyrics", args_schema=GetSongLyricsArgs)
    async def fake_get_song_lyrics(song_id: int) -> str:
        """Get test lyrics."""
        calls.append({"song_id": song_id})
        return json.dumps(
            {
                "song_id": song_id,
                "original_lyrics": "歌词",
                "translated_lyrics": "",
                "status": "available",
            },
            ensure_ascii=False,
        )

    return fake_get_song_lyrics


def playlists_tool(calls: list[dict]) -> BaseTool:
    @tool("get_user_playlists", args_schema=GetUserPlaylistsArgs)
    async def fake_get_user_playlists() -> str:
        """Get test playlists."""
        calls.append({})
        return json.dumps(
            {
                "playlists": [
                    {"playlist_id": 77, "name": "深夜学习", "editable": True}
                ]
            },
            ensure_ascii=False,
        )

    return fake_get_user_playlists


def add_tool(calls: list[dict]) -> BaseTool:
    @tool("add_song_to_playlist", args_schema=AddSongToPlaylistArgs)
    async def fake_add_song_to_playlist(playlist_id: int, song_id: int) -> str:
        """Add test song to playlist."""
        calls.append({"playlist_id": playlist_id, "song_id": song_id})
        return json.dumps(
            {
                "playlist_id": playlist_id,
                "song_id": song_id,
                "status": "added",
                "message": "已添加到歌单",
            },
            ensure_ascii=False,
        )

    return fake_add_song_to_playlist


class MusicAgentGraphTests(unittest.TestCase):
    def test_graph_has_tool_node_and_agent_loop_edges(self) -> None:
        graph = create_current_song_graph()
        drawable = graph.get_graph()

        self.assertEqual(
            set(drawable.nodes),
            {"__start__", "load_song_context", "agent", "tools", "__end__"},
        )
        edges = {(edge.source, edge.target) for edge in drawable.edges}
        self.assertIn(("__start__", "load_song_context"), edges)
        self.assertIn(("load_song_context", "agent"), edges)
        self.assertIn(("agent", "tools"), edges)
        self.assertIn(("agent", "__end__"), edges)
        self.assertIn(("tools", "agent"), edges)

    def test_no_tool_call_answers_and_routes_to_end(self) -> None:
        final = AIMessage(content="你好，我可以帮你找歌。")
        graph = create_current_song_graph(
            assistant=scripted_assistant([final]),
        )

        result = asyncio.run(invoke(graph, "direct", "你好"))

        self.assertEqual(result["response"]["message"], final.content)
        self.assertEqual(result["tool_call_count"], 0)
        self.assertEqual(route_after_agent({"messages": [final]}), END)

    def _assert_single_tool(
        self,
        *,
        name: str,
        args: dict,
        fake_tool: BaseTool,
        calls: list[dict],
    ) -> None:
        graph = create_current_song_graph(
            tools=[fake_tool],
            assistant=scripted_assistant(
                [
                    tool_call(name, f"call-{name}", args),
                    AIMessage(content="工具调用完成。"),
                ]
            ),
        )

        result = asyncio.run(invoke(graph, f"thread-{name}", "执行工具"))

        self.assertEqual(calls, [args] if args else [{}])
        self.assertEqual(result["tool_call_count"], 1)
        self.assertIsInstance(result["messages"][-2], ToolMessage)
        self.assertEqual(result["messages"][-2].name, name)
        self.assertEqual(result["response"]["message"], "工具调用完成。")

    def test_search_music_single_tool_call(self) -> None:
        calls: list[dict] = []
        self._assert_single_tool(
            name="search_music",
            args={"keyword": "Open Arms", "limit": 3},
            fake_tool=search_tool(calls),
            calls=calls,
        )

    def test_get_song_lyrics_single_tool_call(self) -> None:
        calls: list[dict] = []
        self._assert_single_tool(
            name="get_song_lyrics",
            args={"song_id": 123},
            fake_tool=lyrics_tool(calls),
            calls=calls,
        )

    def test_get_user_playlists_single_tool_call(self) -> None:
        calls: list[dict] = []
        self._assert_single_tool(
            name="get_user_playlists",
            args={},
            fake_tool=playlists_tool(calls),
            calls=calls,
        )

    def test_add_song_to_playlist_write_tool_call(self) -> None:
        calls: list[dict] = []
        self._assert_single_tool(
            name="add_song_to_playlist",
            args={"playlist_id": 77, "song_id": 33},
            fake_tool=add_tool(calls),
            calls=calls,
        )

    def test_real_tool_receives_session_from_runtime_not_model_args(self) -> None:
        observed_cookies: list[str | None] = []

        async def fake_get_user_playlists(client: MusicServiceClient):
            observed_cookies.append(client._session_cookie)
            return UserPlaylistsResult(
                playlists=[
                    UserPlaylist(
                        playlist_id=77,
                        name="深夜学习",
                        editable=True,
                    )
                ]
            )

        graph = create_current_song_graph(
            tools=MUSIC_AGENT_TOOLS,
            assistant=scripted_assistant(
                [
                    tool_call("get_user_playlists", "runtime-auth"),
                    AIMessage(content="读取完成。"),
                ]
            ),
        )

        with patch.object(
            MusicServiceClient,
            "get_user_playlists",
            fake_get_user_playlists,
        ):
            result = asyncio.run(
                invoke(
                    graph,
                    "runtime-auth",
                    "列出我的歌单",
                    session_cookie="JSESSIONID=trusted-session",
                )
            )

        tool_message = next(
            message for message in result["messages"] if isinstance(message, ToolMessage)
        )
        self.assertEqual(
            observed_cookies,
            ["JSESSIONID=trusted-session"],
            tool_message.content,
        )
        self.assertNotIn("trusted-session", tool_message.content)

    def test_multi_tool_chaining_runs_tools_back_to_agent(self) -> None:
        search_calls: list[dict] = []
        playlist_calls: list[dict] = []
        add_calls: list[dict] = []
        graph = create_current_song_graph(
            tools=[
                search_tool(search_calls),
                playlists_tool(playlist_calls),
                add_tool(add_calls),
            ],
            assistant=scripted_assistant(
                [
                    tool_call("search_music", "search-1", {"keyword": "Open Arms"}),
                    tool_call("get_user_playlists", "playlists-1"),
                    tool_call(
                        "add_song_to_playlist",
                        "add-1",
                        {"playlist_id": 77, "song_id": 11},
                    ),
                    AIMessage(content="已把《第一首》加入“深夜学习”。"),
                ]
            ),
        )

        result = asyncio.run(
            invoke(graph, "tool-chain", "把 Open Arms 加入深夜学习歌单")
        )

        self.assertEqual(len(search_calls), 1)
        self.assertEqual(len(playlist_calls), 1)
        self.assertEqual(add_calls, [{"playlist_id": 77, "song_id": 11}])
        self.assertEqual(result["tool_call_count"], 3)
        self.assertEqual(
            [message.name for message in result["messages"] if isinstance(message, ToolMessage)],
            ["search_music", "get_user_playlists", "add_song_to_playlist"],
        )

    def test_updates_show_tools_to_agent_loop(self) -> None:
        calls: list[dict] = []
        graph = create_current_song_graph(
            tools=[search_tool(calls)],
            assistant=scripted_assistant(
                [
                    tool_call("search_music", "search-loop", {"keyword": "Open Arms"}),
                    AIMessage(content="找到了。"),
                ]
            ),
        )

        async def collect_nodes() -> list[str]:
            nodes: list[str] = []
            async for part in graph.astream(
                {
                    "messages": [HumanMessage(content="搜索 Open Arms")],
                    "current_song_id": None,
                    "song_context": None,
                    "response": None,
                    "tool_call_count": 0,
                },
                {"configurable": {"thread_id": "updates-loop"}},
                context=MusicAgentContext(),
                stream_mode="updates",
                version="v2",
            ):
                nodes.extend(part["data"].keys())
            return nodes

        self.assertEqual(
            asyncio.run(collect_nodes()),
            ["load_song_context", "agent", "tools", "agent"],
        )

    def test_max_tool_call_count_returns_friendly_final_message(self) -> None:
        calls: list[dict] = []
        responses = [
            tool_call("search_music", f"limit-{index}", {"keyword": f"歌曲 {index}"})
            for index in range(MAX_TOOL_CALLS + 1)
        ]
        graph = create_current_song_graph(
            tools=[search_tool(calls)],
            assistant=scripted_assistant(responses),
        )

        result = asyncio.run(invoke(graph, "tool-limit", "不断搜索"))

        self.assertEqual(len(calls), MAX_TOOL_CALLS)
        self.assertEqual(result["tool_call_count"], MAX_TOOL_CALLS)
        self.assertEqual(result["response"]["message"], TOOL_LIMIT_MESSAGE)
        self.assertFalse(result["messages"][-1].tool_calls)

    def test_same_thread_can_add_previous_third_recommendation(self) -> None:
        search_calls: list[dict] = []
        playlist_calls: list[dict] = []
        add_calls: list[dict] = []
        observed: list[list] = []
        graph = create_current_song_graph(
            tools=[
                search_tool(search_calls),
                playlists_tool(playlist_calls),
                add_tool(add_calls),
            ],
            assistant=scripted_assistant(
                [
                    tool_call(
                        "search_music",
                        "recommend-search",
                        {"keyword": "深夜学习", "limit": 3},
                    ),
                    AIMessage(content="1. 第一首\n2. 第二首\n3. 第三首"),
                    tool_call("get_user_playlists", "previous-playlists"),
                    tool_call(
                        "add_song_to_playlist",
                        "previous-add",
                        {"playlist_id": 77, "song_id": 33},
                    ),
                    AIMessage(content="已把刚才第三首加入“深夜学习”歌单。"),
                ],
                observed,
            ),
            checkpointer=create_memory_checkpointer(),
        )

        async def scenario():
            await invoke(graph, "same-thread", "推荐三首适合深夜学习的歌")
            return await invoke(
                graph,
                "same-thread",
                "把刚才第三首加入‘深夜学习’歌单",
            )

        result = asyncio.run(scenario())

        second_turn_messages = observed[2]
        self.assertTrue(
            any(
                isinstance(message, ToolMessage) and '"song_id": 33' in message.content
                for message in second_turn_messages
            )
        )
        self.assertTrue(
            any(
                isinstance(message, AIMessage) and "3. 第三首" in str(message.content)
                for message in second_turn_messages
            )
        )
        self.assertEqual(add_calls, [{"playlist_id": 77, "song_id": 33}])
        self.assertIn("已把刚才第三首", result["response"]["message"])

    def test_new_thread_does_not_inherit_old_messages(self) -> None:
        observed: list[list] = []
        graph = create_current_song_graph(
            assistant=scripted_assistant(
                [AIMessage(content="旧会话"), AIMessage(content="新会话")],
                observed,
            ),
            checkpointer=create_memory_checkpointer(),
        )

        async def scenario():
            await invoke(graph, "old-thread", "旧问题")
            await invoke(graph, "new-thread", "新问题")

        asyncio.run(scenario())

        self.assertEqual([message.content for message in observed[0]], ["旧问题"])
        self.assertEqual([message.content for message in observed[1]], ["新问题"])

    def test_tool_exception_becomes_tool_message_and_agent_degrades(self) -> None:
        calls: list[dict] = []
        observed: list[list] = []
        graph = create_current_song_graph(
            tools=[search_tool(calls, fail=True)],
            assistant=scripted_assistant(
                [
                    tool_call("search_music", "failed-search", {"keyword": "Open Arms"}),
                    AIMessage(content="音乐服务暂时不可用，请稍后再试。"),
                ],
                observed,
            ),
        )

        result = asyncio.run(invoke(graph, "tool-error", "搜索 Open Arms"))

        tool_message = next(
            message for message in observed[1] if isinstance(message, ToolMessage)
        )
        error = json.loads(tool_message.content)
        self.assertFalse(error["success"])
        self.assertEqual(error["error"], "tool_execution_failed")
        self.assertNotIn("secret upstream error", tool_message.content)
        self.assertIn("暂时不可用", result["response"]["message"])

    def test_same_thread_refreshes_and_clears_current_song_context(self) -> None:
        observed_contexts: list[SongContext | None] = []

        async def assistant(messages, current_song_id, context):
            observed_contexts.append(context)
            return AIMessage(content="已更新")

        provider = AsyncMock()
        provider.get_song.side_effect = [song(1, "第一首"), song(2, "第二首")]
        graph = create_current_song_graph(provider=provider, assistant=assistant)

        async def scenario():
            await invoke(graph, "song-switch", "分析这首", 1)
            await invoke(graph, "song-switch", "现在这首呢", 2)
            return await invoke(graph, "song-switch", "停止播放了", None)

        result = asyncio.run(scenario())

        self.assertEqual(
            [context.song_id if context else None for context in observed_contexts],
            [1, 2, None],
        )
        self.assertIsNone(result["song_context"])

    def test_song_context_failure_degrades_without_failing_graph(self) -> None:
        observed_contexts: list[SongContext | None] = []

        async def assistant(messages, current_song_id, context):
            observed_contexts.append(context)
            return AIMessage(content="当前歌曲信息暂时不可用。")

        provider = AsyncMock()
        provider.get_song.side_effect = MusicContextProviderError("unavailable")
        graph = create_current_song_graph(provider=provider, assistant=assistant)

        result = asyncio.run(invoke(graph, "context-failure", "分析这首", 1))

        self.assertEqual(observed_contexts, [None])
        self.assertIn("暂时不可用", result["response"]["message"])


if __name__ == "__main__":
    unittest.main()

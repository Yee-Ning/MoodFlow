"""New music mutations retain the production guard on current user intent."""

import unittest

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from app.agent.foundation.subgraphs.tool_calling.graph import _action_intent_errors
from app.agent.foundation.subgraphs.tool_calling.graph import create_tool_calling_subgraph
from tests.test_tool_calling_subgraph import invoke, scripted_assistant, tool_call


class MusicActionAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def test_music_mutations_require_current_positive_request(self):
        requests = {
            "insert_next_track": ("把晴天放到下一首", {}),
            "append_to_queue": ("把晴天加到队列末尾", {}),
            "play_album": ("播放这张专辑", {}),
            "play_playlist": ("播放我的学习歌单", {}),
            "jump_to_queue_item": ("跳到第 3 首", {"index": 3}),
            "clear_queue": ("清空播放队列", {}),
            "create_playlist_tool": ("新建学习歌单", {"name": "学习"}),
            "remove_song_from_playlist_tool": ("从学习歌单移除晴天", {}),
        }
        cases = [(name, text, args) for name, (text, args) in requests.items()]
        cases += [
            ("set_song_liked_tool", "收藏这首歌", {"liked": True}),
            ("set_song_liked_tool", "取消红心", {"liked": False}),
        ]
        for name, text, args in cases:
            for query, allowed in ((text, True), ("搜索周杰伦", False), ("不要" + text, False)):
                with self.subTest(tool=name, query=query):
                    errors = _action_intent_errors({
                        "current_input": query,
                        "messages": [tool_call(name, "mutation", args)],
                    })
                    self.assertEqual(not errors, allowed)
        self.assertTrue(_action_intent_errors({
            "current_input": "取消红心",
            "messages": [tool_call("set_song_liked_tool", "wrong-like", {"liked": True})],
        }))

    async def test_unrequested_playlist_creation_never_reaches_executor(self):
        calls = []

        @tool("create_playlist_tool")
        async def create_playlist(name: str) -> str:
            """Record an attempted cloud playlist creation."""
            calls.append(name)
            return '{"playlist_id":77,"name":"学习","status":"created","message":"已创建"}'

        observed = []
        graph = create_tool_calling_subgraph(
            tools=[create_playlist], assistant=scripted_assistant([
                tool_call("create_playlist_tool", "create", {"name": "学习"}),
                AIMessage(content="需要用户明确要求才能创建歌单。"),
            ], observed),
        )
        result = await invoke(graph, text="搜索周杰伦")
        self.assertEqual(calls, [])
        self.assertEqual(result["task_result"].data["entities"]["playlists"], [])
        rejection = next(message for message in observed[1]["messages"] if isinstance(message, ToolMessage))
        self.assertIn("unauthorized_action", str(rejection.content))

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from langchain_core.messages import AIMessage

from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.rule_routing import route_by_rules
from app.agent.foundation.subgraphs.tool_calling import (
    CLIENT_ACTION_TOOLS, PlaylistReference, ReferencedEntities, create_tool_calling_subgraph,
)
from app.schemas.music import (
    CreatedPlaylistResult, PlaylistDetailResult, MusicTrack, SongLikeResult,
    RemoveSongFromPlaylistResult, UserPlaylist, UserPlaylistsResult,
)
from app.tools.music import MUSIC_AGENT_TOOLS, MusicServiceClient, MusicServiceError
from tests.test_music_tool import run_with_transport
from tests.test_tool_calling_subgraph import invoke, scripted_assistant, search_tool, tool_call


def like_result(liked=True):
    return SongLikeResult(song_id=101, liked=liked, status="synced", message="已同步")


def playlist_detail():
    return PlaylistDetailResult(
        playlist_id=77, name="学习", description="", creator="测试用户",
        cover_url="", track_count=1,
        songs=[MusicTrack(song_id=101, name="晴天", artists=["周杰伦"],
                          album="叶惠美", cover_url="", duration=269000)],
    )


class MusicAssetClientTests(unittest.TestCase):
    def test_authenticated_writes_use_backend_and_safe_contracts(self):
        cases = [
            ("POST", "/api/songs/101/like", {"liked": "false"},
             {"songId": 101, "liked": False, "status": "synced", "message": "已取消"},
             lambda client: client.set_song_liked(101, False)),
            ("POST", "/api/playlists", {"name": "学习", "privacy": "10"},
             {"playlistId": 77, "name": "学习", "status": "created", "message": "已创建"},
             lambda client: client.create_playlist(" 学习 ", 10)),
            ("DELETE", "/api/playlists/77/songs/101", {},
             {"status": "removed", "message": "已移除"},
             lambda client: client.remove_song_from_playlist(77, 101)),
        ]
        for method, path, params, data, factory in cases:
            with self.subTest(path=path):
                def handler(request):
                    self.assertEqual(request.method, method)
                    self.assertEqual(request.url.path, path)
                    self.assertEqual(dict(request.url.params), params)
                    self.assertEqual(request.headers["cookie"], "JSESSIONID=fake-session")
                    return httpx.Response(200, json={"code": 200, "message": "success", "data": data})
                result = run_with_transport(httpx.MockTransport(handler),
                                            lambda: factory(MusicServiceClient("JSESSIONID=fake-session")))
                self.assertIn(result.status, {"synced", "created", "removed"})

    def test_writes_report_auth_timeout_and_bad_response(self):
        for status in (401, 502):
            with self.subTest(status=status), self.assertRaises(MusicServiceError) as raised:
                run_with_transport(httpx.MockTransport(lambda request: httpx.Response(
                    status, json={"code": status, "message": "操作失败"},
                )), lambda: MusicServiceClient().set_song_liked(101, True))
            self.assertEqual(raised.exception.code, f"http_{status}")

        def timeout(request):
            raise httpx.ReadTimeout("private failure", request=request)
        with self.assertRaises(MusicServiceError) as raised:
            run_with_transport(httpx.MockTransport(timeout),
                               lambda: MusicServiceClient().create_playlist("学习"))
        self.assertEqual(raised.exception.code, "timeout")
        for data in (
            {"songId": 999, "liked": True, "status": "synced", "message": "ok"},
            {"songId": 101, "liked": False, "status": "synced", "message": "ok"},
        ):
            with self.subTest(data=data), self.assertRaises(MusicServiceError) as raised:
                run_with_transport(httpx.MockTransport(lambda request: httpx.Response(
                    200, json={"code": 200, "message": "ok", "data": data},
                )), lambda: MusicServiceClient().set_song_liked(101, True))
            self.assertEqual(raised.exception.code, "invalid_response")
        with self.assertRaises(ValueError):
            asyncio.run(MusicServiceClient().create_playlist(" "))


class MusicAssetGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_then_add_grounds_new_playlist_without_llm_ids(self):
        graph = create_tool_calling_subgraph(
            tools=[search_tool(), *MUSIC_AGENT_TOOLS[1:]],
            assistant=scripted_assistant([
                tool_call("create_playlist_tool", "create", {"name": "学习", "privacy": 10}),
                tool_call("search_music", "search", {"keyword": "晴天"}),
                tool_call("add_song_to_playlist", "add", {"playlist_id": 77, "song_id": 101}),
                AIMessage(content="已新建学习歌单并添加歌曲。"),
            ]),
        )
        created = CreatedPlaylistResult(playlist_id=77, name="学习", status="created", message="已创建")
        from app.schemas.music import AddSongToPlaylistResult
        added = AddSongToPlaylistResult(playlist_id=77, song_id=101, status="added", message="已添加")
        with (
            patch.object(MusicServiceClient, "create_playlist", AsyncMock(return_value=created)) as create,
            patch.object(MusicServiceClient, "add_song_to_playlist", AsyncMock(return_value=added)) as add,
        ):
            result = await invoke(graph, text="新建学习歌单并加入晴天", session_cookie="JSESSIONID=fake")
        create.assert_awaited_once_with("学习", 10)
        add.assert_awaited_once_with(77, 101)
        self.assertEqual(result["task_result"].status, TaskStatus.SUCCESS)
        playlist = result["task_result"].data["entities"]["playlists"][0]
        self.assertEqual((playlist["playlist_id"], playlist["editable"]), (77, True))

    async def test_remove_reads_user_playlists_and_current_details(self):
        graph = create_tool_calling_subgraph(
            tools=MUSIC_AGENT_TOOLS,
            assistant=scripted_assistant([
                tool_call("get_user_playlists", "lists"),
                tool_call("get_playlist_detail_tool", "detail", {"playlist_id": 77}),
                tool_call("remove_song_from_playlist_tool", "remove", {"playlist_id": 77, "song_id": 101}),
                AIMessage(content="已从学习歌单移除晴天。"),
            ]),
        )
        playlists = UserPlaylistsResult(playlists=[UserPlaylist(playlist_id=77, name="学习", editable=True)])
        removed = RemoveSongFromPlaylistResult(playlist_id=77, song_id=101, status="removed", message="已移除")
        with (
            patch.object(MusicServiceClient, "get_user_playlists", AsyncMock(return_value=playlists)),
            patch.object(MusicServiceClient, "get_playlist_detail", AsyncMock(return_value=playlist_detail())),
            patch.object(MusicServiceClient, "remove_song_from_playlist", AsyncMock(return_value=removed)) as remove,
        ):
            result = await invoke(graph, text="把晴天从我的学习歌单移除")
        remove.assert_awaited_once_with(77, 101)
        self.assertEqual(result["task_result"].status, TaskStatus.SUCCESS)
        self.assertEqual(result["task_result"].data["entities"]["songs"][0]["song_id"], 101)

    async def test_remove_remote_failure_is_failed_even_after_successful_read(self):
        playlists = UserPlaylistsResult(playlists=[UserPlaylist(playlist_id=77, name="学习", editable=True)])
        # Read the editable playlist in this turn so its authority survives the detail projection.
        graph = create_tool_calling_subgraph(tools=MUSIC_AGENT_TOOLS, assistant=scripted_assistant([
            tool_call("get_user_playlists", "lists"),
            tool_call("get_playlist_detail_tool", "detail", {"playlist_id": 77}),
            tool_call("remove_song_from_playlist_tool", "remove", {"playlist_id": 77, "song_id": 101}),
            AIMessage(content="已从歌单移除歌曲。"),
        ]))
        with (
            patch.object(MusicServiceClient, "get_user_playlists", AsyncMock(return_value=playlists)),
            patch.object(MusicServiceClient, "get_playlist_detail", AsyncMock(return_value=playlist_detail())),
            patch.object(MusicServiceClient, "remove_song_from_playlist", AsyncMock(side_effect=MusicServiceError("timeout", "移除超时"))) as remove,
        ):
            result = await invoke(graph)
        remove.assert_awaited_once_with(77, 101)
        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)
        self.assertEqual(result["task_result"].error, "移除超时")
        self.assertEqual(result["task_result"].content, "云端操作未完成：移除超时")

    async def test_create_failure_has_no_reusable_playlist_reference(self):
        graph = create_tool_calling_subgraph(tools=MUSIC_AGENT_TOOLS, assistant=scripted_assistant([
            tool_call("create_playlist_tool", "create", {"name": "学习"}),
            AIMessage(content="创建失败。"),
        ]))
        with patch.object(MusicServiceClient, "create_playlist", AsyncMock(side_effect=MusicServiceError("http_401", "请登录"))):
            result = await invoke(graph)
        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)
        self.assertEqual(result["task_result"].data["entities"]["playlists"], [])

    async def test_remove_rejects_missing_detail_wrong_song_and_foreign_playlist(self):
        for read_detail, song_id, editable in ((False, 101, True), (True, 999, True), (True, 101, False)):
            with self.subTest(read_detail=read_detail, song_id=song_id, editable=editable):
                responses = []
                if read_detail:
                    responses.append(tool_call("get_playlist_detail_tool", "detail", {"playlist_id": 77}))
                responses += [tool_call("remove_song_from_playlist_tool", "remove", {
                    "playlist_id": 77, "song_id": song_id,
                }), AIMessage(content="未移除，需确认歌单与歌曲。")]
                graph = create_tool_calling_subgraph(tools=MUSIC_AGENT_TOOLS, assistant=scripted_assistant(responses))
                with (
                    patch.object(MusicServiceClient, "get_playlist_detail", AsyncMock(return_value=playlist_detail())),
                    patch.object(MusicServiceClient, "remove_song_from_playlist", AsyncMock()) as remove,
                ):
                    result = await invoke(graph, prior_entities=[ReferencedEntities(playlists=[
                        PlaylistReference(position=1, playlist_id=77, name="学习", editable=editable),
                    ])])
                remove.assert_not_awaited()
                self.assertEqual(result["task_result"].data["tool_call_details"][-1]["status"], "error")

    async def test_like_server_success_emits_local_action_and_failure_does_not(self):
        for failure in (False, True):
            graph = create_tool_calling_subgraph(
                tools=MUSIC_AGENT_TOOLS,
                assistant=scripted_assistant([
                    tool_call("set_song_liked_tool", "like", {"song_id": 101, "liked": True}),
                    AIMessage(content="同步失败。" if failure else "已同步云端。"),
                ]),
            )
            fake = AsyncMock(side_effect=MusicServiceError("http_401", "请先登录")) if failure else AsyncMock(return_value=like_result())
            with patch.object(MusicServiceClient, "set_song_liked", fake):
                result = await invoke(graph, context=RoutingContext(has_current_song=True, current_song_id=101))
            task = result["task_result"]
            self.assertEqual(len(task.data["client_actions"]), 0 if failure else 1)
            self.assertEqual(task.status, TaskStatus.FAILED if failure else TaskStatus.SUCCESS)

    async def test_like_rejects_fabricated_ids_and_local_only_bypass(self):
        for tool_name, song_id in (("set_song_liked_tool", 999), ("set_song_liked_tool", "999"),
                                   ("set_song_liked_tool", True), ("set_liked", 101)):
            graph = create_tool_calling_subgraph(
                tools=[*MUSIC_AGENT_TOOLS, *CLIENT_ACTION_TOOLS],
                assistant=scripted_assistant([
                    tool_call(tool_name, "bad-like", {"song_id": song_id, "liked": True}),
                    AIMessage(content="未执行收藏。"),
                ]),
            )
            with patch.object(MusicServiceClient, "set_song_liked", AsyncMock()) as sync:
                result = await invoke(graph, context=RoutingContext(has_current_song=True, current_song_id=101))
            sync.assert_not_awaited()
            self.assertEqual(result["task_result"].data["client_actions"], [])

    async def test_direct_like_and_unlike_use_runtime_session_without_model(self):
        for query, liked in (("收藏这首歌", True), ("取消收藏这首歌", False), ("把这首歌的喜欢取消", False)):
            graph = create_tool_calling_subgraph(assistant=scripted_assistant([]))
            cookies = []
            async def sync(client, song_id, target):
                cookies.append(client._session_cookie)
                self.assertEqual((song_id, target), (101, liked))
                return like_result(liked)
            with patch.object(MusicServiceClient, "set_song_liked", sync):
                result = await invoke(graph, text=query, intents=route_by_rules(query).intents,
                                      session_cookie="JSESSIONID=fake-runtime", context=RoutingContext(
                                          has_current_song=True, current_song_id=101,
                                      ))
            self.assertEqual(cookies, ["JSESSIONID=fake-runtime"])
            self.assertEqual(result["task_result"].data["client_actions"][0]["arguments"]["liked"], liked)

    async def test_direct_like_failure_does_not_emit_local_success(self):
        graph = create_tool_calling_subgraph(assistant=scripted_assistant([]))
        with patch.object(MusicServiceClient, "set_song_liked", AsyncMock(
            side_effect=MusicServiceError("timeout", "同步超时"),
        )):
            result = await invoke(graph, text="收藏这首歌", intents=route_by_rules("收藏这首歌").intents,
                                  context=RoutingContext(has_current_song=True, current_song_id=101))
        task = result["task_result"]
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.data["client_actions"], [])
        self.assertEqual(task.content, "同步超时")

    async def test_direct_player_commands_have_no_model_or_server_calls(self):
        cases = (("跳到播放列表第 5 首", "jump_to_queue_item"), ("切到第五首", "jump_to_queue_item"),
                 ("清空播放队列", "clear_queue"), ("展开歌词", "open_lyrics"))
        for query, action in cases:
            graph = create_tool_calling_subgraph(assistant=scripted_assistant([]))
            result = await invoke(graph, text=query, intents=route_by_rules(query).intents,
                                  context=RoutingContext(has_current_song=True, current_song_id=101,
                                                         queue_size=5, current_index=0))
            self.assertEqual(result["task_result"].data["client_actions"][0]["name"], action)
            self.assertEqual(result["task_result"].data["tool_calls"], 0)
        result = await invoke(graph, text="切到第六首", intents=route_by_rules("切到第六首").intents,
                              context=RoutingContext(queue_size=5, current_index=0))
        self.assertEqual(result["task_result"].data["client_actions"], [])
        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)

    def test_cloud_and_direct_routes_reject_negated_commands(self):
        for query, expected in (("新建一个学习歌单", "create_playlist"),
                                ("把晴天从我的学习歌单移除", "remove_song_from_playlist")):
            result = route_by_rules(query)
            self.assertTrue(result.is_complete)
            self.assertEqual([item.sub_intent for item in result.intents], [expected])
        for query in ("不要清空队列", "不要取消收藏这首歌", "不要把这首歌的喜欢取消",
                      "不要展开歌词", "不要切到第5首", "不要新建学习歌单"):
            self.assertEqual(route_by_rules(query).intents, [])

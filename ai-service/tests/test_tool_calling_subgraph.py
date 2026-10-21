import json
import unittest
from collections.abc import Sequence
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from pydantic import ValidationError

from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    RoutingContext,
)
from app.agent.foundation.subgraphs.tool_calling import (
    CLIENT_ACTION_TOOLS,
    AlbumReference,
    ArtistReference,
    ClientActionName,
    PlaylistReference,
    ReferencedEntities,
    SongReference,
    create_tool_calling_subgraph,
)
from app.agent.foundation.subgraphs.tool_calling.graph import (
    TOOL_PHASE_LIMITS,
    TOOL_LIMIT_MESSAGE,
    _extract_entities,
    _track_matches_reference,
)
from app.schemas.context import MusicAgentContext
from app.schemas.music import (
    AlbumDetailResult,
    AddSongToPlaylistArgs,
    SearchMusicArgs,
    MusicEntity,
    MusicTrack,
    PlaylistDetailResult,
    SongLikeResult,
    UserPlaylist,
    UserPlaylistsResult,
)
from app.tools.music import MUSIC_AGENT_TOOLS, MusicServiceClient, MusicServiceError


def routing_intent(sub_intent: str, *, order: int = 0) -> IntentItem:
    evidence = sub_intent.replace("_", " ")
    return IntentItem(
        intent=IntentType.TOOL_CALLING,
        sub_intent=sub_intent,
        confidence=1.0,
        source=IntentSource.RULE,
        evidence=evidence,
        start=0,
        end=len(evidence),
        order=order,
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
    observed: list[dict] | None = None,
):
    queue = list(responses)

    async def assistant(messages, current_input, routing_context, prior_entities):
        if observed is not None:
            observed.append(
                {
                    "messages": list(messages),
                    "current_input": current_input,
                    "routing_context": routing_context,
                    "prior_entities": list(prior_entities),
                }
            )
        if not queue:
            raise AssertionError("Scripted assistant ran more times than expected")
        response = queue.pop(0)
        if any(call["name"] in {"create_playlist_tool", "add_song_to_playlist"}
               for call in response.tool_calls):
            from app.agent.foundation.agent_history_source import bind_produced_message
            from app.agent.foundation.subgraphs.tool_calling.playlist_inheritance import (
                current_invocation, producer_state,
            )

            # Simulated producer binding, not a claim of a real service receipt.
            # Never derive the scope from replayed history or fixture text.
            state = producer_state.get()
            assert state is not None
            response = bind_produced_message(response, current_invocation(state))
        return response

    return assistant


async def invoke(
    graph,
    *,
    text: str = "执行操作",
    intents: list[IntentItem] | None = None,
    context: RoutingContext | None = None,
    prior_entities: list[ReferencedEntities] | None = None,
    session_cookie: str | None = None,
):
    return await graph.ainvoke(
        {
            "messages": [HumanMessage(content=text)],
            "current_input": text,
            "task_id": "tool-task",
            "routing_intents": intents or [],
            "routing_context": context or RoutingContext(),
            "prior_entities": prior_entities or [],
            "tool_calls": [],
            "tool_results": [],
            "client_actions": [],
            "tool_call_count": 0,
            "direct_complete": False,
            "answer": None,
            "task_result": None,
        },
        context=MusicAgentContext(session_cookie=session_cookie),
    )


def search_tool(*, fail: bool = False, timeout: bool = False,
                calls: list[dict] | None = None) -> BaseTool:
    @tool("search_music", args_schema=SearchMusicArgs)
    async def fake_search_music(keyword: str, limit: int = 10) -> str:
        """Search fixture music."""
        if calls is not None:
            calls.append({"keyword": keyword, "limit": limit})
        if timeout:
            raise TimeoutError("private upstream timeout")
        if fail:
            raise RuntimeError("private upstream failure")
        return json.dumps(
            {
                "keyword": keyword,
                "limit": limit,
                "songs": [
                    {
                        "song_id": 101,
                        "name": "测试歌曲",
                        "artists": ["测试歌手"],
                        "album": "测试专辑",
                        "cover_url": "https://example.test/cover.jpg",
                        "duration": 180000,
                    }
                ],
            },
            ensure_ascii=False,
        )

    return fake_search_music


def add_to_playlist_tool(calls: list[dict]) -> BaseTool:
    @tool("add_song_to_playlist", args_schema=AddSongToPlaylistArgs)
    async def fake_add_song_to_playlist(playlist_id: int, song_id: int) -> str:
        """Add fixture music to a playlist."""
        calls.append({"playlist_id": playlist_id, "song_id": song_id})
        return json.dumps(
            {
                "playlist_id": playlist_id,
                "song_id": song_id,
                "status": "added",
                "message": "已添加",
            },
            ensure_ascii=False,
        )

    return fake_add_song_to_playlist


class ToolCallingSubgraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_insert_append_and_jump_emit_client_actions(self) -> None:
        song_payload = {
            "song_id": 101,
            "name": "测试歌曲",
            "artists": ["测试歌手"],
            "album": "测试专辑",
            "cover_url": "https://example.test/cover.jpg",
            "duration": 180000,
        }
        graph = create_tool_calling_subgraph(
            tools=[search_tool(), *CLIENT_ACTION_TOOLS],
            assistant=scripted_assistant([
                tool_call("search_music", "queue-search", {"keyword": "测试歌曲"}),
                tool_call("insert_next_track", "queue-insert", {"song": song_payload}),
                tool_call("append_to_queue", "queue-append", {"song": song_payload}),
                tool_call("jump_to_queue_item", "queue-jump", {"index": 3}),
                AIMessage(content="已发送队列操作。"),
            ]),
        )

        result = await invoke(
            graph,
            text="把测试歌曲放到下一首，随后加到末尾，再跳到第 3 首",
            context=RoutingContext(queue_size=5, current_index=0),
        )

        actions = result["task_result"].data["client_actions"]
        self.assertEqual(
            [action["name"] for action in actions],
            ["insert_next_track", "append_to_queue", "jump_to_queue_item"],
        )
        self.assertEqual(actions[0]["arguments"]["song"]["song_id"], 101)
        self.assertEqual(actions[2]["arguments"], {"index": 3})

    async def test_queue_jump_rejects_out_of_range_index(self) -> None:
        graph = create_tool_calling_subgraph(
            tools=CLIENT_ACTION_TOOLS,
            assistant=scripted_assistant([
                tool_call("jump_to_queue_item", "bad-jump", {"index": 6}),
                AIMessage(content="目标位置超出队列范围。"),
            ]),
        )

        result = await invoke(
            graph,
            text="跳到播放列表第 6 首",
            context=RoutingContext(queue_size=5, current_index=0),
        )

        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)
        self.assertEqual(result["task_result"].data["client_actions"], [])

    async def test_album_detail_can_replace_queue_with_full_album(self) -> None:
        album_track = MusicTrack(
            song_id=185809, name="晴天", artists=["周杰伦"],
            album="叶惠美", duration=269000,
        )

        async def fake_search_entities(client, keyword, *, entity_type=None, limit=5):
            return [MusicEntity(
                entity_id=18905, entity_type="album", name="叶惠美",
                artist="周杰伦", album="叶惠美",
            )]

        async def fake_get_album_detail(client, album_id):
            return AlbumDetailResult(
                album_id=album_id, name="叶惠美", artist="周杰伦",
                cover_url="", songs=[album_track],
            )

        graph = create_tool_calling_subgraph(assistant=scripted_assistant([
            tool_call("search_entities_tool", "album-search", {
                "query": "周杰伦 叶惠美", "entity_type": "album",
            }),
            tool_call("get_album_detail_tool", "album-detail", {"album_id": 18905}),
            tool_call("play_album", "album-play", {
                "album_id": 18905,
                "name": "叶惠美",
                "tracks": [album_track.model_dump(mode="json")],
            }),
            AIMessage(content="已请求播放整张《叶惠美》。"),
        ]))
        with (
            patch.object(MusicServiceClient, "search_entities", fake_search_entities),
            patch.object(MusicServiceClient, "get_album_detail", fake_get_album_detail),
        ):
            result = await invoke(graph, text="播放周杰伦的《叶惠美》这张专辑")

        action = result["task_result"].data["client_actions"][0]
        self.assertEqual(action["name"], "play_album")
        self.assertEqual(action["arguments"]["tracks"][0]["song_id"], 185809)

    async def test_playlist_detail_can_replace_queue_with_full_playlist(self) -> None:
        playlist_track = MusicTrack(
            song_id=185809, name="晴天", artists=["周杰伦"], album="叶惠美",
        )

        async def fake_get_user_playlists(client):
            return UserPlaylistsResult(playlists=[
                UserPlaylist(playlist_id=77, name="私人收藏", editable=True),
            ])

        async def fake_get_playlist_detail(client, playlist_id):
            return PlaylistDetailResult(
                playlist_id=playlist_id, name="私人收藏", description="",
                cover_url="", creator="测试用户", track_count=1,
                songs=[playlist_track],
            )

        graph = create_tool_calling_subgraph(assistant=scripted_assistant([
            tool_call("get_user_playlists", "playlist-list"),
            tool_call("get_playlist_detail_tool", "playlist-detail", {
                "playlist_id": 77,
            }),
            tool_call("play_playlist", "playlist-play", {
                "playlist_id": 77,
                "name": "私人收藏",
                "tracks": [playlist_track.model_dump(mode="json")],
            }),
            AIMessage(content="已请求播放私人收藏歌单。"),
        ]))
        with (
            patch.object(MusicServiceClient, "get_user_playlists", fake_get_user_playlists),
            patch.object(MusicServiceClient, "get_playlist_detail", fake_get_playlist_detail),
        ):
            result = await invoke(graph, text="播放我的私人收藏歌单")

        action = result["task_result"].data["client_actions"][0]
        self.assertEqual(action["name"], "play_playlist")
        self.assertEqual(action["arguments"]["playlist_id"], 77)

    def test_reference_models_share_id_name_type_and_reject_mismatch(self) -> None:
        references = [
            ArtistReference(position=1, id=6452, name="周杰伦"),
            AlbumReference(position=1, id=18905, name="叶惠美"),
            SongReference(position=1, song_id=185809, name="晴天"),
            PlaylistReference(position=1, playlist_id=77, name="学习"),
        ]
        self.assertEqual(
            [item.model_dump()["type"] for item in references],
            ["artist", "album", "song", "playlist"],
        )
        self.assertEqual([item.model_dump()["id"] for item in references],
                         [6452, 18905, 185809, 77])
        with self.assertRaises(ValidationError):
            PlaylistReference(position=1, playlist_id=77, id=88, name="学习")

    def test_detailed_results_upgrade_catalog_references(self) -> None:
        references = _extract_entities([
            {"name": "search_entities_tool", "content": json.dumps({
                "query": "学习", "entity_type": None, "entities": [
                    {"id": 77, "type": "playlist", "name": "学习"},
                    {"id": 185809, "type": "song", "name": "晴天",
                     "artist": "周杰伦", "album": "", "metadata": {}},
                ],
            }, ensure_ascii=False)},
            {"name": "get_user_playlists", "content": UserPlaylistsResult(
                playlists=[UserPlaylist(playlist_id=77, name="学习", editable=True)]
            ).model_dump_json()},
            {"name": "search_music", "content": json.dumps({
                "keyword": "晴天", "limit": 1, "songs": [{
                    "song_id": 185809, "name": "晴天", "artists": ["周杰伦"],
                    "album": "叶惠美", "cover_url": "", "duration": 269000,
                }],
            }, ensure_ascii=False)},
        ])
        self.assertTrue(references.playlists[0].editable)
        self.assertEqual(references.songs[0].album, "叶惠美")
        self.assertTrue(references.songs[0].playable)
        catalog_only = _extract_entities([{"name": "search_entities_tool", "content":
            json.dumps({"query": "晴天", "entities": [{
                "id": 185809, "type": "song", "name": "晴天",
                "artist": "周杰伦", "album": "叶惠美",
            }]}, ensure_ascii=False)}])
        self.assertFalse(_track_matches_reference(
            MusicTrack(song_id=185809, name="晴天", artists=["周杰伦"],
                       album="叶惠美"),
            catalog_only.songs[0],
        ))

    async def test_search_artist_uses_entity_tool_and_keeps_reference(self) -> None:
        queries: list[tuple[str, str | None]] = []
        observed: list[dict] = []

        async def fake_search_entities(client, keyword, *, entity_type=None, limit=5):
            queries.append((keyword, entity_type))
            return [MusicEntity(
                entity_id=6452, entity_type="artist", name="周杰伦",
                artist="周杰伦", cover_url="", metadata={},
            )]

        graph = create_tool_calling_subgraph(
            tools=MUSIC_AGENT_TOOLS,
            assistant=scripted_assistant([
                tool_call("search_entities_tool", "artist-search", {
                    "query": "周杰伦", "entity_type": "artist",
                }),
                AIMessage(content="找到歌手周杰伦。"),
            ], observed),
        )
        with patch.object(MusicServiceClient, "search_entities", fake_search_entities):
            result = await invoke(graph, text="搜索周杰伦")

        self.assertEqual(queries, [("周杰伦", "artist")])
        tool_result = next(message for message in observed[1]["messages"]
                           if isinstance(message, ToolMessage))
        self.assertEqual(json.loads(str(tool_result.content))["entities"][0]["type"],
                         "artist")
        self.assertEqual(json.loads(str(tool_result.content))["entities"][0]["id"],
                         6452)
        self.assertEqual(result["task_result"].status, TaskStatus.SUCCESS)
        artist = result["task_result"].data["entities"]["artists"][0]
        self.assertEqual((artist["id"], artist["name"], artist["type"]),
                         (6452, "周杰伦", "artist"))

    async def test_user_playlist_name_resolves_to_detail_and_songs(self) -> None:
        observed_ids: list[int] = []

        async def fake_get_user_playlists(client):
            return UserPlaylistsResult(playlists=[
                UserPlaylist(playlist_id=77, name="学习", editable=True),
            ])

        async def fake_get_playlist_detail(client, playlist_id):
            observed_ids.append(playlist_id)
            return PlaylistDetailResult(
                playlist_id=playlist_id, name="学习", description="",
                cover_url="", creator="测试用户", track_count=1,
                songs=[MusicTrack(song_id=185809, name="晴天",
                                  artists=["周杰伦"], album="叶惠美")],
            )

        graph = create_tool_calling_subgraph(
            tools=MUSIC_AGENT_TOOLS,
            assistant=scripted_assistant([
                tool_call("get_user_playlists", "my-playlists"),
                tool_call("get_playlist_detail_tool", "playlist-detail", {
                    "playlist_id": 77,
                }),
                AIMessage(content="你的学习歌单里有《晴天》，歌手是周杰伦。"),
            ]),
        )
        with (
            patch.object(MusicServiceClient, "get_user_playlists", fake_get_user_playlists),
            patch.object(MusicServiceClient, "get_playlist_detail", fake_get_playlist_detail),
        ):
            result = await invoke(graph, text="查看我的学习歌单里面有哪些歌")

        self.assertEqual(observed_ids, [77])
        self.assertEqual(result["task_result"].status, TaskStatus.SUCCESS)
        self.assertEqual(result["task_result"].data["entities"]["songs"][0]["song_id"], 185809)
        self.assertIn("晴天", result["task_result"].content)

    async def test_playlist_detail_rejects_untrusted_id(self) -> None:
        calls: list[int] = []

        async def fake_get_playlist_detail(client, playlist_id):
            calls.append(playlist_id)
            raise AssertionError("untrusted ID should never reach backend")

        graph = create_tool_calling_subgraph(
            tools=MUSIC_AGENT_TOOLS,
            assistant=scripted_assistant([
                tool_call("get_playlist_detail_tool", "untrusted-detail", {
                    "playlist_id": 999,
                }),
                AIMessage(content="请先确定歌单。"),
            ]),
        )
        with patch.object(MusicServiceClient, "get_playlist_detail", fake_get_playlist_detail):
            result = await invoke(graph, text="查看歌单")
        self.assertEqual(calls, [])
        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)

    async def test_playlist_detail_api_failure_is_safe(self) -> None:
        observed: list[dict] = []

        async def fake_get_playlist_detail(client, playlist_id):
            raise MusicServiceError("timeout", "歌单详情暂时无法读取。")

        graph = create_tool_calling_subgraph(
            tools=MUSIC_AGENT_TOOLS,
            assistant=scripted_assistant([
                tool_call("get_playlist_detail_tool", "failed-detail", {
                    "playlist_id": 77,
                }),
                AIMessage(content="歌单详情暂时无法读取。"),
            ], observed),
        )
        prior = ReferencedEntities(playlists=[
            PlaylistReference(position=1, playlist_id=77, name="学习")
        ])
        with patch.object(MusicServiceClient, "get_playlist_detail", fake_get_playlist_detail):
            result = await invoke(graph, text="查看学习歌单", prior_entities=[prior])

        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)
        tool_message = next(message for message in observed[1]["messages"]
                            if isinstance(message, ToolMessage))
        payload = json.loads(str(tool_message.content))
        self.assertEqual(payload["error"], "timeout")
        self.assertNotIn("Traceback", str(tool_message.content))

    async def test_graph_contains_direct_and_tool_loop_paths(self) -> None:
        graph = create_tool_calling_subgraph(
            assistant=scripted_assistant([AIMessage(content="完成")])
        )
        drawable = graph.get_graph()

        self.assertEqual(
            set(drawable.nodes),
            {
                "__start__",
                "direct_actions",
                "agent",
                "validate_tool_calls",
                "tools",
                "capture_tool_results",
                "reject_tool_calls",
                "__end__",
            },
        )
        edges = {(edge.source, edge.target) for edge in drawable.edges}
        self.assertIn(("direct_actions", "__end__"), edges)
        self.assertIn(("agent", "validate_tool_calls"), edges)
        self.assertIn(("validate_tool_calls", "tools"), edges)
        self.assertIn(("validate_tool_calls", "reject_tool_calls"), edges)
        self.assertIn(("tools", "capture_tool_results"), edges)
        self.assertIn(("capture_tool_results", "agent"), edges)

    async def test_rule_pause_uses_deterministic_path_without_llm(self) -> None:
        observed: list[dict] = []
        graph = create_tool_calling_subgraph(
            assistant=scripted_assistant([], observed)
        )
        result = await invoke(
            graph,
            text="暂停一下",
            intents=[routing_intent("pause_music")],
            context=RoutingContext(
                has_current_song=True,
                current_song_id=101,
                queue_size=1,
                current_index=0,
            ),
        )

        task = result["task_result"]
        self.assertEqual(task.status, TaskStatus.SUCCESS)
        self.assertEqual(observed, [])
        self.assertEqual(task.data["tool_calls"], 0)
        self.assertEqual(
            task.data["client_actions"][0]["name"],
            ClientActionName.PAUSE_PLAYER,
        )
        self.assertEqual(task.data["client_actions"][0]["arguments"], {})

    async def test_direct_actions_are_explicit_and_volume_is_clamped(self) -> None:
        graph = create_tool_calling_subgraph(
            assistant=scripted_assistant([])
        )
        with patch.object(MusicServiceClient, "set_song_liked", AsyncMock(return_value=SongLikeResult(
            song_id=101, liked=True, status="synced", message="已同步",
        ))) as sync:
            result = await invoke(
                graph,
                intents=[
                    routing_intent("lower_volume", order=0),
                    routing_intent("favorite_song", order=1),
                ],
                context=RoutingContext(
                    has_current_song=True, current_song_id=101, volume=0.1,
                    queue_size=1, current_index=0, current_song_liked=False,
                ),
            )
        sync.assert_awaited_once_with(101, True)
        self.assertEqual(result["task_result"].data["tool_calls"], 1)

        actions = result["task_result"].data["client_actions"]
        self.assertEqual([item["name"] for item in actions], ["set_volume", "set_liked"])
        self.assertEqual(actions[0]["arguments"], {"volume": 0.0})
        self.assertEqual(actions[1]["arguments"], {"song_id": 101, "liked": True})

    async def test_missing_current_song_requests_input_without_side_effect(self) -> None:
        graph = create_tool_calling_subgraph(
            assistant=scripted_assistant([])
        )
        result = await invoke(
            graph,
            text="收藏这首歌",
            intents=[routing_intent("favorite_song")],
        )

        task = result["task_result"]
        self.assertEqual(task.status, TaskStatus.NEEDS_INPUT)
        self.assertEqual(task.data["client_actions"], [])
        self.assertIn("先选择", task.content)

    async def test_search_then_play_emits_action_and_reference_entities(self) -> None:
        play_song_tool = next(
            item for item in CLIENT_ACTION_TOOLS if item.name == "play_song"
        )
        graph = create_tool_calling_subgraph(
            tools=[search_tool(), play_song_tool],
            assistant=scripted_assistant(
                [
                    tool_call(
                        "search_music",
                        "search-1",
                        {"keyword": "测试歌曲", "limit": 1},
                    ),
                    tool_call(
                        "play_song",
                        "play-1",
                        {
                            "song": {
                                "song_id": 101,
                                "name": "测试歌曲",
                                "artists": ["测试歌手"],
                                "album": "测试专辑",
                                "cover_url": "https://example.test/cover.jpg",
                                "duration": 180000,
                            }
                        },
                    ),
                    AIMessage(content="已请求播放《测试歌曲》。"),
                ]
            ),
        )

        result = await invoke(graph, text="搜索并播放测试歌曲")

        task = result["task_result"]
        self.assertEqual(task.status, TaskStatus.SUCCESS)
        self.assertEqual(task.data["tool_calls"], 2)
        self.assertEqual(
            [item["name"] for item in task.data["tool_call_details"]],
            ["search_music", "play_song"],
        )
        self.assertEqual(task.data["client_actions"][0]["name"], "play_song")
        self.assertEqual(
            task.data["client_actions"][0]["arguments"]["song"]["song_id"],
            101,
        )
        self.assertEqual(task.data["entities"]["songs"][0]["position"], 1)
        self.assertEqual(task.data["entities"]["songs"][0]["song_id"], 101)

    async def test_search_then_play_rejects_unrequested_favorite_from_model(self) -> None:
        play_song_tool = next(item for item in CLIENT_ACTION_TOOLS if item.name == "play_song")
        liked_tool = next(item for item in CLIENT_ACTION_TOOLS if item.name == "set_liked")
        search = tool_call("search_music", "search-1", {"keyword": "测试歌曲"})
        liked = tool_call("set_liked", "liked-1", {"song_id": 101, "liked": True})
        observed: list[dict] = []
        graph = create_tool_calling_subgraph(
            tools=[search_tool(), play_song_tool, liked_tool],
            assistant=scripted_assistant([
                AIMessage(content="", tool_calls=[*search.tool_calls, *liked.tool_calls]),
                tool_call("search_music", "search-2", {"keyword": "测试歌曲"}),
                tool_call("play_song", "play-1", {"song": {
                    "song_id": 101, "name": "测试歌曲", "artists": ["测试歌手"],
                    "album": "测试专辑", "cover_url": "https://example.test/cover.jpg",
                    "duration": 180000,
                }}),
                AIMessage(content="已请求播放。"),
            ], observed),
        )

        result = await invoke(graph, text="搜索测试歌曲，然后播放它")

        actions = result["task_result"].data["client_actions"]
        self.assertEqual([action["name"] for action in actions], ["play_song"])
        rejected = [json.loads(str(message.content))["error"]
                    for message in observed[1]["messages"] if isinstance(message, ToolMessage)]
        self.assertIn("unauthorized_action", rejected)
        self.assertNotIn("set_liked", [item["name"] for item in result["task_result"].data["tool_call_details"] if item["status"] == "success"])

    async def test_explicit_favorite_is_allowed_but_negated_favorite_is_not(self) -> None:
        liked_tool = next(item for item in MUSIC_AGENT_TOOLS if item.name == "set_song_liked_tool")
        context = RoutingContext(has_current_song=True, current_song_id=101)
        graph = create_tool_calling_subgraph(
            tools=[liked_tool],
            assistant=scripted_assistant([
                tool_call("set_song_liked_tool", "liked-yes", {"song_id": 101, "liked": True}),
                AIMessage(content="已发送收藏指令。"),
            ]),
        )
        synced = SongLikeResult(song_id=101, liked=True, status="synced", message="已同步")
        with patch.object(MusicServiceClient, "set_song_liked", AsyncMock(return_value=synced)) as sync:
            allowed = await invoke(graph, text="收藏当前这首歌", context=context)
        sync.assert_awaited_once_with(101, True)
        self.assertEqual([a["name"] for a in allowed["task_result"].data["client_actions"]], ["set_liked"])

        graph = create_tool_calling_subgraph(
            tools=[liked_tool],
            assistant=scripted_assistant([
                tool_call("set_song_liked_tool", "liked-no", {"song_id": 101, "liked": True}),
                AIMessage(content="不会收藏。"),
            ]),
        )
        with patch.object(MusicServiceClient, "set_song_liked", AsyncMock()) as sync:
            denied = await invoke(graph, text="不要收藏这首歌，分析它", context=context)
        sync.assert_not_awaited()
        self.assertEqual(denied["task_result"].data["client_actions"], [])

    async def test_prior_entities_are_passed_to_assistant(self) -> None:
        prior = ReferencedEntities(
            songs=[
                SongReference(
                    position=1,
                    song_id=101,
                    name="测试歌曲",
                    artists=["测试歌手"],
                )
            ],
            playlists=[
                PlaylistReference(
                    position=1,
                    playlist_id=77,
                    name="学习",
                    editable=True,
                )
            ],
        )
        observed: list[dict] = []
        graph = create_tool_calling_subgraph(
            assistant=scripted_assistant([AIMessage(content="已识别历史对象。")], observed)
        )

        await invoke(graph, text="把刚才第一首加入刚才的歌单", prior_entities=[prior])

        self.assertEqual(observed[0]["prior_entities"], [prior])
        self.assertEqual(observed[0]["current_input"], "把刚才第一首加入刚才的歌单")

    async def test_trusted_prior_ids_allow_playlist_mutation(self) -> None:
        calls: list[dict] = []
        prior = ReferencedEntities(
            songs=[
                SongReference(
                    position=1,
                    song_id=101,
                    name="测试歌曲",
                    artists=["测试歌手"],
                )
            ],
            playlists=[
                PlaylistReference(
                    position=1,
                    playlist_id=77,
                    name="学习",
                    editable=True,
                )
            ],
        )
        graph = create_tool_calling_subgraph(
            tools=[add_to_playlist_tool(calls)],
            assistant=scripted_assistant(
                [
                    tool_call(
                        "add_song_to_playlist",
                        "trusted-add",
                        {"playlist_id": 77, "song_id": 101},
                    ),
                    AIMessage(content="已加入歌单。"),
                ]
            ),
        )

        result = await invoke(
            graph,
            text="把刚才第一首加入刚才的歌单",
            prior_entities=[prior],
        )

        self.assertEqual(calls, [{"playlist_id": 77, "song_id": 101}])
        self.assertEqual(result["task_result"].status, TaskStatus.SUCCESS)

    async def test_untrusted_song_payload_is_rejected_before_client_action(self) -> None:
        play_song_tool = next(
            item for item in CLIENT_ACTION_TOOLS if item.name == "play_song"
        )
        observed: list[dict] = []
        graph = create_tool_calling_subgraph(
            tools=[play_song_tool],
            assistant=scripted_assistant(
                [
                    tool_call(
                        "play_song",
                        "hallucinated-play",
                        {
                            "song": {
                                "song_id": 999,
                                "name": "不存在的结果",
                                "artists": ["未知"],
                                "album": "",
                                "cover_url": "",
                                "duration": 0,
                            }
                        },
                    ),
                    AIMessage(content="我还需要先搜索这首歌。"),
                ],
                observed,
            ),
        )

        result = await invoke(graph, text="播放不存在的结果")

        task = result["task_result"]
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.data["client_actions"], [])
        rejected = next(
            message
            for message in observed[1]["messages"]
            if isinstance(message, ToolMessage)
        )
        payload = json.loads(str(rejected.content))
        self.assertEqual(payload["error"], "untrusted_entity_reference")

    async def test_authenticated_server_tool_receives_runtime_cookie(self) -> None:
        observed_cookies: list[str | None] = []

        async def fake_get_user_playlists(client: MusicServiceClient):
            observed_cookies.append(client._session_cookie)
            return UserPlaylistsResult(
                playlists=[
                    UserPlaylist(
                        playlist_id=77,
                        name="学习",
                        editable=True,
                    )
                ]
            )

        graph = create_tool_calling_subgraph(
            tools=MUSIC_AGENT_TOOLS,
            assistant=scripted_assistant(
                [
                    tool_call("get_user_playlists", "playlist-1"),
                    AIMessage(content="已读取歌单。"),
                ]
            ),
        )
        with patch.object(
            MusicServiceClient,
            "get_user_playlists",
            fake_get_user_playlists,
        ):
            result = await invoke(
                graph,
                text="列出我的歌单",
                session_cookie="JSESSIONID=trusted-session",
            )

        self.assertEqual(observed_cookies, ["JSESSIONID=trusted-session"])
        self.assertEqual(
            result["task_result"].data["entities"]["playlists"][0]["playlist_id"],
            77,
        )
        self.assertNotIn("trusted-session", result["task_result"].model_dump_json())

    async def test_tool_failure_is_safe_and_marks_task_failed(self) -> None:
        observed: list[dict] = []
        graph = create_tool_calling_subgraph(
            tools=[search_tool(fail=True)],
            assistant=scripted_assistant(
                [
                    tool_call("search_music", "failed-search", {"keyword": "测试"}),
                    AIMessage(content="音乐服务暂时不可用，请稍后再试。"),
                ],
                observed,
            ),
        )

        result = await invoke(graph, text="搜索测试")

        tool_message = next(
            message
            for message in observed[1]["messages"]
            if isinstance(message, ToolMessage)
        )
        self.assertNotIn("private upstream failure", str(tool_message.content))
        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)
        self.assertTrue(result["task_result"].error)

    async def test_tool_timeout_is_reported_without_private_details(self) -> None:
        graph = create_tool_calling_subgraph(
            tools=[search_tool(timeout=True)],
            assistant=scripted_assistant([
                tool_call("search_music", "timeout-1", {"keyword": "测试"}),
                AIMessage(content="搜索暂时失败，请稍后再试。"),
            ]),
        )

        result = await invoke(graph, text="搜索测试")

        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)
        self.assertNotIn("private upstream timeout", result["task_result"].model_dump_json())

    async def test_invalid_tool_arguments_never_execute(self) -> None:
        calls: list[dict] = []
        graph = create_tool_calling_subgraph(
            tools=[search_tool(calls=calls)],
            assistant=scripted_assistant([
                tool_call("search_music", "invalid-1", {"keyword": "测试", "limit": -1}),
                AIMessage(content="搜索参数无效，请重新描述。"),
            ]),
        )

        result = await invoke(graph, text="搜索测试")

        self.assertEqual(calls, [])
        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)

    async def test_invalid_batch_rejects_valid_call_before_any_execution(self) -> None:
        calls: list[dict] = []
        valid = tool_call("search_music", "valid-search", {"keyword": "测试"})
        invalid = tool_call("search_music", "invalid-search", {"limit": 0})
        batch = AIMessage(content="", tool_calls=[*valid.tool_calls, *invalid.tool_calls])
        observed: list[dict] = []
        graph = create_tool_calling_subgraph(
            tools=[search_tool(calls=calls)],
            assistant=scripted_assistant([
                batch,
                AIMessage(content="参数不完整，请重新说明。"),
            ], observed),
        )

        result = await invoke(graph, text="搜索测试")

        self.assertEqual(calls, [])
        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)
        errors = [
            json.loads(str(message.content))["error"]
            for message in observed[1]["messages"]
            if isinstance(message, ToolMessage)
        ]
        self.assertIn("invalid_tool_arguments", errors)
        self.assertIn("batch_rejected", errors)

    async def test_unknown_tool_is_rejected_by_allowlist(self) -> None:
        calls: list[dict] = []
        observed: list[dict] = []
        graph = create_tool_calling_subgraph(
            tools=[search_tool(calls=calls)],
            assistant=scripted_assistant([
                tool_call("delete_everything", "unknown-1"),
                AIMessage(content="无法执行该操作。"),
            ], observed),
        )

        result = await invoke(graph, text="执行未知工具")

        self.assertEqual(calls, [])
        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)
        rejected = next(
            message for message in observed[1]["messages"]
            if isinstance(message, ToolMessage)
        )
        self.assertEqual(json.loads(str(rejected.content))["error"], "unknown_tool")

    async def test_unparseable_tool_call_fails_without_executing_valid_batch_member(self) -> None:
        calls: list[dict] = []
        valid = tool_call("search_music", "valid-1", {"keyword": "测试"})
        malformed = AIMessage(
            content="",
            tool_calls=valid.tool_calls,
            invalid_tool_calls=[{
                "name": "search_music", "args": "{broken", "id": "broken-1",
                "error": "invalid JSON",
            }],
        )
        graph = create_tool_calling_subgraph(
            tools=[search_tool(calls=calls)],
            assistant=scripted_assistant([malformed]),
        )

        result = await invoke(graph, text="搜索测试")

        self.assertEqual(calls, [])
        self.assertEqual(result["task_result"].status, TaskStatus.FAILED)
        self.assertEqual(result["task_result"].data["reason"], "malformed_tool_call")
        self.assertNotIn("{broken", result["task_result"].model_dump_json())

    async def test_duplicate_tool_calls_do_not_execute_twice(self) -> None:
        calls: list[dict] = []
        repeated = tool_call("search_music", "same-2", {"keyword": "测试"})
        graph = create_tool_calling_subgraph(
            tools=[search_tool(calls=calls)],
            assistant=scripted_assistant([
                tool_call("search_music", "same-1", {"keyword": "测试"}),
                repeated,
                AIMessage(content="已使用首次搜索结果。"),
            ]),
        )

        result = await invoke(graph, text="搜索测试")

        self.assertEqual(calls, [{"keyword": "测试", "limit": 10}])
        self.assertEqual(result["task_result"].data["tool_calls"], 2)
        self.assertEqual(result["task_result"].data["tool_call_details"][1]["status"], "error")

    async def test_duplicate_batch_can_retry_once_without_double_execution(self) -> None:
        calls: list[dict] = []
        first = tool_call("search_music", "batch-1", {"keyword": "测试"})
        second = tool_call("search_music", "batch-2", {"keyword": "测试"})
        batch = AIMessage(content="", tool_calls=[*first.tool_calls, *second.tool_calls])
        graph = create_tool_calling_subgraph(
            tools=[search_tool(calls=calls)],
            assistant=scripted_assistant([
                batch,
                tool_call("search_music", "retry-1", {"keyword": "测试"}),
                AIMessage(content="已搜索一次。"),
            ]),
        )

        result = await invoke(graph, text="搜索测试")

        self.assertEqual(calls, [{"keyword": "测试", "limit": 10}])
        self.assertEqual(result["task_result"].status, TaskStatus.SUCCESS)

    async def test_tool_limit_stops_loop_with_failed_result(self) -> None:
        responses = [
            tool_call(
                "search_music",
                f"search-{index}",
                {"keyword": f"测试 {index}"},
            )
            for index in range(TOOL_PHASE_LIMITS["retrieval"] + 1)
        ]
        graph = create_tool_calling_subgraph(
            tools=[search_tool()],
            assistant=scripted_assistant(responses),
        )

        result = await invoke(graph, text="不断搜索")

        task = result["task_result"]
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.content, TOOL_LIMIT_MESSAGE)
        self.assertEqual(task.data["tool_calls"], TOOL_PHASE_LIMITS["retrieval"])
        self.assertEqual(task.data["tool_budget_stop"]["budget"], "retrieval")


if __name__ == "__main__":
    unittest.main()

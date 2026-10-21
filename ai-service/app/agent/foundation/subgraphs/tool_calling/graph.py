import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, ValidationError

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.tool_calling.assistant import (
    ToolCallingAssistant,
    all_tool_calling_tools,
    answer_tool_calling_task,
    bind_tool_calling_tools,
)
from app.agent.foundation.subgraphs.tool_calling.client_tools import (
    CLIENT_ACTION_TOOL_NAMES,
)
from app.agent.foundation.subgraphs.tool_calling.direct_actions import (
    make_action_id,
    resolve_direct_actions,
)
from app.agent.foundation.subgraphs.tool_calling.models import (
    AlbumReference,
    ArtistReference,
    ClientAction,
    ClientActionName,
    PendingClientActionPayload,
    PlaylistReference,
    ReferencedEntities,
    SongReference,
    SetLikedArguments,
    ToolExecutionRecord,
    ToolSelection,
)
from app.agent.structured_output import (
    SemanticValidationError,
    StructuredOutputError,
    StructuredOutputService,
)
from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState
from app.agent.foundation.subgraphs.tool_calling.scope import is_simple_song_playback, SONG_PLAYBACK_TOOLS
from app.schemas.context import MusicAgentContext
from app.schemas.music import (
    AlbumDetailResult,
    CreatedPlaylistResult,
    EntitySearchResult,
    MusicSearchResult,
    MusicTrack,
    PlaylistDetailResult,
    SongLikeResult,
    ToolFailure,
    UserPlaylistsResult,
)
from app.tools.music import handle_music_tool_error
from app.agent.foundation.long_horizon.operations import WRITES, journal_tool_call
from app.agent.foundation.agent_history_source import bind_tool_result
from app.agent.foundation.subgraphs.tool_calling.playlist_inheritance import (
    capture_created_receipts,
    inherited_playlist_id,
    producer_state,
    requests_inherited_add,
)

MAX_TOOL_CALLS = 12
TOOL_PHASE_LIMITS = {"retrieval": 8, "execution": 8}
logger = logging.getLogger(__name__)
MAX_VISIBLE_RESPONSE_CHARACTERS = 2_000
TOOL_LIMIT_MESSAGE = (
    "这次任务需要的工具调用次数超过安全上限，我已停止继续操作。"
    "请缩小请求范围或分成两次完成。"
)
INVALID_TOOL_OUTPUT_MESSAGE = "工具选择或参数格式无效，已停止后续工具执行。"


def _tool_call_phase(name: str) -> str:
    return "execution" if name in WRITES or name in CLIENT_ACTION_TOOL_NAMES else "retrieval"


def _tool_phase_counts(state: ToolCallingState) -> dict[str, int]:
    counts = state.get("tool_call_counts_by_phase")
    if counts is not None:
        return {phase: counts.get(phase, 0) for phase in TOOL_PHASE_LIMITS}
    # Adapters start each task at zero. Preserve admitted attempts if an existing
    # caller supplies a populated private state without the new optional field.
    counts = dict.fromkeys(TOOL_PHASE_LIMITS, 0)
    for call in state.get("tool_calls", []):
        counts[_tool_call_phase(str(call.get("name") or ""))] += 1
    return counts


# Tool output and conversation history can supply entity references, but only
# the assigned user clause can authorize a player or library mutation.
_ACTION_REQUESTS = {
    "pause_player": r"暂停|停一下|停住",
    "resume_player": r"继续播放|恢复播放|接着播放",
    "play_next": r"下一首|下一曲|切歌|换歌",
    "play_previous": r"上一首|上一曲|切回上一首",
    "set_volume": r"音量|调大|调小|关小|声音大|声音小",
    "set_muted": r"静音|取消静音",
    "seek_to": r"进度|跳到|定位到|拖到|快进|快退",
    "seek_by": r"快进|快退|前进|后退|进度",
    "set_play_mode": r"播放模式|单曲循环|列表循环|随机播放|顺序播放",
    "play_song": r"播放|放一首|来一首|听一下|听这首|听它",
    "insert_next_track": r"下一首|接下来播放|下一个播放|插播",
    "append_to_queue": r"(?:加到|加入|添加到|放到).{0,8}(?:队列|列表|末尾|最后)|(?:稍后|待会儿?|等会儿?)(?:播放|放)",
    "play_album": r"播放|放|听",
    "play_playlist": r"播放|放|听",
    "jump_to_queue_item": r"切到|跳到|切换到",
    "clear_queue": r"清空",
    "set_liked": r"收藏|喜欢|点赞|点个喜欢",
    "set_song_liked_tool": r"收藏|喜欢|点赞|点个喜欢|红心",
    "create_playlist_tool": r"(?:新建|创建).{0,40}歌单",
    "remove_song_from_playlist_tool": r"移除|删除",
    "delete_playlist_tool": r"(?:确认|确定).{0,6}删除.{0,20}歌单",
    "open_lyrics": r"打开歌词|显示歌词|查看歌词|展开歌词",
    "add_song_to_playlist": r"加入.{0,8}歌单|添加到.{0,8}歌单|放进.{0,8}歌单|(?:新建|创建).{0,40}歌单.{0,40}(?:加入|添加|放进)",
}
_ACTION_CLAUSE = re.compile(r"[,，.。!！?？;；]|然后|接着|随后|并且|同时|以及|但是|不过|但")
_ACTION_NEGATION = re.compile(r"(?:别|不要|不用|无需|不再|不要再)[^,，.。!！?？;；]{0,8}$")


def _action_intent_errors(
    state: ToolCallingState, *, inherited_playlist_id: int | None = None,
) -> dict[str, str]:
    text = state["current_input"]
    clauses = _ACTION_CLAUSE.split(text)
    errors: dict[str, str] = {}
    for call in _latest_tool_calls(state):
        name = call.get("name")
        pattern = _ACTION_REQUESTS.get(name)
        if pattern is None:
            continue
        args = call.get("args") or {}
        if name in {"set_liked", "set_song_liked_tool"} and args.get("liked") is False:
            pattern = r"取消收藏|取消喜欢|取消红心|不喜欢|移除收藏"
        allowed = False
        for clause in clauses:
            for match in re.finditer(pattern, clause, re.IGNORECASE):
                if _ACTION_NEGATION.search(clause[:match.start()]):
                    continue
                if name in {"set_liked", "set_song_liked_tool"} and args.get("liked") is True and re.search(
                    r"(?:取消|不)\s*$", clause[:match.start()]
                ):
                    continue
                allowed = True
                break
            if allowed:
                break
        if (not allowed and name == "add_song_to_playlist"
                and inherited_playlist_id is not None
                and type(args.get("playlist_id")) is int
                and args["playlist_id"] == inherited_playlist_id):
            allowed = requests_inherited_add(
                text, _ACTION_REQUESTS["create_playlist_tool"], _ACTION_CLAUSE, _ACTION_NEGATION,
            )
        if not allowed:
            errors[str(call.get("id") or "")] = "当前任务没有明确请求这个操作；请只执行本轮用户明确要求的动作。"
    return errors


def _scoped_action_intent_errors(state: ToolCallingState) -> dict[str, str]:
    # The same per-call proof is used by routing and rejection diagnostics.
    errors = _action_intent_errors(state)
    for call in _latest_tool_calls(state):
        call_id = str(call.get("id") or "")
        if call_id not in errors:
            continue
        playlist_id = inherited_playlist_id(state, call)
        if playlist_id is not None:
            inherited_errors = _action_intent_errors(state, inherited_playlist_id=playlist_id)
            if call_id not in inherited_errors:
                errors.pop(call_id)
    return errors


def _route_after_direct(state: ToolCallingState) -> str:
    return END if state.get("direct_complete") else "agent"


def _route_after_agent(state: ToolCallingState) -> str:
    return tools_condition(state)


def _read_json(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _extract_entities(tool_results: list[dict[str, Any]]) -> ReferencedEntities:
    songs: list[SongReference] = []
    artists: list[ArtistReference] = []
    albums: list[AlbumReference] = []
    playlists: list[PlaylistReference] = []
    seen_song_ids: set[int] = set()
    seen_artist_ids: set[int] = set()
    seen_album_ids: set[int] = set()
    seen_playlist_ids: set[int] = set()
    for result in tool_results:
        content = result.get("content")
        if result.get("name") == "search_music" and isinstance(content, str):
            try:
                parsed = MusicSearchResult.model_validate_json(content)
            except ValueError:
                continue
            for song in parsed.songs:
                existing = next(
                    (index for index, item in enumerate(songs)
                     if item.song_id == song.song_id), None
                )
                position = existing + 1 if existing is not None else len(songs) + 1
                reference = SongReference(
                    position=position,
                    queueable=True,
                    **song.model_dump(mode="json"),
                )
                if existing is not None:
                    songs[existing] = reference
                    continue
                seen_song_ids.add(song.song_id)
                songs.append(reference)
        elif result.get("name") == "search_entities_tool" and isinstance(content, str):
            try:
                parsed_entities = EntitySearchResult.model_validate_json(content)
            except ValueError:
                continue
            for entity in parsed_entities.entities:
                if entity.type == "artist" and entity.id not in seen_artist_ids:
                    seen_artist_ids.add(entity.id)
                    artists.append(ArtistReference(
                        position=len(artists) + 1, id=entity.id,
                        name=entity.name, cover_url=entity.cover_url,
                    ))
                elif entity.type == "album" and entity.id not in seen_album_ids:
                    seen_album_ids.add(entity.id)
                    albums.append(AlbumReference(
                        position=len(albums) + 1, id=entity.id,
                        name=entity.name, artist=entity.artist,
                        cover_url=entity.cover_url,
                    ))
                elif entity.type == "playlist" and entity.id not in seen_playlist_ids:
                    seen_playlist_ids.add(entity.id)
                    playlists.append(PlaylistReference(
                        position=len(playlists) + 1,
                        playlist_id=entity.id, name=entity.name,
                    ))
                elif entity.type == "song" and entity.id not in seen_song_ids:
                    seen_song_ids.add(entity.id)
                    duration = entity.metadata.get("duration")
                    songs.append(SongReference(
                        position=len(songs) + 1, song_id=entity.id,
                        name=entity.name,
                        artists=[name for name in entity.artist.split("、") if name],
                        album=entity.album, cover_url=entity.cover_url,
                        duration=duration if isinstance(duration, int) and duration >= 0 else 0,
                        playable=False,
                        queueable=False,
                    ))
        elif result.get("name") == "create_playlist_tool" and isinstance(content, str):
            try:
                created = CreatedPlaylistResult.model_validate_json(content)
            except ValueError:
                continue
            if created.playlist_id not in seen_playlist_ids:
                seen_playlist_ids.add(created.playlist_id)
                playlists.append(PlaylistReference(
                    position=len(playlists) + 1, playlist_id=created.playlist_id,
                    name=created.name, editable=True,
                ))
        elif result.get("name") == "get_user_playlists" and isinstance(content, str):
            try:
                parsed = UserPlaylistsResult.model_validate_json(content)
            except ValueError:
                continue
            for playlist in parsed.playlists:
                if playlist.playlist_id in seen_playlist_ids:
                    index = next(
                        index for index, item in enumerate(playlists)
                        if item.playlist_id == playlist.playlist_id
                    )
                    playlists[index] = PlaylistReference(
                        position=index + 1,
                        **playlist.model_dump(mode="json"),
                    )
                    continue
                seen_playlist_ids.add(playlist.playlist_id)
                playlists.append(
                    PlaylistReference(
                        position=len(playlists) + 1,
                        **playlist.model_dump(mode="json"),
                    )
                )
        elif result.get("name") == "get_playlist_detail_tool" and isinstance(content, str):
            try:
                detail = PlaylistDetailResult.model_validate_json(content)
            except ValueError:
                continue
            if detail.playlist_id not in seen_playlist_ids:
                seen_playlist_ids.add(detail.playlist_id)
                playlists.append(PlaylistReference(
                    position=len(playlists) + 1,
                    playlist_id=detail.playlist_id, name=detail.name,
                ))
            for song in detail.songs:
                existing = next(
                    (index for index, item in enumerate(songs)
                     if item.song_id == song.song_id), None
                )
                reference = SongReference(
                    position=existing + 1 if existing is not None else len(songs) + 1,
                    playable=False,
                    queueable=True,
                    **song.model_dump(mode="json"),
                )
                if existing is not None:
                    songs[existing] = reference
                    continue
                seen_song_ids.add(song.song_id)
                songs.append(reference)
        elif result.get("name") == "get_album_detail_tool" and isinstance(content, str):
            try:
                detail = AlbumDetailResult.model_validate_json(content)
            except ValueError:
                continue
            if detail.album_id not in seen_album_ids:
                seen_album_ids.add(detail.album_id)
                albums.append(AlbumReference(
                    position=len(albums) + 1,
                    id=detail.album_id,
                    name=detail.name,
                    artist=detail.artist,
                    cover_url=detail.cover_url,
                ))
            for song in detail.songs:
                existing = next(
                    (index for index, item in enumerate(songs)
                     if item.song_id == song.song_id), None
                )
                reference = SongReference(
                    position=existing + 1 if existing is not None else len(songs) + 1,
                    playable=False,
                    queueable=True,
                    **song.model_dump(mode="json"),
                )
                if existing is not None:
                    songs[existing] = reference
                    continue
                seen_song_ids.add(song.song_id)
                songs.append(reference)
    return ReferencedEntities(
        songs=songs, artists=artists, albums=albums, playlists=playlists
    )


def _extract_client_actions(
    state: ToolCallingState,
) -> list[ClientAction]:
    actions: list[ClientAction] = []
    for index, result in enumerate(state.get("tool_results", [])):
        if result.get("name") == "set_song_liked_tool":
            try:
                synced = SongLikeResult.model_validate_json(result.get("content"))
            except (ValueError, TypeError):
                continue
            actions.append(ClientAction(
                action_id=make_action_id(state["task_id"], str(result.get("tool_call_id") or f"like:{index}")),
                task_id=state["task_id"], name=ClientActionName.SET_LIKED,
                arguments=SetLikedArguments(song_id=synced.song_id, liked=synced.liked),
            ))
            continue
        if result.get("name") not in CLIENT_ACTION_TOOL_NAMES:
            continue
        payload = _read_json(result.get("content"))
        if payload is None or payload.get("status") != "pending_client_execution":
            continue
        pending = PendingClientActionPayload.model_validate(payload)
        actions.append(
            ClientAction(
                action_id=make_action_id(
                    state["task_id"],
                    str(result.get("tool_call_id") or f"tool:{index}"),
                ),
                task_id=state["task_id"],
                **pending.client_action,
            )
        )
    return actions


def _latest_tool_calls(state: ToolCallingState) -> list[dict[str, Any]]:
    for message in reversed(state["messages"]):
        if isinstance(message, AIMessage) and message.tool_calls:
            return [dict(call) for call in message.tool_calls]
    return []


def _known_references(
    state: ToolCallingState,
) -> tuple[
    dict[int, SongReference],
    dict[int, AlbumReference],
    dict[int, PlaylistReference],
]:
    current = _extract_entities(state.get("tool_results", []))
    groups = [*state.get("prior_entities", []), current]
    songs = {
        song.song_id: song
        for group in groups
        for song in group.songs
    }
    albums = {
        album.id: album
        for group in groups
        for album in group.albums
    }
    playlists = {
        playlist.playlist_id: playlist
        for group in groups
        for playlist in group.playlists
    }
    return songs, albums, playlists


def _track_fields_match(track: MusicTrack, reference: SongReference) -> bool:
    return (
        track.song_id == reference.song_id
        and track.name == reference.name
        and track.artists == reference.artists
        and track.album == reference.album
        and track.cover_url == reference.cover_url
        and track.duration == reference.duration
    )


def _track_matches_reference(track: MusicTrack, reference: SongReference) -> bool:
    return reference.playable and _track_fields_match(track, reference)


def _queue_track_matches_reference(
    track: MusicTrack,
    reference: SongReference,
) -> bool:
    return reference.queueable and _track_fields_match(track, reference)


def _known_collection_details(
    state: ToolCallingState,
) -> tuple[dict[int, AlbumDetailResult], dict[int, PlaylistDetailResult]]:
    albums: dict[int, AlbumDetailResult] = {}
    playlists: dict[int, PlaylistDetailResult] = {}
    for result in state.get("tool_results", []):
        content = result.get("content")
        if not isinstance(content, str):
            continue
        try:
            if result.get("name") == "get_album_detail_tool":
                detail = AlbumDetailResult.model_validate_json(content)
                albums[detail.album_id] = detail
            elif result.get("name") == "get_playlist_detail_tool":
                detail = PlaylistDetailResult.model_validate_json(content)
                playlists[detail.playlist_id] = detail
        except ValueError:
            continue
    return albums, playlists


def _validated_tracks(raw_tracks: Any) -> list[MusicTrack] | None:
    if not isinstance(raw_tracks, list):
        return None
    try:
        return [MusicTrack.model_validate(item) for item in raw_tracks]
    except ValueError:
        return None


def _reference_errors(state: ToolCallingState) -> dict[str, str]:
    songs, albums, playlists = _known_references(state)
    album_details, playlist_details = _known_collection_details(state)
    current_song_id = state["routing_context"].current_song_id
    known_song_ids = {*songs, *([current_song_id] if current_song_id else [])}
    from app.schemas.music import LikedSongsResult
    cloud_liked_ids = set()
    for result in state.get("tool_results", []):
        if result.get("name") == "get_liked_songs":
            try:
                cloud_liked_ids.update(LikedSongsResult.model_validate_json(result["content"]).song_ids)
            except (ValueError, KeyError):
                continue
    errors: dict[str, str] = {}
    for call in _latest_tool_calls(state):
        call_id = str(call.get("id", ""))
        name = call.get("name")
        args = call.get("args")
        if not call_id or not isinstance(args, dict):
            continue
        id_fields = {
            "get_song_lyrics": ("song_id",), "set_liked": ("song_id",),
            "set_song_liked_tool": ("song_id",), "get_album_detail_tool": ("album_id",),
            "get_playlist_detail_tool": ("playlist_id",), "play_album": ("album_id",),
            "play_playlist": ("playlist_id",), "add_song_to_playlist": ("song_id", "playlist_id"),
            "remove_song_from_playlist_tool": ("song_id", "playlist_id"),
            "delete_playlist_tool": ("playlist_id",),
        }.get(name, ())
        # Grounding checks must see the same IDs as the executor, even if a schema
        # would otherwise coerce a numeric string or boolean into an integer.
        if any(type(args.get(field)) is not int for field in id_fields):
            errors[call_id] = "实体 ID 必须使用可信结果中的整数，不得转换或猜测。"
            continue
        if name in {"get_song_lyrics", "set_liked", "set_song_liked_tool"}:
            song_id = args.get("song_id")
            permitted_ids = known_song_ids | cloud_liked_ids if name == "set_song_liked_tool" else known_song_ids
            if isinstance(song_id, int) and song_id not in permitted_ids:
                errors[call_id] = "歌曲 ID 没有可信来源；请先搜索歌曲或请用户明确选择。"
            if name == "set_liked":
                synced = [
                    _read_json(result.get("content")) or {}
                    for result in state.get("tool_results", [])
                    if result.get("name") == "set_song_liked_tool"
                ]
                if not any(
                    item.get("status") == "synced"
                    and item.get("song_id") == song_id
                    and item.get("liked") == args.get("liked")
                    for item in synced
                ):
                    errors[call_id] = "请先调用 set_song_liked_tool 同步云端，成功后自动更新本地红心。"
        elif name == "get_song_popularity_tool":
            identities = args.get("song_ids")
            if not isinstance(identities, list) or any(
                type(identity) is not int or identity not in songs for identity in identities
            ):
                errors[call_id] = "评论核实的歌曲 ID 必须来自可信目录结果；请先搜索，不得猜测。"
        elif name == "play_song":
            try:
                track = MusicTrack.model_validate(args.get("song"))
            except ValueError:
                continue
            reference = songs.get(track.song_id)
            if reference is None or not _track_matches_reference(track, reference):
                errors[call_id] = "播放歌曲必须完整使用 search_music 或历史结果；请先搜索。"
        elif name in {"insert_next_track", "append_to_queue"}:
            try:
                track = MusicTrack.model_validate(args.get("song"))
            except ValueError:
                continue
            reference = songs.get(track.song_id)
            if reference is None or not _queue_track_matches_reference(track, reference):
                errors[call_id] = "队列歌曲必须完整使用歌曲搜索或专辑/歌单详情结果。"
        elif name == "get_album_detail_tool":
            album_id = args.get("album_id")
            if isinstance(album_id, int) and album_id not in albums:
                errors[call_id] = "专辑 ID 没有可信来源；请先搜索专辑。"
        elif name == "get_playlist_detail_tool":
            playlist_id = args.get("playlist_id")
            if isinstance(playlist_id, int) and playlist_id not in playlists:
                errors[call_id] = "歌单 ID 没有可信来源；请先搜索歌单或读取当前用户歌单。"
        elif name == "play_album":
            album_id = args.get("album_id")
            tracks = _validated_tracks(args.get("tracks"))
            detail = album_details.get(album_id) if isinstance(album_id, int) else None
            if (
                detail is None
                or args.get("name") != detail.name
                or tracks != detail.songs
            ):
                errors[call_id] = "播放专辑必须完整使用本轮 get_album_detail_tool 的结果。"
        elif name == "play_playlist":
            playlist_id = args.get("playlist_id")
            tracks = _validated_tracks(args.get("tracks"))
            detail = (
                playlist_details.get(playlist_id)
                if isinstance(playlist_id, int)
                else None
            )
            if (
                detail is None
                or args.get("name") != detail.name
                or tracks != detail.songs
            ):
                errors[call_id] = "播放歌单必须完整使用本轮 get_playlist_detail_tool 的结果。"
        elif name == "jump_to_queue_item":
            index = args.get("index")
            song_id = args.get("song_id")
            if any(value is not None and type(value) is not int for value in (index, song_id)):
                errors[call_id] = "队列目标必须使用整数序号或可信歌曲 ID。"
            elif isinstance(index, int) and index > state["routing_context"].queue_size:
                errors[call_id] = "目标位置超出当前播放队列范围。"
            elif isinstance(song_id, int) and song_id not in known_song_ids:
                errors[call_id] = "目标歌曲 ID 没有可信来源；请先搜索歌曲。"
        elif name == "delete_playlist_tool":
            from app.tools.music import has_delete_confirmation
            playlist_id = args.get("playlist_id")
            if not args.get("confirmed") or not has_delete_confirmation(state["current_input"], playlist_id):
                errors[call_id] = "删除整张歌单需要用户确认准确歌单 ID；请先展示目标并请求二次确认。"
            elif playlist_id not in playlists or not playlists[playlist_id].editable:
                errors[call_id] = "删除前必须从当前用户歌单取得可信的可编辑目标。"
        elif name in {"add_song_to_playlist", "remove_song_from_playlist_tool"}:
            song_id = args.get("song_id")
            playlist_id = args.get("playlist_id")
            if isinstance(song_id, int) and song_id not in known_song_ids:
                errors[call_id] = "歌曲 ID 没有可信来源；请先搜索歌曲。"
            if isinstance(playlist_id, int):
                playlist = playlists.get(playlist_id)
                if playlist is None:
                    errors[call_id] = "歌单 ID 没有可信来源；请先读取当前用户歌单。"
                elif not playlist.editable:
                    errors[call_id] = "目标歌单不可编辑；请让用户选择可编辑歌单。"
            if name == "remove_song_from_playlist_tool":
                detail = playlist_details.get(playlist_id) if isinstance(playlist_id, int) else None
                if detail is None or not any(song.song_id == song_id for song in detail.songs):
                    errors[call_id] = "移除前必须读取本轮目标歌单详情，并确认目标歌曲在其中。"
    return errors


def _duplicate_call_errors(state: ToolCallingState) -> dict[str, str]:
    latest = _latest_tool_calls(state)
    if not latest:
        return {}
    prior_calls = state.get("tool_calls", [])[:-len(latest)]
    attempted_ids = {
        str(item.get("tool_call_id"))
        for item in state.get("tool_results", [])
        if (_read_json(item.get("content")) or {}).get("error")
        not in {"duplicate_tool_call", "batch_rejected", "untrusted_entity_reference"}
    }

    def signature(call: dict[str, Any]) -> tuple[str, str] | None:
        name, args = call.get("name"), call.get("args")
        if not isinstance(name, str) or not isinstance(args, dict):
            return None
        return name, json.dumps(args, ensure_ascii=False, sort_keys=True)

    seen = {
        item for call in prior_calls
        if str(call.get("id")) in attempted_ids
        if (item := signature(call)) is not None
    }
    errors: dict[str, str] = {}
    for call in latest:
        item = signature(call)
        if item is None:
            continue
        if item in seen:
            errors[str(call.get("id"))] = "相同参数的工具请求已经在本次任务中执行，不能重复执行。"
        else:
            seen.add(item)
    return errors


def _final_task_result(
    state: ToolCallingState,
    message: AIMessage,
) -> tuple[str, TaskResult, list[ClientAction]]:
    content = str(message.text).strip()
    if len(content) > MAX_VISIBLE_RESPONSE_CHARACTERS:
        content = content[: MAX_VISIBLE_RESPONSE_CHARACTERS - 1].rstrip() + "…"
    actions = _extract_client_actions(state)
    entities = _extract_entities(state.get("tool_results", []))
    failures = [
        payload
        for result in state.get("tool_results", [])
        if (payload := _read_json(result.get("content"))) is not None
        and payload.get("success") is False
    ]
    execution_failures = [
        payload for payload in failures
        if payload.get("error") not in {
            "duplicate_tool_call", "batch_rejected", "untrusted_entity_reference",
            "invalid_tool_arguments", "invalid_tool_selection", "unknown_tool",
        }
    ]
    all_tools_failed = bool(state.get("tool_results")) and len(failures) == len(
        state.get("tool_results", [])
    )
    write_failures = [
        payload
        for result in state.get("tool_results", [])
        if result.get("name") in {
            "add_song_to_playlist", "remove_song_from_playlist_tool",
            "create_playlist_tool", "set_song_liked_tool",
            "delete_playlist_tool",
        }
        if (payload := _read_json(result.get("content"))) is not None
        and (payload.get("success") is False or payload.get("status") == "forbidden")
    ]
    limit_reached = content == TOOL_LIMIT_MESSAGE
    status = (
        TaskStatus.FAILED
        if all_tools_failed or write_failures or execution_failures or limit_reached
        else TaskStatus.SUCCESS
    )
    error = None
    if limit_reached:
        error = TOOL_LIMIT_MESSAGE
    elif all_tools_failed:
        error = str(failures[0].get("message") or "工具调用失败，请稍后重试。")
    elif write_failures:
        error = str(write_failures[0].get("message") or "云端操作未完成，请稍后重试。")
    elif execution_failures:
        error = str(execution_failures[0].get("message") or "工具调用失败，请稍后重试。")
    actual_write_failures = [
        item for item in write_failures
        if item.get("error") not in {
            "untrusted_entity_reference", "duplicate_tool_call", "batch_rejected",
            "invalid_tool_arguments", "invalid_tool_selection", "unknown_tool",
        }
    ]
    if actual_write_failures and not limit_reached:
        # Persistence acknowledgements come from the service, even if the model
        # mistakenly describes a failed write as successful.
        content = "云端操作未完成：" + str(actual_write_failures[0].get("message") or "请稍后重试。")
    elif execution_failures and not limit_reached:
        content = "操作未全部完成：" + error
    if (status is TaskStatus.SUCCESS and is_simple_song_playback(state["current_input"])
        and not any(action.name is ClientActionName.PLAY_SONG for action in actions)):
        status = TaskStatus.NEEDS_INPUT
        content = "未找到可确认的指定歌曲版本，尚未发送播放指令。请明确要播放的版本。"
    return (
        content,
        TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.TOOL_CALLING,
            status=status,
            content=content,
            data={
                "client_actions": [
                    action.model_dump(mode="json") for action in actions
                ],
                "entities": entities.model_dump(mode="json"),
                "tool_calls": state.get("tool_call_count", 0),
                "tool_call_counts_by_phase": _tool_phase_counts(state),
                **({"tool_budget_stop": state["tool_budget_stop"]}
                   if state.get("tool_budget_stop") is not None else {}),
                "tool_call_details": [
                    {"name": str(item.get("name") or "unknown"),
                     "status": str(item.get("status") or "unknown"),
                     **({"error": payload.get("error"), "message": payload.get("message")}
                        if (payload := _read_json(item.get("content"))) and payload.get("success") is False else {})}
                    for item in state.get("tool_results", [])
                ],
            },
            error=error,
        ),
        actions,
    )


def _fail_invalid_tool_output(
    state: ToolCallingState,
    reason: str,
) -> dict[str, Any]:
    _, result, actions = _final_task_result(state, AIMessage(content=INVALID_TOOL_OUTPUT_MESSAGE))
    result = result.model_copy(update={
        "status": TaskStatus.FAILED, "content": INVALID_TOOL_OUTPUT_MESSAGE,
        "error": INVALID_TOOL_OUTPUT_MESSAGE, "data": {**result.data, "reason": reason},
    })
    return {
        "messages": [AIMessage(content=INVALID_TOOL_OUTPUT_MESSAGE)],
        "answer": INVALID_TOOL_OUTPUT_MESSAGE,
        "client_actions": actions,
        "task_result": result,
    }


def build_tool_calling_subgraph(
    *,
    assistant: ToolCallingAssistant | None = None,
    model: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
    niche_request_text: str | None = None,
) -> StateGraph:
    graph_tools = list(tools or all_tool_calling_tools())
    tools_by_name = {item.name: item for item in graph_tools}
    bound_model = bind_tool_calling_tools(model, graph_tools) if model is not None else None

    def _argument_errors(state: ToolCallingState) -> dict[str, tuple[str, str]]:
        errors: dict[str, tuple[str, str]] = {}
        for call in _latest_tool_calls(state):
            call_id = str(call.get("id") or "")
            if not call_id:
                continue

            def allowlisted(selection: ToolSelection) -> None:
                if selection.name not in tools_by_name:
                    raise SemanticValidationError("unknown_tool")

            try:
                selected = StructuredOutputService(
                    "tool_selection", ToolSelection,
                    semantic_validator=allowlisted, max_attempts=1,
                ).validate(call)
            except StructuredOutputError as exc:
                code = (
                    "unknown_tool" if exc.failure_type.value == "semantic_validation_error"
                    else "invalid_tool_selection"
                )
                errors[call_id] = (code, INVALID_TOOL_OUTPUT_MESSAGE)
                continue
            # LangChain removes injected ToolRuntime fields from this public schema.
            schema = tools_by_name[selected.name].tool_call_schema
            if not isinstance(schema, type) or not issubclass(schema, BaseModel):
                errors[call_id] = ("invalid_tool_schema", INVALID_TOOL_OUTPUT_MESSAGE)
                continue
            if set(selected.args).difference(schema.model_fields):
                errors[call_id] = ("invalid_tool_arguments", INVALID_TOOL_OUTPUT_MESSAGE)
                continue
            try:
                StructuredOutputService(
                    f"tool_arguments.{selected.name}", schema, max_attempts=1,
                ).validate(selected.args)
            except StructuredOutputError as exc:
                message = INVALID_TOOL_OUTPUT_MESSAGE
                if (selected.name == "get_song_popularity_tool"
                    and getattr(exc.__cause__, "code", None) == "comment_tool_batch_limit:song_ids"):
                    from app.schemas.popularity import COMMENT_TOOL_BATCH_GUIDANCE

                    message = COMMENT_TOOL_BATCH_GUIDANCE
                errors[call_id] = ("invalid_tool_arguments", message)
        return errors

    async def agent(state: ToolCallingState) -> dict[str, Any]:
        from app.agent.handoff.niche_readback import finish_niche_readback, niche_read_limit

        request = niche_request_text or state["current_input"]
        niche_scope = niche_read_limit(request) is not None
        completed = finish_niche_readback(
            request, state["task_id"], state.get("tool_results", []),
            [*(song for refs in state.get("prior_entities", []) for song in refs.songs),
             *(_extract_entities(state.get("tool_results", [])).songs)],
        ) if niche_scope else None
        if completed is not None:
            content, entities, receipt = completed
            message = AIMessage(content=content)
            answer, result, actions = _final_task_result(state, message)
            result = result.model_copy(update={"data": {**result.data,
                "entities": entities.model_dump(mode="json"), "niche_readback": receipt.model_dump(mode="json")}})
            return {"messages": [message], "answer": answer, "task_result": result, "client_actions": actions}
        if assistant is not None:
            token = producer_state.set(state)
            try:
                result = await assistant(
                    state["messages"],
                    state["current_input"],
                    state["routing_context"],
                    state.get("prior_entities", []),
                )
            finally:
                producer_state.reset(token)
        else:
            from app.llm.runtime import RuntimeLimitError

            try:
                result = await answer_tool_calling_task(
                    state["messages"],
                    state["current_input"],
                    state["routing_context"],
                    state.get("prior_entities", []),
                    model=bound_model,
                    continuity_context=state.get("continuity_context"),
                    search_only=bool(graph_tools) and all(
                        item.name in {"search_music", "search_entities_tool"}
                        for item in graph_tools
                    ),
                    song_playback={item.name for item in graph_tools} == SONG_PLAYBACK_TOOLS,
                    route_task_id=state["task_id"],
                    invocation_run_id=state.get("pack_run_id"),
                )
            except RuntimeLimitError as exc:
                # Preserve successful tools/actions when the next model request
                # is refused. The parent still records its original stop reason.
                message = "本轮操作达到资源上限，后续步骤未执行。"
                answer, task_result, actions = _final_task_result(state, AIMessage(content=message))
                task_result = task_result.model_copy(update={
                    "status": TaskStatus.FAILED, "content": message, "error": message,
                    "data": {**task_result.data, "reason": exc.reason},
                })
                return {"messages": [AIMessage(content=message)], "answer": message,
                        "task_result": task_result, "client_actions": actions}

        if result.invalid_tool_calls or any(
            not isinstance(call.get("id"), str) or not call["id"].strip()
            for call in result.tool_calls
        ):
            return _fail_invalid_tool_output(state, "malformed_tool_call")

        if niche_scope and not result.tool_calls:
            stopped = _fail_invalid_tool_output(state, "niche_verification_missing")
            message = "尚无可完整核对歌曲目录与评论数的成功回执，未提供冷门推荐。"
            stopped["messages"] = [AIMessage(content=message)]
            stopped["answer"] = message
            stopped["task_result"] = stopped["task_result"].model_copy(update={"content": message, "error": message})
            return stopped

        tool_call_count = state.get("tool_call_count", 0)
        requested_calls = len(result.tool_calls)
        phase_counts = _tool_phase_counts(state)
        requested_by_phase = dict.fromkeys(TOOL_PHASE_LIMITS, 0)
        for call in result.tool_calls:
            requested_by_phase[_tool_call_phase(call["name"])] += 1
        exceeded = (["total"] if tool_call_count + requested_calls > MAX_TOOL_CALLS else []) + [
            phase for phase, limit in TOOL_PHASE_LIMITS.items()
            if phase_counts[phase] + requested_by_phase[phase] > limit
        ]
        stop = None
        if requested_calls and exceeded:
            stop = {
                "budget": exceeded[0], "exceeded_budgets": exceeded,
                "used": {**phase_counts, "total": tool_call_count},
                "requested": {**requested_by_phase, "total": requested_calls},
                "limits": {**TOOL_PHASE_LIMITS, "total": MAX_TOOL_CALLS},
            }
            logger.warning("Tool call budget exceeded: %s", stop)
            result = AIMessage(content=TOOL_LIMIT_MESSAGE)
            requested_calls = 0
        else:
            phase_counts = {phase: used + requested_by_phase[phase]
                            for phase, used in phase_counts.items()}

        update: dict[str, Any] = {
            "messages": [result],
            "tool_call_count": tool_call_count + requested_calls,
            "tool_call_counts_by_phase": phase_counts,
            "tool_calls": [
                *state.get("tool_calls", []),
                *[dict(call) for call in result.tool_calls],
            ],
        }
        if stop is not None:
            update["tool_budget_stop"] = stop
        if not result.tool_calls:
            answer, task_result, client_actions = _final_task_result({**state, **update}, result)
            update.update(
                answer=answer,
                task_result=task_result,
                client_actions=client_actions,
            )
        return update

    def capture_tool_results(state: ToolCallingState) -> dict[str, Any]:
        captured_ids = {
            str(item.get("tool_call_id"))
            for item in state.get("tool_results", [])
        }
        results = list(state.get("tool_results", []))
        calls_by_id = {
            str(call.get("id")): call
            for call in state.get("tool_calls", [])
            if call.get("id") is not None
        }
        for message in state["messages"]:
            if not isinstance(message, ToolMessage):
                continue
            call_id = str(message.tool_call_id)
            if call_id in captured_ids or call_id not in calls_by_id:
                continue
            captured_ids.add(call_id)
            try:
                record = ToolExecutionRecord.model_validate({
                    "tool_call_id": call_id,
                    "name": message.name or calls_by_id[call_id].get("name", ""),
                    "content": message.content,
                    "status": getattr(message, "status", "success"),
                })
            except ValidationError:
                record = ToolExecutionRecord(
                    tool_call_id=call_id,
                    name=str(calls_by_id[call_id].get("name") or "unknown"),
                    content=ToolFailure(
                        error="invalid_tool_result",
                        message="工具结果格式无效，本次未使用该结果。",
                    ).model_dump_json(),
                    status="error",
                )
            results.append(record.model_dump(mode="json"))
        return {"tool_results": results, "created_playlist_receipts": capture_created_receipts(state)}

    def reject_invalid_tool_calls(state: ToolCallingState) -> dict[str, Any]:
        argument_errors = _argument_errors(state)
        reference_errors = _reference_errors(state)
        duplicate_errors = _duplicate_call_errors(state)
        action_errors = _scoped_action_intent_errors(state)
        messages: list[ToolMessage] = []
        for call in _latest_tool_calls(state):
            call_id = str(call.get("id", ""))
            # A scoped-out write is still an unauthorized action. Preserve
            # the authority diagnostic independently of the schema allowlist.
            if call_id in action_errors:
                error_code = "unauthorized_action"
                message = action_errors[call_id]
            elif call_id in argument_errors:
                error_code, message = argument_errors[call_id]
            elif call_id in reference_errors:
                error_code = "untrusted_entity_reference"
                message = reference_errors[call_id]
            elif call_id in duplicate_errors:
                error_code = "duplicate_tool_call"
                message = duplicate_errors[call_id]
            else:
                error_code = "batch_rejected"
                message = "同一批工具调用含有重复或无效请求，本次未执行；请调整后重试。"
            messages.append(
                bind_tool_result(ToolMessage(
                    content=ToolFailure(
                        error=error_code,
                        message=message,
                    ).model_dump_json(),
                    tool_call_id=call_id,
                    name=str(call.get("name") or "unknown"),
                    status="error",
                ), state["messages"][-1])
            )
        return {"messages": messages}

    def route_validated_calls(state: ToolCallingState) -> str:
        return (
            "reject_tool_calls"
            if (_argument_errors(state) or _reference_errors(state)
                or _duplicate_call_errors(state) or _scoped_action_intent_errors(state))
            else "tools"
        )

    async def scoped_tool_call(request, execute):
        is_create = request.tool_call.get("name") == "create_playlist_tool"
        executed_create = False

        async def execute_creation(tool_request):
            nonlocal executed_create
            result = await execute(tool_request)
            executed_create = isinstance(result, ToolMessage) and result.status == "success"
            return result

        output = await journal_tool_call(request, execute_creation if is_create else execute)
        output = bind_tool_result(output, request.state["messages"][-1])
        if is_create and isinstance(output, ToolMessage) and "owner_agent" in output.additional_kwargs:
            # Journal replay/reconciliation is not a fresh successful create in
            # this task. Attest only the actual execute callback, never history.
            output = output.model_copy(update={"additional_kwargs": {
                **output.additional_kwargs, "executed_create": executed_create,
            }})
        return output

    async def direct_actions(state: ToolCallingState, runtime):
        from uuid import uuid4
        output = await resolve_direct_actions(state, runtime)
        return {**output, "pack_run_id": f"call:{uuid4()}", "created_playlist_receipts": []}

    builder = StateGraph(
        ToolCallingState,
        context_schema=MusicAgentContext,
        output_schema=SubGraphOutput,
    )
    builder.add_node("direct_actions", direct_actions)
    builder.add_node("agent", agent)
    builder.add_node("validate_tool_calls", lambda _: {})
    builder.add_node(
        "tools",
        ToolNode(graph_tools, handle_tool_errors=handle_music_tool_error, awrap_tool_call=scoped_tool_call),
    )
    builder.add_node("capture_tool_results", capture_tool_results)
    builder.add_node("reject_tool_calls", reject_invalid_tool_calls)
    builder.add_edge(START, "direct_actions")
    builder.add_conditional_edges(
        "direct_actions",
        _route_after_direct,
        {"agent": "agent", END: END},
    )
    builder.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "validate_tool_calls", END: END},
    )
    builder.add_conditional_edges(
        "validate_tool_calls",
        route_validated_calls,
        {"tools": "tools", "reject_tool_calls": "reject_tool_calls"},
    )
    builder.add_edge("tools", "capture_tool_results")
    builder.add_edge("reject_tool_calls", "capture_tool_results")
    builder.add_edge("capture_tool_results", "agent")
    return builder


def create_tool_calling_subgraph(
    *,
    assistant: ToolCallingAssistant | None = None,
    model: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
    niche_request_text: str | None = None,
) -> CompiledStateGraph:
    return build_tool_calling_subgraph(
        assistant=assistant,
        model=model,
        tools=tools,
        niche_request_text=niche_request_text,
    ).compile(checkpointer=False)

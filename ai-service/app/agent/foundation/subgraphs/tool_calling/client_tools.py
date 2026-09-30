"""Allow-listed LangChain tools that enqueue commands for the browser player."""

from langchain_core.tools import BaseTool, tool

from app.agent.foundation.subgraphs.tool_calling.models import (
    EmptyActionArguments,
    JumpToQueueItemArguments,
    PendingClientActionPayload,
    PlayAlbumArguments,
    PlayPlaylistArguments,
    PlaySongArguments,
    SeekByArguments,
    SeekToArguments,
    SetLikedArguments,
    SetMutedArguments,
    SetPlayModeArguments,
    SetVolumeArguments,
)
from app.schemas.music import MusicTrack


def _pending(name: str, arguments: dict) -> str:
    return PendingClientActionPayload(
        client_action={"name": name, "arguments": arguments}
    ).model_dump_json()


@tool("pause_player", args_schema=EmptyActionArguments)
async def pause_player_tool() -> str:
    """暂停当前浏览器播放器。只有存在当前歌曲时才能调用。"""
    return _pending("pause_player", {})


@tool("resume_player", args_schema=EmptyActionArguments)
async def resume_player_tool() -> str:
    """继续播放当前浏览器播放器。只有存在当前歌曲时才能调用。"""
    return _pending("resume_player", {})


@tool("play_next", args_schema=EmptyActionArguments)
async def play_next_tool() -> str:
    """播放当前队列的下一首歌曲。"""
    return _pending("play_next", {})


@tool("play_previous", args_schema=EmptyActionArguments)
async def play_previous_tool() -> str:
    """播放当前队列的上一首歌曲。"""
    return _pending("play_previous", {})


@tool("set_volume", args_schema=SetVolumeArguments)
async def set_volume_tool(volume: float) -> str:
    """把浏览器播放器音量明确设置为 0 到 1 之间的值。"""
    return _pending("set_volume", {"volume": volume})


@tool("set_muted", args_schema=SetMutedArguments)
async def set_muted_tool(muted: bool) -> str:
    """明确设置浏览器播放器的静音状态。"""
    return _pending("set_muted", {"muted": muted})


@tool("seek_to", args_schema=SeekToArguments)
async def seek_to_tool(seconds: float) -> str:
    """把当前歌曲进度跳转到指定秒数。"""
    return _pending("seek_to", {"seconds": seconds})


@tool("seek_by", args_schema=SeekByArguments)
async def seek_by_tool(offset_seconds: float) -> str:
    """把当前歌曲进度向前或向后移动指定秒数。"""
    return _pending("seek_by", {"offset_seconds": offset_seconds})


@tool("set_play_mode", args_schema=SetPlayModeArguments)
async def set_play_mode_tool(mode: str) -> str:
    """明确设置播放模式：sequence、list-loop、single-loop 或 shuffle。"""
    return _pending("set_play_mode", {"mode": mode})


@tool("play_song", args_schema=PlaySongArguments)
async def play_song_tool(song: MusicTrack) -> str:
    """播放 search_music 返回的一首真实歌曲；必须原样传入完整歌曲对象。"""
    return _pending("play_song", {"song": song.model_dump(mode="json")})


@tool("insert_next_track", args_schema=PlaySongArguments)
async def insert_next_track_tool(song: MusicTrack) -> str:
    """把可信工具结果中的歌曲插入为队列下一首，不中断当前播放。"""
    return _pending("insert_next_track", {"song": song.model_dump(mode="json")})


@tool("append_to_queue", args_schema=PlaySongArguments)
async def append_to_queue_tool(song: MusicTrack) -> str:
    """把可信工具结果中的歌曲追加到当前播放队列末尾，不中断当前播放。"""
    return _pending("append_to_queue", {"song": song.model_dump(mode="json")})


@tool("play_album", args_schema=PlayAlbumArguments)
async def play_album_tool(album_id: int, name: str, tracks: list[MusicTrack]) -> str:
    """用 get_album_detail_tool 的完整结果替换队列并从专辑第一首开始播放。"""
    return _pending("play_album", {
        "album_id": album_id,
        "name": name,
        "tracks": [track.model_dump(mode="json") for track in tracks],
    })


@tool("play_playlist", args_schema=PlayPlaylistArguments)
async def play_playlist_tool(
    playlist_id: int,
    name: str,
    tracks: list[MusicTrack],
) -> str:
    """用 get_playlist_detail_tool 的完整结果替换队列并从歌单第一首开始播放。"""
    return _pending("play_playlist", {
        "playlist_id": playlist_id,
        "name": name,
        "tracks": [track.model_dump(mode="json") for track in tracks],
    })


@tool("jump_to_queue_item", args_schema=JumpToQueueItemArguments)
async def jump_to_queue_item_tool(
    song_id: int | None = None,
    index: int | None = None,
) -> str:
    """切换到当前队列中的真实 song_id，或从 1 开始计数的 index 位置。"""
    args = JumpToQueueItemArguments(song_id=song_id, index=index)
    return _pending("jump_to_queue_item", args.model_dump(exclude_none=True))


@tool("set_liked", args_schema=SetLikedArguments)
async def set_liked_tool(song_id: int, liked: bool) -> str:
    """仅更新浏览器红心；必须已有 set_song_liked_tool 的同目标云端成功结果。"""
    return _pending("set_liked", {"song_id": song_id, "liked": liked})


@tool("open_lyrics", args_schema=EmptyActionArguments)
async def open_lyrics_tool() -> str:
    """展开当前浏览器播放器的歌词面板。"""
    return _pending("open_lyrics", {})


@tool("clear_queue", args_schema=EmptyActionArguments)
async def clear_queue_tool() -> str:
    """清空浏览器播放队列并停止当前播放。"""
    return _pending("clear_queue", {})


CLIENT_ACTION_TOOLS: list[BaseTool] = [
    pause_player_tool,
    resume_player_tool,
    play_next_tool,
    play_previous_tool,
    set_volume_tool,
    set_muted_tool,
    seek_to_tool,
    seek_by_tool,
    set_play_mode_tool,
    play_song_tool,
    insert_next_track_tool,
    append_to_queue_tool,
    play_album_tool,
    play_playlist_tool,
    jump_to_queue_item_tool,
    set_liked_tool,
    open_lyrics_tool,
    clear_queue_tool,
]
CLIENT_ACTION_TOOL_NAMES = frozenset(item.name for item in CLIENT_ACTION_TOOLS)

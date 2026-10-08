import logging
import re
from typing import Annotated, Any, Literal

import httpx
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from app.config.settings import get_settings
from app.schemas.context import MusicAgentContext
from app.schemas.music import (
    AlbumDetailResult,
    AddSongToPlaylistArgs,
    AddSongToPlaylistResult,
    CreatePlaylistArgs,
    CreatedPlaylistResult,
    DeletePlaylistArgs,
    DeletedPlaylistResult,
    LikedSongsResult,
    SpringDeletedPlaylistResponse,
    RemoveSongFromPlaylistArgs,
    RemoveSongFromPlaylistResult,
    SetSongLikedArgs,
    SongLikeResult,
    SpringCreatedPlaylistResponse,
    SpringSongLikeResponse,
    SpringLikedSongsResponse,
    EntitySearchItem,
    EntitySearchResult,
    GetAlbumDetailArgs,
    GetPlaylistDetailArgs,
    GetSongLyricsArgs,
    MusicSearchResult,
    SearchMusicArgs,
    SearchEntitiesArgs,
    PlaylistDetailResult,
    SongLyricsResult,
    SpringMusicSearchResponse,
    SpringAlbumDetailResponse,
    SpringMusicEntityDetailResponse,
    SpringMusicEntitySearchResponse,
    SpringPlaylistMutationResponse,
    SpringPlaylistDetailResponse,
    SpringSongLyricsResponse,
    SpringUserPlaylistsResponse,
    ToolFailure,
    UserPlaylist,
    UserPlaylistsResult,
    MusicEntity,
)

logger = logging.getLogger(__name__)


class MusicServiceError(RuntimeError):
    """A safe, model-readable failure returned by the Spring music service."""

    def __init__(self, code: str, public_message: str | None = None) -> None:
        if public_message is None:
            public_message = code
            code = "music_service_error"
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


# Backwards-compatible name used by the existing isolated search tests.
MusicSearchError = MusicServiceError


class MusicServiceClient:
    """HTTP-only adapter from Python tools to the trusted Spring Boot API."""

    def __init__(self, session_cookie: str | None = None) -> None:
        self._session_cookie = session_cookie

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        settings = get_settings()
        url = f"{str(settings.spring_boot_base_url).rstrip('/')}{path}"
        headers = (
            {"Cookie": self._session_cookie}
            if self._session_cookie is not None
            else None
        )

        try:
            async with httpx.AsyncClient(
                timeout=settings.spring_boot_timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise MusicServiceError(
                "timeout",
                "音乐服务请求超时，请稍后再试。",
            ) from exc
        except httpx.RequestError as exc:
            raise MusicServiceError(
                "unavailable",
                "暂时无法连接音乐服务，请稍后再试。",
            ) from exc

        if not response.is_success:
            message = self._read_error_message(response)
            if response.status_code == 401:
                message = message or "请先登录网易云音乐。"
            raise MusicServiceError(
                f"http_{response.status_code}",
                message or "音乐服务暂时不可用，请稍后再试。",
            )

        try:
            return response.json()
        except ValueError as exc:
            raise MusicServiceError(
                "invalid_response",
                "音乐服务返回了无法识别的数据。",
            ) from exc

    @staticmethod
    def _read_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return ""
        if isinstance(payload, dict) and isinstance(payload.get("message"), str):
            return payload["message"].strip()
        return ""

    @staticmethod
    def _validate_response(payload: Any, schema: type[BaseModel]) -> BaseModel:
        try:
            parsed = schema.model_validate(payload)
        except ValidationError as exc:
            raise MusicServiceError(
                "invalid_response",
                "音乐服务返回了无法识别的数据。",
            ) from exc
        if getattr(parsed, "code", None) != 200:
            raise MusicServiceError(
                "business_error",
                str(getattr(parsed, "message", "音乐服务请求失败。")),
            )
        return parsed

    async def search_music(self, keyword: str, limit: int = 10) -> MusicSearchResult:
        search_input = SearchMusicArgs(keyword=keyword, limit=limit)
        logger.info(
            "Searching music through Spring Boot: keyword=%r limit=%d",
            search_input.keyword,
            search_input.limit,
        )
        payload = await self._request(
            "GET",
            "/api/music/search",
            params={"keyword": search_input.keyword, "limit": search_input.limit},
        )
        parsed = self._validate_response(payload, SpringMusicSearchResponse)
        assert isinstance(parsed, SpringMusicSearchResponse)
        return MusicSearchResult(
            keyword=search_input.keyword,
            limit=search_input.limit,
            songs=parsed.data,
        )

    async def search_entities(
        self,
        keyword: str,
        *,
        entity_type: str | None = None,
        limit: int = 5,
    ) -> list[MusicEntity]:
        search_input = SearchMusicArgs(keyword=keyword, limit=limit)
        params: dict[str, Any] = {
            "keyword": search_input.keyword,
            "limit": search_input.limit,
        }
        if entity_type is not None:
            if entity_type not in {"song", "artist", "album", "playlist"}:
                raise ValueError("unsupported music entity type")
            params["type"] = entity_type
        payload = await self._request(
            "GET",
            "/api/music/entities/search",
            params=params,
        )
        parsed = self._validate_response(
            payload,
            SpringMusicEntitySearchResponse,
        )
        assert isinstance(parsed, SpringMusicEntitySearchResponse)
        return parsed.data

    async def get_entity_detail(
        self,
        entity_type: str,
        entity_id: int,
    ) -> MusicEntity:
        if entity_type not in {"song", "artist", "album"}:
            raise ValueError("unsupported music entity type")
        if entity_id <= 0:
            raise ValueError("music entity id must be positive")
        payload = await self._request(
            "GET",
            f"/api/music/entities/{entity_type}/{entity_id}",
        )
        parsed = self._validate_response(
            payload,
            SpringMusicEntityDetailResponse,
        )
        assert isinstance(parsed, SpringMusicEntityDetailResponse)
        if (
            parsed.data.entity_type != entity_type
            or parsed.data.entity_id != entity_id
        ):
            raise MusicServiceError(
                "invalid_response",
                "音乐服务返回了不匹配的实体数据。",
            )
        return parsed.data

    async def get_song_lyrics(self, song_id: int) -> SongLyricsResult:
        lyrics_input = GetSongLyricsArgs(song_id=song_id)
        payload = await self._request(
            "GET",
            f"/api/songs/{lyrics_input.song_id}/lyrics",
        )
        parsed = self._validate_response(payload, SpringSongLyricsResponse)
        assert isinstance(parsed, SpringSongLyricsResponse)
        return SongLyricsResult(
            song_id=lyrics_input.song_id,
            original_lyrics=parsed.data.lyric,
            translated_lyrics=parsed.data.translation,
            status=parsed.data.status,
        )

    async def get_user_playlists(self) -> UserPlaylistsResult:
        payload = await self._request("GET", "/api/user/playlists")
        parsed = self._validate_response(payload, SpringUserPlaylistsResponse)
        assert isinstance(parsed, SpringUserPlaylistsResponse)
        return UserPlaylistsResult(
            playlists=[
                UserPlaylist(
                    playlist_id=item.playlist_id,
                    name=item.name,
                    editable=not item.subscribed,
                )
                for item in parsed.data
            ]
        )

    async def get_playlist_detail(self, playlist_id: int) -> PlaylistDetailResult:
        detail_input = GetPlaylistDetailArgs(playlist_id=playlist_id)
        payload = await self._request(
            "GET", f"/api/playlists/{detail_input.playlist_id}"
        )
        parsed = self._validate_response(payload, SpringPlaylistDetailResponse)
        assert isinstance(parsed, SpringPlaylistDetailResponse)
        playlist = parsed.data.playlist
        if playlist.playlist_id != detail_input.playlist_id:
            raise MusicServiceError(
                "invalid_response", "音乐服务返回了不匹配的歌单数据。"
            )
        return PlaylistDetailResult(
            playlist_id=playlist.playlist_id,
            name=playlist.name,
            description=parsed.data.description,
            cover_url=playlist.cover_img_url,
            creator=playlist.creator,
            track_count=playlist.track_count,
            songs=parsed.data.songs,
            creator_id=playlist.creator_id,
            special_type=playlist.special_type,
            subscribed=playlist.subscribed,
        )

    async def get_album_detail(self, album_id: int) -> AlbumDetailResult:
        detail_input = GetAlbumDetailArgs(album_id=album_id)
        payload = await self._request("GET", f"/api/albums/{detail_input.album_id}")
        parsed = self._validate_response(payload, SpringAlbumDetailResponse)
        assert isinstance(parsed, SpringAlbumDetailResponse)
        album = parsed.data.album
        if album.entity_type != "album" or album.entity_id != detail_input.album_id:
            raise MusicServiceError(
                "invalid_response", "音乐服务返回了不匹配的专辑数据。"
            )
        return AlbumDetailResult(
            album_id=album.entity_id,
            name=album.name,
            artist=album.artist,
            cover_url=album.cover_url,
            songs=parsed.data.songs,
        )

    async def add_song_to_playlist(
        self,
        playlist_id: int,
        song_id: int,
    ) -> AddSongToPlaylistResult:
        mutation_input = AddSongToPlaylistArgs(
            playlist_id=playlist_id,
            song_id=song_id,
        )
        payload = await self._request(
            "POST",
            (
                f"/api/playlists/{mutation_input.playlist_id}"
                f"/songs/{mutation_input.song_id}"
            ),
        )
        parsed = self._validate_response(payload, SpringPlaylistMutationResponse)
        assert isinstance(parsed, SpringPlaylistMutationResponse)
        return AddSongToPlaylistResult(
            playlist_id=mutation_input.playlist_id,
            song_id=mutation_input.song_id,
            status=parsed.data.status,
            message=parsed.data.message,
        )

    async def set_song_liked(self, song_id: int, liked: bool) -> SongLikeResult:
        args = SetSongLikedArgs(song_id=song_id, liked=liked)
        payload = await self._request(
            "POST", f"/api/songs/{args.song_id}/like",
            params={"liked": str(args.liked).lower()},
        )
        parsed = self._validate_response(payload, SpringSongLikeResponse)
        assert isinstance(parsed, SpringSongLikeResponse)
        if parsed.data.song_id != args.song_id or parsed.data.liked != args.liked:
            raise MusicServiceError("invalid_response", "音乐服务返回了不匹配的红心状态。")
        return parsed.data

    async def get_liked_song_ids(self) -> list[int]:
        """Read the authenticated account's full cloud like list; never mutate it."""
        payload = await self._request("GET", "/api/user/liked-songs")
        parsed = self._validate_response(payload, SpringLikedSongsResponse)
        return parsed.data

    async def get_account_id(self) -> int:
        """Resolve ownership for deletion evidence using the same authenticated session."""
        from app.schemas.context import SpringUserIdentityResponse
        payload = await self._request("GET", "/api/user/profile")
        parsed = self._validate_response(payload, SpringUserIdentityResponse)
        return parsed.data.user_id

    async def delete_playlist(self, playlist_id: int, confirmed: bool = False) -> DeletedPlaylistResult:
        args = DeletePlaylistArgs(playlist_id=playlist_id, confirmed=confirmed)
        if not args.confirmed:
            raise MusicServiceError("http_422", "删除整张歌单需要用户确认准确歌单 ID。")
        payload = await self._request("DELETE", f"/api/playlists/{args.playlist_id}",
                                      params={"confirmed": "true"})
        parsed = self._validate_response(payload, SpringDeletedPlaylistResponse)
        if parsed.data.playlist_id != args.playlist_id:
            raise MusicServiceError("invalid_response", "音乐服务返回了不匹配的删除歌单数据。")
        return parsed.data

    async def create_playlist(self, name: str, privacy: int = 0) -> CreatedPlaylistResult:
        args = CreatePlaylistArgs(name=name, privacy=privacy)
        payload = await self._request("POST", "/api/playlists", params=args.model_dump())
        parsed = self._validate_response(payload, SpringCreatedPlaylistResponse)
        assert isinstance(parsed, SpringCreatedPlaylistResponse)
        if parsed.data.name != args.name:
            raise MusicServiceError("invalid_response", "音乐服务返回了不匹配的新建歌单。")
        return parsed.data

    async def remove_song_from_playlist(
        self, playlist_id: int, song_id: int,
    ) -> RemoveSongFromPlaylistResult:
        args = RemoveSongFromPlaylistArgs(playlist_id=playlist_id, song_id=song_id)
        payload = await self._request(
            "DELETE", f"/api/playlists/{args.playlist_id}/songs/{args.song_id}"
        )
        parsed = self._validate_response(payload, SpringPlaylistMutationResponse)
        assert isinstance(parsed, SpringPlaylistMutationResponse)
        return RemoveSongFromPlaylistResult(
            **args.model_dump(), status=parsed.data.status, message=parsed.data.message,
        )


async def search_music(keyword: str, limit: int = 10) -> MusicSearchResult:
    """Compatibility entry point for direct search execution and existing callers."""
    return await MusicServiceClient().search_music(keyword, limit)


def _authenticated_client(
    runtime: ToolRuntime[MusicAgentContext, Any],
) -> MusicServiceClient:
    context = runtime.context
    session_cookie = context.session_cookie if context is not None else None
    return MusicServiceClient(session_cookie=session_cookie)


@tool(
    "search_music",
    args_schema=SearchMusicArgs,
    description=(
        "通过 MoodFlow 的 Spring Boot 后端搜索真实音乐。需要查找具体歌曲、歌手、"
        "专辑或为推荐结果取得可继续操作的真实 song_id 时调用。不要编造搜索结果。"
    ),
)
async def search_music_tool(keyword: str, limit: int = 10) -> str:
    result = await search_music(keyword, limit)
    return result.model_dump_json()


@tool(
    "search_entities_tool",
    args_schema=SearchEntitiesArgs,
    description=(
        "按 query 搜索真实音乐实体，可指定 entity_type 为 artist、album、playlist 或 song。"
        "返回统一的实体 ID、名称、类型及元数据；搜索歌手、专辑或歌单时优先调用。"
    ),
)
async def search_entities_tool(
    query: str,
    entity_type: str | None = None,
    limit: int = 5,
) -> str:
    search_input = SearchEntitiesArgs(
        query=query, entity_type=entity_type, limit=limit
    )
    entities = await MusicServiceClient().search_entities(
        search_input.query,
        entity_type=search_input.entity_type,
        limit=search_input.limit,
    )
    return EntitySearchResult(
        query=search_input.query,
        entity_type=search_input.entity_type,
        entities=[EntitySearchItem(
            id=entity.entity_id,
            name=entity.name,
            type=entity.entity_type,
            artist=entity.artist,
            album=entity.album,
            cover_url=entity.cover_url,
            metadata=entity.metadata,
        ) for entity in entities],
    ).model_dump_json()


@tool(
    "get_song_lyrics",
    description="根据真实 song_id 获取完整原文歌词、翻译和歌词状态。",
)
async def get_song_lyrics_tool(
    song_id: Annotated[int, Field(gt=0, description="要获取歌词的真实歌曲 ID。")],
    runtime: ToolRuntime[MusicAgentContext, Any],
) -> str:
    result = await _authenticated_client(runtime).get_song_lyrics(song_id)
    return result.model_dump_json()


@tool(
    "get_user_playlists",
    description=(
        "读取当前已登录用户的真实歌单，返回 playlist_id、名称和是否可编辑。"
        "当前用户身份由服务端会话注入，不接受 user_id 参数。"
    ),
)
async def get_user_playlists_tool(
    runtime: ToolRuntime[MusicAgentContext, Any],
) -> str:
    result = await _authenticated_client(runtime).get_user_playlists()
    return result.model_dump_json()


@tool(
    "get_playlist_detail_tool",
    description=(
        "用从 search_entities_tool 或 get_user_playlists 取得的真实 playlist_id "
        "读取歌单基本信息和完整歌曲列表。个人歌单应先调用 get_user_playlists。"
    ),
)
async def get_playlist_detail_tool(
    playlist_id: Annotated[int, Field(gt=0, description="可信来源的真实歌单 ID。")],
    runtime: ToolRuntime[MusicAgentContext, Any],
) -> str:
    result = await _authenticated_client(runtime).get_playlist_detail(playlist_id)
    return result.model_dump_json()


@tool(
    "get_album_detail_tool",
    args_schema=GetAlbumDetailArgs,
    description=(
        "用 search_entities_tool 取得的真实 album_id 读取整张专辑的完整歌曲列表。"
        "需要播放整张专辑时，先搜索专辑，再调用本工具。"
    ),
)
async def get_album_detail_tool(album_id: int) -> str:
    result = await MusicServiceClient().get_album_detail(album_id)
    return result.model_dump_json()


@tool(
    "add_song_to_playlist",
    description=(
        "把真实 song_id 加入真实 playlist_id。必须先从搜索/历史工具结果取得歌曲 ID，"
        "并从当前用户歌单工具结果取得目标歌单 ID。登录身份和归属校验由后端负责。"
    ),
)
async def add_song_to_playlist_tool(
    playlist_id: Annotated[int, Field(gt=0, description="目标歌单的真实 ID。")],
    song_id: Annotated[int, Field(gt=0, description="要加入歌单的真实歌曲 ID。")],
    runtime: ToolRuntime[MusicAgentContext, Any],
) -> str:
    result = await _authenticated_client(runtime).add_song_to_playlist(
        playlist_id,
        song_id,
    )
    return result.model_dump_json()


@tool("set_song_liked_tool")
async def set_song_liked_tool(
    song_id: Annotated[int, Field(gt=0)],
    liked: Annotated[bool, Field(strict=True)],
    runtime: ToolRuntime[MusicAgentContext, Any],
) -> str:
    """设置网易云云端红心状态。ID 来自当前歌曲或可信工具结果；成功后自动更新播放器红心。"""
    result = await _authenticated_client(runtime).set_song_liked(song_id, liked)
    return result.model_dump_json()


@tool("create_playlist_tool")
async def create_playlist_tool(
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40, pattern=r"\S")],
    runtime: ToolRuntime[MusicAgentContext, Any],
    privacy: Literal[0, 10] = 0,
) -> str:
    """按用户指定名称新建自建歌单，privacy 为 0 公开或 10 私密；返回可信歌单 ID 可继续添加歌曲。"""
    result = await _authenticated_client(runtime).create_playlist(name, privacy)
    return result.model_dump_json()


@tool("remove_song_from_playlist_tool")
async def remove_song_from_playlist_tool(
    playlist_id: Annotated[int, Field(gt=0)],
    song_id: Annotated[int, Field(gt=0)],
    runtime: ToolRuntime[MusicAgentContext, Any],
) -> str:
    """从当前用户可编辑歌单移除一首歌。先读用户歌单，再读取目标歌单详情确认歌曲 ID 和归属。"""
    result = await _authenticated_client(runtime).remove_song_from_playlist(playlist_id, song_id)
    return result.model_dump_json()


@tool("get_liked_songs")
async def get_liked_songs_tool(runtime: ToolRuntime[MusicAgentContext, Any]) -> str:
    """只读：读取当前登录账号云端红心歌曲 ID 集合，不添加、不取消红心，不执行任何写入。"""
    ids = await _authenticated_client(runtime).get_liked_song_ids()
    return LikedSongsResult(song_ids=sorted(set(ids))).model_dump_json()


def has_delete_confirmation(text: str, playlist_id: int) -> bool:
    """Model arguments cannot manufacture permission; require trusted user text and exact target."""
    # A later cancellation/question in the same turn overrides an earlier
    # affirmative clause. Do not discard it when splitting the input.
    if re.search(r"别|不要|不用|无需|不再|取消|不确认|撤销|先不|停止|是否|能否|能不能|可以吗", text):
        return False
    for clause in re.split(r"[,，。;；!?！？\n]|但是|不过|然后", text):
        if re.fullmatch(
            rf"\s*[“\"']?(?:请|麻烦)?(?:帮我)?(?:我)?(?:确认|确定)(?:要)?删除(?:整张|这张)?歌单"
            rf"\s*(?:(?:ID|编号)\s*[:：=]?\s*)?{playlist_id}\s*[”\"']?\s*",
            clause, re.IGNORECASE,
        ):
            return True
    return False


@tool("delete_playlist_tool")
async def delete_playlist_tool(
    playlist_id: Annotated[int, Field(gt=0, strict=True)],
    runtime: ToolRuntime[MusicAgentContext, Any],
    confirmed: Annotated[bool, Field(strict=True)] = False,
) -> str:
    """删除整张当前账号自建歌单。先读歌单并展示准确名称/ID，请用户二次确认。
    仅当本轮用户明确说“确认删除歌单 ID”且 confirmed=true 才执行；模型不能代为确认。
    系统/收藏/归属不明歌单拒绝删除。必须有 operation journal，删除后由 A 只读核对补记成功。
    """
    from app.agent.foundation.long_horizon.operations import active_operations
    text = (runtime.state or {}).get("current_input", "")
    if not confirmed or not has_delete_confirmation(text, playlist_id):
        raise MusicServiceError("http_422", f"请用户明确回复“确认删除歌单 {playlist_id}”；未执行删除。")
    if active_operations.get() is None:
        raise MusicServiceError("http_422", "缺少 operation journal，未执行删除。")
    result = await _authenticated_client(runtime).delete_playlist(playlist_id, confirmed)
    return result.model_dump_json()


MUSIC_AGENT_TOOLS: list[BaseTool] = [
    search_music_tool,
    search_entities_tool,
    get_song_lyrics_tool,
    get_user_playlists_tool,
    get_playlist_detail_tool,
    get_album_detail_tool,
    add_song_to_playlist_tool,
    set_song_liked_tool,
    create_playlist_tool,
    remove_song_from_playlist_tool,
    get_liked_songs_tool,
    delete_playlist_tool,
]


def handle_music_tool_error(error: Exception) -> str:
    """Convert tool failures into safe JSON ToolMessage content for the model."""
    if isinstance(error, MusicServiceError):
        code = error.code
        message = error.public_message
    else:
        code = "tool_execution_failed"
        message = "工具调用失败，请根据已有信息回答，并建议用户稍后重试。"
    logger.warning("Music tool execution failed: code=%s", code, exc_info=error)
    state = getattr(error, "write_state", None)
    if state == "unknown":
        message = "原写请求没有有效成功回执，实际效果仍未知；只允许读回核对，禁止重复写入。原始错误：" + message
    return ToolFailure(error=code, message=message, operation_id=getattr(error, "operation_id", None),
                       write_state=state).model_dump_json(exclude_none=True)

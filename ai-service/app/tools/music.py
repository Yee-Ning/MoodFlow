import logging
from typing import Annotated, Any

import httpx
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, Field, ValidationError

from app.config.settings import get_settings
from app.schemas.context import MusicAgentContext
from app.schemas.music import (
    AddSongToPlaylistArgs,
    AddSongToPlaylistResult,
    GetSongLyricsArgs,
    MusicSearchResult,
    SearchMusicArgs,
    SongLyricsResult,
    SpringMusicSearchResponse,
    SpringMusicEntityDetailResponse,
    SpringMusicEntitySearchResponse,
    SpringPlaylistMutationResponse,
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
                timeout=settings.spring_boot_timeout_seconds
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
            if entity_type not in {"song", "artist", "album"}:
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


MUSIC_AGENT_TOOLS: list[BaseTool] = [
    search_music_tool,
    get_song_lyrics_tool,
    get_user_playlists_tool,
    add_song_to_playlist_tool,
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
    return ToolFailure(error=code, message=message).model_dump_json()

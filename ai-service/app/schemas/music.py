from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)


NonBlankText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=r"\S"),
]


class SearchMusicArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: NonBlankText = Field(
        max_length=100,
        description="歌曲名、歌手名、专辑名或其他明确的音乐搜索关键词。",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="最多返回多少首匹配歌曲。",
    )


class SearchEntitiesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: NonBlankText = Field(max_length=100, description="音乐实体搜索关键词。")
    entity_type: Literal["song", "artist", "album", "playlist"] | None = None
    limit: int = Field(default=5, ge=1, le=50)


class GetPlaylistDetailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: int = Field(gt=0, description="从搜索或当前用户歌单取得的真实歌单 ID。")


class GetAlbumDetailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    album_id: int = Field(gt=0, description="从实体搜索取得的真实专辑 ID。")


class GetSongLyricsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_id: int = Field(gt=0, description="要获取歌词的真实歌曲 ID。")


class GetUserPlaylistsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddSongToPlaylistArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: int = Field(gt=0, description="目标歌单的真实 ID。")
    song_id: int = Field(gt=0, description="要加入歌单的真实歌曲 ID。")


class RemoveSongFromPlaylistArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: int = Field(gt=0, description="当前用户可编辑的真实歌单 ID。")
    song_id: int = Field(gt=0, description="歌单详情中的真实歌曲 ID。")


class CreatePlaylistArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: NonBlankText = Field(max_length=40, description="用户指定的新歌单名称。")
    privacy: Literal[0, 10] = Field(default=0, description="0 公开，10 私密。")


class DeletePlaylistArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: int = Field(gt=0, strict=True)
    confirmed: bool = Field(default=False, strict=True)


class SetSongLikedArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_id: int = Field(gt=0, description="上下文或工具结果中的真实歌曲 ID。")
    liked: bool = Field(strict=True, description="明确设置云端红心状态，不使用切换。")


class MusicTrack(BaseModel):
    """Normalized track data returned to the model by search_music."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    song_id: int = Field(
        validation_alias=AliasChoices("song_id", "id"),
        gt=0,
    )
    name: NonBlankText
    artists: list[NonBlankText] = Field(min_length=1)
    album: str
    cover_url: str = Field(
        default="",
        validation_alias=AliasChoices("cover_url", "coverUrl"),
    )
    duration: int = Field(default=0, ge=0)

    @field_validator("album", "cover_url")
    @classmethod
    def strip_optional_text(cls, value: str) -> str:
        return value.strip()


class MusicSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: NonBlankText
    limit: int = Field(ge=1, le=50)
    songs: list[MusicTrack]


class MusicEntityReference(BaseModel):
    """A related catalog entity returned by the Spring music service."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    entity_id: int = Field(validation_alias=AliasChoices("entity_id", "id"), gt=0)
    entity_type: Literal["song", "artist", "album", "playlist"] = Field(
        validation_alias=AliasChoices("entity_type", "entityType")
    )
    name: NonBlankText
    artist: str = ""
    album: str = ""

    @field_validator("artist", "album")
    @classmethod
    def strip_reference_text(cls, value: str) -> str:
        return value.strip()


class MusicEntity(BaseModel):
    """Normalized song, artist, or album entity exposed by Spring Boot."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    entity_id: int = Field(validation_alias=AliasChoices("entity_id", "id"), gt=0)
    entity_type: Literal["song", "artist", "album", "playlist"] = Field(
        validation_alias=AliasChoices("entity_type", "entityType")
    )
    name: NonBlankText
    artist: str = ""
    album: str = ""
    cover_url: str = Field(
        default="",
        validation_alias=AliasChoices("cover_url", "coverUrl"),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    related_entities: list[MusicEntityReference] = Field(
        default_factory=list,
        validation_alias=AliasChoices("related_entities", "relatedEntities"),
    )

    @field_validator("artist", "album", "cover_url")
    @classmethod
    def strip_entity_text(cls, value: str) -> str:
        return value.strip()


class SpringMusicEntitySearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: list[MusicEntity]


class SpringMusicEntityDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: MusicEntity


class EntitySearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0)
    name: NonBlankText
    type: Literal["song", "artist", "album", "playlist"]
    source: Literal["netease"] = "netease"
    artist: str = ""
    album: str = ""
    cover_url: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EntitySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: NonBlankText
    entity_type: Literal["song", "artist", "album", "playlist"] | None = None
    entities: list[EntitySearchItem]


class SongLyricsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_id: int = Field(gt=0)
    original_lyrics: str
    translated_lyrics: str
    status: Literal["available", "instrumental", "unavailable"]


class UserPlaylist(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    playlist_id: int = Field(
        validation_alias=AliasChoices("playlist_id", "id"),
        gt=0,
    )
    name: NonBlankText
    editable: bool


class UserPlaylistsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlists: list[UserPlaylist]


class AddSongToPlaylistResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: int = Field(gt=0)
    song_id: int = Field(gt=0)
    status: Literal["added", "duplicate", "forbidden"]
    message: NonBlankText


class RemoveSongFromPlaylistResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: int = Field(gt=0)
    song_id: int = Field(gt=0)
    status: Literal["removed", "not_found", "forbidden"]
    message: NonBlankText


class CreatedPlaylistResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    playlist_id: int = Field(gt=0, validation_alias=AliasChoices("playlist_id", "playlistId"))
    name: NonBlankText
    status: Literal["created"]
    message: NonBlankText


class SongLikeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    song_id: int = Field(gt=0, validation_alias=AliasChoices("song_id", "songId"))
    liked: bool = Field(strict=True)
    status: Literal["synced"]
    message: NonBlankText


class LikedSongsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_ids: list[Annotated[int, Field(gt=0, strict=True)]]


class DeletedPlaylistResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    playlist_id: int = Field(gt=0, validation_alias=AliasChoices("playlist_id", "playlistId"))
    status: Literal["deleted"]
    message: NonBlankText


class SpringDeletedPlaylistResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: DeletedPlaylistResult


class SpringCreatedPlaylistResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: CreatedPlaylistResult


class SpringSongLikeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: SongLikeResult


class SpringLikedSongsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: list[Annotated[int, Field(gt=0, strict=True)]]


class ToolFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: Literal[False] = False
    error: str
    message: str
    operation_id: str | None = None
    write_state: Literal["failed", "unknown", "confirmed"] | None = None


class SpringMusicSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: list[MusicTrack]


class SpringSongLyrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lyric: str
    translation: str
    status: Literal["available", "instrumental", "unavailable"]


class SpringSongLyricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: SpringSongLyrics


class SpringPlaylist(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    playlist_id: int = Field(validation_alias="id", gt=0)
    name: NonBlankText
    cover_img_url: str = Field(validation_alias="coverImgUrl")
    track_count: int = Field(validation_alias="trackCount", ge=0)
    creator: NonBlankText
    subscribed: bool
    creator_id: int | None = Field(default=None, gt=0, strict=True, validation_alias="creatorId")
    special_type: int | None = Field(default=None, strict=True, validation_alias="specialType")


class SpringUserPlaylistsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: list[SpringPlaylist]


class SpringPlaylistDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist: SpringPlaylist
    description: str
    songs: list[MusicTrack]


class SpringPlaylistDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: SpringPlaylistDetail


class PlaylistDetailResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: int = Field(gt=0)
    name: NonBlankText
    description: str
    cover_url: str
    creator: NonBlankText
    track_count: int = Field(ge=0)
    songs: list[MusicTrack]
    creator_id: int | None = Field(default=None, gt=0, strict=True)
    special_type: int | None = Field(default=None, strict=True)
    subscribed: bool | None = None


class SpringAlbumDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    album: MusicEntity
    songs: list[MusicTrack]


class SpringAlbumDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: SpringAlbumDetail


class AlbumDetailResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    album_id: int = Field(gt=0)
    name: NonBlankText
    artist: str
    cover_url: str
    songs: list[MusicTrack]


class SpringPlaylistMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["added", "duplicate", "forbidden", "removed", "not_found"]
    message: NonBlankText


class SpringPlaylistMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: SpringPlaylistMutation

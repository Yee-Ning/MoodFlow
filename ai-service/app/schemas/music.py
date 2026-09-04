from typing import Annotated, Literal

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


class GetSongLyricsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_id: int = Field(gt=0, description="要获取歌词的真实歌曲 ID。")


class GetUserPlaylistsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddSongToPlaylistArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    playlist_id: int = Field(gt=0, description="目标歌单的真实 ID。")
    song_id: int = Field(gt=0, description="要加入歌单的真实歌曲 ID。")


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


class ToolFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: Literal[False] = False
    error: str
    message: str


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


class SpringUserPlaylistsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: list[SpringPlaylist]


class SpringPlaylistMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["added", "duplicate", "forbidden"]
    message: NonBlankText


class SpringPlaylistMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int
    message: str
    data: SpringPlaylistMutation

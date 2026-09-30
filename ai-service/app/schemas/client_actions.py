"""Strict commands that the browser may execute for an AI response."""

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from app.schemas.music import MusicTrack
from app.schemas.player import PlayMode


class ClientActionName(StrEnum):
    PAUSE_PLAYER = "pause_player"
    RESUME_PLAYER = "resume_player"
    PLAY_NEXT = "play_next"
    PLAY_PREVIOUS = "play_previous"
    SET_VOLUME = "set_volume"
    SET_MUTED = "set_muted"
    SEEK_TO = "seek_to"
    SEEK_BY = "seek_by"
    SET_PLAY_MODE = "set_play_mode"
    PLAY_SONG = "play_song"
    INSERT_NEXT_TRACK = "insert_next_track"
    APPEND_TO_QUEUE = "append_to_queue"
    PLAY_ALBUM = "play_album"
    PLAY_PLAYLIST = "play_playlist"
    JUMP_TO_QUEUE_ITEM = "jump_to_queue_item"
    SET_LIKED = "set_liked"
    OPEN_LYRICS = "open_lyrics"
    CLEAR_QUEUE = "clear_queue"


class EmptyActionArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SetVolumeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    volume: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class SetMutedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    muted: bool


class SeekToArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seconds: float = Field(ge=0.0, allow_inf_nan=False)


class SeekByArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    offset_seconds: float = Field(ge=-86_400, le=86_400, allow_inf_nan=False)


class SetPlayModeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: PlayMode


class PlaySongArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    song: MusicTrack


class PlayAlbumArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    album_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=300)
    tracks: list[MusicTrack] = Field(min_length=1, max_length=1000)


class PlayPlaylistArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    playlist_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=300)
    tracks: list[MusicTrack] = Field(min_length=1, max_length=1000)


class JumpToQueueItemArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    song_id: int | None = Field(default=None, gt=0)
    index: int | None = Field(
        default=None,
        gt=0,
        description="从 1 开始的队列位置。",
    )

    @model_validator(mode="after")
    def require_exactly_one_target(self) -> "JumpToQueueItemArguments":
        if (self.song_id is None) == (self.index is None):
            raise ValueError("provide exactly one of song_id or index")
        return self

    @model_serializer(mode="wrap")
    def omit_unused_target(self, handler):
        return {
            key: value
            for key, value in handler(self).items()
            if value is not None
        }


class SetLikedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    song_id: int = Field(gt=0)
    liked: bool


ClientActionArguments = Annotated[
    EmptyActionArguments
    | SetVolumeArguments
    | SetMutedArguments
    | SeekToArguments
    | SeekByArguments
    | SetPlayModeArguments
    | PlaySongArguments
    | PlayAlbumArguments
    | PlayPlaylistArguments
    | JumpToQueueItemArguments
    | SetLikedArguments,
    Field(union_mode="left_to_right"),
]


_EXPECTED_ARGUMENT_TYPE: dict[ClientActionName, type[BaseModel]] = {
    ClientActionName.PAUSE_PLAYER: EmptyActionArguments,
    ClientActionName.RESUME_PLAYER: EmptyActionArguments,
    ClientActionName.PLAY_NEXT: EmptyActionArguments,
    ClientActionName.PLAY_PREVIOUS: EmptyActionArguments,
    ClientActionName.SET_VOLUME: SetVolumeArguments,
    ClientActionName.SET_MUTED: SetMutedArguments,
    ClientActionName.SEEK_TO: SeekToArguments,
    ClientActionName.SEEK_BY: SeekByArguments,
    ClientActionName.SET_PLAY_MODE: SetPlayModeArguments,
    ClientActionName.PLAY_SONG: PlaySongArguments,
    ClientActionName.INSERT_NEXT_TRACK: PlaySongArguments,
    ClientActionName.APPEND_TO_QUEUE: PlaySongArguments,
    ClientActionName.PLAY_ALBUM: PlayAlbumArguments,
    ClientActionName.PLAY_PLAYLIST: PlayPlaylistArguments,
    ClientActionName.JUMP_TO_QUEUE_ITEM: JumpToQueueItemArguments,
    ClientActionName.SET_LIKED: SetLikedArguments,
    ClientActionName.OPEN_LYRICS: EmptyActionArguments,
    ClientActionName.CLEAR_QUEUE: EmptyActionArguments,
}


class ClientAction(BaseModel):
    """Allow-listed command emitted by AI Service and executed by the browser."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    name: ClientActionName
    arguments: ClientActionArguments

    @model_validator(mode="after")
    def arguments_match_action(self) -> "ClientAction":
        expected = _EXPECTED_ARGUMENT_TYPE[self.name]
        if not isinstance(self.arguments, expected):
            raise ValueError(f"{self.name.value} requires {expected.__name__}")
        return self


CLIENT_ACTION_ADAPTER = TypeAdapter(ClientAction)

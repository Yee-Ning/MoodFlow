from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.agent.foundation.routing import PlayMode
from app.schemas.music import MusicTrack


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
    SET_LIKED = "set_liked"
    OPEN_LYRICS = "open_lyrics"


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
    ClientActionName.SET_LIKED: SetLikedArguments,
    ClientActionName.OPEN_LYRICS: EmptyActionArguments,
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


class SongReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int = Field(gt=0)
    song_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=300)
    artists: list[str] = Field(default_factory=list)
    album: str = Field(default="", max_length=300)
    cover_url: str = ""
    duration: int = Field(default=0, ge=0)


class PlaylistReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    position: int = Field(gt=0)
    playlist_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=300)
    editable: bool


class ReferencedEntities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    songs: list[SongReference] = Field(default_factory=list)
    playlists: list[PlaylistReference] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.songs and not self.playlists


class PendingClientActionPayload(BaseModel):
    """Payload returned by a LangChain command tool before browser execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = Field(default="pending_client_execution", pattern="^pending_client_execution$")
    client_action: dict[str, Any]


CLIENT_ACTION_ADAPTER = TypeAdapter(ClientAction)

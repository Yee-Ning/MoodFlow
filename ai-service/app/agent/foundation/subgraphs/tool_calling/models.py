from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.client_actions import (
    CLIENT_ACTION_ADAPTER,
    ClientAction,
    ClientActionArguments,
    ClientActionName,
    EmptyActionArguments,
    PlaySongArguments,
    SeekByArguments,
    SeekToArguments,
    SetLikedArguments,
    SetMutedArguments,
    SetPlayModeArguments,
    SetVolumeArguments,
)


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

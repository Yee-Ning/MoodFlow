"""Shared, read-only browser player snapshot contracts."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlayerState(StrEnum):
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class PlayMode(StrEnum):
    SEQUENCE = "sequence"
    LIST_LOOP = "list-loop"
    SINGLE_LOOP = "single-loop"
    SHUFFLE = "shuffle"


class PlayerContext(BaseModel):
    """Trusted snapshot supplied by the MoodFlow browser application."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    has_current_song: bool = False
    current_song_id: int | None = Field(default=None, gt=0)
    player_state: PlayerState = PlayerState.UNKNOWN
    volume: float = Field(default=0.72, ge=0.0, le=1.0, allow_inf_nan=False)
    is_muted: bool = False
    play_mode: PlayMode = PlayMode.LIST_LOOP
    current_time: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    duration: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    queue_size: int = Field(default=0, ge=0)
    current_index: int = Field(default=-1, ge=-1)
    current_song_liked: bool | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> "PlayerContext":
        if self.current_song_id is not None and not self.has_current_song:
            raise ValueError("current_song_id requires has_current_song=true")
        if self.current_index >= self.queue_size and self.current_index != -1:
            raise ValueError("current_index must refer to an item in the queue")
        if self.current_time > self.duration and self.duration > 0:
            raise ValueError("current_time cannot exceed duration")
        return self

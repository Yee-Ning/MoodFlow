from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IntentType(StrEnum):
    CURRENT_SONG = "current_song"
    TOOL_CALLING = "tool_calling"
    WORLDLINE = "worldline"
    GENERAL_CHAT = "general_chat"
    UNKNOWN = "unknown"


class IntentSource(StrEnum):
    RULE = "rule"
    MODEL = "model"
    SEMANTIC = "semantic"
    LLM = "llm"


class PlayerState(StrEnum):
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class RoutingContext(BaseModel):
    """Read-only application context available to every router layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    has_current_song: bool = False
    current_song_id: int | None = Field(default=None, gt=0)
    player_state: PlayerState = PlayerState.UNKNOWN

    @model_validator(mode="after")
    def song_id_requires_current_song(self) -> "RoutingContext":
        if self.current_song_id is not None and not self.has_current_song:
            raise ValueError("current_song_id requires has_current_song=true")
        return self


class IntentItem(BaseModel):
    """One intent recognized by a layer of the hybrid router."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: IntentType
    sub_intent: str | None = Field(default=None, min_length=1, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)
    source: IntentSource
    evidence: str = Field(min_length=1, max_length=4_000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    order: int = Field(ge=0)

    @model_validator(mode="after")
    def span_must_be_ordered(self) -> "IntentItem":
        if self.end <= self.start:
            raise ValueError("intent span end must be greater than start")
        return self


class UnresolvedSegment(BaseModel):
    """An exact slice of the original query; offsets are never concatenated-text offsets."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=4_000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    order: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_slice(self) -> "UnresolvedSegment":
        if not self.text.strip() or self.end - self.start != len(self.text):
            raise ValueError("segment span must match nonblank text length")
        return self


class RouteKey(StrEnum):
    CURRENT_SONG = "current_song"
    TOOL_CALLING = "tool_calling"
    WORLDLINE = "worldline"


class RouteTask(BaseModel):
    """One future router-produced unit of work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    route_key: RouteKey


class RouteDecision(BaseModel):
    """Partial or complete result shared by the hybrid router layers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intents: list[IntentItem] = Field(default_factory=list)
    unresolved_text: str = Field(default="", max_length=4_000)
    unresolved_segments: list[UnresolvedSegment] = Field(default_factory=list)
    is_complete: bool = False
    needs_semantic: bool = False
    needs_llm: bool = False
    needs_clarification: bool = False
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    # Graph Foundation V1 still uses explicitly injected development tasks.
    # A later Intent Merger will project final intents into this task list.
    tasks: list[RouteTask] = Field(default_factory=list)

    @model_validator(mode="after")
    def task_ids_must_be_unique(self) -> "RouteDecision":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("route task ids must be unique")
        return self

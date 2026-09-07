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


class PlayMode(StrEnum):
    SEQUENCE = "sequence"
    LIST_LOOP = "list-loop"
    SINGLE_LOOP = "single-loop"
    SHUFFLE = "shuffle"


class RoutingContext(BaseModel):
    """Read-only application context available to every router layer."""

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
    def song_id_requires_current_song(self) -> "RoutingContext":
        if self.current_song_id is not None and not self.has_current_song:
            raise ValueError("current_song_id requires has_current_song=true")
        if self.current_index >= self.queue_size and self.current_index != -1:
            raise ValueError("current_index must refer to an item in the queue")
        if self.current_time > self.duration and self.duration > 0:
            raise ValueError("current_time cannot exceed duration")
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
    GENERAL_CHAT = "general_chat"


class RouteTask(BaseModel):
    """One future router-produced unit of work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    route_key: RouteKey
    intent_orders: list[int] = Field(default_factory=list)


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
        if len({task.route_key for task in self.tasks}) != len(self.tasks):
            raise ValueError("route keys must be unique; group same-route intents")
        if [item.order for item in self.intents] != list(range(len(self.intents))):
            raise ValueError("intent orders must be contiguous from zero")
        if self.is_complete and (
            self.unresolved_text or self.unresolved_segments or self.needs_semantic
            or self.needs_llm or self.needs_clarification
        ):
            raise ValueError("complete decisions cannot have unresolved routing work")
        for task in self.tasks:
            if len(set(task.intent_orders)) != len(task.intent_orders):
                raise ValueError("task intent references must be unique")
            for order in task.intent_orders:
                if not 0 <= order < len(self.intents):
                    raise ValueError("task references missing intent")
                if self.intents[order].intent.value != task.route_key.value:
                    raise ValueError("task route does not match referenced intent")
        return self

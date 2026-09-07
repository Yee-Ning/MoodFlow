from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.player import PlayMode, PlayerContext, PlayerState


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


class RoutingContext(PlayerContext):
    """Read-only application context available to every router layer."""

    pass


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
    """One executable unit of work produced by a final route decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    route_key: RouteKey
    input_text: str = Field(min_length=1, max_length=4_000)
    intent_orders: list[int] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def task_fields_must_be_consistent(self) -> "RouteTask":
        if not self.input_text.strip():
            raise ValueError("route task input must not be blank")
        if len(self.intent_orders) != len(set(self.intent_orders)):
            raise ValueError("task intent references must be unique")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("task dependencies must be unique")
        if self.task_id in self.depends_on:
            raise ValueError("route task cannot depend on itself")
        return self


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
    clarification_question: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000,
    )
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    tasks: list[RouteTask] = Field(default_factory=list)

    @model_validator(mode="after")
    def task_ids_must_be_unique(self) -> "RouteDecision":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("route task ids must be unique")
        if [item.order for item in self.intents] != list(range(len(self.intents))):
            raise ValueError("intent orders must be contiguous from zero")
        if self.tasks and not self.is_complete:
            raise ValueError("incomplete route decisions cannot dispatch tasks")
        if self.is_complete and (
            self.unresolved_text or self.unresolved_segments or self.needs_semantic
            or self.needs_llm or self.needs_clarification
        ):
            raise ValueError("complete decisions cannot have unresolved routing work")
        if self.needs_clarification != (self.clarification_question is not None):
            raise ValueError(
                "clarification decisions must contain exactly one clarification question"
            )

        assigned_orders: list[int] = []
        prior_task_ids: set[str] = set()
        for task in self.tasks:
            if not set(task.depends_on) <= prior_task_ids:
                raise ValueError("task dependencies must reference earlier tasks")
            for order in task.intent_orders:
                if not 0 <= order < len(self.intents):
                    raise ValueError("task references missing intent")
                if self.intents[order].intent.value != task.route_key.value:
                    raise ValueError("task route does not match referenced intent")
            if task.intent_orders:
                evidence = list(
                    dict.fromkeys(
                        self.intents[order].evidence
                        for order in task.intent_orders
                    )
                )
                if task.input_text != "；".join(evidence):
                    raise ValueError("task input must match its referenced intent evidence")
            assigned_orders.extend(task.intent_orders)
            prior_task_ids.add(task.task_id)

        if len(assigned_orders) != len(set(assigned_orders)):
            raise ValueError("an intent cannot be assigned to multiple tasks")
        if self.is_complete and self.intents and self.tasks:
            if sorted(assigned_orders) != list(range(len(self.intents))):
                raise ValueError("complete decisions must assign every intent exactly once")
        return self

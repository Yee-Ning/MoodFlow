from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, field_validator, model_validator

from app.schemas.client_actions import ClientAction
from app.schemas.player import PlayerContext

if TYPE_CHECKING:
    from app.agent.foundation.routing import RoutingContext


class ChatContext(BaseModel):
    """Lightweight application-state references supplied by the frontend."""

    model_config = ConfigDict(extra="forbid")

    current_song_id: int | None = Field(default=None, gt=0)
    current_playlist_id: int | None = Field(default=None, gt=0)
    page: str | None = Field(default=None, max_length=200)

    @field_validator("page")
    @classmethod
    def normalize_page(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DecisionReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["event", "artifact"]
    target_id: str = Field(min_length=1, max_length=200)


class UserDecisionRequest(BaseModel):
    """Transport DTO; importing chat schemas must not construct agent graphs."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    decision_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=4000)
    kind: Literal["decision", "constraint"] = "decision"
    effect: Literal["add", "revoke", "supersede"] = "add"
    supersedes: tuple[DecisionReferenceRequest, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def replacement_has_reference(self):
        if self.effect != "add" and not self.supersedes:
            raise ValueError("constraint replacement requires explicit references")
        return self


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(max_length=4000)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    resume: bool = False
    new_session: bool = False
    decisions: tuple[UserDecisionRequest, ...] = Field(default=(), max_length=16)
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)
    current_song_id: int | None = Field(default=None, gt=0)
    context: ChatContext | None = None
    player_context: PlayerContext | None = None

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        return value

    @model_validator(mode="after")
    def valid_lifecycle(self):
        if self.resume:
            if self.message or self.new_session or self.decisions or not self.thread_id:
                raise ValueError("resume requires an existing thread and empty input")
        elif not self.message:
            raise ValueError("message must not be blank")
        return self

    def continuity_options(self):
        values = {key: getattr(self, key) for key in ("request_id", "resume", "new_session") if getattr(self, key)}
        if self.decisions:
            from app.agent.foundation.long_horizon.public_events import PublicDecision
            values["decisions"] = tuple(PublicDecision.model_validate(item.model_dump(mode="json")) for item in self.decisions)
        return values

    @field_validator("thread_id")
    @classmethod
    def normalize_thread_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("thread_id must not be blank")
        return normalized

    def resolved_current_song_id(self) -> int | None:
        """Prefer the new top-level field while keeping older clients compatible."""
        if "current_song_id" in self.model_fields_set:
            return self.current_song_id
        if self.player_context is not None:
            return self.player_context.current_song_id
        return self.context.current_song_id if self.context is not None else None

    def resolved_routing_context(self) -> "RoutingContext":
        """Resolve a full player snapshot while preserving legacy request fields."""
        from app.agent.foundation.routing import RoutingContext

        current_song_id = self.resolved_current_song_id()
        if self.player_context is None:
            return RoutingContext(
                has_current_song=current_song_id is not None,
                current_song_id=current_song_id,
            )
        if "current_song_id" not in self.model_fields_set:
            return RoutingContext.model_validate(
                self.player_context.model_dump(mode="python")
            )
        return RoutingContext.model_validate(
            {
                **self.player_context.model_dump(mode="python"),
                "has_current_song": current_song_id is not None,
                "current_song_id": current_song_id,
            }
        )


MessageText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000, pattern=r"\S"),
]
SearchQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"\S"),
]


class _StructuredOutputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: MessageText


class ChatOutput(_StructuredOutputBase):
    type: Literal["chat"]
    search_query: None


class MusicSearchOutput(_StructuredOutputBase):
    type: Literal["music_search"]
    search_query: SearchQuery


class MusicRecommendationOutput(_StructuredOutputBase):
    type: Literal["music_recommendation"]
    search_query: None


class FoundationTaskOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    status: Literal["success", "failed", "needs_input"]
    subgraph: Literal["current_song", "tool_calling", "general_chat", "worldline"]
    answer: str | None = Field(max_length=10_000)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_answer_and_sources(self) -> "FoundationTaskOutput":
        if self.status == "success" and not (self.answer and self.answer.strip()):
            raise ValueError("successful task must contain an answer")
        source_ids = {
            item.get("id") for item in self.sources if isinstance(item.get("id"), str)
        }
        if any(item.get("id") not in source_ids for item in self.citations):
            raise ValueError("citations must reference available sources")
        return self


class FoundationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["chat"] = "chat"
    status: Literal["success", "failed", "needs_input"]
    message: str = Field(min_length=1)
    search_query: None = None
    results: list[FoundationTaskOutput] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result_statuses(self) -> "FoundationResponse":
        statuses = {item.status for item in self.results}
        if self.status == "success" and statuses.difference({"success"}):
            raise ValueError("successful response cannot contain failed tasks")
        if self.status == "needs_input" and "failed" in statuses:
            raise ValueError("input request cannot contain failed tasks")
        return self


StructuredOutput = Annotated[
    ChatOutput | MusicSearchOutput | MusicRecommendationOutput,
    Field(discriminator="type"),
]
"""Strict structured payload returned by the LLM."""

STRUCTURED_OUTPUT_ADAPTER = TypeAdapter(StructuredOutput)

ChatResponse = StructuredOutput


class MessageStreamEvent(BaseModel):
    type: Literal["message"] = "message"
    delta: str = Field(min_length=1)


class UpdateStreamEvent(BaseModel):
    type: Literal["update"] = "update"
    node: Literal[
        "load_song_context",
        "agent",
        "tools",
        "prepare_context",
        "intent_router",
        "development_route",
        "current_song_subgraph",
        "tool_calling_subgraph",
        "worldline_subgraph",
        "general_chat_subgraph",
        "aggregate_results",
        "final_response",
        "clarify",
    ]
    status: Literal["completed"] = "completed"


class ClientActionStreamEvent(BaseModel):
    type: Literal["action"] = "action"
    action: ClientAction


class FinalStreamEvent(BaseModel):
    type: Literal["final"] = "final"
    response: StructuredOutput | FoundationResponse


SongAssistantStreamEvent = (
    MessageStreamEvent
    | UpdateStreamEvent
    | ClientActionStreamEvent
    | FinalStreamEvent
)

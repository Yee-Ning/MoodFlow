from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.foundation.routing import (
    IntentItem, IntentType, RoutingContext, UnresolvedSegment,
)


class LLMIntentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: IntentType
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidence: str = Field(min_length=1, max_length=4_000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def valid_candidate(self) -> "LLMIntentCandidate":
        if self.intent is IntentType.UNKNOWN or self.end <= self.start:
            raise ValueError("return no candidates for unknown intent; span must be ordered")
        return self


class LLMStructuredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intents: list[LLMIntentCandidate] = Field(max_length=16)
    needs_clarification: bool


class LLMRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_text: str = Field(min_length=1, max_length=4_000)
    segment: UnresolvedSegment
    routing_context: RoutingContext = Field(default_factory=RoutingContext)
    model_scores: dict[str, float] = Field(default_factory=dict)
    model_intents: list[IntentItem] = Field(default_factory=list)
    escalation_reason: str

    @model_validator(mode="after")
    def grounded_segment(self) -> "LLMRouteRequest":
        segment = self.segment
        if self.original_text[segment.start:segment.end] != segment.text:
            raise ValueError("segment must be an exact slice of original_text")
        return self


class LLMRouteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    segment: UnresolvedSegment
    intents: list[IntentItem]
    unresolved_segments: list[UnresolvedSegment]
    needs_clarification: bool

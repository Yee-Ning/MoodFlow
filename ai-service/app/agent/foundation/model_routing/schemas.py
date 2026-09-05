import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.foundation.routing import IntentItem, IntentType


class ModelRouteResult(BaseModel):
    """Typed result emitted by ModelRouter before any future intent merger."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intents: list[IntentItem] = Field(default_factory=list)
    scores: dict[str, float]
    accepted_labels: list[str] = Field(default_factory=list)
    unresolved_text: str = Field(default="", max_length=4_000)
    needs_llm: bool = False

    @model_validator(mode="after")
    def result_must_be_consistent(self) -> "ModelRouteResult":
        trainable_labels = {
            intent.name for intent in IntentType if intent is not IntentType.UNKNOWN
        }
        if set(self.scores) != trainable_labels:
            missing = sorted(trainable_labels - set(self.scores))
            extra = sorted(set(self.scores) - trainable_labels)
            raise ValueError(f"score labels mismatch: missing={missing}, extra={extra}")
        if any(
            not math.isfinite(score) or score < 0.0 or score > 1.0
            for score in self.scores.values()
        ):
            raise ValueError("scores must be within [0, 1]")
        if len(self.accepted_labels) != len(set(self.accepted_labels)):
            raise ValueError("accepted_labels must not contain duplicates")
        if any(label not in trainable_labels for label in self.accepted_labels):
            raise ValueError("accepted_labels contains a non-trainable intent")
        intent_labels = [item.intent.name for item in self.intents]
        if intent_labels != self.accepted_labels:
            raise ValueError("accepted_labels must match intents in order")
        if not self.intents and not self.needs_llm:
            raise ValueError("an empty intent result must set needs_llm=true")
        return self

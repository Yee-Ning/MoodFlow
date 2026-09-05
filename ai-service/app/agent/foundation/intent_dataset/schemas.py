from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.agent.foundation.routing import IntentType


TRAINABLE_INTENTS: tuple[IntentType, ...] = tuple(
    intent for intent in IntentType if intent is not IntentType.UNKNOWN
)


class DatasetSource(StrEnum):
    MANUAL = "manual"
    SYNTHETIC = "synthetic"
    RULE_BOOTSTRAP = "rule_bootstrap"


class DatasetProvenance(BaseModel):
    """Lightweight lineage used to keep related paraphrases in one split."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seed_id: str | None = Field(default=None, min_length=1, max_length=128)
    generation_method: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("seed_id", "generation_method")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("provenance values must not be blank")
        return stripped


def _parse_intent(value: Any) -> IntentType:
    if isinstance(value, IntentType):
        return value
    if not isinstance(value, str):
        raise ValueError("intent labels must be strings or IntentType values")
    candidate = value.strip()
    try:
        return IntentType[candidate.upper()]
    except KeyError:
        try:
            return IntentType(candidate.lower())
        except ValueError as error:
            raise ValueError(f"unknown intent label: {value}") from error


class IntentDatasetSample(BaseModel):
    """Canonical JSONL row for supervised multi-label intent classification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4_000)
    labels: list[IntentType] = Field(min_length=1)
    source: DatasetSource
    is_multi_intent: bool = False
    is_hard_case: bool = False
    provenance: DatasetProvenance | None = None

    @field_validator("id", "text")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("labels", mode="before")
    @classmethod
    def parse_labels(cls, value: Any) -> list[IntentType]:
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
            raise ValueError("labels must be a list")
        return [_parse_intent(item) for item in value]

    @model_validator(mode="after")
    def validate_training_labels(self) -> "IntentDatasetSample":
        if IntentType.UNKNOWN in self.labels:
            raise ValueError("UNKNOWN is not a trainable business intent")
        if len(self.labels) != len(set(self.labels)):
            raise ValueError("labels must not contain duplicates")
        object.__setattr__(self, "is_multi_intent", len(self.labels) > 1)
        return self

    @field_serializer("labels")
    def serialize_labels(self, labels: list[IntentType]) -> list[str]:
        return [label.name for label in labels]


def label_signature(labels: list[IntentType]) -> tuple[str, ...]:
    positions = {intent: index for index, intent in enumerate(TRAINABLE_INTENTS)}
    return tuple(label.name for label in sorted(labels, key=positions.__getitem__))

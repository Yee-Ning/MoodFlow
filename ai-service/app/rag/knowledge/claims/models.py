"""Atomic, source-grounded claim contracts."""

from __future__ import annotations

from datetime import date as Date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


ClaimObjectValue = str | int | float | bool


class ClaimPredicate(StrEnum):
    BORN_ON = "born_on"
    DEBUTED = "debuted"
    RELEASED = "released"
    FIRST_ALBUM_OF = "first_album_of"
    INCLUDES_TRACK = "includes_track"
    HAS_GENRE = "has_genre"
    CONTAINS_MUSIC_ELEMENT = "contains_music_element"
    WON_AWARD = "won_award"
    CHART_POSITION = "chart_position"
    PERFORMED_AT = "performed_at"
    COLLABORATED_WITH = "collaborated_with"
    SIGNED_TO = "signed_to"
    OCCURRED_AT = "occurred_at"
    OTHER = "other"


class ClaimSource(StrEnum):
    RULE = "rule"
    LLM = "llm"


class ClaimExtractionStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ClaimTime(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int | None = Field(default=None, ge=1000, le=2999)
    date: Date | None = None
    start_year: int | None = Field(default=None, ge=1000, le=2999)
    end_year: int | None = Field(default=None, ge=1000, le=2999)
    raw: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_range(self) -> "ClaimTime":
        if (self.start_year is None) != (self.end_year is None):
            raise ValueError("claim time range requires both endpoints")
        if (
            self.start_year is not None
            and self.end_year is not None
            and self.start_year > self.end_year
        ):
            raise ValueError("claim time start_year must not exceed end_year")
        if not any(
            value is not None
            for value in (self.year, self.date, self.start_year, self.end_year, self.raw)
        ):
            raise ValueError("empty claim time must be null")
        return self


class ClaimCandidate(BaseModel):
    """Provider-facing claim before deterministic identity and source assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: str = Field(min_length=1, max_length=2_000)
    subject_entity_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$"
    )
    predicate: ClaimPredicate
    object_entity_id: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$"
    )
    object_value: ClaimObjectValue | None = None
    time: ClaimTime | None = None
    evidence_span: str = Field(min_length=1, max_length=5_000)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_object(self) -> "ClaimCandidate":
        if (self.object_entity_id is None) == (self.object_value is None):
            raise ValueError("claim requires exactly one object form")
        return self


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(pattern=r"^claim:[0-9a-f]{24}$")
    statement: str = Field(min_length=1, max_length=2_000)
    subject_entity_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$"
    )
    predicate: ClaimPredicate
    object_entity_id: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$"
    )
    object_value: ClaimObjectValue | None = None
    time: ClaimTime | None = None
    source_chunk_id: str = Field(min_length=1, max_length=200)
    evidence_span: str = Field(min_length=1, max_length=5_000)
    confidence: float = Field(ge=0, le=1)
    extraction_source: ClaimSource

    @model_validator(mode="after")
    def validate_object(self) -> "Claim":
        if (self.object_entity_id is None) == (self.object_value is None):
            raise ValueError("claim requires exactly one object form")
        return self


class ClaimExtractionOutput(BaseModel):
    """Single-call Structured Output contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: list[ClaimCandidate] = Field(max_length=100)


class ClaimExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: list[Claim] = Field(default_factory=list, max_length=200)
    status: ClaimExtractionStatus = ClaimExtractionStatus.SUCCESS
    warnings: list[str] = Field(default_factory=list, max_length=100)
    errors: list[str] = Field(default_factory=list, max_length=20)

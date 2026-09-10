"""Contracts for automatic Source Ingestion V2 decisions and provenance."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .models import CandidateSource


class DiscoveredCandidate(CandidateSource):
    """A traceable discovery result that is evaluated by automatic gates."""

    discovery_query: str = Field(min_length=1, max_length=1_000)
    replacement_group: str = Field(
        pattern=r"^[a-z0-9][a-z0-9-]{2,127}$"
    )
    priority: int = Field(default=1, ge=1, le=100)
    replaces_source_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9-]{2,127}$"
    )
    canonical_url: HttpUrl | None = None
    external_id: str | None = Field(default=None, min_length=1, max_length=300)


class FilterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    accepted: bool
    reasons: list[str] = Field(default_factory=list)


class DuplicateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    is_duplicate: bool
    reason: str | None = None
    duplicate_of: str | None = None


class SnapshotValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    valid: bool
    reasons: list[str] = Field(default_factory=list)
    size_bytes: int = Field(ge=0)
    media_type: str
    detected_encoding: str | None = None

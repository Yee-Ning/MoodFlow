"""Contracts for automatic Source Ingestion V2 decisions and provenance."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .models import CandidateSource, FileType


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


class SourceAttemptStatus(StrEnum):
    FILTERED_OUT = "filtered_out"
    FETCH_FAILED = "fetch_failed"
    SNAPSHOT_VALID = "snapshot_valid"
    UNRESOLVED_SOURCE_GAP = "unresolved_source_gap"
    REPLACEMENT_ATTEMPT = "replacement_attempt"
    DUPLICATE_CANDIDATE = "duplicate_candidate"


class SourceAttemptRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: str = Field(min_length=1, max_length=100)
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,127}$")
    status: SourceAttemptStatus
    occurred_at: datetime
    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    publisher: str = Field(min_length=1, max_length=300)
    file_type: FileType
    source_tier: int = Field(ge=1, le=4)
    topics: list[str] = Field(min_length=1, max_length=20)
    replacement_group: str = Field(min_length=1, max_length=128)
    attempt_number: int = Field(default=0, ge=0)
    replaces_source_id: str | None = None
    reason: str | None = Field(default=None, max_length=1_000)
    duplicate_of: str | None = Field(default=None, max_length=128)
    raw_path: str | None = Field(default=None, max_length=1_000)


class AutoIngestionRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    total_discovered: int = Field(ge=0)
    filtered_out: int = Field(ge=0)
    duplicate_candidates: int = Field(ge=0)
    fetch_attempted: int = Field(ge=0)
    fetch_success: int = Field(ge=0)
    fetch_failed: int = Field(ge=0)
    replacement_success: int = Field(ge=0)
    unresolved_source_gap: int = Field(ge=0)
    total_valid_snapshots: int = Field(ge=0)

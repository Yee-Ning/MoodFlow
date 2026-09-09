"""Strict contracts for Source Ingestion V1."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
)


CORPUS_VERSION = "jay-chou-corpus-v1"


class FileType(StrEnum):
    HTML = "html"
    PDF = "pdf"
    JSON = "json"
    TXT = "txt"


class ReviewStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"


class SourceQuality(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceType(StrEnum):
    OFFICIAL_PROFILE = "official_profile"
    RECORD_LABEL = "record_label"
    OFFICIAL_NEWS = "official_news"
    OFFICIAL_VIDEO = "official_video"
    GOVERNMENT_RECORD = "government_record"
    INTERVIEW = "interview"
    MEDIA_ARTICLE = "media_article"
    ACADEMIC_PAPER = "academic_paper"
    ENCYCLOPEDIA = "encyclopedia"
    METADATA_API = "metadata_api"


class SourceNotApprovedError(PermissionError):
    """A pending or rejected source attempted to cross the fetch boundary."""


class CandidateSource(BaseModel):
    """A traceable source that must be reviewed before it may be fetched."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,127}$")
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    publisher: str = Field(min_length=1, max_length=300)
    author: str | None = Field(default=None, max_length=300)
    published_at: date | None = None
    source_type: SourceType
    file_type: FileType
    artist: str = Field(min_length=1, max_length=300)
    album: str | None = Field(default=None, max_length=300)
    topics: list[str] = Field(min_length=1, max_length=20)
    source_tier: int = Field(ge=1, le=4)
    source_quality: SourceQuality
    reason: str = Field(min_length=1, max_length=1_000)
    status: ReviewStatus = ReviewStatus.PENDING

    @field_validator("title", "publisher", "author", "artist", "album", "reason")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("text fields must not be blank")
        return normalized

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("topics must not contain blank values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("topics must be unique")
        return normalized


class RawSnapshotRecord(BaseModel):
    """Append-only provenance record for one immutable raw response body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,127}$")
    original_url: HttpUrl
    content_type: str = Field(min_length=1, max_length=300)
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fetched_at: datetime
    raw_path: str = Field(min_length=1, max_length=1_000)
    http_status: int = Field(ge=100, le=599)
    source_quality: SourceQuality
    corpus_version: str = Field(min_length=1, max_length=100)


def ensure_approved(candidate: CandidateSource) -> None:
    if candidate.status is not ReviewStatus.APPROVED:
        raise SourceNotApprovedError(
            f"source {candidate.source_id} is {candidate.status.value}; "
            "only approved sources may be fetched"
        )

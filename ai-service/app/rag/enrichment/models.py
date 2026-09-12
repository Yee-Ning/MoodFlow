"""Strict contracts for offline chunk metadata enrichment."""

from __future__ import annotations

from datetime import date as Date
from enum import StrEnum
from typing import Annotated, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.rag.chunk.models import ChildChunk, ParentChunk


class ContentType(StrEnum):
    ARTIST_BIOGRAPHY = "artist_biography"
    CAREER_EVENT = "career_event"
    SONG_DESCRIPTION = "song_description"
    ALBUM_DESCRIPTION = "album_description"
    MUSIC_STYLE = "music_style"
    MUSIC_ANALYSIS = "music_analysis"
    AWARD_EVENT = "award_event"
    CHART_EVENT = "chart_event"
    PERFORMANCE_EVENT = "performance_event"
    COLLABORATION = "collaboration"
    RELEASE_EVENT = "release_event"
    INTERVIEW = "interview"
    NEWS = "news"
    REVIEW = "review"
    COMMENTARY = "commentary"
    DISCOGRAPHY = "discography"
    GENERAL_MUSIC_KNOWLEDGE = "general_music_knowledge"
    UNKNOWN = "unknown"


class EntityTypeHint(StrEnum):
    ARTIST = "artist"
    SONG = "song"
    ALBUM = "album"
    GENRE = "genre"
    AWARD = "award"
    CHART = "chart"
    LABEL = "label"
    PERSON = "person"
    ORGANIZATION = "organization"
    PLACE = "place"
    EVENT = "event"
    OTHER = "other"
    UNKNOWN = "unknown"


class MentionSource(StrEnum):
    RULE = "rule"
    LLM = "llm"
    METADATA = "metadata"


class EnrichmentStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


ShortPhrase = Annotated[str, Field(min_length=1, max_length=200)]
WarningText = Annotated[str, Field(min_length=1, max_length=500)]


class EntityMention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=500)
    normalized_text: str = Field(min_length=1, max_length=500)
    type_hint: EntityTypeHint = EntityTypeHint.UNKNOWN
    confidence: float = Field(ge=0, le=1)
    source: MentionSource


class PrimarySubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=500)
    type_hint: EntityTypeHint = EntityTypeHint.UNKNOWN
    confidence: float = Field(ge=0, le=1)


class ContentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    language: str | None = Field(default=None, min_length=2, max_length=35)
    content_type: ContentType = ContentType.UNKNOWN


class SemanticMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    topics: list[ShortPhrase] = Field(default_factory=list, max_length=8)
    keywords: list[ShortPhrase] = Field(default_factory=list, max_length=12)


class TemporalMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int | None = Field(default=None, ge=1000, le=2999)
    date: Date | None = None
    start_year: int | None = Field(default=None, ge=1000, le=2999)
    end_year: int | None = Field(default=None, ge=1000, le=2999)
    raw_mentions: list[ShortPhrase] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_range(self) -> "TemporalMetadata":
        if (self.start_year is None) != (self.end_year is None):
            raise ValueError("temporal range requires both start_year and end_year")
        if (
            self.start_year is not None
            and self.end_year is not None
            and self.start_year > self.end_year
        ):
            raise ValueError("temporal start_year must not exceed end_year")
        return self


class EntityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_subject: PrimarySubject | None = None
    mentions: list[EntityMention] = Field(default_factory=list, max_length=100)


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str | None = Field(default=None, max_length=128)
    source_type: str | None = Field(default=None, max_length=200)
    source_name: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=2_000)
    source_published_at: str | None = Field(default=None, max_length=100)


class MetadataConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1, max_length=200)
    existing: JsonValue
    candidate: JsonValue
    resolution: str = Field(default="keep_existing", min_length=1, max_length=100)


class QualityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enrichment_status: EnrichmentStatus
    warnings: list[WarningText] = Field(default_factory=list, max_length=100)
    errors: list[WarningText] = Field(default_factory=list, max_length=20)
    metadata_conflicts: list[MetadataConflict] = Field(
        default_factory=list, max_length=100
    )


class ProvenanceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enrichment_version: str = Field(min_length=1, max_length=50)
    schema_version: str = Field(min_length=1, max_length=50)
    prompt_version: str = Field(min_length=1, max_length=50)
    input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ChunkEnrichment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: ContentMetadata = Field(default_factory=ContentMetadata)
    semantic: SemanticMetadata = Field(default_factory=SemanticMetadata)
    temporal: TemporalMetadata = Field(default_factory=TemporalMetadata)
    entities: EntityMetadata = Field(default_factory=EntityMetadata)
    source: SourceMetadata = Field(default_factory=SourceMetadata)
    quality: QualityMetadata
    provenance: ProvenanceMetadata


class EnrichedParentChunk(ParentChunk):
    enrichment: ChunkEnrichment


class EnrichedChildChunk(ChildChunk):
    enrichment: ChunkEnrichment


EnrichedChunk: TypeAlias = EnrichedParentChunk | EnrichedChildChunk
BaseChunk: TypeAlias = ParentChunk | ChildChunk


class SemanticEntityMention(BaseModel):
    """Provider-facing mention; normalization and source are assigned locally."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=500)
    type_hint: EntityTypeHint
    confidence: float = Field(ge=0, le=1)


class SemanticPrimarySubject(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=500)
    type_hint: EntityTypeHint
    confidence: float = Field(ge=0, le=1)


class SemanticEnrichmentOutput(BaseModel):
    """Single-call Structured Output schema for semantic-only metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_type: ContentType
    topics: list[ShortPhrase] = Field(max_length=8)
    keywords: list[ShortPhrase] = Field(max_length=12)
    entity_mentions: list[SemanticEntityMention] = Field(max_length=100)
    primary_subject: SemanticPrimarySubject | None


class RuleEnrichmentResult(BaseModel):
    """Normalized deterministic/inherited candidate before semantic merge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: ContentMetadata = Field(default_factory=ContentMetadata)
    semantic: SemanticMetadata = Field(default_factory=SemanticMetadata)
    temporal: TemporalMetadata = Field(default_factory=TemporalMetadata)
    entities: EntityMetadata = Field(default_factory=EntityMetadata)
    source: SourceMetadata = Field(default_factory=SourceMetadata)

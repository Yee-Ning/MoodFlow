"""Strict models for registry-backed music entity linking."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntityType(StrEnum):
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


class EntityLinkingStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class CanonicalEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str = Field(pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$")
    entity_type: EntityType
    canonical_name: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(ge=0, le=1)


class RegistryEntity(BaseModel):
    """Versioned registry record; ambiguous aliases require contextual evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str = Field(pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$")
    entity_type: EntityType
    canonical_name: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    ambiguous_aliases: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_id_prefix(self) -> "RegistryEntity":
        if not self.entity_id.startswith(f"{self.entity_type.value}:"):
            raise ValueError("entity_id prefix must match entity_type")
        return self


class EntityRegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    registry_version: str = Field(pattern=r"^v\d+$")
    entities: list[RegistryEntity]


class UnresolvedMention(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=500)
    normalized_text: str = Field(min_length=1, max_length=500)
    type_hint: str = Field(min_length=1, max_length=50)
    confidence: float = Field(ge=0, le=1)
    candidate_entity_ids: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=500)


class EntityLinkingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    linked_entities: list[CanonicalEntity] = Field(default_factory=list, max_length=200)
    unresolved_mentions: list[UnresolvedMention] = Field(
        default_factory=list, max_length=200
    )
    status: EntityLinkingStatus = EntityLinkingStatus.SUCCESS
    warnings: list[str] = Field(default_factory=list, max_length=100)
    errors: list[str] = Field(default_factory=list, max_length=20)


class EntityResolutionChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mention_text: str = Field(min_length=1, max_length=500)
    selected_entity_id: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_]*$"
    )
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)


class EntityDisambiguationOutput(BaseModel):
    """Provider-facing Structured Output contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resolutions: list[EntityResolutionChoice] = Field(max_length=100)

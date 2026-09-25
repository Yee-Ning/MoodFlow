"""Strict contracts for the evaluation-only WorldLine Gold Dataset V1."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)


SCHEMA_VERSION = "worldline-gold-v1"
DATASET_VERSION = "v1"
DATASET_KIND = "real_gold_fixture"


def content_sha256(value: str) -> str:
    """Return the hash used by source-ledger and evidence integrity checks."""

    return sha256(value.encode("utf-8")).hexdigest()


class GoldOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class GoldReviewStatus(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class IdentityStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class AmbiguityStatus(StrEnum):
    UNAMBIGUOUS = "unambiguous"
    NAME_COLLISION = "name_collision"
    PROVIDER_CONFLICT = "provider_conflict"


class RegistryScope(StrEnum):
    PRODUCTION = "production_registry"
    FIXTURE_ONLY = "fixture_only_registry"


class CatalogReferenceGold(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    entity_id: str = Field(min_length=1, max_length=200)
    entity_type: Literal["artist"]
    name: str = Field(min_length=1, max_length=300)

    @property
    def node_id(self) -> str:
        return f"{self.provider}:artist:{self.entity_id}"


class GoldArtist(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_name: str = Field(min_length=1, max_length=300)
    knowledge_entity_id: str = Field(
        pattern=r"^artist:[a-z0-9][a-z0-9_]*$"
    )
    aliases: list[str] = Field(default_factory=list, max_length=100)
    registry_scope: RegistryScope
    catalog_reference: CatalogReferenceGold | None = None

    @model_validator(mode="after")
    def validate_aliases(self) -> "GoldArtist":
        normalized = [value.strip().casefold() for value in self.aliases]
        if any(not value for value in normalized):
            raise ValueError("gold_artist_alias_blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("gold_artist_alias_duplicate")
        if self.canonical_name.strip().casefold() in normalized:
            raise ValueError("gold_artist_alias_repeats_canonical_name")
        return self


class GoldTargetGenre(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_id: str = Field(pattern=r"^genre:[a-z0-9][a-z0-9_]*$")
    canonical_name: str = Field(min_length=1, max_length=200)
    registry_version: str = Field(pattern=r"^v\d+$")


class GoldSemanticRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_entity_id: str = Field(
        pattern=r"^artist:[a-z0-9][a-z0-9_]*$"
    )
    target_genre: GoldTargetGenre
    relation_type: Literal["associated_with"]


class GoldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=500)
    source_title: str = Field(min_length=1, max_length=500)
    publisher: str = Field(min_length=1, max_length=300)
    source_url: AnyHttpUrl | None = None
    source_type: str = Field(min_length=1, max_length=100)
    language: str = Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]+)*$")
    exact_span: str = Field(min_length=1, max_length=4_000)
    evidence_text: str = Field(min_length=1, max_length=20_000)
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_source_id: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_span(self) -> "GoldEvidence":
        if self.span_end > len(self.evidence_text):
            raise ValueError("gold_evidence_span_out_of_bounds")
        if self.evidence_text[self.span_start : self.span_end] != self.exact_span:
            raise ValueError("gold_evidence_exact_span_mismatch")
        return self


class GoldExpected(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: GoldOutcome
    reason: str = Field(min_length=1, max_length=500)
    runtime_relation: Literal["associated_with"]
    runtime_rejection_reason: str | None = Field(default=None, max_length=500)


class GoldIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_status: IdentityStatus
    catalog_status: IdentityStatus
    ambiguity_status: AmbiguityStatus
    expected_catalog_node_id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9_-]*:artist:.+$",
    )
    identity_evidence_source_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
    )


class GoldReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: GoldReviewStatus
    method: str = Field(min_length=1, max_length=200)
    relation_reviewer: str | None = Field(default=None, max_length=200)
    identity_reviewer: str | None = Field(default=None, max_length=200)
    adjudicator: str | None = Field(default=None, max_length=200)
    reviewed_at: datetime | None = None
    notes: str = Field(default="", max_length=4_000)

    @model_validator(mode="after")
    def validate_approval(self) -> "GoldReview":
        if self.status is GoldReviewStatus.APPROVED:
            if not self.relation_reviewer or not self.identity_reviewer:
                raise ValueError("gold_review_approved_reviewer_missing")
            if self.reviewed_at is None:
                raise ValueError("gold_review_approved_timestamp_missing")
        return self


class GoldCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    dataset_version: Literal[DATASET_VERSION]
    dataset_kind: Literal[DATASET_KIND]
    case_id: str = Field(pattern=r"^wl-gold-v1-[a-z0-9][a-z0-9-]*$")
    region: Literal["cjk_chinese", "western", "japanese", "korean"]
    tags: list[str] = Field(default_factory=list, max_length=20)
    artist: GoldArtist
    semantic_relation: GoldSemanticRelation
    evidence: list[GoldEvidence] = Field(default_factory=list, max_length=20)
    expected: GoldExpected
    identity: GoldIdentity
    review: GoldReview

    @model_validator(mode="after")
    def validate_gold_contract(self) -> "GoldCase":
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("gold_case_tags_duplicate")
        if self.semantic_relation.source_entity_id != self.artist.knowledge_entity_id:
            raise ValueError("gold_case_relation_source_mismatch")

        registry = GenreVocabularyRegistry.load()
        genre = registry.get(self.semantic_relation.target_genre.canonical_id)
        if genre is None:
            raise ValueError("gold_case_genre_not_registered")
        if genre.canonical_name != self.semantic_relation.target_genre.canonical_name:
            raise ValueError("gold_case_genre_name_mismatch")
        if registry.version != self.semantic_relation.target_genre.registry_version:
            raise ValueError("gold_case_genre_registry_version_mismatch")

        if self.expected.outcome is GoldOutcome.ACCEPTED:
            if not self.evidence:
                raise ValueError("gold_case_accepted_evidence_required")
            if self.artist.catalog_reference is None:
                raise ValueError("gold_case_accepted_catalog_reference_required")
            if self.identity.knowledge_status is not IdentityStatus.RESOLVED:
                raise ValueError("gold_case_accepted_knowledge_identity_unresolved")
            if self.identity.catalog_status is not IdentityStatus.RESOLVED:
                raise ValueError("gold_case_accepted_catalog_identity_unresolved")
            if self.identity.ambiguity_status is not AmbiguityStatus.UNAMBIGUOUS:
                raise ValueError("gold_case_accepted_identity_ambiguous")
            if (
                self.identity.expected_catalog_node_id
                != self.artist.catalog_reference.node_id
            ):
                raise ValueError("gold_case_accepted_catalog_node_mismatch")
        return self


class GoldSourceLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=500)
    fixture_source_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    publisher: str = Field(min_length=1, max_length=300)
    url: AnyHttpUrl | None = None
    provenance: str | None = Field(default=None, min_length=1, max_length=2_000)
    language: str = Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]+)*$")
    source_type: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=20_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_integrity(self) -> "GoldSourceLedgerEntry":
        if self.url is None and self.provenance is None:
            raise ValueError("gold_source_location_required")
        if content_sha256(self.content) != self.content_hash:
            raise ValueError("gold_source_content_hash_mismatch")
        return self

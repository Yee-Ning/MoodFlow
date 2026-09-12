"""Final KnowledgeEnrichedChunk contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from app.rag.enrichment.models import EnrichedChildChunk, EnrichedParentChunk

from .entity_linking.models import CanonicalEntity, UnresolvedMention
from .verification.models import VerifiedClaim, VerificationStatus


class KnowledgeStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


class KnowledgeEntities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    linked_entities: list[CanonicalEntity] = Field(default_factory=list, max_length=200)
    unresolved_mentions: list[UnresolvedMention] = Field(
        default_factory=list, max_length=200
    )


class KnowledgeStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)
    claim_count: int = Field(ge=0)
    supported_claim_count: int = Field(ge=0)
    partially_supported_claim_count: int = Field(ge=0)
    contradicted_claim_count: int = Field(ge=0)
    insufficient_evidence_claim_count: int = Field(ge=0)

    @classmethod
    def build(
        cls,
        entities: KnowledgeEntities,
        claims: list[VerifiedClaim],
    ) -> "KnowledgeStats":
        counts = {status: 0 for status in VerificationStatus}
        for item in claims:
            counts[item.verification.status] += 1
        return cls(
            entity_count=len(entities.linked_entities),
            unresolved_count=len(entities.unresolved_mentions),
            claim_count=len(claims),
            supported_claim_count=counts[VerificationStatus.SUPPORTED],
            partially_supported_claim_count=counts[
                VerificationStatus.PARTIALLY_SUPPORTED
            ],
            contradicted_claim_count=counts[VerificationStatus.CONTRADICTED],
            insufficient_evidence_claim_count=counts[
                VerificationStatus.INSUFFICIENT_EVIDENCE
            ],
        )


class KnowledgeQuality(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: KnowledgeStatus
    warnings: list[str] = Field(default_factory=list, max_length=200)
    errors: list[str] = Field(default_factory=list, max_length=50)


class KnowledgeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_linking_version: str = Field(pattern=r"^v\d+$")
    claim_extraction_version: str = Field(pattern=r"^v\d+$")
    claim_verification_version: str = Field(pattern=r"^v\d+$")
    registry_version: str = Field(pattern=r"^v\d+$")
    prompt_version: str = Field(pattern=r"^v\d+$")
    knowledge_input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class KnowledgeEnrichment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entities: KnowledgeEntities
    claims: list[VerifiedClaim] = Field(default_factory=list, max_length=200)
    stats: KnowledgeStats
    quality: KnowledgeQuality
    provenance: KnowledgeProvenance


class KnowledgeEnrichedParentChunk(EnrichedParentChunk):
    knowledge: KnowledgeEnrichment


class KnowledgeEnrichedChildChunk(EnrichedChildChunk):
    knowledge: KnowledgeEnrichment


KnowledgeEnrichedChunk: TypeAlias = (
    KnowledgeEnrichedParentChunk | KnowledgeEnrichedChildChunk
)

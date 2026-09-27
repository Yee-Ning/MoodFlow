"""Source-local claim verification contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer

from ..claims.models import Claim


class VerificationStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ClaimVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: VerificationStatus
    confidence: float = Field(ge=0, le=1)
    evidence_span: str | None = Field(default=None, min_length=1, max_length=5_000)
    reason: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def supported_requires_evidence(self) -> "ClaimVerification":
        if self.status in {
            VerificationStatus.SUPPORTED,
            VerificationStatus.PARTIALLY_SUPPORTED,
            VerificationStatus.CONTRADICTED,
        } and not self.evidence_span:
            raise ValueError("verification status requires evidence")
        return self


class VerifiedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim: Claim
    verification: ClaimVerification
    # Internal provenance envelope; provider-facing Claim/ClaimCandidate schemas
    # and predicate vocabulary stay unchanged. Empty legacy records serialize
    # identically, preserving existing release hashes.
    grounding: "ChildClaimGrounding | None" = None

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        result = handler(self)
        if self.grounding is None:
            result.pop("grounding", None)
        return result


class ChildClaimGrounding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    derived_from: str
    source_id: str
    document_id: str
    parent_chunk_id: str
    child_chunk_id: str
    parent_start: int = Field(ge=0)
    parent_end: int = Field(gt=0)
    child_start: int = Field(ge=0)
    child_end: int = Field(gt=0)
    parent_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    child_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


VerifiedClaim.model_rebuild()

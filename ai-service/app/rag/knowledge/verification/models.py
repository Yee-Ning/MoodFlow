"""Source-local claim verification contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

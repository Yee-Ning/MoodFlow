"""Reviewed sidecar assignments for normalized documents missing document_type."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import NormalizedCorpusError


class DocumentTypeReviewStatus(StrEnum):
    APPROVED = "APPROVED"
    PENDING = "PENDING"


class DocumentTypeOrigin(StrEnum):
    NORMALIZED = "normalized"
    SIDECAR = "sidecar"


class DocumentTypeAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=128)
    document_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")
    review_status: DocumentTypeReviewStatus
    reason: str = Field(min_length=1, max_length=1_000)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    reviewed_by: str | None = Field(default=None, min_length=1, max_length=200)
    reviewed_at: date | None = None

    @model_validator(mode="after")
    def validate_review_evidence(self) -> "DocumentTypeAssignment":
        if self.review_status is DocumentTypeReviewStatus.APPROVED:
            if not self.evidence:
                raise ValueError("approved document type requires evidence")
            if self.reviewed_by is None or self.reviewed_at is None:
                raise ValueError("approved document type requires review attribution")
        return self


class DocumentTypeSidecar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sidecar_version: str = Field(pattern=r"^v\d+\.\d+$")
    assignment_policy: str = Field(min_length=1, max_length=1_000)
    assignments: list[DocumentTypeAssignment] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_assignments(self) -> "DocumentTypeSidecar":
        source_ids = [assignment.source_id for assignment in self.assignments]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("document type sidecar source_ids must be unique")
        return self

    def approved_assignments(self) -> dict[str, DocumentTypeAssignment]:
        return {
            assignment.source_id: assignment
            for assignment in self.assignments
            if assignment.review_status is DocumentTypeReviewStatus.APPROVED
        }


class DocumentTypeResolver:
    """Fill missing types from approved entries without mutating normalized data."""

    def __init__(
        self,
        sidecar: DocumentTypeSidecar | None,
        *,
        sidecar_checksum: str | None = None,
    ) -> None:
        self.sidecar = sidecar
        self.sidecar_checksum = sidecar_checksum
        self.assignments = sidecar.approved_assignments() if sidecar else {}
        if self.assignments and sidecar_checksum is None:
            raise NormalizedCorpusError("document_type_sidecar_checksum_required")

    def resolve(
        self, source_id: str, normalized_document_type: str | None
    ) -> tuple[str | None, DocumentTypeOrigin | None, str | None, str | None]:
        assignment = self.assignments.get(source_id)
        if normalized_document_type is not None:
            if (
                assignment is not None
                and assignment.document_type != normalized_document_type
            ):
                raise NormalizedCorpusError(
                    f"document_type_sidecar_conflict:{source_id}"
                )
            return (
                normalized_document_type,
                DocumentTypeOrigin.NORMALIZED,
                None,
                None,
            )
        if assignment is None:
            return None, None, None, None
        assert self.sidecar is not None
        return (
            assignment.document_type,
            DocumentTypeOrigin.SIDECAR,
            self.sidecar.sidecar_version,
            self.sidecar_checksum,
        )

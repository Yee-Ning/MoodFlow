"""Strict Clean-stage documents, audits, and execution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class CleanedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, max_length=300)
    source_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=500)
    source_type: str | None = Field(default=None, max_length=200)
    document_type: str | None = Field(default=None, max_length=200)
    document_type_origin: str | None = Field(default=None, max_length=50)
    document_type_sidecar_version: str | None = Field(default=None, max_length=50)
    document_type_sidecar_checksum: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    title: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=300)
    published_at: date | None = None
    language: str = Field(min_length=2, max_length=35)
    original_file_type: str = Field(min_length=1, max_length=50)
    corpus_version: str = Field(min_length=1, max_length=100)
    cleaned_text: str
    clean_version: str = Field(pattern=r"^v\d+\.\d+$")
    rule_registry_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    rules_applied: list[str]
    warnings: list[str]
    needs_review: bool
    normalized_path: str = Field(min_length=1, max_length=1_000)
    normalized_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CleanDocumentAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    source_id: str
    source: str
    source_type: str | None = None
    document_type: str | None = None
    document_type_origin: str | None = None
    document_type_sidecar_version: str | None = None
    document_type_sidecar_checksum: str | None = None
    before_length: int = Field(ge=0)
    after_length: int = Field(ge=0)
    removed_chars: int = Field(ge=0)
    removed_ratio: float = Field(ge=0)
    changed: bool
    applied_rules: list[str]
    rule_hits: dict[str, int]
    needs_review: bool
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class CleanExecutionResult:
    document: CleanedDocument
    audit: CleanDocumentAudit
    before_text: str
    after_text: str

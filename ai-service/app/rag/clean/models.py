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


class LineDiffStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    before_lines: int = Field(ge=0)
    after_lines: int = Field(ge=0)
    deleted_lines: int = Field(ge=0)
    added_lines: int = Field(ge=0)
    unchanged_lines: int = Field(ge=0)
    diff_ratio: float = Field(ge=0.0, le=1.0)


class BlockLanguageAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_index: int = Field(ge=0)
    detected_language: str = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)
    char_count: int = Field(ge=0)
    is_english: bool = False


class LanguageAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_language: str = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)
    has_english_blocks: bool = False
    english_block_count: int = Field(default=0, ge=0)
    policy_action: str = Field(
        default="retained_unfiltered",
        description="Policy for English blocks (default retained, pending West decision)",
    )
    block_annotations: list[BlockLanguageAnnotation] = Field(default_factory=list)


class StageMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_index: int = Field(ge=1, le=6)
    stage_name: str = Field(min_length=1, max_length=50)
    before_chars: int = Field(ge=0)
    after_chars: int = Field(ge=0)
    removed_chars: int = Field(ge=0)
    removed_blocks: int = Field(default=0, ge=0)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class CleanDocumentReport(BaseModel):
    """Independent per-document comprehensive clean report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    report_version: str = "v0.2"
    document_id: str
    source_id: str
    source: str
    source_type: str | None = None
    document_type: str | None = None
    clean_version: str
    rule_registry_checksum: str
    before_length: int = Field(ge=0)
    after_length: int = Field(ge=0)
    removed_chars: int = Field(ge=0)
    removed_ratio: float = Field(ge=0.0)
    changed: bool
    rules_applied: list[str]
    rule_hits: dict[str, int]
    warnings: list[str]
    needs_review: bool
    line_diff: LineDiffStats | None = None
    stages: list[StageMetrics] = Field(default_factory=list)
    language: LanguageAnnotation | None = None
    sample_diff: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CleanExecutionResult:
    document: CleanedDocument
    audit: CleanDocumentAudit
    before_text: str
    after_text: str
    line_diff: LineDiffStats | None = None
    stages: tuple[StageMetrics, ...] = ()
    language_annotation: LanguageAnnotation | None = None
    single_report: CleanDocumentReport | None = None

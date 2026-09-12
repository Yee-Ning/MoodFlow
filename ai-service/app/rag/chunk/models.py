"""Strict persisted contracts for Chunk V1."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ChunkType(StrEnum):
    NARRATIVE = "narrative"
    TABLE = "table"


class TableHeaderState(StrEnum):
    EXPLICIT = "explicit"
    INFERRED = "inferred"
    NONE = "none"


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class TableRowFragment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_row_index: int = Field(ge=0)
    fragment_index: int = Field(ge=0)
    fragment_count: int = Field(ge=1)
    content: str = Field(min_length=1)
    owns_source_row: bool = False


class TableChunkData(BaseModel):
    """Lossless table row payload carried beside the retrieval representation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    table_id: str = Field(min_length=1, max_length=300)
    header_state: TableHeaderState
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    row_fragments: list[TableRowFragment] = Field(default_factory=list)
    row_start: int = Field(ge=0)
    row_end: int = Field(ge=0)
    source_row_count: int = Field(ge=0)


class ParentChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(min_length=1, max_length=300)
    chunk_id: str = Field(min_length=1, max_length=100)
    parent_id: None = None
    chunk_index: int = Field(ge=0)
    chunk_type: ChunkType
    section_path: list[str] = Field(default_factory=list)
    breadcrumb: str = Field(min_length=1)
    content: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    table_data: TableChunkData | None = None


class ChildChunk(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(min_length=1, max_length=300)
    chunk_id: str = Field(min_length=1, max_length=100)
    parent_id: str = Field(min_length=1, max_length=100)
    chunk_index: int = Field(ge=0)
    chunk_type: ChunkType
    section_path: list[str] = Field(default_factory=list)
    breadcrumb: str = Field(min_length=1)
    content: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    retrieval_content: str = Field(min_length=1)
    previous_chunk_id: str | None = Field(default=None, max_length=100)
    next_chunk_id: str | None = Field(default=None, max_length=100)
    table_data: TableChunkData | None = None


class ChunkValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=100)
    severity: ValidationSeverity
    message: str = Field(min_length=1, max_length=1_000)
    chunk_id: str | None = Field(default=None, max_length=100)
    table_id: str | None = Field(default=None, max_length=300)


class ChunkValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    issues: list[ChunkValidationIssue] = Field(default_factory=list)
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)


class ChunkStats(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_count: int = Field(ge=0)
    child_count: int = Field(ge=0)
    narrative_parent_count: int = Field(ge=0)
    narrative_child_count: int = Field(ge=0)
    table_parent_count: int = Field(ge=0)
    table_child_count: int = Field(ge=0)
    parent_tokens: int = Field(ge=0)
    child_tokens: int = Field(ge=0)
    min_child_tokens: int = Field(ge=0)
    max_child_tokens: int = Field(ge=0)
    average_child_tokens: float = Field(ge=0)
    source_table_rows: int = Field(ge=0)
    emitted_table_rows: int = Field(ge=0)
    validation_errors: int = Field(ge=0)
    validation_warnings: int = Field(ge=0)


class ChunkedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, max_length=300)
    source_id: str = Field(min_length=1, max_length=128)
    chunk_version: str = Field(pattern=r"^v\d+$")
    config_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    token_counter: str = Field(min_length=1, max_length=500)
    parents: list[ParentChunk] = Field(default_factory=list)
    children: list[ChildChunk] = Field(default_factory=list)
    validation: ChunkValidationReport
    stats: ChunkStats

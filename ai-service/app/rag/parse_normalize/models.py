"""Parser-independent contracts for Parse + Normalize V1."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from app.rag.source_ingestion.models import CandidateSource, RawSnapshotRecord


NORMALIZATION_VERSION = "1.1.0"


class DetectedFileType(StrEnum):
    HTML = "html"
    MARKDOWN = "markdown"
    TXT = "txt"
    JSON = "json"
    PDF = "pdf"
    UNKNOWN = "unknown"


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    QUOTE = "quote"
    CODE = "code"
    TABLE = "table"
    IMAGE = "image"
    OTHER = "other"


class ParseStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    FALLBACK_USED = "fallback_used"
    MANUAL_REVIEW = "manual_review"
    FAILED = "failed"


class ParserAttemptStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class DocumentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str = Field(min_length=1, max_length=300)
    block_type: BlockType
    text: str
    level: int | None = Field(default=None, ge=1, le=6)
    order_index: int = Field(ge=0)
    parent_block_id: str | None = Field(default=None, max_length=300)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def heading_requires_level(self) -> "DocumentBlock":
        if self.block_type is BlockType.HEADING and self.level is None:
            raise ValueError("heading blocks require a level")
        return self


class DocumentTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table_id: str = Field(min_length=1, max_length=300)
    page_number: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    markdown: str
    rows: list[list[str]] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class DocumentImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image_id: str = Field(min_length=1, max_length=300)
    page_number: int | None = Field(default=None, ge=1)
    bbox: tuple[float, float, float, float] | None = None
    path: str = Field(min_length=1, max_length=1_000)
    content_type: str = Field(min_length=1, max_length=200)
    caption: str | None = Field(default=None, max_length=2_000)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class OutputArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str = Field(min_length=1, max_length=1_000)
    content: bytes
    content_type: str = Field(min_length=1, max_length=200)


class ParseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot: RawSnapshotRecord
    raw_bytes: bytes
    candidate: CandidateSource | None = None
    force_reparse: bool = False


class ParserResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = None
    author: str | None = None
    published_at: date | None = None
    language: str = Field(default="und", min_length=2, max_length=35)
    blocks: list[DocumentBlock]
    tables: list[DocumentTable] = Field(default_factory=list)
    images: list[DocumentImage] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ParseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text_length: int = Field(ge=0)
    heading_count: int = Field(ge=0)
    paragraph_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    empty_content: bool
    garbled_char_ratio: float = Field(ge=0, le=1)
    duplicated_line_ratio: float = Field(ge=0, le=1)
    chars_per_page: float = Field(default=0, ge=0)
    empty_page_ratio: float = Field(default=0, ge=0, le=1)
    replacement_char_ratio: float = Field(default=0, ge=0, le=1)
    suspicious_line_break_ratio: float = Field(default=0, ge=0, le=1)
    image_count: int = Field(default=0, ge=0)
    parser_error_count: int = Field(default=0, ge=0)


class ParserAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parser: str = Field(min_length=1, max_length=100)
    parser_version: str = Field(min_length=1, max_length=50)
    status: ParserAttemptStatus
    quality_score: float | None = Field(default=None, ge=0, le=1)
    error: str | None = Field(default=None, max_length=500)
    elapsed_ms: float = Field(ge=0)
    fallback_reason: str | None = Field(default=None, max_length=500)
    ocr_used: bool = False


class ParseReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=128)
    parser_name: str = Field(min_length=1, max_length=100)
    selected_parser: str | None = Field(default=None, max_length=100)
    parser_attempts: list[ParserAttempt] = Field(default_factory=list)
    quality_score: float = Field(ge=0, le=1)
    status: ParseStatus
    warnings: list[str] = Field(default_factory=list)
    metrics: ParseMetrics
    elapsed_ms: float = Field(ge=0)


class ParserExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: ParserResult | None
    selected_parser: str = Field(min_length=1, max_length=100)
    selected_parser_version: str = Field(min_length=1, max_length=50)
    report: ParseReport | None = None
    artifacts: list[OutputArtifact] = Field(default_factory=list)


class NormalizedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, max_length=300)
    source_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=300)
    published_at: date | None = None
    language: str = Field(min_length=2, max_length=35)
    original_file_type: DetectedFileType
    raw_path: str = Field(min_length=1, max_length=1_000)
    parser_name: str = Field(min_length=1, max_length=100)
    parser_version: str = Field(min_length=1, max_length=50)
    normalization_version: str = Field(min_length=1, max_length=50)
    raw_content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    markdown: str
    blocks: list[DocumentBlock]
    tables: list[DocumentTable] = Field(default_factory=list)
    images: list[DocumentImage] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    parse_quality_score: float = Field(ge=0, le=1)
    corpus_version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_block_sequence(self) -> "NormalizedDocument":
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("block IDs must be unique")
        order_indexes = [block.order_index for block in self.blocks]
        if order_indexes != list(range(len(self.blocks))):
            raise ValueError("block order indexes must be contiguous and start at zero")
        table_ids = [table.table_id for table in self.tables]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("table IDs must be unique")
        image_ids = [image.image_id for image in self.images]
        if len(image_ids) != len(set(image_ids)):
            raise ValueError("image IDs must be unique")
        return self

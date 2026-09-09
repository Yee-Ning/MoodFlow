"""PDF-only profiles and routing identities."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class PDFProfile(StrEnum):
    SIMPLE_TEXT = "SIMPLE_TEXT"
    COMPLEX_LAYOUT = "COMPLEX_LAYOUT"
    SCANNED = "SCANNED"
    UNKNOWN = "UNKNOWN"


class PDFParserMode(StrEnum):
    AUTO = "auto"
    PYMUPDF = "pymupdf"
    MINERU = "mineru"
    DOCLING = "docling"


class PDFBackend(StrEnum):
    PYMUPDF4LLM = "pymupdf4llm"
    MINERU = "mineru"
    DOCLING = "docling"


class PDFPageInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_number: int = Field(ge=1)
    text_chars: int = Field(ge=0)
    empty_text: bool
    image_count: int = Field(ge=0)
    suspected_multi_column: bool
    table_hint: bool


class PDFInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: PDFProfile
    page_count: int = Field(ge=1)
    native_text_available: bool
    extracted_text_chars: int = Field(ge=0)
    average_chars_per_page: float = Field(ge=0)
    empty_text_page_ratio: float = Field(ge=0, le=1)
    image_count: int = Field(ge=0)
    suspected_scan: bool
    suspected_multi_column: bool
    table_hint: bool
    document_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    pages: list[PDFPageInspection]

"""Strict artifacts for offline index preparation and build reporting."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class IndexFailureStage(StrEnum):
    PROJECTION = "projection"
    EMBEDDING = "embedding"
    UPSERT = "upsert"


class PreparedIndexDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    point_id: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    chunk_id: str = Field(min_length=1, max_length=100)
    parent_id: str = Field(min_length=1, max_length=100)
    doc_id: str = Field(min_length=1, max_length=300)
    dense_text: str = Field(min_length=1)
    bm25_text: str = Field(min_length=1)
    input_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    payload: dict[str, JsonValue]


class IndexPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document: PreparedIndexDocument
    dense_vector: list[float] = Field(min_length=1)


class IndexFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1, max_length=100)
    point_id: str | None = None
    stage: IndexFailureStage
    error: str = Field(min_length=1, max_length=1_000)


class IndexBuildManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = Field(pattern=r"^(completed|partial)$")
    created_at: datetime
    indexing_version: str
    schema_version: str
    projection_version: str
    source_path: str
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    collection_name: str
    dense_vector_name: str
    dense_model: str
    dense_model_revision: str
    dense_dimension: int = Field(ge=1)
    dense_distance: str
    dense_normalized: bool
    bm25_vector_name: str
    bm25_model: str
    bm25_options: dict[str, JsonValue]
    hnsw_m: int = Field(ge=1)
    hnsw_ef_construct: int = Field(ge=1)
    parent_count: int = Field(ge=0)
    child_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    indexed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    pruned_count: int = Field(ge=0)
    collection_point_count: int | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=50)

"""Strict artifacts for offline index preparation and build reporting."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class IndexFailureStage(StrEnum):
    PROJECTION = "projection"
    EMBEDDING = "embedding"
    UPSERT = "upsert"
    VERIFICATION = "verification"


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

    manifest_version: str = Field(default="v3", pattern=r"^v\d+$")
    status: str = Field(pattern=r"^(completed|partial)$")
    ready_for_retrieval: bool
    created_at: datetime
    indexing_version: str
    schema_version: str
    projection_version: str
    source_path: str
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_release_id: str
    source_release_manifest_path: str
    source_release_manifest_fingerprint: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    config_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    projection_path: str
    projection_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    collection_name: str
    dense_vector_name: str
    dense_model: str
    dense_model_revision: str
    dense_dimension: int = Field(ge=1)
    dense_distance: str
    dense_normalized: bool
    # Historical manifests parse, but online binding rejects unknown identity.
    dense_max_length: int | None = Field(default=None, ge=32, le=32_768)
    dense_pooling: str | None = None
    bm25_vector_name: str
    bm25_model: str
    bm25_options: dict[str, JsonValue]
    # None preserves parsing of historical artifacts, never implies IDF enabled.
    bm25_idf_modifier: bool | None = None
    hnsw_m: int = Field(ge=1)
    hnsw_ef_construct: int = Field(ge=1)
    parent_count: int = Field(ge=0)
    child_count: int = Field(ge=0)
    eligible_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    projected_count: int = Field(ge=0)
    excluded_count: int = Field(ge=0)
    limited_to: int | None = Field(default=None, ge=1)
    indexed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    embedding_cache_path: str | None = None
    embedding_cache_hit_count: int = Field(default=0, ge=0)
    embedding_computed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(ge=0)
    pruned_count: int = Field(ge=0)
    collection_point_count: int | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def ready_requires_complete_full_build(self) -> "IndexBuildManifest":
        if self.ready_for_retrieval and (
            self.status != "completed"
            or self.limited_to is not None
            or self.failed_count
            or self.collection_point_count != self.projected_count
        ):
            raise ValueError("ready index manifest must describe a complete full build")
        return self

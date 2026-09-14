"""Strict contracts for the online retrieval candidate stage."""

from __future__ import annotations

from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    model_validator,
)

from app.rag.query_understanding.models import (
    IndexProfile,
    QueryVariantKind,
    SearchChannel,
)


class RetrievalStatus(StrEnum):
    SUCCESS = "success"
    DEGRADED = "degraded"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


class ChannelEvidence(BaseModel):
    """One retrieval leg that contributed to a fused candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1, max_length=128)
    subquery_id: str = Field(min_length=1, max_length=128)
    retrieval_leg: str = Field(min_length=1, max_length=160)
    variant_index: int = Field(ge=0, le=9)
    variant_kind: QueryVariantKind
    channel: SearchChannel
    rank: int = Field(ge=1, le=1_000)
    raw_score: float
    weight: float = Field(gt=0, le=10)


class DeferredParentContext(BaseModel):
    """Parent payload carried through retrieval but expanded only after reranking."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=20_000)
    token_count: int = Field(ge=0)
    breadcrumb: str = Field(default="", max_length=2_000)


class RetrievalCandidate(BaseModel):
    """One Parent candidate represented by its highest-RRF matched Child."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point_id: str = Field(min_length=1, max_length=128)
    parent_id: str = Field(min_length=1, max_length=128)
    doc_id: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    primary_child_id: str = Field(min_length=1, max_length=128)
    primary_child_content: str = Field(min_length=1, max_length=8_000)
    primary_child_breadcrumb: str = Field(default="", max_length=2_000)
    primary_child_token_count: int = Field(ge=0)
    parent_context: DeferredParentContext
    source_id: str = Field(min_length=1, max_length=300)
    source_name: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl | None = None
    entity_ids: list[str] = Field(default_factory=list, max_length=50)
    supported_claims: list[dict[str, JsonValue]] = Field(
        default_factory=list,
        max_length=100,
    )
    fusion_score: float = Field(ge=0)
    channel_evidence: list[ChannelEvidence] = Field(min_length=1, max_length=20)
    matched_child_ids: list[str] = Field(min_length=1, max_length=20)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_candidate(self) -> "RetrievalCandidate":
        if self.primary_child_id not in self.matched_child_ids:
            raise ValueError("primary chunk must be one of the matched children")
        if not any(
            item.chunk_id == self.primary_child_id for item in self.channel_evidence
        ):
            raise ValueError("primary chunk must have retrieval evidence")
        if self.parent_context.chunk_id != self.parent_id:
            raise ValueError("deferred parent context must match parent id")
        if len(self.matched_child_ids) != len(set(self.matched_child_ids)):
            raise ValueError("matched child ids must be unique")
        evidence_keys = [
            (item.chunk_id, item.variant_index, item.channel, item.rank)
            for item in self.channel_evidence
        ]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("channel evidence must be unique")
        return self


class RetrievalTimings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    embedding_ms: float = Field(default=0, ge=0)
    qdrant_ms: float = Field(default=0, ge=0)
    processing_ms: float = Field(default=0, ge=0)
    total_ms: float = Field(default=0, ge=0)


class RetrievalResult(BaseModel):
    """Retriever-neutral output that deliberately stops before reranking."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: RetrievalStatus
    plan_id: str = Field(pattern=r"^query-plan:[0-9a-f]{24}$")
    index_profile: IndexProfile
    candidates: list[RetrievalCandidate] = Field(default_factory=list, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    failed_channels: list[SearchChannel] = Field(default_factory=list, max_length=10)
    timings: RetrievalTimings = Field(default_factory=RetrievalTimings)

    @model_validator(mode="after")
    def validate_result(self) -> "RetrievalResult":
        if self.status is RetrievalStatus.SUCCESS and not self.candidates:
            raise ValueError("successful retrieval requires candidates")
        if self.status in {RetrievalStatus.EMPTY, RetrievalStatus.UNAVAILABLE}:
            if self.candidates:
                raise ValueError("empty or unavailable retrieval cannot have candidates")
        parent_ids = [item.parent_id for item in self.candidates]
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("retrieval candidates must be unique by parent")
        if len(self.failed_channels) != len(set(self.failed_channels)):
            raise ValueError("failed channels must be unique")
        return self

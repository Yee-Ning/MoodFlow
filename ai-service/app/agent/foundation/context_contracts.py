"""Observable Pack/call projections, never sources for a later selection."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.agent.foundation.context_pointers import EventPointer


class ProjectionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManifestEvent(ProjectionContract):
    item_id: str
    stage: Literal["compressed", "dropped", "deduplicated"]
    reason: str
    token_before: int = Field(ge=0)
    token_after: int = Field(ge=0)
    task_id: str | None = None
    session_id: str | None = None
    request_id: str | None = None


class RagChunkAudit(ProjectionContract):
    item_id: str
    doc_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    source_url: str | None = None
    title: str
    chunk_id: str = Field(min_length=1)
    fusion_score: float = Field(ge=0, allow_inf_nan=False)
    rerank_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    score: float = Field(ge=0, allow_inf_nan=False)
    score_basis: Literal["rerank", "rrf_fallback"]
    candidate_tokens: int = Field(ge=0)
    final_tokens: int = Field(ge=0)
    included: bool
    reasons: list[str]


class RagModelUsage(ProjectionContract):
    call_id: str = Field(min_length=1)
    stage: Literal["embedding", "rerank"]
    model: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: Literal[0]
    total_tokens: int = Field(ge=0)
    estimated: bool
    usage_basis: str = Field(min_length=1)
    status: Literal["completed", "unknown"]
    budget_scope: Literal["l2+l3", "unscoped"]
    usage_ref: EventPointer | None = None

    @model_validator(mode="after")
    def coherent_usage(self):
        if self.total_tokens != self.input_tokens:
            raise ValueError("non-generative retrieval usage must equal input tokens")
        return self


class RagCollectionAudit(ProjectionContract):
    mode: Literal["empty", "shadow", "hybrid"]
    rerank_enabled: Literal[True]
    retrieval_status: str
    rerank_status: str
    candidate_limit: int = Field(ge=0)
    rejected_without_provenance: int = Field(ge=0)
    warnings: list[str]
    plan_id: str | None = None
    index_profile: dict[str, str] | None = None
    rerank_input_count: int | None = Field(default=None, ge=0)
    rerank_fallback_used: bool | None = None


class SourceManifest(ProjectionContract):
    candidate_tokens: int = Field(ge=0)
    final_tokens: int = Field(ge=0)
    events: list[ManifestEvent]
    dedup_dropped: list[str]
    chunks: list[RagChunkAudit] = Field(default_factory=list)
    model_usage: list[RagModelUsage] = Field(default_factory=list)
    collection: RagCollectionAudit | None = None


class PackManifest(ProjectionContract):
    tokens: int = Field(ge=0)
    budget: int = Field(gt=0)
    sources: dict[str, SourceManifest]


class BudgetEvent(ProjectionContract):
    trace_id: str
    task_id: str
    agent: Literal["general_chat", "curator", "explorer"]
    level: Literal["l2", "l3"]
    stage: Literal["truncate_history", "summarize_history", "stop_sources", "honest_degradation",
                   "task_degradation", "refuse"]
    agent_tokens: int = Field(ge=0)
    task_tokens: int = Field(ge=0)


class CallManifest(ProjectionContract):
    # A standalone SDK boundary has no Pack; do not fabricate a second Pack.
    tokens: int | None = Field(default=None, ge=0)
    budget: int | None = Field(default=None, gt=0)
    sources: dict[str, SourceManifest] | None = None
    call_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    agent: Literal["general_chat", "curator", "explorer", "jev"]
    model: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated: bool
    usage_basis: str = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    output_limit_exceeded: bool
    status: Literal["completed", "unknown"]
    budget_events: list[BudgetEvent]
    usage_ref: EventPointer | None = None

    @model_validator(mode="after")
    def coherent_projection(self):
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("call manifest split must equal total")
        if self.output_limit_exceeded != (self.output_tokens > self.max_output_tokens):
            raise ValueError("call manifest output limit disagrees")
        if self.task_id != self.trace_id:
            raise ValueError("call manifest task scope must be the original run")
        present = [value is not None for value in (self.tokens, self.budget, self.sources)]
        if any(present) and not all(present):
            raise ValueError("call manifest requires the complete Pack projection")
        return self

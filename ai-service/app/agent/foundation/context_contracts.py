"""Observable Pack/call projections, never sources for a later selection."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from app.agent.foundation.context_pointers import EventPointer

AgentOwner = Literal["general_chat", "curator", "explorer"]
AgentPhase = Literal["answer", "read_observation", "song_analysis", "tool_step", "tool_observation",
                     "query_analysis", "narrative", "branches", "relay"]
BudgetStage = Literal["truncate_history", "summarize_history", "stop_sources", "honest_degradation",
                      "task_degradation", "refuse"]


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

    @model_serializer(mode="wrap")
    def serialize_source(self, handler):
        document = handler(self)
        # Reading a legacy observation must not invent absent RAG audit fields.
        # Explicit empty fields and audit added after validation remain observable.
        for name in ("chunks", "model_usage", "collection"):
            if name not in self.model_fields_set and not getattr(self, name):
                document.pop(name, None)
        return document


class PromptManifest(ProjectionContract):
    prompt_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    variant: str | None = Field(default=None, min_length=1, max_length=128)
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class EnvelopeManifest(ProjectionContract):
    # References only: this DTO neither validates nor consumes a transfer payload.
    transfer_id: str = Field(pattern=r"^transfer:[0-9a-f]{64}$")
    run_id: str = Field(min_length=1, max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=160)
    schema_version: Literal[1]
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    summary_ref: EventPointer | None = None
    commit_ref: EventPointer | None = None
    selected_fields: tuple[str, ...] = ()


class CheckpointManifest(ProjectionContract):
    # Observation of the existing CheckpointRef shape. Importing long_horizon's
    # package here would initialize its service and cycle back into the pipeline.
    execution_thread_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=160)
    checkpoint_ns: str = Field(max_length=200)
    checkpoint_id: str = Field(min_length=1, max_length=160)
    graph_version: Literal["handoff:v1"] = "handoff:v1"
    public_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SessionManifest(ProjectionContract):
    session_id: str = Field(min_length=1, max_length=160)
    projection_version: int | None = Field(default=None, ge=1, strict=True)
    source_event_cursor: int | None = Field(default=None, ge=0, strict=True)
    state_revision: int | None = Field(default=None, ge=0, strict=True)
    checkpoint_ref: CheckpointManifest | None = None

    @model_validator(mode="after")
    def checkpoint_matches_session(self):
        if self.checkpoint_ref is not None and self.checkpoint_ref.session_id != self.session_id:
            raise ValueError("checkpoint observation must match the session")
        return self


class SourceSelectionManifest(ProjectionContract):
    item_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    type: str = Field(min_length=1)
    references: tuple[str, ...] = ()
    profile_version: int | None = Field(default=None, ge=1, strict=True)
    final_tokens: int = Field(ge=0, strict=True)


class BudgetSnapshot(ProjectionContract):
    # Balances can be negative after an observed provider overshoot.
    owner_remaining: int = Field(strict=True)
    run_remaining: int = Field(strict=True)
    reserved_tokens: int = Field(ge=0, strict=True)
    degradation_stages: tuple[BudgetStage, ...] = ()


class AgentContextManifest(ProjectionContract):
    schema_version: Literal[1] = 1
    recipe_id: str = Field(min_length=1, max_length=128)
    recipe_version: str = Field(min_length=1, max_length=64)
    owner_agent: AgentOwner
    route_key: Literal["general_chat", "current_song", "tool_calling", "worldline"]
    phase: AgentPhase
    prompts: tuple[PromptManifest, ...] = Field(min_length=1)
    envelope: EnvelopeManifest | None = None
    session: SessionManifest | None = None
    source_selection: tuple[SourceSelectionManifest, ...]
    budget_snapshot: BudgetSnapshot | None = None


class ManifestProjection(ProjectionContract):
    agent_context: AgentContextManifest | None = None

    @model_serializer(mode="wrap")
    def serialize_projection(self, handler):
        document = handler(self)
        if self.agent_context is None:
            document.pop("agent_context", None)
        return document

    def legacy_projection(self) -> dict:
        """Explicit v1 observation for consumers that only know aggregate fields."""
        document = self.model_dump(mode="json")
        document.pop("agent_context", None)
        return document


class PackManifest(ManifestProjection):
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


class CallManifest(ManifestProjection):
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

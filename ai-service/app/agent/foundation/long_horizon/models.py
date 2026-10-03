from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class HistoryEventType(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    ROUTE_DECISION = "route_decision"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    RETRIEVAL = "retrieval"
    RAG_EVIDENCE = "rag_evidence"
    ARTIFACT_CREATED = "artifact_created"
    CHECKPOINT = "checkpoint"
    HANDOFF = "handoff"
    AGENT_TRANSFER = "agent_transfer"
    SESSION_HANDOFF = "session_handoff"
    RUN_PROGRESS = "run_progress"
    REALITY_REVALIDATION = "reality_revalidation"
    CONTEXT_TRACE = "context_trace"
    ERROR = "error"
    USER_DECISION = "user_decision"
    ACTION_ACK = "action_ack"
    CLIENT_ACTION = "client_action"
    MODEL_USAGE = "model_usage"
    HISTORY_ARCHIVE = "history_archive"
    NAMESPACE_MIGRATION = "namespace_migration"


class HistoryPointer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["event", "artifact"]
    target_id: str = Field(min_length=1, max_length=160)


class HistoryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=160)
    event_type: HistoryEventType
    timestamp: datetime
    node: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] | None = None
    storage_ref: str | None = Field(default=None, min_length=1, max_length=500)
    references: list[HistoryPointer] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(default=None, max_length=160)
    event_key: str | None = Field(default=None, min_length=1, max_length=160)
    event_seq: int | None = Field(default=None, gt=0)

    @property
    def semantic_type(self) -> HistoryEventType:
        if self.event_type is HistoryEventType.HANDOFF and self.node == "rollover":
            return HistoryEventType.SESSION_HANDOFF
        return self.event_type

    @model_validator(mode="after")
    def exactly_one_body(self) -> "HistoryEvent":
        if (self.payload is None) == (self.storage_ref is None):
            raise ValueError("history event requires exactly one of payload or storage_ref")
        return self


def same_event_content(left: HistoryEvent, right: HistoryEvent) -> bool:
    # A retry may have a new transport request ID; preserve the first receipt's
    # provenance when its logical event and public body are identical.
    excluded = {"event_id", "timestamp", "event_seq", "request_id"}
    return left.model_dump(exclude=excluded) == right.model_dump(exclude=excluded)


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    task_id: str
    session_id: str
    artifact_type: str
    created_at: datetime
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    payload: Any
    projection: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"artifact:{self.artifact_id}"


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    conversation_id: str
    created_at: datetime
    closed_at: datetime | None = None


class HandoffDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=4000)
    summary: str = Field(default="", max_length=12000)
    constraints: list[str] = Field(default_factory=list)
    constraint_refs: dict[str, list[HistoryPointer]] = Field(default_factory=dict)
    preferences: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    decision_refs: dict[str, list[HistoryPointer]] = Field(default_factory=dict)
    current_stage: str = Field(min_length=1, max_length=1000)
    completed_steps: list[str] = Field(default_factory=list)
    pending_items: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    important_entities: list[dict[str, Any]] = Field(default_factory=list)
    key_message_refs: list[HistoryPointer] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[HistoryPointer] = Field(default_factory=list)
    revalidate_fields: list[str] = Field(default_factory=list)
    last_known_state: dict[str, Any] = Field(default_factory=dict)


class HandoffProjection(HandoffDraft):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: str
    task_id: str
    session_id: str
    created_at: datetime
    token_count: int = Field(ge=0)
    token_budget: int = Field(gt=0)
    dropped_fields: list[str] = Field(default_factory=list)

    @property
    def agent_progress(self) -> AgentProgress:
        """Legacy projections contain no evidence of an agent owner."""
        return AgentProgress()


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    conversation_id: str
    session_id: str
    previous_session_id: str | None = None
    parent_session_id: str | None = None
    inherited_handoff_id: str | None = None
    created_at: datetime
    closed_at: datetime | None = None


class ColdStartContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    session_id: str
    handoff: HandoffProjectionV2 | HandoffProjection | None = None
    key_events: list[HistoryEvent] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    revalidated_state: dict[str, Any] = Field(default_factory=dict)
    historical_state: dict[str, Any] = Field(default_factory=dict)
    changed_fields: list[str] = Field(default_factory=list)


class RolloverReason(StrEnum):
    TOKEN_THRESHOLD = "token_threshold"
    EXPLICIT_CHECKPOINT = "explicit_checkpoint"
    ARTIFACT_CREATED = "artifact_created"


class RolloverResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    closed_session: SessionRecord
    handoff: HandoffProjectionV2 | HandoffProjection
    new_session: SessionRecord
    reason: RolloverReason


AgentId = Literal["general_chat", "curator", "explorer"]
TransferPhase = Literal[
    "unknown", "proposed", "rejected", "committed", "target_started",
    "target_finished", "reconciled",
]


class ContinuityContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContinuityIdentity(ContinuityContract):
    conversation_id: str = Field(min_length=1, max_length=160)
    continuity_task_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=160)
    graph_mode: Literal["foundation", "handoff"]
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    route_task_id: str | None = Field(default=None, min_length=1, max_length=128)


class CheckpointRef(ContinuityContract):
    execution_thread_id: str = Field(min_length=1, max_length=200)
    session_id: str = Field(min_length=1, max_length=160)
    checkpoint_ns: str = Field(max_length=200)
    checkpoint_id: str = Field(min_length=1, max_length=160)
    graph_version: Literal["handoff:v1"] = "handoff:v1"
    public_state_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class AgentTransferSummaryV1(ContinuityContract):
    schema_version: Literal[1] = 1
    transfer_id: str = Field(pattern=r"^transfer:[0-9a-f]{64}$")
    run_id: str = Field(min_length=1, max_length=128)
    hop_index: int = Field(ge=0, le=5)
    source_agent: AgentId
    target_agent: AgentId
    route_task_id: str = Field(min_length=1, max_length=128)
    reason_code: str = Field(min_length=1, max_length=64)
    candidate_summary: str = Field(default="", max_length=2000)
    # Legacy history remains readable; execution requires a pinned digest.
    payload_digest: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    completed_result_refs: tuple[HistoryPointer, ...] = Field(default=(), max_length=32)
    pending_task_refs: tuple[str, ...] = Field(default=(), max_length=32)
    entity_refs: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_refs: tuple[HistoryPointer, ...] = Field(default=(), max_length=12)
    summary_provenance: Literal["server_state;candidate_text_untrusted"] = (
        "server_state;candidate_text_untrusted"
    )


class TransferJournalEvent(ContinuityContract):
    schema_version: Literal[1] = 1
    phase: TransferPhase
    transfer_id: str = Field(pattern=r"^transfer:[0-9a-f]{64}$")
    summary: AgentTransferSummaryV1
    summary_ref: HistoryPointer | None = None
    checkpoint_ref: CheckpointRef | None = None
    result_refs: tuple[HistoryPointer, ...] = Field(default=(), max_length=32)
    retry_allowed: bool = False

    @model_validator(mode="after")
    def committed_requires_checkpoint(self):
        if self.phase == "unknown":
            raise ValueError("unknown is not a journal phase")
        if self.transfer_id != self.summary.transfer_id:
            raise ValueError("transfer summary identity mismatch")
        if self.phase in {"committed", "reconciled", "target_started", "target_finished"}:
            if self.checkpoint_ref is None:
                raise ValueError("committed transfer requires checkpoint confirmation")
        return self


class PublicTaskResult(ContinuityContract):
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=128)
    route_task_id: str = Field(min_length=1, max_length=128)
    route_key: Literal["general_chat", "current_song", "tool_calling", "worldline"]
    status: Literal["success", "needs_input", "failed"]
    content: str = Field(default="", max_length=1000)


class PublicRunMetadata(ContinuityContract):
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=128)
    entry_agent: AgentId
    current_agent: AgentId
    status: Literal["running", "finalizing", "completed", "needs_input", "failed"]
    started_at: AwareDatetime
    chain: tuple[AgentId, ...] = Field(min_length=1, max_length=6)
    accepted_hop_count: int = Field(ge=0, le=5)
    pending_task_ids: tuple[str, ...] = Field(default=(), max_length=32)
    plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    usage: tuple[PublicAgentUsage, ...]
    limits: PublicRunLimits

    @model_validator(mode="after")
    def public_chain_is_consistent(self):
        AgentProgress(
            last_run_id=self.run_id, last_agent=self.current_agent,
            recent_run_chain=self.chain, accepted_hop_count=self.accepted_hop_count,
        )
        if self.chain[0] != self.entry_agent:
            raise ValueError("public run entry must match chain head")
        if tuple(item.agent for item in self.usage) != ("general_chat", "curator", "explorer"):
            raise ValueError("public run usage requires each agent in registry order")
        return self


class PublicAgentUsage(ContinuityContract):
    agent: AgentId
    tokens: int = Field(ge=0)
    retries: int = Field(ge=0)
    reported_tokens: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    model_calls: int = Field(ge=0)


class PublicRunLimits(ContinuityContract):
    max_handoffs: int = Field(ge=1, le=5)
    max_retries_per_agent: int = Field(ge=0, le=1)
    agent_timeout_seconds: int = Field(gt=0)
    run_timeout_seconds: int = Field(gt=0)
    agent_token_budget: int = Field(gt=0)
    run_token_budget: int = Field(gt=0)


class RunProgressRecord(ContinuityContract):
    schema_version: Literal[1] = 1
    run: PublicRunMetadata
    checkpoint_ref: CheckpointRef
    result_refs: tuple[HistoryPointer, ...] = Field(default=(), max_length=32)
    transfer_refs: tuple[HistoryPointer, ...] = Field(default=(), max_length=5)


class PublicHistoryError(ContinuityContract):
    code: Literal["idempotency_conflict", "integrity_conflict"]
    related_key_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class AgentProgress(ContinuityContract):
    last_run_id: str | None = None
    last_agent: AgentId | None = None
    run_status: Literal["unknown", "running", "finalizing", "completed", "needs_input", "failed"] = "unknown"
    last_route_task_id: str | None = None
    recent_run_chain: tuple[AgentId, ...] = Field(default=(), max_length=6)
    accepted_hop_count: int = Field(default=0, ge=0, le=5)
    transfer_phase: TransferPhase = "unknown"
    pending_transfer_ref: HistoryPointer | None = None
    run_state_ref: HistoryPointer | None = None
    progress_refs: tuple[HistoryPointer, ...] = Field(default=(), max_length=32)
    earlier_chain_refs: tuple[HistoryPointer, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def chain_matches_owner(self):
        if self.recent_run_chain:
            if self.last_agent != self.recent_run_chain[-1]:
                raise ValueError("projection owner must match chain tail")
            if len(self.recent_run_chain) != self.accepted_hop_count + 1:
                raise ValueError("projection count must be derived from chain")
            edges = {("general_chat", "curator"), ("curator", "general_chat"),
                     ("curator", "explorer"), ("explorer", "curator")}
            if any(edge not in edges for edge in zip(self.recent_run_chain, self.recent_run_chain[1:])):
                raise ValueError("projection contains forbidden transfer edge")
        elif self.last_agent is not None or self.accepted_hop_count:
            raise ValueError("owner cannot be guessed without a chain")
        return self


class HandoffDraftV2(HandoffDraft):
    schema_version: Literal[2] = 2
    projection_version: Literal[2] = 2
    agent_progress: AgentProgress = Field(default_factory=AgentProgress)
    source_event_cursor: int = Field(default=0, ge=0)
    source_checkpoint_ref: CheckpointRef | None = None
    state_revision: str | None = Field(default=None, max_length=160)
    counter_name: str = "unicode_heuristic:v1"


class HandoffProjectionV2(HandoffDraftV2):
    model_config = ConfigDict(extra="forbid", frozen=True)

    handoff_id: str
    task_id: str
    session_id: str
    created_at: datetime
    token_count: int = Field(ge=0)
    token_budget: int = Field(gt=0)
    dropped_fields: list[str] = Field(default_factory=list)


def read_handoff_projection(body: dict[str, Any]) -> HandoffProjection | HandoffProjectionV2:
    if body.get("schema_version", 1) == 2:
        return HandoffProjectionV2.model_validate(body)
    if body.get("schema_version", 1) != 1:
        raise ValueError("unsupported session projection version")
    return HandoffProjection.model_validate({key: value for key, value in body.items() if key != "schema_version"})


class StateObservation(ContinuityContract):
    field: str = Field(min_length=1, max_length=128)
    value: Any = None
    source: str = Field(min_length=1, max_length=128)
    observed_at: AwareDatetime
    revision: str | None = Field(default=None, max_length=160)
    status: Literal["valid", "changed", "unknown"] = "valid"
    scope: str = Field(min_length=1, max_length=160)


class LiveSnapshot(ContinuityContract):
    observations: tuple[StateObservation, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def unique_fields(self):
        if len({item.field for item in self.observations}) != len(self.observations):
            raise ValueError("duplicate live state field")
        return self


class RevalidationReport(ContinuityContract):
    field_results: dict[str, Literal["valid", "changed", "unknown"]] = Field(default_factory=dict)
    transfer_integrity: Literal["valid", "uncommitted", "missing_receipt", "conflict", "unknown"] = "unknown"
    ref_integrity: Literal["valid", "conflict", "unknown"] = "unknown"
    checkpoint_integrity: Literal["valid", "conflict", "unknown"] = "unknown"
    budget_state: Literal["valid", "exhausted", "unknown"] = "unknown"
    required_drilldown: tuple[HistoryPointer, ...] = ()
    invalidated_dependencies: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    run_id: str | None = None
    current_agent: AgentId | None = None
    remaining_deadline: float = Field(default=0, ge=0)
    run_status: str = "unknown"
    write_status: Literal["none", "confirmed", "unknown"] = "unknown"


class RecoveryPlan(ContinuityContract):
    action: Literal["resume_target", "resume_source", "refresh_facts", "needs_input", "stop"]
    resume_checkpoint_ref: CheckpointRef | None = None
    resume_agent: AgentId | None = None
    run_id: str | None = None
    remaining_deadline: float = Field(default=0, ge=0)
    required_reads: tuple[str, ...] = ()
    blocked_reason: str | None = None
    evidence_refs: tuple[HistoryPointer, ...] = ()


ColdStartContext.model_rebuild()
RolloverResult.model_rebuild()
PublicRunMetadata.model_rebuild()

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    REALITY_REVALIDATION = "reality_revalidation"
    CONTEXT_TRACE = "context_trace"
    ERROR = "error"


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

    @model_validator(mode="after")
    def exactly_one_body(self) -> "HistoryEvent":
        if (self.payload is None) == (self.storage_ref is None):
            raise ValueError("history event requires exactly one of payload or storage_ref")
        return self


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


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
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
    handoff: HandoffProjection | None = None
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
    handoff: HandoffProjection
    new_session: SessionRecord
    reason: RolloverReason

"""Small model-selected request and server-assembled transfer context."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agent.foundation.long_horizon.models import HistoryPointer
from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RouteKey, RouteTask
from app.agent.foundation.subgraphs.tool_calling.models import ReferencedEntities
from app.agent.handoff.registry import AGENT_REGISTRY, AgentKey, next_agent_for_task


Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"\S")
]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=512, pattern=r"\S")]
Summary = Annotated[
    str, StringConstraints(min_length=1, max_length=2_000, pattern=r"\S")
]
UserInput = Annotated[
    str, StringConstraints(min_length=1, max_length=4_000, pattern=r"\S")
]
FinalResponse = Annotated[
    str, StringConstraints(min_length=1, max_length=10_000, pattern=r"\S")
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TransferArguments(ContractModel):
    """Only these fields are exposed to the model's fixed-target transfer tool."""

    reason: Reason
    context_summary: Summary


class HandoffRequest(TransferArguments):
    target_agent: AgentKey


class CompletedTaskSummary(ContractModel):
    task_id: Identifier
    route_key: RouteKey
    status: Literal[TaskStatus.SUCCESS] = TaskStatus.SUCCESS
    content: str = Field(default="", max_length=1_000)


class HandoffEnvelope(ContractModel):
    """Built from trusted run state, never accepted as tool arguments.

    Results carry a bounded public summary rather than arbitrary TaskResult.data.
    Entity references aid resolution; they do not authorize actions.
    """

    schema_version: Literal[1] = 1
    run_id: Identifier
    tool_call_id: Identifier
    source_agent: AgentKey
    request: HandoffRequest
    original_input: UserInput
    active_task: RouteTask
    pending_tasks: tuple[RouteTask, ...] = Field(min_length=1, max_length=32)
    completed_results: tuple[CompletedTaskSummary, ...] = Field(
        default=(), max_length=32
    )
    entities: ReferencedEntities = Field(default_factory=ReferencedEntities)
    evidence_refs: tuple[HistoryPointer, ...] = Field(default=(), max_length=12)

    @model_validator(mode="after")
    def validate_payload(self) -> "HandoffEnvelope":
        if self.active_task != self.pending_tasks[0]:
            raise ValueError("active task must be the first pending task")
        if (
            self.request.target_agent
            not in AGENT_REGISTRY[self.source_agent].transfer_targets
        ):
            raise ValueError("context contains a forbidden transfer edge")
        if (
            next_agent_for_task(self.source_agent, self.active_task.route_key)
            != self.request.target_agent
        ):
            raise ValueError("context target must lead to the active capability owner")
        pending_ids = [task.task_id for task in self.pending_tasks]
        completed_ids = [result.task_id for result in self.completed_results]
        if len(set(pending_ids + completed_ids)) != len(pending_ids + completed_ids):
            raise ValueError("transferred tasks must be unique and disjoint")
        if len(self.model_dump_json().encode("utf-8")) > 65_536:
            raise ValueError("handoff context exceeds the 64 KiB payload limit")
        return self


class HandoffCode(StrEnum):
    ACCEPTED = "accepted"
    INVALID_EDGE = "invalid_edge"
    WRONG_TARGET = "wrong_target"
    STALE_REQUEST = "stale_request"
    NO_PROGRESS = "no_progress"
    HANDOFF_LIMIT = "handoff_limit"
    TOKEN_LIMIT = "token_limit"
    RUN_TIMEOUT = "run_timeout"
    RUN_NOT_RUNNING = "run_not_running"
    GUARD_REJECTED = "guard_rejected"
    GUARD_UNAVAILABLE = "guard_unavailable"
    GUARD_SHADOW = "guard_shadow"
    INVALID_TRANSFER = "invalid_transfer"
    MODEL_CALL = "model_call"
    RETRY = "retry"
    RETRY_LIMIT = "retry_limit"
    TIME_LIMIT = "time_limit"
    TASK_FAILED = "task_failed"
    CANCELLED = "cancelled"
    ALL_TASKS_DONE = "all_tasks_done"
    CLARIFICATION = "clarification"


class HandoffResult(ContractModel):
    code: HandoffCode
    envelope: HandoffEnvelope | None = None

    @model_validator(mode="after")
    def accepted_requires_context(self) -> "HandoffResult":
        if (self.code is HandoffCode.ACCEPTED) != (self.envelope is not None):
            raise ValueError("only accepted transfers carry context")
        return self

    @property
    def accepted(self) -> bool:
        return self.code is HandoffCode.ACCEPTED


class HandoffTrace(ContractModel):
    """Allowlisted trace metadata; request text, entities and credentials stay out."""

    schema_version: Literal[1] = 1
    trace_id: Identifier
    run_id: Identifier
    tool_call_id: Identifier
    source_agent: AgentKey
    target_agent: AgentKey
    code: HandoffCode
    guard_score: float | None = Field(default=None, ge=0, le=1)
    duration_ms: int = Field(ge=0, strict=True)
    tokens: int = Field(ge=0, strict=True)

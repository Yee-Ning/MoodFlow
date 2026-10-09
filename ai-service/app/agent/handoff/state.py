"""Executable lifecycle contract; no model, tool, clock or graph side effects."""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from langchain_core.messages import AnyMessage
from pydantic import AwareDatetime, Field, model_validator

from app.agent.foundation.long_horizon.models import HistoryPointer
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteDecision, RouteTask, RoutingContext
from app.agent.foundation.state.base import BaseAgentState
from app.agent.foundation.subgraphs.tool_calling.models import ReferencedEntities
from app.agent.handoff.contracts import (
    ContractModel,
    FinalResponse,
    HandoffCode,
    HandoffEnvelope,
    HandoffRequest,
    HandoffResult,
    Identifier,
    UserInput,
)
from app.agent.handoff.registry import (
    AGENT_REGISTRY,
    CAPABILITY_OWNERS,
    AgentKey,
    next_agent_for_task,
)
from app.agent.handoff.integrity import transfer_payload


class RunStatus(StrEnum):
    RUNNING = "running"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class StopReason(StrEnum):
    ALL_TASKS_DONE = "all_tasks_done"
    CLARIFICATION = "clarification"
    TASK_FAILED = "task_failed"
    HANDOFF_LIMIT = "handoff_limit"
    TOKEN_LIMIT = "token_limit"
    TIME_LIMIT = "time_limit"
    RETRY_LIMIT = "retry_limit"
    INVALID_TRANSFER = "invalid_transfer"
    CANCELLED = "cancelled"


class RunLimits(ContractModel):
    max_handoffs: int = Field(default=5, ge=1, le=5, strict=True)
    max_retries_per_agent: int = Field(default=1, ge=0, le=1, strict=True)
    agent_timeout_seconds: int = Field(default=45, gt=0, strict=True)
    run_timeout_seconds: int = Field(default=120, gt=0, strict=True)
    agent_token_budget: int = Field(default=64_000, gt=0, strict=True)
    run_token_budget: int = Field(default=128_000, gt=0, strict=True)

    @model_validator(mode="after")
    def limits_are_consistent(self) -> "RunLimits":
        if self.agent_timeout_seconds > self.run_timeout_seconds:
            raise ValueError("agent deadline cannot exceed run deadline")
        if self.agent_token_budget > self.run_token_budget:
            raise ValueError("agent token budget cannot exceed run token budget")
        return self


class AgentUsage(ContractModel):
    agent: AgentKey
    tokens: int = Field(default=0, ge=0, strict=True)
    retries: int = Field(default=0, ge=0, strict=True)
    reported_tokens: int = Field(default=0, ge=0, strict=True)
    estimated_tokens: int = Field(default=0, ge=0, strict=True)
    model_calls: int = Field(default=0, ge=0, strict=True)


class HandoffHop(ContractModel):
    tool_call_id: Identifier
    source_agent: AgentKey
    target_agent: AgentKey
    task_id: Identifier
    completed_count: int = Field(ge=0, strict=True)


class HandoffRun(ContractModel):
    schema_version: int = Field(default=1, ge=1, le=1, strict=True)
    run_id: Identifier
    original_input: UserInput
    plan: RouteDecision
    entry_agent: AgentKey
    current_agent: AgentKey
    started_at: AwareDatetime
    limits: RunLimits = Field(default_factory=RunLimits)
    status: RunStatus = RunStatus.RUNNING
    stop_reason: StopReason | None = None
    final_response: FinalResponse | None = None
    results: tuple[TaskResult, ...] = Field(default=(), max_length=32)
    hops: tuple[HandoffHop, ...] = Field(default=(), max_length=5)
    usage: tuple[AgentUsage, ...]

    @property
    def handoff_count(self) -> int:
        return len(self.hops)

    @property
    def handoff_chain(self) -> tuple[AgentKey, ...]:
        return (self.entry_agent, *(hop.target_agent for hop in self.hops))

    @property
    def pending_tasks(self) -> tuple[RouteTask, ...]:
        return tuple(self.plan.tasks[len(self.results) :])

    @property
    def total_tokens(self) -> int:
        return sum(item.tokens for item in self.usage)

    @property
    def terminal(self) -> bool:
        return self.status in {
            RunStatus.COMPLETED,
            RunStatus.NEEDS_INPUT,
            RunStatus.FAILED,
        }

    @model_validator(mode="after")
    def validate_run(self) -> "HandoffRun":
        if len(self.plan.tasks) > 32:
            raise ValueError("a run can contain at most 32 tasks")
        for intent in self.plan.intents:
            if (
                intent.end - intent.start != len(intent.evidence)
                or self.original_input[intent.start : intent.end] != intent.evidence
            ):
                raise ValueError("intent evidence must match the original user input")
        for segment in self.plan.unresolved_segments:
            if self.original_input[segment.start : segment.end] != segment.text:
                raise ValueError(
                    "unresolved evidence must match the original user input"
                )
        for task in self.plan.tasks:
            if not task.task_id.strip():
                raise ValueError("task ID must not be blank")
            if not task.intent_orders and task.input_text != self.original_input:
                raise ValueError(
                    "tasks without intent evidence must preserve the full user input"
                )
        if [item.agent for item in self.usage] != list(AgentKey):
            raise ValueError(
                "usage must contain each agent exactly once in registry order"
            )
        if any(item.retries > self.limits.max_retries_per_agent for item in self.usage):
            raise ValueError("agent retry limit exceeded")
        if len(self.results) > len(self.plan.tasks):
            raise ValueError("result has no corresponding task")
        for index, result in enumerate(self.results):
            task = self.plan.tasks[index]
            if (result.task_id, result.route_key) != (task.task_id, task.route_key):
                raise ValueError("results must match task order and capability")
            if (
                index < len(self.results) - 1
                and result.status is not TaskStatus.SUCCESS
            ):
                raise ValueError(
                    "execution cannot continue after failed or needs-input results"
                )
        if len(self.hops) > self.limits.max_handoffs:
            raise ValueError("handoff limit exceeded")
        agent = self.entry_agent
        call_ids: set[str] = set()
        previous: HandoffHop | None = None
        for hop in self.hops:
            if (
                hop.source_agent != agent
                or hop.target_agent not in AGENT_REGISTRY[agent].transfer_targets
            ):
                raise ValueError("handoff chain contains an invalid edge")
            if hop.tool_call_id in call_ids:
                raise ValueError("handoff call IDs must be unique")
            if not 0 <= hop.completed_count <= len(
                self.results
            ) or hop.completed_count >= len(self.plan.tasks):
                raise ValueError("handoff progress snapshot is invalid")
            task = self.plan.tasks[hop.completed_count]
            if (
                hop.task_id != task.task_id
                or next_agent_for_task(hop.source_agent, task.route_key)
                != hop.target_agent
            ):
                raise ValueError(
                    "handoff must lead to the owner of the next pending task"
                )
            if previous is not None:
                if hop.completed_count < previous.completed_count:
                    raise ValueError("handoff progress cannot move backwards")
                if (
                    hop.target_agent == previous.source_agent
                    and hop.completed_count == previous.completed_count
                ):
                    raise ValueError(
                        "agent reversal without task progress is forbidden"
                    )
            call_ids.add(hop.tool_call_id)
            previous, agent = hop, hop.target_agent
        if agent != self.current_agent:
            raise ValueError("current agent must match the end of the handoff chain")
        last_status = self.results[-1].status if self.results else None
        all_done = (
            bool(self.plan.tasks)
            and len(self.results) == len(self.plan.tasks)
            and last_status is TaskStatus.SUCCESS
        )
        if self.status is RunStatus.RUNNING:
            if (
                not self.plan.is_complete
                or not self.pending_tasks
                or last_status in {TaskStatus.FAILED, TaskStatus.NEEDS_INPUT}
            ):
                raise ValueError("running state requires executable pending tasks")
            if self.stop_reason is not None:
                raise ValueError("running state cannot have a stop reason")
        elif self.status in {RunStatus.FINALIZING, RunStatus.COMPLETED}:
            if self.stop_reason is StopReason.ALL_TASKS_DONE:
                if not all_done:
                    raise ValueError(
                        "completion requires success for every planned task"
                    )
            elif (
                self.status is RunStatus.FINALIZING
                and self.stop_reason is StopReason.TASK_FAILED
            ):
                if last_status is not TaskStatus.FAILED:
                    raise ValueError("task failure requires a failed result")
            else:
                raise ValueError("invalid finalization reason")
        elif self.status is RunStatus.NEEDS_INPUT:
            if self.stop_reason is not StopReason.CLARIFICATION or (
                self.plan.is_complete and last_status is not TaskStatus.NEEDS_INPUT
            ):
                raise ValueError("needs-input state requires a clarification")
        elif self.stop_reason is None or self.stop_reason in {
            StopReason.ALL_TASKS_DONE,
            StopReason.CLARIFICATION,
        }:
            raise ValueError("failed state requires a failure reason")
        if self.terminal != (self.final_response is not None):
            raise ValueError("terminal states must contain exactly one final response")
        return self


class HandoffState(BaseAgentState):
    """Messages remain session-scoped; handoff_run is replaced for each new turn."""

    handoff_run: HandoffRun
    routing_context: RoutingContext


def _replace(run: HandoffRun, **updates: Any) -> HandoffRun:
    return HandoffRun.model_validate({**run.model_dump(), **updates})


def new_run(
    run_id: str,
    original_input: str,
    plan: RouteDecision,
    *,
    started_at: datetime,
    entry_agent: AgentKey | None = None,
    limits: RunLimits | None = None,
) -> HandoffRun:
    agent = entry_agent or (
        CAPABILITY_OWNERS[plan.tasks[0].route_key]
        if plan.tasks
        else AgentKey.GENERAL_CHAT
    )
    needs_input = not plan.is_complete or not plan.tasks
    # An empty complete plan cannot be dispatched. Require a router clarification instead.
    if plan.is_complete and not plan.tasks:
        raise ValueError("complete plans must contain an executable task")
    return HandoffRun(
        run_id=run_id,
        original_input=original_input,
        plan=plan.model_dump(),
        entry_agent=agent,
        current_agent=agent,
        started_at=started_at,
        limits=(limits or RunLimits()).model_dump(),
        usage=tuple(AgentUsage(agent=key) for key in AgentKey),
        status=RunStatus.NEEDS_INPUT if needs_input else RunStatus.RUNNING,
        stop_reason=StopReason.CLARIFICATION if needs_input else None,
        final_response=(plan.clarification_question or "请补充你希望完成的具体任务。")
        if needs_input
        else None,
    )


def create_handoff_state(
    run: HandoffRun,
    *,
    messages: list[AnyMessage] | None = None,
    routing_context: RoutingContext | None = None,
    continuity_context: dict[str, Any] | None = None,
) -> HandoffState:
    return HandoffState(
        messages=list(messages or []),
        current_input=run.original_input,
        handoff_run=HandoffRun.model_validate(run.model_dump()),
        routing_context=routing_context or RoutingContext(),
        continuity_context=dict(continuity_context or {}),
    )


def record_task_result(run: HandoffRun, result: TaskResult) -> HandoffRun:
    if run.status is not RunStatus.RUNNING:
        raise ValueError("only running tasks accept results")
    task = run.pending_tasks[0]
    if CAPABILITY_OWNERS[task.route_key] != run.current_agent:
        raise ValueError("only the assigned capability owner can produce a task result")
    updates: dict[str, Any] = {"results": (*run.results, result.model_dump())}
    if result.status is TaskStatus.NEEDS_INPUT:
        updates.update(
            status=RunStatus.NEEDS_INPUT,
            stop_reason=StopReason.CLARIFICATION,
            final_response=result.content,
        )
    elif result.status is TaskStatus.FAILED:
        updates.update(status=RunStatus.FINALIZING, stop_reason=StopReason.TASK_FAILED)
    elif len(run.results) + 1 == len(run.plan.tasks):
        updates.update(
            status=RunStatus.FINALIZING, stop_reason=StopReason.ALL_TASKS_DONE
        )
    return _replace(run, **updates)


def finish_run(
    run: HandoffRun, response: str, *, reason: StopReason | None = None
) -> HandoffRun:
    """Bounded deterministic exit, including when the model/guard is unavailable."""
    if run.terminal:
        raise ValueError("a terminal run cannot be reopened or finalized twice")
    stop = reason or run.stop_reason
    if stop is None:
        raise ValueError("unfinished tasks require an explicit failure reason")
    status = (
        RunStatus.COMPLETED if stop is StopReason.ALL_TASKS_DONE else RunStatus.FAILED
    )
    return _replace(run, status=status, stop_reason=stop, final_response=response)


def request_handoff(
    run: HandoffRun,
    request: HandoffRequest,
    *,
    source_agent: AgentKey,
    tool_call_id: str,
    now: datetime,
    entities: ReferencedEntities | None = None,
    evidence_refs: tuple[HistoryPointer, ...] = (),
) -> tuple[HandoffRun, HandoffResult]:
    """Pure contract transition; rejected transfers never change counters or agent."""
    if now.tzinfo is None or now.utcoffset() is None or now < run.started_at:
        raise ValueError("transfer time must be aware and not precede the run")
    code: HandoffCode | None = None
    if run.status is not RunStatus.RUNNING:
        code = HandoffCode.RUN_NOT_RUNNING
    elif source_agent != run.current_agent or tool_call_id in {
        hop.tool_call_id for hop in run.hops
    }:
        code = HandoffCode.STALE_REQUEST
    elif now >= run.started_at + timedelta(seconds=run.limits.run_timeout_seconds):
        code = HandoffCode.RUN_TIMEOUT
    elif run.total_tokens >= run.limits.run_token_budget or any(
        item.tokens >= run.limits.agent_token_budget for item in run.usage
    ):
        code = HandoffCode.TOKEN_LIMIT
    elif run.handoff_count >= run.limits.max_handoffs:
        code = HandoffCode.HANDOFF_LIMIT
    elif request.target_agent not in AGENT_REGISTRY[source_agent].transfer_targets:
        code = HandoffCode.INVALID_EDGE
    elif (
        run.hops
        and request.target_agent == run.hops[-1].source_agent
        and len(run.results) == run.hops[-1].completed_count
    ):
        code = HandoffCode.NO_PROGRESS
    elif request.target_agent != next_agent_for_task(
        run.current_agent, run.pending_tasks[0].route_key
    ):
        code = HandoffCode.WRONG_TARGET
    if code is not None:
        return run, HandoffResult(code=code)
    envelope = HandoffEnvelope.model_validate(transfer_payload(
        run, request, source_agent=source_agent, tool_call_id=tool_call_id,
        entities=entities or ReferencedEntities(), evidence_refs=evidence_refs,
    ))
    hop = HandoffHop(
        tool_call_id=tool_call_id,
        source_agent=source_agent,
        target_agent=request.target_agent,
        task_id=run.pending_tasks[0].task_id,
        completed_count=len(run.results),
    )
    updated = _replace(
        run, current_agent=request.target_agent, hops=(*run.hops, hop.model_dump())
    )
    return updated, HandoffResult(code=HandoffCode.ACCEPTED, envelope=envelope)

"""Parent graph for the GeneralChat / Curator / Explorer business network."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from time import monotonic, perf_counter
from typing import Any, NotRequired
from uuid import uuid4

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command

from app.agent.checkpointing import create_runtime_checkpointer
from app.agent.foundation.main_graph import create_hybrid_router
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteDecision
from app.agent.foundation.state.main import MainState
from app.agent.foundation.entry_context import EntryContextEngine, build_lite_pack, triage_scope
from app.agent.handoff.agents import choose_transfer, execute_owned_task
from app.agent.handoff.progress import with_combination_progress, explain_combination_progress
from app.agent.handoff.guard import configured_guard
from app.agent.handoff.integrity import TransferIntegrityConflict
from app.agent.handoff.hop_context import hop_source_scope
from app.agent.handoff.registry import AGENT_REGISTRY, CAPABILITY_OWNERS, AgentKey
from app.agent.handoff.routing import LazyHybridRouter, configured_entry_router, input_question
from app.agent.handoff.state import (
    HandoffRun,
    AgentUsage,
    RunLimits,
    RunStatus,
    StopReason,
    finish_run,
    new_run,
    record_task_result,
)
from app.agent.handoff.tools import execute_transfer, utc_now
from app.agent.handoff.trace import TraceRecorder
from app.llm.runtime import RequestLedger, RuntimeLimitError, request_scope
from app.schemas.context import MusicAgentContext

EXECUTION_FAILURE = "本次音乐或聊天任务暂时无法完成，请稍后再试。"


class HandoffGraphState(MainState):
    # Store JSON primitives so the existing strict checkpoint serializer needs
    # no broader module/deserialization allowlist.
    handoff_run: NotRequired[dict[str, Any] | None]
    transfer_envelope: NotRequired[dict[str, Any] | None]
    handoff_failed: NotRequired[bool]
    handoff_needs_input: NotRequired[bool]
    run_started_at: NotRequired[str]
    run_id: NotRequired[str]
    handoff_trace: NotRequired[list[dict]]
    routing_usage: NotRequired[list[dict]]
    guard_retry: NotRequired[bool]
    guard_retry_deadline: NotRequired[str | None]


def build_handoff_graph(
    *,
    intent_router=None,
    transfer_model=None,
    general_model=None,
    curator_model=None,
    song_model=None,
    worldline_subgraph=None,
    provider=None,
    tool_tools=None,
    subgraphs=None,
    limits: RunLimits | None = None,
    clock: Callable[[], datetime] = utc_now,
    handoff_guard=None,
    trace_sink=None,
    transfer_journal=None,
    entry_context: EntryContextEngine | None = None,
) -> StateGraph:
    router = intent_router or configured_entry_router(LazyHybridRouter(create_hybrid_router))
    entry = entry_context or EntryContextEngine()
    bounds = limits or RunLimits()

    def prepare(state):
        raw = state["current_input"]
        text = raw.strip() if isinstance(raw, str) else raw
        context = state.get("continuity_context", {})
        return {
            "current_input": text,
            "route_decision": None,
            "final_response": None,
            "handoff_run": None,
            "transfer_envelope": None,
            "handoff_failed": False,
            "handoff_needs_input": False,
            "run_started_at": context.get("started_at") or clock().isoformat(),
            "run_id": context.get("run_id") or uuid4().hex,
            "handoff_trace": [],
            "routing_usage": [],
            "guard_retry": False,
            "guard_retry_deadline": None,
            "entry_intent": None,
            "context_manifest": {},
        }

    async def route(state) -> Command:
        question = input_question(state["current_input"])
        if question:
            # Invalid input cannot fit HandoffRun.UserInput. End before creating
            # a run, retaining the same needs-input response flags.
            return Command(goto="final_response", update={
                "handoff_needs_input": True, "final_response": question,
            })
        trace = TraceRecorder(state["run_id"], AgentKey.GENERAL_CHAT, sink=trace_sink)
        remaining = (
            bounds.run_timeout_seconds
            - (
                clock() - datetime.fromisoformat(state["run_started_at"])
            ).total_seconds()
        )
        ledger = RequestLedger(
            tuple(AgentUsage(agent=key) for key in AgentKey),
            AgentKey.GENERAL_CHAT,
            bounds,
            deadline=remaining,
            emit=trace.emit,
            run_id=state["run_id"],
        )
        try:
            with request_scope(ledger):
                ledger.check()
                ledger.check_task_budget()
                # Reuse the deployed entry router's local preflight/rule plan so
                # negation, unsupported-action handling and task spans stay intact.
                from app.agent.handoff.routing import HandoffEntryRouter
                entry_started = perf_counter()
                fast = None
                if isinstance(router, HandoffEntryRouter):
                    clauses = router.preflight.inspect(state["current_input"])
                    if clauses and not router.preflight.preflight_question(clauses):
                        fast = router.rule_plan(state["current_input"], state["routing_context"], clauses)
                elif entry_context is not None:
                    fast = entry.preflight(state)
                lite = None if fast else build_lite_pack(state)
                async with asyncio.timeout(max(0, remaining)):
                    if fast:
                        from app.agent.handoff.observability import emit
                        emit("jev.handoff_entry_local", {"decision_class": "rule", "source": "rule",
                             "attempt_class": "Single" if len(fast.tasks) == 1 else "Multi",
                             "tasks": len(fast.tasks), "v5_attempted": False,
                             "latency_ms": round((perf_counter() - entry_started) * 1000, 2)})
                        routed = fast
                    else:
                        with triage_scope(lite):
                            routed = await router.route(state["current_input"], state["routing_context"])
                ledger.check()
                decision = RouteDecision.model_validate(routed.model_dump())
                packed = await entry.complete(state, decision, lite=lite)
            run = new_run(
                state["run_id"],
                state["current_input"],
                decision,
                started_at=datetime.fromisoformat(state["run_started_at"]),
                entry_agent=AgentKey.GENERAL_CHAT,
                limits=bounds,
            )
            run = HandoffRun.model_validate(
                {**run.model_dump(), "usage": tuple(ledger.usage.values())}
            )
        except (Exception, asyncio.CancelledError) as exc:
            trace.emit(
                code="cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else ledger.blocked
                or ("time_limit" if isinstance(exc, TimeoutError) else "task_failed")
            )
            return Command(
                goto="final_response",
                update={
                    "handoff_failed": True,
                    "final_response": ledger.limit_message if ledger.blocked == "token_limit" else EXECUTION_FAILURE,
                    "handoff_trace": trace.events,
                    "routing_usage": list(ledger.usage.values()),
                },
            )
        return Command(
            goto="final_response" if run.terminal else "general_chat_agent",
            update={
                **packed,
                "route_decision": decision,
                "handoff_run": run.model_dump(mode="json"),
                "handoff_trace": trace.events,
                "routing_usage": list(ledger.usage.values()),
            },
        )

    async def agent(state, runtime: Runtime[MusicAgentContext]) -> Command:
        from app.agent.foundation.long_horizon.coordinator import active_call

        continuity_call = active_call.get()
        journal = transfer_journal or (continuity_call.coordinator.journal if continuity_call else None)
        run = HandoffRun.model_validate(state["handoff_run"])
        task = run.pending_tasks[0]
        trace = TraceRecorder(
            run.run_id,
            run.current_agent,
            events=state.get("handoff_trace", []),
            sink=trace_sink,
        )
        now = clock()
        remaining = (
            run.limits.run_timeout_seconds - (now - run.started_at).total_seconds()
        )
        node_window = min(remaining, run.limits.agent_timeout_seconds)
        if state.get("guard_retry") and state.get("guard_retry_deadline"):
            node_window = min(
                node_window,
                (
                    datetime.fromisoformat(state["guard_retry_deadline"]) - now
                ).total_seconds(),
            )
        if node_window <= 0:
            trace.emit(code="time_limit")
            stopped = finish_run(run, EXECUTION_FAILURE, reason=StopReason.TIME_LIMIT)
            return Command(
                goto="final_response",
                update={
                    "handoff_run": stopped.model_dump(mode="json"),
                    "handoff_trace": trace.events,
                },
            )
        ledger = RequestLedger(
            run.usage,
            run.current_agent,
            run.limits,
            deadline=node_window,
            emit=trace.emit,
            run_id=run.run_id,
        )
        response = None
        result = None
        command = None
        try:
            with request_scope(ledger):
                ledger.check()
                ledger.check_task_budget()
                if journal is not None:
                    await journal.await_committed(
                        _continuity_identity(state, run), run, timeout=max(0, node_window),
                        messages=state.get("messages", ()), envelope=state.get("transfer_envelope"),
                    )
                    ledger.check()
                    if continuity_call is not None:
                        await continuity_call.coordinator.before_agent(state, run)
                if ledger.remaining <= 0:
                    raise RuntimeLimitError("token_limit")
                async with asyncio.timeout(max(0, ledger.deadline - monotonic())), hop_source_scope(
                    state, run, journal=journal,
                    identity=_continuity_identity(state, run) if journal is not None else None,
                ):
                    if CAPABILITY_OWNERS[task.route_key] != run.current_agent:
                        response = await choose_transfer(
                            state,
                            run,
                            model=transfer_model,
                            feedback=state.get("guard_retry"),
                        )
                        ledger.check()
                        run = HandoffRun.model_validate(
                            {**run.model_dump(), "usage": tuple(ledger.usage.values())}
                        )
                        command = await execute_transfer(
                            response,
                            run,
                            clock=clock,
                            guard=handoff_guard or configured_guard(),
                            trace=trace,
                            journal=journal,
                            identity=(
                                _continuity_identity(state, run)
                                if journal is not None else None
                            ),
                        )
                    else:
                        result = await execute_owned_task(
                            state,
                            run,
                            runtime,
                            general_model=general_model,
                            curator_model=curator_model,
                            song_model=song_model,
                            worldline_subgraph=worldline_subgraph,
                            provider=provider,
                            tool_tools=tool_tools,
                            subgraphs=subgraphs,
                        )
                    ledger.check()
                if (
                    clock() - run.started_at
                ).total_seconds() >= run.limits.run_timeout_seconds:
                    raise RuntimeLimitError("time_limit")
                run = HandoffRun.model_validate(
                    {**run.model_dump(), "usage": tuple(ledger.usage.values())}
                )
                if command is not None:
                    snapshot = {
                        **command.update["handoff_run"],
                        "usage": tuple(ledger.usage.values()),
                    }
                    command.update["handoff_run"] = HandoffRun.model_validate(
                        snapshot
                    ).model_dump(mode="json")
                    command.update["handoff_trace"] = trace.events
                    command.update["guard_retry_deadline"] = (
                        (now + timedelta(seconds=node_window)).isoformat()
                        if command.update.get("guard_retry")
                        else None
                    )
                    return command
                result = with_combination_progress(run, result)
                updated = record_task_result(run, result)
        except TransferIntegrityConflict:
            trace.emit(code="guard_rejected")
            # Preserve the conflicting state for diagnosis; no fallback/result or retry.
            raise
        except (Exception, asyncio.CancelledError) as exc:
            run = HandoffRun.model_validate(
                {**run.model_dump(), "usage": tuple(ledger.usage.values())}
            )
            reason = (
                StopReason.CANCELLED
                if isinstance(exc, asyncio.CancelledError)
                else StopReason(exc.reason)
                if isinstance(exc, RuntimeLimitError)
                else StopReason.TIME_LIMIT
                if isinstance(exc, TimeoutError)
                else StopReason(ledger.blocked)
                if ledger.blocked is not None
                else StopReason.TASK_FAILED
            )
            trace.emit(code=reason.value)
            if CAPABILITY_OWNERS[task.route_key] != run.current_agent:
                from app.agent.handoff.tools import rejected_command

                failed = rejected_command(run, response)
                stopped = finish_run(
                    run,
                    ledger.limit_message if reason is StopReason.TOKEN_LIMIT else EXECUTION_FAILURE,
                    reason=reason
                    if reason is not StopReason.TASK_FAILED
                    else StopReason.INVALID_TRANSFER,
                )
                failed.update.update(
                    handoff_run=stopped.model_dump(mode="json"),
                    handoff_trace=trace.events,
                )
                return failed
            if result is None or result.status is not TaskStatus.FAILED:
                result = TaskResult(
                    task_id=task.task_id,
                    route_key=task.route_key,
                    status=TaskStatus.FAILED,
                    content=ledger.limit_message if reason is StopReason.TOKEN_LIMIT else EXECUTION_FAILURE,
                    error=EXECUTION_FAILURE,
                    data={"reason": reason.value},
                )
            result = with_combination_progress(run, result)
            updated = record_task_result(run, result)
            if reason is not StopReason.TASK_FAILED:
                updated = finish_run(updated, ledger.limit_message if reason is StopReason.TOKEN_LIMIT else EXECUTION_FAILURE, reason=reason)
        if result.status is TaskStatus.FAILED:
            # A bounded GeneralChat text fallback; no tools/model or new transfer.
            trace.emit(code="task_failed")
        return Command(
            goto="final_response"
            if updated.status is not RunStatus.RUNNING
            else AGENT_REGISTRY[updated.current_agent].node_name,
            update={
                "handoff_run": updated.model_dump(mode="json"),
                "task_results": [result],
                "transfer_envelope": None,
                "handoff_trace": trace.events,
                "guard_retry": False,
                "guard_retry_deadline": None,
            },
        )

    def final(state):
        raw = state.get("handoff_run")
        if raw is None:
            text = state.get("final_response") or EXECUTION_FAILURE
            return {
                "final_response": text,
                "handoff_failed": not state.get("handoff_needs_input", False),
                "messages": [AIMessage(content=text)],
            }
        run = HandoffRun.model_validate(raw)
        if len(run.plan.tasks) > 1 and (run.status in {RunStatus.FAILED, RunStatus.NEEDS_INPUT}
                                       or run.stop_reason is StopReason.TASK_FAILED):
            text = explain_combination_progress(run)
            if run.terminal:
                run = HandoffRun.model_validate({**run.model_dump(), "final_response": text})
            else:
                run = finish_run(run, text)
        if not run.terminal:
            contents = [result.content for result in run.results if result.content]
            if run.stop_reason is StopReason.TASK_FAILED:
                contents.append(
                    "部分任务执行失败，我还在这里。你可以稍后重试，或把请求拆成更小的步骤。"
                )
            run = finish_run(run, ("\n".join(contents) or "本次请求已处理。")[:10_000])
        trace = TraceRecorder(
            run.run_id,
            run.current_agent,
            events=state.get("handoff_trace", []),
            sink=trace_sink,
        )
        trace.emit(code=run.stop_reason.value)
        return {
            "handoff_run": run.model_dump(mode="json"),
            "final_response": run.final_response,
            "handoff_failed": run.status is RunStatus.FAILED,
            "handoff_needs_input": run.status is RunStatus.NEEDS_INPUT,
            "transfer_envelope": None,
            "messages": [AIMessage(content=run.final_response)],
            "handoff_trace": trace.events,
            "guard_retry": False,
            "guard_retry_deadline": None,
        }

    builder = StateGraph(HandoffGraphState, context_schema=MusicAgentContext)
    builder.add_node("prepare_context", prepare)
    builder.add_node(
        "intent_router", route, destinations=("general_chat_agent", "final_response")
    )
    for spec in AGENT_REGISTRY.values():
        builder.add_node(
            spec.node_name,
            agent,
            destinations=tuple(item.node_name for item in AGENT_REGISTRY.values())
            + ("final_response",),
        )
    builder.add_node("final_response", final)
    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", "intent_router")
    builder.add_edge("final_response", END)
    return builder


def create_handoff_graph(
    *, checkpointer=None, checkpointer_factory=create_runtime_checkpointer, **kwargs
):
    saver = checkpointer if checkpointer is not None else checkpointer_factory()
    if kwargs.get("transfer_journal") is not None:
        from app.agent.handoff.continuity import JournalCheckpointer

        saver = JournalCheckpointer(saver, kwargs["transfer_journal"])
    return build_handoff_graph(**kwargs).compile(checkpointer=saver)


def _continuity_identity(state, run):
    from app.agent.foundation.long_horizon.models import ContinuityIdentity

    context = state["continuity_context"]
    return ContinuityIdentity(
        conversation_id=context["conversation_id"], continuity_task_id=context["task_id"],
        session_id=context["session_id"], request_id=context["request_id"],
        graph_mode="handoff", run_id=run.run_id,
        route_task_id=run.pending_tasks[0].task_id,
    )

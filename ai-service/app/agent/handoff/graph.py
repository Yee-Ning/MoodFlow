"""Parent graph for the GeneralChat / Curator / Explorer business network."""

import asyncio
from collections.abc import Callable
from datetime import datetime
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
from app.agent.handoff.agents import choose_transfer, execute_owned_task
from app.agent.handoff.registry import AGENT_REGISTRY, CAPABILITY_OWNERS, AgentKey
from app.agent.handoff.state import (
    HandoffRun,
    RunLimits,
    RunStatus,
    StopReason,
    finish_run,
    new_run,
    record_task_result,
)
from app.agent.handoff.tools import execute_transfer, utc_now
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
) -> StateGraph:
    router = intent_router or create_hybrid_router()
    bounds = limits or RunLimits()

    def prepare(state):
        text = state["current_input"].strip()
        if not text or len(text) > 4_000:
            raise ValueError("invalid handoff input")
        return {
            "current_input": text,
            "route_decision": None,
            "final_response": None,
            "handoff_run": None,
            "transfer_envelope": None,
            "handoff_failed": False,
            "handoff_needs_input": False,
            "run_started_at": clock().isoformat(),
        }

    async def route(state) -> Command:
        try:
            async with asyncio.timeout(bounds.run_timeout_seconds):
                decision = RouteDecision.model_validate(
                    (
                        await router.route(
                            state["current_input"], state["routing_context"]
                        )
                    ).model_dump()
                )
            run = new_run(
                uuid4().hex,
                state["current_input"],
                decision,
                started_at=datetime.fromisoformat(state["run_started_at"]),
                entry_agent=AgentKey.GENERAL_CHAT,
                limits=bounds,
            )
        except Exception:
            return Command(
                goto="final_response",
                update={"handoff_failed": True, "final_response": EXECUTION_FAILURE},
            )
        return Command(
            goto="final_response" if run.terminal else "general_chat_agent",
            update={
                "route_decision": decision,
                "handoff_run": run.model_dump(mode="json"),
            },
        )

    async def agent(state, runtime: Runtime[MusicAgentContext]) -> Command:
        run = HandoffRun.model_validate(state["handoff_run"])
        task = run.pending_tasks[0]
        remaining = (
            run.limits.run_timeout_seconds - (clock() - run.started_at).total_seconds()
        )
        if remaining <= 0:
            stopped = finish_run(run, EXECUTION_FAILURE, reason=StopReason.TIME_LIMIT)
            return Command(
                goto="final_response",
                update={"handoff_run": stopped.model_dump(mode="json")},
            )
        try:
            async with asyncio.timeout(
                min(remaining, run.limits.agent_timeout_seconds)
            ):
                if CAPABILITY_OWNERS[task.route_key] != run.current_agent:
                    response = await choose_transfer(state, run, model=transfer_model)
                    return await execute_transfer(response, run, clock=clock)
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
                updated = record_task_result(run, result)
        except Exception:
            if CAPABILITY_OWNERS[task.route_key] != run.current_agent:
                stopped = finish_run(
                    run, EXECUTION_FAILURE, reason=StopReason.INVALID_TRANSFER
                )
                return Command(
                    goto="final_response",
                    update={"handoff_run": stopped.model_dump(mode="json")},
                )
            result = TaskResult(
                task_id=task.task_id,
                route_key=task.route_key,
                status=TaskStatus.FAILED,
                content=EXECUTION_FAILURE,
                error=EXECUTION_FAILURE,
            )
            updated = record_task_result(run, result)
        return Command(
            goto="final_response"
            if updated.status is not RunStatus.RUNNING
            else AGENT_REGISTRY[updated.current_agent].node_name,
            update={
                "handoff_run": updated.model_dump(mode="json"),
                "task_results": [result],
                "transfer_envelope": None,
            },
        )

    def final(state):
        raw = state.get("handoff_run")
        if raw is None:
            text = state.get("final_response") or EXECUTION_FAILURE
            return {
                "final_response": text,
                "handoff_failed": True,
                "messages": [AIMessage(content=text)],
            }
        run = HandoffRun.model_validate(raw)
        if not run.terminal:
            contents = [result.content for result in run.results if result.content]
            if run.stop_reason is StopReason.TASK_FAILED:
                contents.append("部分任务执行失败，请稍后重试。")
            run = finish_run(run, "\n".join(contents) or "本次请求已处理。")
        return {
            "handoff_run": run.model_dump(mode="json"),
            "final_response": run.final_response,
            "handoff_failed": run.status is RunStatus.FAILED,
            "handoff_needs_input": run.status is RunStatus.NEEDS_INPUT,
            "transfer_envelope": None,
            "messages": [AIMessage(content=run.final_response)],
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
    return build_handoff_graph(**kwargs).compile(
        checkpointer=checkpointer
        if checkpointer is not None
        else checkpointer_factory()
    )

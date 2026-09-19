"""SSE-facing runner for the Foundation MainGraph."""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent.foundation.graph_factory import create_main_graph
from app.agent.foundation.models import TaskResult
from app.agent.foundation.response import foundation_response
from app.agent.foundation.routing import RouteDecision, RoutingContext
from app.agent.foundation.state.main import create_main_state
from app.agent.foundation.subgraphs.tool_calling.models import ClientAction
from app.schemas.chat import (
    ClientActionStreamEvent,
    FinalStreamEvent,
    FoundationResponse,
    MessageStreamEvent,
    SongAssistantStreamEvent,
    UpdateStreamEvent,
)
from app.schemas.context import MusicAgentContext

FOUNDATION_UPDATE_NODES = frozenset(
    {
        "prepare_context",
        "intent_router",
        "development_route",
        "current_song_subgraph",
        "tool_calling_subgraph",
        "worldline_subgraph",
        "general_chat_subgraph",
        "aggregate_results",
        "final_response",
        "clarify",
    }
)
FOUNDATION_RECURSION_LIMIT = 40


@lru_cache
def get_foundation_graph():
    """Build the model router and checkpointer once when Foundation is enabled."""
    return create_main_graph()


def _task_results(update: Any) -> list[TaskResult]:
    if not isinstance(update, dict):
        return []
    return [
        raw if isinstance(raw, TaskResult) else TaskResult.model_validate(raw)
        for raw in update.get("task_results", [])
    ]


async def stream_foundation_assistant(
    message: str,
    thread_id: str,
    routing_context: RoutingContext,
    *,
    session_cookie: str | None = None,
    graph: Any | None = None,
) -> AsyncIterator[SongAssistantStreamEvent]:
    final_response: str | None = None
    task_results: list[TaskResult] = []
    route_decision: RouteDecision | None = None
    emitted_action_ids: set[str] = set()
    initial_state = create_main_state(
        message,
        messages=[HumanMessage(content=message)],
        routing_context=routing_context,
    )

    async for part in (graph or get_foundation_graph()).astream(
        initial_state,
        {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": FOUNDATION_RECURSION_LIMIT,
        },
        context=MusicAgentContext(session_cookie=session_cookie),
        stream_mode=["messages", "updates"],
        version="v2",
    ):
        if part["type"] == "messages":
            message_chunk, metadata = part["data"]
            if (
                metadata.get("langgraph_node") != "agent"
                or metadata.get("ls_integration") != "langchain_chat_model"
            ):
                continue
            delta = str(message_chunk.text)
            if delta:
                yield MessageStreamEvent(delta=delta)
            continue

        if part["type"] != "updates":
            continue

        for node, update in part["data"].items():
            if node not in FOUNDATION_UPDATE_NODES:
                continue
            yield UpdateStreamEvent(node=node, status="completed")

            if node in {"intent_router", "development_route"} and isinstance(update, dict):
                raw_decision = update.get("route_decision")
                if raw_decision is not None:
                    route_decision = (
                        raw_decision if isinstance(raw_decision, RouteDecision)
                        else RouteDecision.model_validate(raw_decision)
                    )

            if node.endswith("_subgraph"):
                task_results.extend(_task_results(update))

            if node == "tool_calling_subgraph":
                for result in _task_results(update):
                    for raw_action in result.data.get("client_actions", []):
                        action = ClientAction.model_validate(raw_action)
                        if action.action_id in emitted_action_ids:
                            continue
                        emitted_action_ids.add(action.action_id)
                        yield ClientActionStreamEvent(action=action)

            if node in {"final_response", "clarify"} and isinstance(update, dict):
                raw_final = update.get("final_response")
                if isinstance(raw_final, str) and raw_final.strip():
                    final_response = raw_final.strip()

    if final_response is None:
        raise RuntimeError("Foundation MainGraph returned no final response")
    yield FinalStreamEvent(
        response=foundation_response(
            final_response,
            task_results,
            needs_clarification=(
                route_decision.needs_clarification
                if route_decision is not None else False
            ),
        )
    )


async def invoke_foundation_assistant(
    message: str,
    thread_id: str,
    routing_context: RoutingContext,
    *,
    session_cookie: str | None = None,
    graph: Any | None = None,
) -> FoundationResponse:
    output = await (graph or get_foundation_graph()).ainvoke(
        create_main_state(
            message,
            messages=[HumanMessage(content=message)],
            routing_context=routing_context,
        ),
        {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": FOUNDATION_RECURSION_LIMIT,
        },
        context=MusicAgentContext(session_cookie=session_cookie),
    )
    message_text = output.get("final_response")
    if not isinstance(message_text, str) or not message_text.strip():
        raise RuntimeError("Foundation MainGraph returned no final response")
    decision = output.get("route_decision")
    if decision is not None and not isinstance(decision, RouteDecision):
        decision = RouteDecision.model_validate(decision)
    active_ids = {task.task_id for task in decision.tasks} if decision else set()
    results = [
        item if isinstance(item, TaskResult) else TaskResult.model_validate(item)
        for item in output.get("task_results", [])
        if (item.task_id if isinstance(item, TaskResult) else item.get("task_id"))
        in active_ids
    ]
    return foundation_response(
        message_text.strip(),
        results,
        needs_clarification=decision.needs_clarification if decision else False,
    )

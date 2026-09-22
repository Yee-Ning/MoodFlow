"""SSE-facing runner for the Foundation MainGraph."""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage

from app.agent.foundation.graph_factory import create_main_graph
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.runtime import get_long_horizon_service
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


def _continuity_start(thread_id: str, routing_context: RoutingContext, *, enabled: bool):
    if not enabled:
        return None, thread_id, {}, None
    service = get_long_horizon_service()
    session = service.open_conversation(thread_id)
    cold = service.cold_start(
        session.session_id,
        live_state=routing_context.model_dump(mode="json"),
    )
    context = {
        "handoff": cold.handoff.model_dump(mode="json", exclude_none=True) if cold.handoff else None,
        "key_events": [item.model_dump(mode="json", exclude_none=True) for item in cold.key_events],
        "artifacts": [
            {"ref": item.ref, "artifact_type": item.artifact_type, "projection": item.projection}
            for item in cold.artifacts
        ],
        "changed_fields": cold.changed_fields,
    }
    return service, thread_id, context, session


def _graph_config(conversation_id: str, session) -> dict[str, Any]:
    configurable = {"thread_id": conversation_id}
    if session is not None:
        # The checkpointer maps session_id to its storage namespace. checkpoint_id
        # remains a saver-managed execution snapshot and is never a task/session ID.
        configurable["session_id"] = session.session_id
    return {
        "configurable": configurable,
        "recursion_limit": FOUNDATION_RECURSION_LIMIT,
    }


def _record_result(service, session, result: TaskResult, request_id: str) -> None:
    payload = result.model_dump(mode="json", exclude_none=True)
    serialized_size = len(result.model_dump_json())
    if serialized_size > 12_000:
        artifact = service.create_artifact(
            task_id=session.task_id, session_id=session.session_id,
            artifact_type=f"task_result:{result.route_key}", payload=payload,
            projection={"task_id": result.task_id, "route_key": result.route_key,
                        "status": result.status.value, "content": result.content},
            metadata={"request_id": request_id},
        )
        service.record_event(
            task_id=session.task_id, session_id=session.session_id,
            event_type=HistoryEventType.STRUCTURED_OUTPUT,
            node=str(result.route_key), source="subgraph",
            storage_ref=artifact.ref, request_id=request_id,
        )
    else:
        service.record_event(
            task_id=session.task_id, session_id=session.session_id,
            event_type=HistoryEventType.STRUCTURED_OUTPUT,
            node=str(result.route_key), source="subgraph", payload=payload,
            request_id=request_id,
        )


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
    continuity, graph_thread_id, continuity_context, session = _continuity_start(
        thread_id, routing_context, enabled=graph is None,
    )
    request_id = f"request:{uuid4().hex}"
    if continuity is not None:
        continuity_context.update({
            "conversation_id": session.conversation_id,
            "task_id": session.task_id,
            "session_id": session.session_id,
            "request_id": request_id,
        })
        continuity.record_event(
            task_id=session.task_id, session_id=session.session_id,
            event_type=HistoryEventType.USER_MESSAGE,
            node="input", source="user", payload={"text": message}, request_id=request_id,
        )
    initial_state = create_main_state(
        message,
        messages=[HumanMessage(content=message)],
        routing_context=routing_context,
        continuity_context=continuity_context,
    )

    async for part in (graph or get_foundation_graph()).astream(
        initial_state,
        _graph_config(graph_thread_id, session),
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
                    if continuity is not None:
                        continuity.record_event(
                            task_id=session.task_id, session_id=session.session_id,
                            event_type=HistoryEventType.ROUTE_DECISION,
                            node=node, source="router",
                            payload=route_decision.model_dump(mode="json"),
                            request_id=request_id,
                        )

            if node.endswith("_subgraph"):
                new_results = _task_results(update)
                task_results.extend(new_results)
                if continuity is not None:
                    for result in new_results:
                        _record_result(continuity, session, result, request_id)

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
    if continuity is not None:
        continuity.record_event(
            task_id=session.task_id, session_id=session.session_id,
            event_type=HistoryEventType.ASSISTANT_MESSAGE,
            node="final_response", source="assistant",
            payload={"text": final_response}, request_id=request_id,
        )
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
    continuity, graph_thread_id, continuity_context, session = _continuity_start(
        thread_id, routing_context, enabled=graph is None,
    )
    request_id = f"request:{uuid4().hex}"
    if continuity is not None:
        continuity_context.update({
            "conversation_id": session.conversation_id,
            "task_id": session.task_id,
            "session_id": session.session_id,
            "request_id": request_id,
        })
        continuity.record_event(
            task_id=session.task_id, session_id=session.session_id,
            event_type=HistoryEventType.USER_MESSAGE,
            node="input", source="user", payload={"text": message}, request_id=request_id,
        )
    output = await (graph or get_foundation_graph()).ainvoke(
        create_main_state(
            message,
            messages=[HumanMessage(content=message)],
            routing_context=routing_context,
            continuity_context=continuity_context,
        ),
        _graph_config(graph_thread_id, session),
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
    if continuity is not None:
        if decision is not None:
            continuity.record_event(
                task_id=session.task_id, session_id=session.session_id,
                event_type=HistoryEventType.ROUTE_DECISION,
                node="intent_router", source="router",
                payload=decision.model_dump(mode="json"), request_id=request_id,
            )
        for result in results:
            _record_result(continuity, session, result, request_id)
        continuity.record_event(
            task_id=session.task_id, session_id=session.session_id,
            event_type=HistoryEventType.ASSISTANT_MESSAGE,
            node="final_response", source="assistant",
            payload={"text": message_text.strip()}, request_id=request_id,
        )
    return foundation_response(
        message_text.strip(),
        results,
        needs_clarification=decision.needs_clarification if decision else False,
    )

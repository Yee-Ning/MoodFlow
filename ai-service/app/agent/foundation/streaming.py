"""SSE-facing runner for the Foundation MainGraph."""

import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import HumanMessage

from app.agent.foundation.graph_factory import create_main_graph
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.runtime import get_long_horizon_service
from app.agent.memory.models import MemoryRetrievalRequest
from app.agent.memory.runtime import get_memory_runtime
from app.agent.foundation.models import TaskResult, TaskStatus
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
logger = logging.getLogger(__name__)


@lru_cache
def get_foundation_graph():
    """Build the model router and checkpointer once when Foundation is enabled."""
    return create_main_graph()


@lru_cache
def get_handoff_graph():
    from app.agent.handoff.graph import create_handoff_graph
    return create_handoff_graph()


def _public_response(message, results, *, needs_clarification=False, execution_failed=False):
    response = foundation_response(message, results, needs_clarification=needs_clarification)
    return response.model_copy(update={"status": TaskStatus.FAILED}) if execution_failed else response


def _task_results(update: Any) -> list[TaskResult]:
    if not isinstance(update, dict):
        return []
    return [
        raw if isinstance(raw, TaskResult) else TaskResult.model_validate(raw)
        for raw in update.get("task_results", [])
    ]


async def _continuity_start(
    thread_id: str,
    routing_context: RoutingContext,
    *,
    enabled: bool,
    user_id: str | None = None,
    memory_runtime: Any | None = None,
):
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


async def _retrieve_query_memories(
    *,
    context: dict[str, Any],
    user_id: str | None,
    message: str,
    enabled: bool,
    memory_runtime: Any | None,
    request_id: str | None = None,
) -> None:
    if user_id is None or (not enabled and memory_runtime is None):
        return
    try:
        memory = await (memory_runtime or get_memory_runtime()).retrieve(
            MemoryRetrievalRequest(user_id=user_id, query=message, top_k=8)
        )
        if memory.user_id != user_id:
            raise ValueError("memory retrieval user mismatch")
    except Exception:
        logger.warning(
            "Long-term memory retrieval unavailable; continuing without memory",
            exc_info=True,
        )
        return
    if memory_runtime is None:
        try:
            from app.config.settings import get_settings, jev_requested
            if jev_requested():
                settings = get_settings()
            else:
                settings = None
            if settings is not None and settings.jev_enabled and settings.jev_memory_filter_enabled:
                from app.agent.decision.jev.client import JevClient
                from app.agent.decision.jev.memory_filter import filter_memories
                memory = await filter_memories(
                    MemoryRetrievalRequest(user_id=user_id, query=message, top_k=8),
                    memory, settings=settings, client=JevClient(settings), trace_id=request_id,
                )
        except Exception as exc:
            logger.warning("jev.memory_filter_fallback error_type=%s", type(exc).__name__)
    context["long_term_memory"] = memory.context_items()
    context["user_id"] = user_id


async def _extract_memories(
    *,
    user_id: str | None,
    thread_id: str,
    user_message: str,
    enabled: bool,
    memory_runtime: Any | None,
) -> None:
    if user_id is None or (not enabled and memory_runtime is None):
        return
    try:
        await (memory_runtime or get_memory_runtime()).extract_and_store(
            user_id=user_id,
            source_thread_id=thread_id,
            user_message=user_message,
        )
    except Exception:
        logger.warning(
            "Long-term memory extraction unavailable; conversation remains successful",
            exc_info=True,
        )


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
    user_id: str | None = None,
    memory_runtime: Any | None = None,
    telemetry_sample_kind: str = "production",
    graph_mode: Literal["foundation", "handoff"] = "foundation",
) -> AsyncIterator[SongAssistantStreamEvent]:
    execution_thread_id = f"handoff:v1:{thread_id}" if graph_mode == "handoff" else thread_id
    selected_graph = graph or (get_handoff_graph() if graph_mode == "handoff" else get_foundation_graph())
    update_nodes = FOUNDATION_UPDATE_NODES | ({"general_chat_agent", "curator_agent"} if graph_mode == "handoff" else set())
    execution_failed = False
    execution_needs_input = False
    final_response: str | None = None
    task_results: list[TaskResult] = []
    route_decision: RouteDecision | None = None
    emitted_action_ids: set[str] = set()
    streamed_nodes: set[str] = set()
    visible_scope: str | None = None
    has_visible_text = False
    continuity, graph_thread_id, continuity_context, session = await _continuity_start(
        execution_thread_id,
        routing_context,
        enabled=graph is None,
        user_id=user_id,
        memory_runtime=memory_runtime,
    )
    request_id = f"request:{uuid4().hex}"
    await _retrieve_query_memories(
        context=continuity_context,
        user_id=user_id,
        message=message,
        enabled=graph is None,
        memory_runtime=memory_runtime,
        request_id=request_id,
    )
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
        continuity_context={**continuity_context, "telemetry_sample_kind": telemetry_sample_kind},
    )

    async for part in selected_graph.astream(
        initial_state,
        _graph_config(graph_thread_id, session),
        context=MusicAgentContext(session_cookie=session_cookie, user_id=user_id),
        stream_mode=["messages", "updates"],
        subgraphs=True,
        version="v2",
    ):
        if part["type"] == "messages":
            # Handoff control/business calls share Agent nodes. Publish only
            # validated parent TaskResults, never raw model/tool-call chunks.
            if graph_mode == "handoff":
                continue
            message_chunk, metadata = part["data"]
            namespace = part.get("ns", ())
            scope = namespace[0] if namespace else ""
            node = scope.split(":", 1)[0]
            if (
                metadata.get("langgraph_node") != "agent"
                or metadata.get("ls_integration") != "langchain_chat_model"
                or node not in {"current_song_subgraph", "general_chat_subgraph"}
            ):
                continue
            delta = str(message_chunk.text)
            if delta:
                if visible_scope != scope and has_visible_text:
                    yield MessageStreamEvent(delta="\n")
                visible_scope = scope
                has_visible_text = True
                streamed_nodes.add(node)
                yield MessageStreamEvent(delta=delta)
            continue

        if part["type"] != "updates":
            continue

        # Nested state updates include private prompts and tool results. Public
        # progress/actions/results are emitted only from the MainGraph boundary.
        if part.get("ns"):
            continue

        for node, update in part["data"].items():
            if node not in update_nodes:
                continue
            public_node = node
            if graph_mode == "handoff":
                if node == "general_chat_agent":
                    public_node = "general_chat_subgraph"
                elif node == "curator_agent":
                    results = _task_results(update)
                    public_node = "current_song_subgraph" if results and results[0].route_key == "current_song" else "tool_calling_subgraph"
            yield UpdateStreamEvent(node=public_node, status="completed")
            if graph_mode == "handoff" and isinstance(update, dict):
                execution_failed = bool(update.get("handoff_failed", execution_failed))
                execution_needs_input = bool(update.get("handoff_needs_input", execution_needs_input))

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

            if node.endswith("_subgraph") or (graph_mode == "handoff" and node in {"general_chat_agent", "curator_agent"}):
                new_results = _task_results(update)
                task_results.extend(new_results)
                # Tool/worldline outputs need grounding/schema validation before
                # display. Emit their public task result as soon as it is ready.
                for result in new_results:
                    if result.content and node not in streamed_nodes:
                        if has_visible_text:
                            yield MessageStreamEvent(delta="\n")
                        yield MessageStreamEvent(delta=result.content)
                        has_visible_text = True
                streamed_nodes.discard(node)
                visible_scope = None
                if continuity is not None:
                    for result in new_results:
                        _record_result(continuity, session, result, request_id)

            if node == "tool_calling_subgraph" or (graph_mode == "handoff" and node == "curator_agent"):
                for result in _task_results(update):
                    if result.route_key != "tool_calling":
                        continue
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
    await _extract_memories(
        user_id=user_id,
        thread_id=thread_id,
        user_message=message,
        enabled=graph is None,
        memory_runtime=memory_runtime,
    )
    yield FinalStreamEvent(
        response=_public_response(
            final_response,
            task_results,
            needs_clarification=(
                route_decision.needs_clarification or execution_needs_input
                if route_decision is not None else False
            ),
            execution_failed=execution_failed,
        )
    )


async def invoke_foundation_assistant(
    message: str,
    thread_id: str,
    routing_context: RoutingContext,
    *,
    session_cookie: str | None = None,
    graph: Any | None = None,
    user_id: str | None = None,
    memory_runtime: Any | None = None,
    telemetry_sample_kind: str = "production",
    graph_mode: Literal["foundation", "handoff"] = "foundation",
) -> FoundationResponse:
    execution_thread_id = f"handoff:v1:{thread_id}" if graph_mode == "handoff" else thread_id
    continuity, graph_thread_id, continuity_context, session = await _continuity_start(
        execution_thread_id,
        routing_context,
        enabled=graph is None,
        user_id=user_id,
        memory_runtime=memory_runtime,
    )
    request_id = f"request:{uuid4().hex}"
    await _retrieve_query_memories(
        context=continuity_context,
        user_id=user_id,
        message=message,
        enabled=graph is None,
        memory_runtime=memory_runtime,
        request_id=request_id,
    )
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
    selected_graph = graph or (get_handoff_graph() if graph_mode == "handoff" else get_foundation_graph())
    output = await selected_graph.ainvoke(
        create_main_state(
            message,
            messages=[HumanMessage(content=message)],
            routing_context=routing_context,
            continuity_context={**continuity_context, "telemetry_sample_kind": telemetry_sample_kind},
        ),
        _graph_config(graph_thread_id, session),
        context=MusicAgentContext(session_cookie=session_cookie, user_id=user_id),
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
    if graph_mode == "handoff" and output.get("handoff_run") is not None:
        from app.agent.handoff.state import HandoffRun
        results = list(HandoffRun.model_validate(output["handoff_run"]).results)
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
    await _extract_memories(
        user_id=user_id,
        thread_id=thread_id,
        user_message=message,
        enabled=graph is None,
        memory_runtime=memory_runtime,
    )
    return _public_response(
        message_text.strip(),
        results,
        needs_clarification=(decision.needs_clarification if decision else False) or (bool(output.get("handoff_needs_input")) if graph_mode == "handoff" else False),
        execution_failed=bool(output.get("handoff_failed", False)) if graph_mode == "handoff" else False,
    )

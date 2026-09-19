"""JSON-safe public view of Foundation task results."""

from typing import Any

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.schemas.chat import FoundationResponse, FoundationTaskOutput


_PUBLIC_METADATA_KEYS = {
    RouteKey.CURRENT_SONG: {"song", "target_source", "rag_used", "reason"},
    RouteKey.TOOL_CALLING: {"entities", "client_actions", "tool_calls", "reason"},
    RouteKey.GENERAL_CHAT: {"response_kind", "reason"},
    RouteKey.WORLDLINE: {"worldline_result", "reason"},
}


def task_output(result: TaskResult) -> FoundationTaskOutput:
    route = RouteKey(result.route_key)
    data = result.model_dump(mode="json")["data"]
    citations: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    if route is RouteKey.WORLDLINE and result.status is TaskStatus.SUCCESS:
        from app.agent.foundation.subgraphs.worldline.models import WorldLineResult

        worldline = WorldLineResult.model_validate(data["worldline_result"])
        citations = [item.model_dump(mode="json") for item in worldline.citations]
        sources = [item.model_dump(mode="json") for item in worldline.sources]
    details = data.get("tool_call_details", []) if route is RouteKey.TOOL_CALLING else []
    return FoundationTaskOutput(
        task_id=result.task_id,
        status=result.status,
        subgraph=route,
        answer=result.content,
        citations=citations,
        sources=sources,
        tool_calls=details,
        metadata={
            key: value for key, value in data.items()
            if key in _PUBLIC_METADATA_KEYS[route]
        },
    )


def foundation_response(
    message: str,
    task_results: list[TaskResult],
    *,
    needs_clarification: bool = False,
) -> FoundationResponse:
    if any(item.status is TaskStatus.FAILED for item in task_results):
        status = TaskStatus.FAILED
    elif needs_clarification or any(
        item.status is TaskStatus.NEEDS_INPUT for item in task_results
    ):
        status = TaskStatus.NEEDS_INPUT
    else:
        status = TaskStatus.SUCCESS
    return FoundationResponse(
        status=status,
        message=message,
        results=[task_output(item) for item in task_results],
    )

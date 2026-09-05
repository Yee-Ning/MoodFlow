from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RouteKey, RouteTask
from app.agent.foundation.state.main import MainState

SubGraphAdapter = Callable[[MainState], Awaitable[dict[str, list[TaskResult]]]]


def _task_for_route(state: MainState, route_key: RouteKey) -> RouteTask:
    decision = state.get("route_decision")
    if decision is None:
        raise RuntimeError("MainGraph has no route decision")

    matches = [task for task in decision.tasks if task.route_key is route_key]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one task for route {route_key.value}")
    return matches[0]


def create_subgraph_adapter(
    route_key: RouteKey,
    subgraph: CompiledStateGraph,
) -> SubGraphAdapter:
    """Isolate MainState from every SubGraph's private state shape."""

    async def invoke_subgraph(state: MainState) -> dict[str, list[TaskResult]]:
        task = _task_for_route(state, route_key)
        common_input: dict[str, Any] = {
            "messages": state["messages"],
            "current_input": state["current_input"],
            "task_id": task.task_id,
            "answer": None,
            "task_result": None,
        }

        if route_key is RouteKey.CURRENT_SONG:
            common_input.update(song_context=None, rag_context=[])
        elif route_key is RouteKey.TOOL_CALLING:
            common_input.update(tool_calls=[], tool_results=[])
        elif route_key is RouteKey.WORLDLINE:
            common_input.update(rag_context=[], worldline_plan=None)

        output = await subgraph.ainvoke(common_input)
        result = output.get("task_result")
        if not isinstance(result, TaskResult):
            result = TaskResult.model_validate(result)
        return {"task_results": [result]}

    return invoke_subgraph

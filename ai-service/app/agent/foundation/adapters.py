from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RouteKey, RouteTask
from app.agent.foundation.state.main import MainState
from app.agent.foundation.subgraphs.tool_calling.models import ReferencedEntities
from app.schemas.context import MusicAgentContext

SubGraphAdapter = Callable[
    [MainState, Runtime[MusicAgentContext]],
    Awaitable[dict[str, list[TaskResult]]],
]


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

    async def invoke_subgraph(
        state: MainState,
        runtime: Runtime[MusicAgentContext],
    ) -> dict[str, list[TaskResult]]:
        task = _task_for_route(state, route_key)
        common_input: dict[str, Any] = {
            "messages": state["messages"],
            "current_input": state["current_input"],
            "task_id": task.task_id,
            "answer": None,
            "task_result": None,
        }
        if task.intent_orders:
            decision = state["route_decision"]
            selected = [decision.intents[order] for order in task.intent_orders]
            # Each subgraph receives only the clauses assigned to this task.
            evidence = list(dict.fromkeys(item.evidence for item in selected))
            common_input["current_input"] = "；".join(evidence)
            common_input["routing_intents"] = selected

        if route_key is RouteKey.CURRENT_SONG:
            common_input.update(song_context=None, rag_context=[])
        elif route_key is RouteKey.TOOL_CALLING:
            active_task_ids = {
                item.task_id for item in state["route_decision"].tasks
            }
            prior_entities: list[ReferencedEntities] = []
            for previous in reversed(state.get("task_results", [])):
                if previous.task_id in active_task_ids:
                    continue
                raw_entities = previous.data.get("entities")
                if raw_entities:
                    prior_entities.append(
                        ReferencedEntities.model_validate(raw_entities)
                    )
                if len(prior_entities) >= 5:
                    break
            common_input.update(
                routing_context=state["routing_context"],
                prior_entities=prior_entities,
                tool_calls=[],
                tool_results=[],
                client_actions=[],
                tool_call_count=0,
                direct_complete=False,
            )
        elif route_key is RouteKey.WORLDLINE:
            common_input.update(rag_context=[], worldline_plan=None)

        output = await subgraph.ainvoke(common_input, context=runtime.context)
        result = output.get("task_result")
        if not isinstance(result, TaskResult):
            result = TaskResult.model_validate(result)
        if result.task_id != task.task_id or result.route_key != route_key:
            raise ValueError("SubGraph result does not match its assigned task")
        return {"task_results": [result]}

    return invoke_subgraph

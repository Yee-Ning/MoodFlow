from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.adapters import create_subgraph_adapter
from app.agent.foundation.models import GraphError, TaskStatus
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.agent.foundation.state.main import MainState
from app.agent.foundation.subgraphs import (
    create_current_song_subgraph,
    create_tool_calling_subgraph,
    create_worldline_subgraph,
)

SUBGRAPH_NODES = {
    RouteKey.CURRENT_SONG: "current_song_subgraph",
    RouteKey.TOOL_CALLING: "tool_calling_subgraph",
    RouteKey.WORLDLINE: "worldline_subgraph",
}


def _validate_development_routes(routes: Sequence[RouteKey]) -> tuple[RouteKey, ...]:
    normalized = tuple(RouteKey(route) for route in routes)
    if not normalized:
        raise ValueError("at least one development route is required")
    if len(normalized) != len(set(normalized)):
        raise ValueError("development routes must be unique")
    return normalized


def build_main_graph(
    *,
    development_routes: Sequence[RouteKey] = (RouteKey.CURRENT_SONG,),
    current_song_subgraph: CompiledStateGraph | None = None,
    tool_calling_subgraph: CompiledStateGraph | None = None,
    worldline_subgraph: CompiledStateGraph | None = None,
) -> StateGraph:
    """Build MainGraph V1 with explicit stub routing and SubGraph adapters."""
    routes = _validate_development_routes(development_routes)
    subgraphs = {
        RouteKey.CURRENT_SONG: (
            current_song_subgraph or create_current_song_subgraph()
        ),
        RouteKey.TOOL_CALLING: tool_calling_subgraph
        or create_tool_calling_subgraph(),
        RouteKey.WORLDLINE: worldline_subgraph or create_worldline_subgraph(),
    }

    def prepare_context(state: MainState) -> dict[str, Any]:
        current_input = state["current_input"].strip()
        if not current_input:
            raise ValueError("current_input must not be blank")
        return {"current_input": current_input, "final_response": None}

    def development_route(_: MainState) -> dict[str, RouteDecision]:
        run_id = uuid4().hex
        return {
            "route_decision": RouteDecision(
                tasks=[
                    RouteTask(
                        task_id=f"{run_id}:{route.value}",
                        route_key=route,
                    )
                    for route in routes
                ]
            )
        }

    def select_stub_subgraphs(state: MainState) -> list[str]:
        decision = state.get("route_decision")
        if decision is None:
            raise RuntimeError("development route produced no decision")
        return [SUBGRAPH_NODES[task.route_key] for task in decision.tasks]

    def aggregate_results(state: MainState) -> dict[str, list[GraphError]]:
        decision = state.get("route_decision")
        current_task_ids = (
            {task.task_id for task in decision.tasks}
            if decision is not None
            else set()
        )
        errors = [
            GraphError(
                message=result.error or "SubGraph task failed",
                task_id=result.task_id,
                route_key=result.route_key,
            )
            for result in state.get("task_results", [])
            if result.task_id in current_task_ids
            and result.status is TaskStatus.FAILED
        ]
        return {"errors": errors}

    def final_response(state: MainState) -> dict[str, str]:
        decision = state.get("route_decision")
        current_task_ids = (
            [task.task_id for task in decision.tasks]
            if decision is not None
            else []
        )
        results_by_id = {
            result.task_id: result
            for result in state.get("task_results", [])
            if result.task_id in current_task_ids
        }
        contents = [
            result.content
            for task_id in current_task_ids
            if (result := results_by_id.get(task_id)) is not None and result.content
        ]
        return {"final_response": "\n".join(contents)}

    builder = StateGraph(MainState)
    builder.add_node("prepare_context", prepare_context)
    builder.add_node("development_route", development_route)
    for route, node_name in SUBGRAPH_NODES.items():
        builder.add_node(
            node_name,
            create_subgraph_adapter(route, subgraphs[route]),
        )
    builder.add_node("aggregate_results", aggregate_results)
    builder.add_node("final_response", final_response)

    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", "development_route")
    builder.add_conditional_edges(
        "development_route",
        select_stub_subgraphs,
        {node_name: node_name for node_name in SUBGRAPH_NODES.values()},
    )
    for node_name in SUBGRAPH_NODES.values():
        builder.add_edge(node_name, "aggregate_results")
    builder.add_edge("aggregate_results", "final_response")
    builder.add_edge("final_response", END)
    return builder

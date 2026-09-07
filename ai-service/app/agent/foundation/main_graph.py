from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.adapters import create_subgraph_adapter
from app.agent.foundation.models import GraphError, TaskStatus
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask, RoutingContext
from app.agent.foundation.state.main import MainState
from app.agent.foundation.subgraphs import (
    create_current_song_subgraph,
    create_tool_calling_subgraph,
    create_worldline_subgraph,
)
from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
from app.schemas.context import MusicAgentContext

if TYPE_CHECKING:
    from app.agent.foundation.hybrid_routing import AsyncIntentRouter


def create_hybrid_router():
    # Importing RuleRouter/Foundation must not initialize the training stack.
    from app.agent.foundation.hybrid_routing import create_hybrid_router as factory
    return factory()

SUBGRAPH_NODES = {
    RouteKey.CURRENT_SONG: "current_song_subgraph",
    RouteKey.TOOL_CALLING: "tool_calling_subgraph",
    RouteKey.WORLDLINE: "worldline_subgraph",
    RouteKey.GENERAL_CHAT: "general_chat_subgraph",
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
    development_routes: Sequence[RouteKey] | None = None,
    intent_router: AsyncIntentRouter | None = None,
    current_song_subgraph: CompiledStateGraph | None = None,
    tool_calling_subgraph: CompiledStateGraph | None = None,
    worldline_subgraph: CompiledStateGraph | None = None,
    general_chat_subgraph: CompiledStateGraph | None = None,
) -> StateGraph:
    """Build dynamically routed MainGraph; explicit development routes bypass classification."""
    if development_routes is not None and intent_router is not None:
        raise ValueError("choose intent_router or development_routes, not both")
    routes = _validate_development_routes(development_routes) if development_routes is not None else None
    router = (intent_router if intent_router is not None else create_hybrid_router()) if routes is None else None
    subgraphs = {
        RouteKey.CURRENT_SONG: (
            current_song_subgraph or create_current_song_subgraph()
        ),
        RouteKey.TOOL_CALLING: tool_calling_subgraph
        or create_tool_calling_subgraph(),
        RouteKey.WORLDLINE: worldline_subgraph or create_worldline_subgraph(),
        RouteKey.GENERAL_CHAT: general_chat_subgraph or create_general_chat_subgraph(),
    }

    def prepare_context(state: MainState) -> dict[str, Any]:
        current_input = state["current_input"].strip()
        if not current_input:
            raise ValueError("current_input must not be blank")
        return {"current_input": current_input, "final_response": None, "route_decision": None}

    async def intent_route(state: MainState) -> dict[str, RouteDecision]:
        decision = await router.route(state["current_input"], state.get("routing_context", RoutingContext()))
        # Check even injected router results before dispatching anything.
        decision = RouteDecision.model_validate(decision.model_dump())
        return {"route_decision": decision}

    def select_next(state: MainState) -> str:
        decision = state["route_decision"]
        if not decision.is_complete or decision.needs_clarification or decision.needs_llm:
            return "clarify"
        completed = {result.task_id for result in state.get("task_results", [])}
        active_ids = {task.task_id for task in decision.tasks}
        if any(result.task_id in active_ids and result.status is TaskStatus.FAILED
               for result in state.get("task_results", [])):
            return "final_response"
        if any(
            result.task_id in active_ids
            and result.status is TaskStatus.NEEDS_INPUT
            for result in state.get("task_results", [])
        ):
            return "final_response"
        for task in decision.tasks:
            if task.task_id not in completed:
                return SUBGRAPH_NODES[task.route_key]
        return "final_response"

    def clarify(_: MainState) -> dict[str, str]:
        return {"final_response": "我还没能确定你希望我做什么，请补充或明确一下请求。"}

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
            and not any(error.task_id == result.task_id for error in state.get("errors", []))
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
        if any(result.status is TaskStatus.FAILED for result in results_by_id.values()):
            contents.append("部分任务执行失败，请稍后重试。")
        return {"final_response": "\n".join(contents)}

    builder = StateGraph(MainState, context_schema=MusicAgentContext)
    builder.add_node("prepare_context", prepare_context)
    routing_node = "development_route" if routes is not None else "intent_router"
    builder.add_node(routing_node, development_route if routes is not None else intent_route)
    for route, node_name in SUBGRAPH_NODES.items():
        builder.add_node(
            node_name,
            create_subgraph_adapter(route, subgraphs[route]),
        )
    builder.add_node("aggregate_results", aggregate_results)
    builder.add_node("final_response", final_response)
    if routes is None:
        builder.add_node("clarify", clarify)

    builder.add_edge(START, "prepare_context")
    builder.add_edge("prepare_context", routing_node)
    if routes is not None:
        builder.add_conditional_edges(
            routing_node, select_stub_subgraphs,
            {node_name: node_name for node_name in SUBGRAPH_NODES.values()},
        )
    else:
        destinations = {name: name for name in (*SUBGRAPH_NODES.values(), "clarify", "final_response")}
        builder.add_conditional_edges(routing_node, select_next, destinations)
    for node_name in SUBGRAPH_NODES.values():
        builder.add_edge(node_name, "aggregate_results")
    if routes is not None:
        builder.add_edge("aggregate_results", "final_response")
    else:
        builder.add_conditional_edges("aggregate_results", select_next, destinations)
        builder.add_edge("clarify", END)
    builder.add_edge("final_response", END)
    return builder

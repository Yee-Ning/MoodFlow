import operator
from typing import Annotated, Any, NotRequired

from langchain_core.messages import AnyMessage

from app.agent.foundation.models import GraphError, TaskResult
from app.agent.foundation.routing import RouteDecision, RoutingContext
from app.agent.foundation.state.base import BaseAgentState


class MainState(BaseAgentState):
    """Cross-SubGraph coordination state owned by MainGraph."""

    route_decision: RouteDecision | None
    routing_context: RoutingContext
    task_results: Annotated[list[TaskResult], operator.add]
    final_response: str | None
    errors: Annotated[list[GraphError], operator.add]
    entry_intent: NotRequired[dict[str, Any] | None]
    context_manifest: NotRequired[dict[str, Any]]
    entry_preflight: NotRequired[dict[str, Any] | None]


def create_main_state(
    current_input: str,
    *,
    messages: list[AnyMessage] | None = None,
    routing_context: RoutingContext | None = None,
    continuity_context: dict[str, Any] | None = None,
) -> MainState:
    """Build a complete initial state without adding runtime configuration."""
    return MainState(
        messages=list(messages or []),
        current_input=current_input,
        route_decision=None,
        routing_context=routing_context or RoutingContext(),
        task_results=[],
        final_response=None,
        errors=[],
        continuity_context=continuity_context or {},
    )

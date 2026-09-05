import operator
from typing import Annotated

from langchain_core.messages import AnyMessage

from app.agent.foundation.models import GraphError, TaskResult
from app.agent.foundation.routing import RouteDecision
from app.agent.foundation.state.base import BaseAgentState


class MainState(BaseAgentState):
    """Cross-SubGraph coordination state owned by MainGraph."""

    route_decision: RouteDecision | None
    task_results: Annotated[list[TaskResult], operator.add]
    final_response: str | None
    errors: Annotated[list[GraphError], operator.add]


def create_main_state(
    current_input: str,
    *,
    messages: list[AnyMessage] | None = None,
) -> MainState:
    """Build a complete initial state without adding runtime configuration."""
    return MainState(
        messages=list(messages or []),
        current_input=current_input,
        route_decision=None,
        task_results=[],
        final_response=None,
        errors=[],
    )

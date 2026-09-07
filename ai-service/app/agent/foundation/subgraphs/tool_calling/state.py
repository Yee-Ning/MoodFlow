from typing import Any

from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.state.base import BaseAgentState
from app.agent.foundation.subgraphs.tool_calling.models import (
    ClientAction,
    ReferencedEntities,
)


class ToolCallingState(BaseAgentState):
    """Private state for server tools and allow-listed browser actions."""

    task_id: str
    routing_context: RoutingContext
    prior_entities: list[ReferencedEntities]
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    client_actions: list[ClientAction]
    tool_call_count: int
    direct_complete: bool
    answer: str | None
    task_result: TaskResult | None

from typing import Any, NotRequired

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
    tool_call_counts_by_phase: NotRequired[dict[str, int]]
    tool_budget_stop: NotRequired[dict[str, Any]]
    direct_complete: bool
    answer: str | None
    task_result: TaskResult | None
    pack_run_id: NotRequired[str]
    created_playlist_receipts: NotRequired[list[dict[str, Any]]]

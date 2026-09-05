from typing import Any

from app.agent.foundation.models import TaskResult
from app.agent.foundation.state.base import BaseAgentState


class ToolCallingState(BaseAgentState):
    """Private state reserved for the future tool-calling workflow."""

    task_id: str
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    answer: str | None
    task_result: TaskResult | None

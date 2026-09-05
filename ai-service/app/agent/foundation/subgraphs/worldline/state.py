from typing import Any

from app.agent.foundation.models import TaskResult
from app.agent.foundation.state.base import BaseAgentState


class WorldLineState(BaseAgentState):
    """Private state reserved for the future world-line workflow."""

    task_id: str
    rag_context: list[dict[str, Any]]
    worldline_plan: dict[str, Any] | None
    answer: str | None
    task_result: TaskResult | None

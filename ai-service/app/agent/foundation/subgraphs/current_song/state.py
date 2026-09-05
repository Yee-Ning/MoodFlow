from typing import Any

from app.agent.foundation.models import TaskResult
from app.agent.foundation.state.base import BaseAgentState


class CurrentSongState(BaseAgentState):
    """Private state reserved for the future current-song workflow."""

    task_id: str
    song_context: dict[str, Any] | None
    rag_context: list[dict[str, Any]]
    answer: str | None
    task_result: TaskResult | None

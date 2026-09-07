from enum import StrEnum
from typing import Any

from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.state.base import BaseAgentState
from app.schemas.context import SongContext


class SongTargetSource(StrEnum):
    PLAYER_CONTEXT = "player_context"
    PRIOR_PLAY_ACTION = "prior_play_action"


class CurrentSongState(BaseAgentState):
    """Private state for resolving and analyzing one selected song."""

    task_id: str
    routing_context: RoutingContext
    prior_task_results: list[TaskResult]
    target_song_id: int | None
    target_source: SongTargetSource | None
    song_context: SongContext | None
    rag_context: list[dict[str, Any]]
    answer: str | None
    task_result: TaskResult | None

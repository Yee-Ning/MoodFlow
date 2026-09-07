from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RoutingContext
from app.agent.foundation.state.base import BaseAgentState


class GeneralChatState(BaseAgentState):
    """Private state for one general-chat task."""

    task_id: str
    routing_context: RoutingContext
    prior_task_results: list[TaskResult]
    answer: str | None
    task_result: TaskResult | None

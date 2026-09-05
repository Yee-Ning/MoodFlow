from typing import TypedDict

from app.agent.foundation.models import TaskResult


class SubGraphOutput(TypedDict):
    """The only SubGraph output shape consumed by a MainGraph adapter."""

    task_result: TaskResult

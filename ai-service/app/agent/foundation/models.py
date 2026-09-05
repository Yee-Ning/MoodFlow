from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class TaskResult(BaseModel):
    """Stable output contract from any SubGraph back to MainGraph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    route_key: str = Field(min_length=1, max_length=64)
    status: TaskStatus
    content: str | None = Field(default=None, max_length=10_000)
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "TaskResult":
        if self.status is TaskStatus.SUCCESS and self.error is not None:
            raise ValueError("successful task result cannot contain an error")
        if self.status is TaskStatus.FAILED and not self.error:
            raise ValueError("failed task result must contain an error")
        return self


class GraphError(BaseModel):
    """Structured, append-only error reported while coordinating graph tasks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str = Field(min_length=1, max_length=2_000)
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    route_key: str | None = Field(default=None, min_length=1, max_length=64)

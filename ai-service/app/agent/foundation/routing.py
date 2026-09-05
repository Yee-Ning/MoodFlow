from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RouteKey(StrEnum):
    CURRENT_SONG = "current_song"
    TOOL_CALLING = "tool_calling"
    WORLDLINE = "worldline"


class RouteTask(BaseModel):
    """One future router-produced unit of work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    route_key: RouteKey


class RouteDecision(BaseModel):
    """Extensible multi-task routing contract; no classification logic lives here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tasks: list[RouteTask] = Field(min_length=1)

    @model_validator(mode="after")
    def task_ids_must_be_unique(self) -> "RouteDecision":
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("route task ids must be unique")
        return self

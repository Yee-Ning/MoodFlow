"""Scoped public references shared by Pack observability and Journal readers."""

from pydantic import BaseModel, ConfigDict, Field


class EventPointer(BaseModel):
    """Offset locates, never alters, the complete append-only fact."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=160)
    offset: int | None = Field(default=None, ge=0, strict=True)

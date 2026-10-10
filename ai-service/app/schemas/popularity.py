"""Strict contract for the new read-only comment summary endpoint."""

from pydantic import BaseModel, ConfigDict, Field


class SongCommentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    song_id: int = Field(alias="songId", strict=True, gt=0)
    comment_count: int = Field(alias="commentCount", strict=True, ge=0)


class SpringSongCommentSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int = Field(strict=True)
    message: str
    data: SongCommentSummary

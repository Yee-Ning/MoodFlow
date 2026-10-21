"""Strict contract for the new read-only comment summary endpoint."""

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class SongCommentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    song_id: int = Field(alias="songId", strict=True, gt=0)
    comment_count: int = Field(alias="commentCount", strict=True, ge=0)


class SpringSongCommentSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int = Field(strict=True)
    message: str
    data: SongCommentSummary


SongId = Annotated[int, Field(strict=True, gt=0)]
COMMENT_TOOL_BATCH_LIMIT = 12
COMMENT_TOOL_BATCH_GUIDANCE = "单次最多 12 个，请分多轮调用"


def _comment_batch_size(value):
    if isinstance(value, (list, tuple, set, frozenset)) and len(value) > COMMENT_TOOL_BATCH_LIMIT:
        raise PydanticCustomError("comment_tool_batch_limit", COMMENT_TOOL_BATCH_GUIDANCE)
    return value


class SongPopularityArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_ids: Annotated[list[SongId], BeforeValidator(_comment_batch_size)] = Field(
        min_length=1, max_length=COMMENT_TOOL_BATCH_LIMIT,
        description="来自本轮目录工具或可信历史目录结果的歌曲整数 ID；单次最多 12 个，请分多轮调用；不要猜测ID。")


class SongPopularityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_id: SongId
    status: Literal["known", "unknown"]
    comment_count: int | None = Field(strict=True, ge=0)
    eligible: bool | None = Field(strict=True)

    @model_validator(mode="after")
    def known_or_unknown(self):
        if self.status == "unknown":
            if self.comment_count is not None or self.eligible is not None:
                raise ValueError("unknown totals have no count or eligibility")
        elif self.comment_count is None or self.eligible is None:
            raise ValueError("known totals require count and eligibility")
        return self


class SongPopularityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal["comment_count"] = "comment_count"
    source: Literal["netease_comment_total"] = "netease_comment_total"
    cache_ttl_seconds: Literal[604800] = 604800
    min_comments: int = Field(strict=True, ge=0)
    max_comments: int = Field(strict=True, ge=0)
    songs: list[SongPopularityItem] = Field(min_length=1, max_length=60)
    qualified_song_ids: list[SongId] = Field(max_length=60)
    rejected_song_ids: list[SongId] = Field(max_length=60)
    unknown_song_ids: list[SongId] = Field(max_length=60)
    message: str = Field(min_length=1, max_length=1000)
    proxy_note: Literal["评论数仅为热度代理，不是播放量。"] = "评论数仅为热度代理，不是播放量。"

    @model_validator(mode="after")
    def policy_and_partitions(self):
        from app.agent.handoff.niche import get_niche_config, rank_niche

        policy = get_niche_config()
        if (self.min_comments, self.max_comments) != (policy.min_comments, policy.max_comments):
            raise ValueError("result must use the existing niche policy")
        ids = [row.song_id for row in self.songs]
        if len(ids) != len(set(ids)):
            raise ValueError("result song IDs must be unique")
        qualified = [row["song_id"] for row in rank_niche(
            [row.model_dump() for row in self.songs], len(ids), config=policy)]
        rejected = [row.song_id for row in self.songs if row.status == "known" and row.song_id not in qualified]
        unknown = [row.song_id for row in self.songs if row.status == "unknown"]
        if (self.qualified_song_ids, self.rejected_song_ids, self.unknown_song_ids) != (qualified, rejected, unknown):
            raise ValueError("result partitions must match existing classification")
        if any(row.status == "known" and row.eligible != (row.song_id in qualified) for row in self.songs):
            raise ValueError("eligibility must match existing classification")
        return self

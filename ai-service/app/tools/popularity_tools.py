"""Expose existing comment reads and niche classification, without new algorithms."""
from langchain_core.tools import tool

from app.agent.handoff.niche import get_niche_config, rank_niche
from app.schemas.popularity import SongPopularityArgs, SongPopularityItem, SongPopularityResult


@tool("get_song_popularity_tool", args_schema=SongPopularityArgs, description=(
    "只读核实网易云歌曲评论总数。冷门/小众推荐必须先调用此工具核实候选歌曲，"
    "输入来自目录搜索或可信历史目录结果的 song_ids，不得猜测ID。"
    "返回实际评论数、按现有NicheConfig判断的区间内ID（按评论数升序）、区间外ID与unknown ID。"
    "评论数仅为热度代理，不是播放量；未知不当0，不参与区间判断。"
    "只推荐有实际评论数且区间内的ID；空结果诚实说明无合格歌曲，不凑数。"
    "阈值由服务端策略决定，不接受阈值参数。沿用7天Redis派生缓存，不能声称数据是即时热度。"
    "不建歌单、不播放、不修改偏好。"
    "单次不超过 12 个 ID；候选多于 12 个时请分多轮调用，每轮结果独立可用。"
))
async def get_song_popularity_tool(song_ids: list[int]) -> str:
    # Lazy import keeps the music registry and existing HTTP client acyclic.
    from app.tools.popularity import get_song_popularity

    counts = await get_song_popularity(song_ids)
    ids = list(dict.fromkeys(song_ids))
    policy = get_niche_config()
    ranked = rank_niche([{"song_id": identity, "comment_count": counts.get(identity)} for identity in ids],
                        len(ids), config=policy)
    qualified = [row["song_id"] for row in ranked]
    known = [identity for identity in ids if identity in counts]
    unknown = [identity for identity in ids if identity not in counts]
    message = (
        f"已核实{len(known)}首的评论总数，{len(qualified)}首满足{policy.min_comments}–{policy.max_comments}区间；"
        "仅推荐区间内且评论数已核实的歌曲，不足所需数量就少荐。"
        if qualified else
        f"当前候选没有满足{policy.min_comments}–{policy.max_comments}评论数区间的已核实歌曲；"
        "返回空集，未知评论数未当作0，不放宽区间或凑数。"
    )
    result = SongPopularityResult(min_comments=policy.min_comments, max_comments=policy.max_comments,
        songs=[SongPopularityItem(song_id=identity, status="known" if identity in counts else "unknown",
            comment_count=counts.get(identity), eligible=(identity in qualified) if identity in counts else None)
            for identity in ids], qualified_song_ids=qualified,
        rejected_song_ids=[identity for identity in known if identity not in qualified],
        unknown_song_ids=unknown, message=message)
    return result.model_dump_json()

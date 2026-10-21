"""Finish bounded, read-only niche recommendations from verified tool receipts."""

from hashlib import sha256
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.tool_calling.models import ReferencedEntities, SongReference
from app.agent.handoff.niche import get_niche_config, rank_niche
from app.schemas.popularity import SongPopularityResult


class NicheReadback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, strict=True, ge=1, le=1)
    source_task_id: str = Field(min_length=1, max_length=128)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    limit: int = Field(strict=True, ge=1, le=12)
    verifications: list[SongPopularityResult] = Field(min_length=1, max_length=8)
    catalogue: list[SongReference] = Field(max_length=600)


def niche_read_limit(text: str) -> int | None:
    """A completion rule only; never authorizes effects or changes route/intent."""
    if not re.search(r"冷门|小众|非热门", text) or "推荐" not in text:
        return None
    match = re.search(r"(?:最多|至多|不超过)\s*(\d{1,2})\s*首", text)
    if match is None or not 1 <= int(match[1]) <= 12:
        return None
    if not re.search(r"只推荐|仅推荐", text):
        return None
    if re.search(r"歌词|分析|探索|世界线|比较|对比|全部核实|逐一核实|必须|至少|恰好|继续找|再找|"
                 r"另(?:外)?(?:再)?推荐|再推荐|也推荐|分别|两组|每组|第二(?:组|批)", text):
        return None
    # Keep compound/positive player or library requests on their existing path.
    for clause in re.split(r"[,，。;；!！?？]|然后|接着|随后|并且|同时|以及|但是|不过|但", text):
        for effect in re.finditer(r"播放|建单|创建|新建|添加|加入|收藏|红心|删除|移除|队列|音量", clause):
            if not re.search(r"(?:不要|不用|无需|别|不)[^,，。;；]{0,10}$", clause[:effect.start()]):
                return None
    bounds = re.search(r"评论(?:总数|数).{0,3}?(\d+)\s*(?:到|至|[–—-])\s*(\d+)", text)
    policy = get_niche_config()
    if bounds and (int(bounds[1]), int(bounds[2])) != (policy.min_comments, policy.max_comments):
        return None
    return int(match[1])


def _label(value: str) -> str:
    value = " ".join(value.split())
    return value if len(value) <= 50 else value[:49] + "…"


def render_niche_readback(receipt: NicheReadback) -> tuple[str, ReferencedEntities] | None:
    counts = {row.song_id: row.comment_count for result in receipt.verifications for row in result.songs}
    catalogue = {row.song_id: row for row in receipt.catalogue}
    ranked = rank_niche([{"song_id": song_id, "comment_count": count} for song_id, count in counts.items()],
                        receipt.limit, config=get_niche_config())
    # A comment receipt proves popularity, never a missing song name or artist.
    if any(row["song_id"] not in catalogue or not catalogue[row["song_id"]].artists for row in ranked):
        return None
    selected = [catalogue[row["song_id"]].model_copy(update={"position": i + 1}) for i, row in enumerate(ranked)]
    known = sum(count is not None for count in counts.values())
    unknown = len(counts) - known
    if selected:
        lines = [f"本次已核实{known}首候选的评论总数，推荐{len(selected)}首："]
        lines.extend(f"{i + 1}. 《{_label(song.name)}》—{_label('、'.join(song.artists))}；"
                     f"song_id={song.song_id}；评论总数{counts[song.song_id]}。" for i, song in enumerate(selected))
        if len(selected) < receipt.limit:
            lines.append("已核实候选中合格歌曲不足所需上限，按要求少荐，不继续搜歌凑数。")
    else:
        policy = get_niche_config()
        lines = [f"本次已核实候选中没有满足{policy.min_comments}–{policy.max_comments}评论数区间的歌曲，返回空集，不凑数。"]
    if unknown:
        lines.append(f"另有{unknown}首评论数未知，未当作0，也未参与区间判断。")
    lines.append("评论数仅为热度代理，不是播放量；沿用7天派生缓存口径，不能视为即时热度。")
    return "\n".join(lines), ReferencedEntities(songs=selected)


def finish_niche_readback(request: str, task_id: str, records: list[dict], catalogue: list[SongReference]):
    limit = niche_read_limit(request)
    if limit is None:
        return None
    verifications = []
    for record in records:
        if record.get("name") != "get_song_popularity_tool" or record.get("status") != "success":
            continue
        try:
            verifications.append(SongPopularityResult.model_validate_json(record["content"]))
        except (ValidationError, KeyError, TypeError):
            return None
    if not verifications:
        return None
    receipt = NicheReadback(source_task_id=task_id, request_sha256=sha256(request.encode()).hexdigest(),
                           limit=limit, verifications=verifications, catalogue=catalogue)
    rendered = render_niche_readback(receipt)
    return (*rendered, receipt) if rendered else None


def reuse_niche_readback(run) -> TaskResult | None:
    """Only consume this run's server-built receipt for later constraint clauses."""
    limit = niche_read_limit(run.original_input)
    task = run.pending_tasks[0]
    if limit is None or task.route_key is not RouteKey.TOOL_CALLING:
        return None
    if not re.match(r"^(?:只推荐|仅推荐|给出|不足\s*\d+\s*首|不要用热门歌充数)", task.input_text.strip()):
        return None
    for source in reversed(run.results):
        if source.status is not TaskStatus.SUCCESS or source.data.get("client_actions"):
            continue
        try:
            receipt = NicheReadback.model_validate(source.data.get("niche_readback"))
        except ValidationError:
            continue
        if (receipt.source_task_id != source.task_id or receipt.limit != limit
                or receipt.request_sha256 != sha256(run.original_input.encode()).hexdigest()):
            continue
        rendered = render_niche_readback(receipt)
        if rendered is None:
            return None
        _, entities = rendered
        return TaskResult(task_id=task.task_id, route_key=task.route_key, status=TaskStatus.SUCCESS,
            content="已沿用本轮核实回执：只保留评论数在服务端区间内的歌曲；不足上限就少荐，未知不当0，不凑数。",
            data={"entities": entities.model_dump(mode="json"), "client_actions": [], "tool_calls": 0,
                  "tool_call_counts_by_phase": {"retrieval": 0, "execution": 0}, "tool_call_details": [],
                  "niche_readback": receipt.model_dump(mode="json"), "niche_readback_reused": True})
    return None

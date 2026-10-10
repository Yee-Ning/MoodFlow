"""Bounded scene curation inside Curator; catalog IDs and A own all effects."""

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from typing import Annotated

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.agent.foundation.long_horizon.execution import ContinuityConflict
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.operations import (
    active_operations,
    journal_tool_call,
)
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.general_chat.results import ResultFact, ResultState
from app.agent.foundation.subgraphs.tool_calling.graph import _reference_errors
from app.agent.foundation.subgraphs.tool_calling.models import (
    PlaylistReference,
    ReferencedEntities,
    SongReference,
)
from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState
from app.agent.handoff.curator_context import prepare_curation_pack
from app.agent.handoff.curator_prompts import PLAN_PROMPT, SELECTION_PROMPT
from app.schemas.context import MusicAgentContext
from app.schemas.music import (
    AddSongToPlaylistResult,
    CreatedPlaylistResult,
    LikedSongsResult,
    MusicSearchResult,
)
from app.tools.music import handle_music_tool_error
from app.agent.handoff.curator_knowledge import Discovery, get_curator_knowledge_service


CURATION_TOOLS = frozenset(
    {"search_music", "get_liked_songs", "create_playlist_tool", "add_song_to_playlist"}
)
MAX_CURATION_CALLS = 49  # v1's 25 calls plus at most 24 knowledge hint searches
Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class SearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    queries: list[Text] = Field(min_length=1, max_length=3)
    tags: list[Text] = Field(min_length=1, max_length=6)


class SongSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    song_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(max_length=20)


@dataclass(frozen=True)
class CurationRequest:
    scene: str
    name: str
    count: int = 15
    privacy: int = 0
    decade: int | None = None
    obscure: bool = False


def _direct_playlist_request(text: str) -> bool:
    """Classify only; the original tool chain still owns authorization and effects."""
    if re.search(r"策展|策划|生成|编排|搭配", text):
        return False
    # Named add/remove/delete clauses are operations, even if their target title
    # contains mood/scene words. Returning None never executes or authorizes them.
    if "歌单" in text and re.search(r"加入|添加|加歌|删歌|删去|删除|移除|移出", text):
        return True
    # In "create [privacy] playlist <title>", the trailing title is an explicit
    # target, not a scene. A quantity or purpose clause instead needs classification.
    named = re.search(
        r"(?:创建|新建|建立|建)(?:一张|一个|一份)?\s*(?:私密|私人|公开)?\s*歌单\s*(.+)",
        text,
    )
    if named and not re.search(r"(?:\d+|[一二三四五六七八九十百]+)\s*首", text):
        title = named[1].strip(" ：:，,。！!？?")
        if title and not re.match(r"用于|适合|用来|根据|围绕|按照|按(?:场景|情绪|年代)", title):
            return True
    return False


def curation_request(text: str) -> CurationRequest | None:
    """Recognize assigned creation clauses only; history/model output grants no authority."""
    text = text.strip()
    obscure = bool(re.search(r"冷门|小众|非热门", text))
    # A preference suffix still belongs to the explicit playlist creation clause.
    text = re.sub(
        r"[，, ]*(?:要|选|来)?(?:冷门|小众|非热门)(?:一点|些|一些)?[。！!]*$", "", text
    ).strip()
    if _direct_playlist_request(text):
        return None
    if re.search(
        r"别|不要|不用|无需|先不|不(?:创建|新建|保存|生成|执行|加歌|写入)|仅推荐|只推荐|再决定|可以吗|取消|是否|能否|假如|如果|假设|搜索|查找|查看|介绍|分析|播放|收藏|删除|移除|已有|现有|名叫|命名|叫做|状态|完成了吗",
        text,
    ):
        return None
    known_scene = bool(
        re.search(
            r"雨.{0,8}(?:夜跑|跑步)|(?:90|九十|1990)\s*年代.{0,8}(?:港乐|粤语)|深夜.{0,8}加班",
            text,
        )
    )
    # Creation alone is not curation. Require scene/mood/era evidence in the
    # assigned clause, or an explicit request to generate/curate a selection.
    curation_signal = bool(re.search(
        r"场景|情绪|心情|氛围|雨天|雨夜|夜跑|跑步|运动|健身|加班|通勤|学习|"
        r"睡前|助眠|旅行|深夜|早晨|年代|港乐|粤语|开心|伤心|难过|放松|提神",
        text,
    ))
    if not curation_signal and not re.search(r"策展|策划|生成|编排|搭配|推荐", text):
        return None
    explicit_request = bool(
        re.search(r"(?:生成|创建|新建|策展|策划|做|推荐|安排|来).{2,40}歌单", text)
    ) or bool(re.match(r"^(?:请|麻烦)?(?:给我|帮我|我想要|来).{2,40}歌单", text))
    bare_scene = bool(
        re.fullmatch(
            r"(?:雨.{0,8}(?:夜跑|跑步)|(?:90|九十|1990)\s*年代.{0,8}(?:港乐|粤语)|深夜.{0,8}加班(?:提神)?)"
            r"(?:歌单)?(?:[，, ]*\d+\s*首)?[。！!]*",
            text,
        )
    )
    bare_playlist = bool(re.fullmatch(r".{2,30}歌单[。！!]*", text)) and not re.search(
        r"我|你|有|是|觉得|喜欢|听过|不错|很好|已经|之前|刚才|什么|怎么样|[？?]", text
    )
    if not explicit_request and not (bare_scene if known_scene else bare_playlist):
        return None
    # Explicit empty-playlist requests belong to the existing execution path.
    if re.search(r"空歌单|不加歌|只.{0,3}(?:新建|创建)", text):
        return None
    scene = re.sub(
        r"^(?:请|麻烦)?(?:帮我|给我|我想要)?(?:生成|创建|新建|策展|策划|推荐|做|安排|来)?(?:一张|一个|一份|张|个|份)?",
        "",
        text,
    )
    scene = re.sub(
        r"(?:歌单)?(?:[，, ]*(?:包含|选|加|加入)?\s*\d+\s*首.*)?[。！!]*$", "", scene
    ).strip()
    scene = re.sub(r"(?:公开|私密|私人)", "", scene).strip()
    if not scene:
        return None
    count_match = re.search(r"(?<!\d)(\d+)\s*首", text)
    count = int(count_match[1]) if count_match else 15
    era = re.search(r"(?:(19\d0|20\d0)|(\d{2}))\s*年代|九十年代", text)
    decade = None
    if era:
        decade = (
            int(era[1])
            if era[1]
            else (
                1900 + int(era[2])
                if era[2] and int(era[2]) >= 30
                else 2000 + int(era[2])
                if era[2]
                else 1990
            )
        )
    return CurationRequest(
        scene=scene[:100],
        name=(scene + "歌单")[:40],
        count=count,
        privacy=10 if re.search(r"私密|私人", text) else 0,
        decade=decade,
        obscure=obscure,
    )


async def _structured(model, text, prompt, schema, payload, continuity):
    pack, accounted, visible = prepare_curation_pack(text, prompt, payload, continuity, model)
    response = await accounted.ainvoke(pack.messages)
    if (
        not isinstance(response, AIMessage)
        or response.tool_calls
        or response.invalid_tool_calls
    ):
        raise ValueError("curation model must return structured data without tools")
    result = schema.model_validate_json(response.text)
    if schema is SongSelection and not set(result.song_ids) <= visible:
        raise ValueError("selection contains IDs outside the budget-selected catalog")
    return result


def _catalog(content, keyword):
    raw = json.loads(content)
    if not isinstance(raw, dict) or not isinstance(raw.get("songs"), list):
        raise ValueError("catalog result must be an object with songs")
    # MusicTrack's compatibility aliases/coercion must not create a trusted ID.
    for song in raw["songs"]:
        if (
            not isinstance(song, dict)
            or type(song.get("song_id", song.get("id"))) is not int
        ):
            raise ValueError("catalog IDs must be integers")
    parsed = MusicSearchResult.model_validate(raw)
    if (
        parsed.keyword != keyword
        or parsed.limit != 50
        or len(parsed.songs) > parsed.limit
    ):
        raise ValueError("catalog result does not match search")
    for song in parsed.songs:
        SongReference(position=1, **song.model_dump())
    return parsed.songs


def _operation_fact(operations, name, args):
    proposals = [
        row
        for row in operations.write_events()
        if row.event_type is HistoryEventType.TOOL_CALL
        and row.payload["name"] == name
        and row.payload["parameters"] == args
    ]
    if not proposals:
        raise ContinuityConflict("curation write has no journal proposal")
    view = operations.state(proposals[-1].payload["operation_id"])
    if view["state"] == "confirmed" and view["confirmation_source"] == "readback":
        fact = ResultFact(
            name, ResultState.CONFIRMED, "operation_journal", view["operation_id"]
        )
    elif view["state"] == "unknown":
        fact = ResultFact(
            name, ResultState.UNKNOWN, "operation_journal", view["operation_id"]
        )
    else:
        detail = (
            "后端已明确拒绝操作，没有成功回执。"
            if view["state"] == "failed"
            else "已有后端成功回执，未另行只读核对；客户端执行状态需另查 ACK。"
        )
        fact = ResultFact(name, None, "operation_journal", view["operation_id"], detail)
    return view, fact


def introduction(request, selected, liked, discovery_ids=()):
    """Only actual selections enter the draft reserved for future GeneralChat polish."""
    names = "、".join(f"《{song.name}》" for song in selected[:3])
    preference = (
        "适配候选中优先安排了你的红心曲目"
        if any(song.song_id in liked for song in selected)
        else "这次选曲以场景适配为主"
    )
    era = "先依据经验证的歌曲年代证据限定候选，再" if request.decade is not None else ""
    discoveries = [song for song in selected if song.song_id in discovery_ids]
    depth = (
        "知识关联补充了初轮目录搜索未出现的"
        + "、".join(f"《{song.name}》" for song in discoveries[:2])
        + "；这是发现路径的补充，未核实平台热度。"
        if discoveries
        else ""
    )
    return (
        f"这份选曲面向“{request.scene}”，从真实目录搜索候选中，{era}参考歌名、歌手和专辑评估适配。"
        f"{preference}，选入{names}等{len(selected)}首；场景适配是此次策展判断。{depth}"
    )


async def execute_curation(task, state, runtime, *, request, model, tools):
    """Fixed read/select/create/add workflow; never retries an uncertain write."""
    records, facts, selected, accepted, warnings = [], [], [], [], []
    candidates, sources, conflicted = {}, {}, set()
    liked, playlist, plan = set(), None, None
    knowledge_sources, discovery_ids, initial_ids = {}, set(), set()
    discovery = Discovery()
    operations = active_operations.get()

    def finish(message, status=TaskStatus.SUCCESS, reason=None):
        draft = (
            introduction(request, accepted, liked, discovery_ids) if accepted else None
        )
        if draft:
            message += "\n" + draft
        entities = ReferencedEntities(
            songs=[
                SongReference(position=i + 1, **song.model_dump())
                for i, song in enumerate(accepted)
            ],
            playlists=[
                PlaylistReference(
                    position=1,
                    playlist_id=playlist.playlist_id,
                    name=playlist.name,
                    editable=True,
                )
            ]
            if playlist
            else [],
        )
        return TaskResult(
            task_id=task.task_id,
            route_key=RouteKey.TOOL_CALLING,
            status=status,
            content=message[:10_000],
            error=message[:2_000] if status is TaskStatus.FAILED else None,
            data={
                "curation": {
                    "scene": request.scene,
                    "target_count": request.count,
                    "search_plan": plan.model_dump() if plan else None,
                    "selected_songs": [song.model_dump() for song in selected],
                    "accepted_song_ids": [song.song_id for song in accepted],
                    "pending_song_ids": [
                        song.song_id for song in selected if song not in accepted
                    ],
                    "catalog_sources": sources,
                    "knowledge_sources": knowledge_sources,
                    "knowledge_traces": discovery.traces,
                    "discovered_song_ids": sorted(discovery_ids),
                    "selected_discovery_ids": [
                        song.song_id
                        for song in selected
                        if song.song_id in discovery_ids
                    ],
                    "playlist": playlist.model_dump() if playlist else None,
                    "introduction": draft,
                    "introduction_song_ids": list(
                        dict.fromkeys(
                            [song.song_id for song in accepted[:3]]
                            + [
                                song.song_id
                                for song in accepted
                                if song.song_id in discovery_ids
                            ][:2]
                        )
                    ),
                    "polish_request": {
                        "schema_version": 1,
                        "draft": draft,
                        "songs": [song.model_dump() for song in accepted],
                        "result_facts": [asdict(f) for f in facts],
                    }
                    if draft
                    else None,
                    "warnings": warnings,
                },
                "entities": entities.model_dump(mode="json"),
                "client_actions": [],
                "result_facts": [asdict(f) for f in facts],
                "tool_calls": len(records),
                "tool_call_details": [
                    {"name": row["name"], "status": row["status"]} for row in records
                ],
                "reason": reason,
            },
        )

    if not 10 <= request.count <= 20:
        return finish(
            "这轮场景歌单支持 10–20 首，请告诉我这个范围内的数量。",
            TaskStatus.NEEDS_INPUT,
            "count_out_of_range",
        )
    if operations is None:
        return finish(
            "缺少可核对的写操作记账，尚未创建歌单或添加歌曲。",
            TaskStatus.FAILED,
            "journal_required",
        )
    scoped = [tool for tool in tools if tool.name in CURATION_TOOLS]
    if {tool.name for tool in scoped} != CURATION_TOOLS:
        return finish(
            "策展需要的现有工具不可用，尚未创建歌单。",
            TaskStatus.FAILED,
            "missing_tools",
        )
    builder = StateGraph(ToolCallingState, context_schema=MusicAgentContext)
    builder.add_node(
        "tools",
        ToolNode(
            scoped,
            handle_tool_errors=handle_music_tool_error,
            awrap_tool_call=journal_tool_call,
        ),
    )
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    executor = builder.compile(checkpointer=False)

    def collect(songs, query):
        for song in songs:
            if song.song_id in conflicted:
                continue
            if song.song_id in candidates and candidates[song.song_id] != song:
                candidates.pop(song.song_id)
                sources.pop(str(song.song_id), None)
                knowledge_sources.pop(str(song.song_id), None)
                conflicted.add(song.song_id)
                continue
            candidates[song.song_id] = song
            sources.setdefault(str(song.song_id), []).append(
                {"tool": "search_music", "keyword": query}
            )

    async def invoke(name, args):
        if len(records) >= MAX_CURATION_CALLS:
            raise ValueError("curation call budget exhausted")
        call = {
            "name": name,
            "args": args,
            "id": f"{task.task_id}:curation:{len(records)}",
            "type": "tool_call",
        }
        tool_state = {
            "messages": [AIMessage(content="", tool_calls=[call])],
            "current_input": task.input_text,
            "routing_context": state["routing_context"],
            "prior_entities": [],
            "tool_results": records,
        }
        if _reference_errors(tool_state):
            raise ValueError("curation target has no catalog reference")
        output = await executor.ainvoke(
            tool_state, context=runtime.context, durability="async"
        )
        response = output["messages"][-1]
        records.append(
            {
                "name": name,
                "content": response.content,
                "status": response.status,
                "tool_call_id": call["id"],
            }
        )
        if name in {"create_playlist_tool", "add_song_to_playlist"}:
            view, fact = _operation_fact(operations, name, args)
            facts.append(fact)
            if view["state"] in {"unknown", "failed"}:
                raise ValueError("write not receipted")
        if response.status != "success":
            raise ValueError("tool failed")
        return response.content

    try:
        # A missing preference read does not license guesses about user taste.
        try:
            liked = set(
                LikedSongsResult.model_validate_json(
                    await invoke("get_liked_songs", {})
                ).song_ids
            )
        except (ValueError, KeyError, TypeError):
            warnings.append("红心历史不可用，本次未按红心偏好排序。")
        plan = await _structured(
            model,
            task.input_text,
            PLAN_PROMPT,
            SearchPlan,
            {"scene": request.scene},
            state.get("continuity_context"),
        )
        for query in dict.fromkeys(plan.queries):
            try:
                songs = _catalog(
                    await invoke("search_music", {"keyword": query, "limit": 50}), query
                )
            except (ValueError, KeyError, TypeError):
                warnings.append("一次目录搜索失败或返回无效数据，未使用其候选。")
                continue
            collect(songs, query)
        initial_ids = set(candidates)
        try:
            discovery = await asyncio.wait_for(
                get_curator_knowledge_service().discover(
                    request, plan.tags, list(candidates.values())
                ),
                timeout=12,
            )
            warnings.extend(discovery.warnings)
        except Exception:
            # Read-only discovery failure never licenses generated songs or dates.
            warnings.append("知识服务不可用，本次仅使用有依据的目录候选。")
        for hint in discovery.hints:
            try:
                songs = _catalog(
                    await invoke(
                        "search_music", {"keyword": hint.name[:100], "limit": 50}
                    ),
                    hint.name[:100],
                )
            except (ValueError, KeyError, TypeError):
                warnings.append("一条知识线索未通过目录核对，未使用其候选。")
                continue
            matches = [song for song in songs if hint.matches(song, songs)]
            collect(matches, hint.name[:100])
            for song in matches:
                if song.song_id not in candidates:
                    continue
                knowledge_sources.setdefault(str(song.song_id), []).append(
                    {
                        "knowledge_id": hint.knowledge_id,
                        "kind": hint.kind,
                        "start_year": hint.start_year,
                        "end_year": hint.end_year,
                        "sources": list(hint.sources),
                    }
                )
        # Era membership must be song-specific, not an artist's career, title,
        # or search query. Every executable ID still comes from search_music.
        if request.decade is not None:
            candidates = {
                identity: song
                for identity, song in candidates.items()
                if any(
                    item["kind"] == "era"
                    for item in knowledge_sources.get(str(identity), [])
                )
            }
            warnings.append("年代候选仅采用经知识服务验证且由目录核对的歌曲年代关联。")
        discovery_ids = set(candidates) - initial_ids
        if request.obscure and not discovery_ids:
            warnings.append(
                "未找到初轮搜索之外的可用知识补充曲目，无法确认冷门程度，不凑数。"
            )
        if not candidates:
            suffix = "缺少可核实的歌曲年代证据。" if request.decade is not None else ""
            return finish(
                f"只找到 0 首可用候选，尚未创建歌单；不会编造或凑数。{suffix}",
                reason="no_candidates",
            )
        # Preference is read-only and never imports songs absent from search.
        ordered = sorted(
            candidates.values(),
            key=lambda song: (
                song.song_id not in liked,
                song.song_id not in discovery_ids,
                -sum(
                    tag.casefold()
                    in (
                        song.name + " " + song.album + " " + " ".join(song.artists)
                    ).casefold()
                    for tag in plan.tags
                ),
            ),
        )[:60]
        selection = await _structured(
            model,
            task.input_text,
            SELECTION_PROMPT,
            SongSelection,
            {
                "scene": request.scene,
                "tags": plan.tags,
                "target_count": request.count,
                "obscure": request.obscure,
                "candidates": [
                    {
                        **song.model_dump(),
                        "liked": song.song_id in liked,
                        "search_sources": sources[str(song.song_id)],
                        "knowledge_sources": knowledge_sources.get(
                            str(song.song_id), []
                        ),
                        "discovered": song.song_id in discovery_ids,
                    }
                    for song in ordered
                ],
            },
            state.get("continuity_context"),
        )
        visible = {song.song_id for song in ordered}
        if (
            len(selection.song_ids) > request.count
            or len(set(selection.song_ids)) != len(selection.song_ids)
            or not set(selection.song_ids) <= visible
        ):
            raise ValueError("selection contains invented or duplicate IDs")
        selected = sorted(
            [candidates[song_id] for song_id in selection.song_ids],
            key=lambda song: song.song_id not in liked,
        )
        if selected:
            # Preserve liked precedence, then alternate the two discovery paths.
            liked_selected = [song for song in selected if song.song_id in liked]
            novel = [
                song
                for song in selected
                if song.song_id in discovery_ids and song.song_id not in liked
            ]
            original = [
                song
                for song in selected
                if song.song_id not in discovery_ids and song.song_id not in liked
            ]
            selected = liked_selected
            while novel or original:
                if novel:
                    selected.append(novel.pop(0))
                if original:
                    selected.append(original.pop(0))
        if not selected:
            return finish(
                "只找到 0 首符合场景的曲目，尚未创建歌单；不会拿无关歌曲凑数。",
                reason="no_matches",
            )
        playlist = CreatedPlaylistResult.model_validate_json(
            await invoke(
                "create_playlist_tool",
                {"name": request.name, "privacy": request.privacy},
            )
        )
        if playlist.name != request.name:
            raise ValueError("created playlist differs from request")
        for song in selected:
            added = AddSongToPlaylistResult.model_validate_json(
                await invoke(
                    "add_song_to_playlist",
                    {"playlist_id": playlist.playlist_id, "song_id": song.song_id},
                )
            )
            if (added.playlist_id, added.song_id) != (
                playlist.playlist_id,
                song.song_id,
            ) or added.status == "forbidden":
                raise ValueError("addition differs from selected target")
            accepted.append(song)
        short = (
            f"只找到 {len(selected)} 首符合的，未凑到 {request.count} 首。"
            if len(selected) < request.count
            else f"选出 {len(selected)} 首。"
        )
        all_confirmed = all(fact.state is ResultState.CONFIRMED for fact in facts)
        wording = (
            "已确认：只读核对已确认歌单创建和所选歌曲加入。"
            if all_confirmed
            else "已生成歌单选曲，创建和加歌已有后端成功回执；未全部只读核对，歌曲尚未全部确认。"
        )
        return finish(
            short + wording + ("\n" + "\n".join(warnings) if warnings else "")
        )
    except (ValueError, KeyError, TypeError, ContinuityConflict):
        unknown = any(fact.state is ResultState.UNKNOWN for fact in facts)
        message = (
            "歌单写入效果未知，已停止后续加歌；不会重复写入。"
            if unknown
            else "策展未全部完成，已停止后续操作；没有编造歌曲或完成状态。"
        )
        if selected:
            message += f"选出 {len(selected)} 首，{len(accepted)} 首已有有效加歌回执，其余未确认。"
        return finish(
            message,
            TaskStatus.FAILED,
            "write_unknown" if unknown else "curation_failed",
        )

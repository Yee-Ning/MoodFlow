"""Explorer reuses the grounded WorldLine workflow, with trusted song binding."""

import re
from functools import lru_cache

from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.adapters import _ground_same_turn_artist_reference
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.current_song.graph import _previous_action_target
from app.agent.foundation.subgraphs.worldline.entity_resolver import (
    _QUOTED_ENTITY,
    _normalized_name,
    parse_entity_query,
)
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    MusicEntityFacts,
    ResolvedEntity,
    WorldLineResult,
)
from app.agent.foundation.subgraphs.worldline.music_service import catalog_entity_id
from app.agent.foundation.subgraphs.worldline.state import create_worldline_state
from app.agent.handoff.context import latest_entities
from app.agent.handoff.song_context import SongContextRead, invoke_song_context
from app.agent.handoff.journey import (
    EvidenceState,
    ListeningJourney,
    build_listening_journey,
    render_journey,
    MAX_JOURNEY_NODES,
)
from app.agent.handoff.explorer_session import (
    get_explorer_sessions,
    is_branch_followup,
    owner_key,
    session_world,
)
from app.agent.handoff.explorer_preferences import (
    preference_weights,
    preference_note,
    read_likes,
)
from app.agent.handoff.explorer_roots import root_guidance
from app.agent.foundation.subgraphs.worldline.service import WorldLineExecutionError
from app.rag.query_understanding.context import _CURRENT_SONG_REFERENCE

_NODE_COUNT = re.compile(r"(?<![第0-9一二三四五六七八九十两])([0-9]+|[一二三四五六七八九十两])\s*(?:个\s*)?节点")


@lru_cache
def get_worldline_subgraph():
    # Instantiate the existing retrieval/generation runtime only when needed.
    return create_worldline_subgraph()


def journey_query(text: str) -> str:
    """Request the existing expansion gates without changing the parent task.

    Quote only a literal root extracted by the existing parser. Leave unresolved
    pronouns/branch references to WorldLine's normal clarification workflow.
    """
    if re.search(
        r"这位|那位|这个|那个|它|他|她|继续|分支", text
    ) and not _QUOTED_ENTITY.search(text):
        return text
    try:
        parsed = parse_entity_query(text)
    except ValueError:
        return text
    label = {
        EntityType.SONG: "歌曲",
        EntityType.ARTIST: "歌手",
        EntityType.ALBUM: "专辑",
    }.get(parsed.expected_entity_type, "")
    return (
        f"探索{label}《{parsed.query}》的音乐世界线；"
        "按时间线整理时代、场景、影响关系与风格；仅保留有来源的节点。"
    )


def journey_node_limit(text):
    matches = _NODE_COUNT.findall(text)
    if not matches:
        return MAX_JOURNEY_NODES
    chinese = dict(zip("一二三四五六七八九十两", (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 2), strict=True))
    values = {int(value) if value.isascii() else chinese[value] for value in matches}
    if len(values) != 1 or 0 in values:
        raise ValueError("journey node count is ambiguous or nonpositive")
    return min(values.pop(), MAX_JOURNEY_NODES)


def scoped_journey_query(text, original_input):
    """Carry one literal root from this request into a rootless child task."""
    if _QUOTED_ENTITY.search(text) or _CURRENT_SONG_REFERENCE.search(text) or is_branch_followup(text):
        return text
    if not re.search(r"旅程|路径|世界线|探索", text):
        return text
    roots = {match.group("name").strip() for match in _QUOTED_ENTITY.finditer(original_input)}
    if len(roots) != 1:
        return text
    try:
        parsed = parse_entity_query(original_input)
    except ValueError:
        return text
    label = {EntityType.SONG: "歌曲", EntityType.ARTIST: "歌手", EntityType.ALBUM: "专辑"}.get(parsed.expected_entity_type, "")
    if re.search(r"\bsong_id\s*[=:]", original_input):
        label = "歌曲"
    return f"{text}；本轮起点为{label}《{parsed.query}》"


def explicit_journey_song_id(text, original_input):
    root = _QUOTED_ENTITY.search(text)
    roots = {match.group("name").strip() for match in _QUOTED_ENTITY.finditer(original_input)}
    if root is None or roots != {root.group("name").strip()}:
        return None
    ids = set(re.findall(r"\bsong_id\s*[=:]\s*([1-9][0-9]*)(?=$|[\s，,;；。)）])", original_input, re.ASCII))
    return int(ids.pop()) if len(ids) == 1 else None


def clarification(task, code, content):
    return TaskResult(
        task_id=task.task_id,
        route_key=RouteKey.WORLDLINE,
        status=TaskStatus.NEEDS_INPUT,
        content=content,
        data={
            "reason": code,
            "journey_queue": [],
            "listening_journey": ListeningJourney(
                root_entity_id=None, evidence_state=EvidenceState.UNVERIFIED, nodes=()
            ).model_dump(mode="json"),
        },
    )


async def decorate_journey(result, world, snapshots, runtime, *, node_limit=MAX_JOURNEY_NODES):
    likes, receipt = await read_likes(runtime)
    weights, reasons, ids = preference_weights(world, snapshots, likes)
    journey = build_listening_journey(
        world,
        None,
        catalog_history=tuple(snapshots),
        node_priorities=weights,
        recommendation_reasons=reasons,
        preference_song_ids=ids,
        node_limit=node_limit,
    )
    return result.model_copy(
        update={
            "content": render_journey(journey)
            + "\n"
            + preference_note(receipt, weights),
            "data": {
                **result.data,
                "listening_journey": journey.model_dump(mode="json"),
                "journey_queue": journey.transfer_payload().model_dump(mode="json")[
                    "journey_queue"
                ],
                "personalization": {**receipt, "matched_node_ids": sorted(weights)},
            },
        }
    )


async def execute_exploration(
    view, run, runtime, *, graph, context_tool, sessions=None
) -> TaskResult:
    task = run.pending_tasks[0]
    text = _ground_same_turn_artist_reference(view, task)
    text = scoped_journey_query(text, run.original_input)
    limit_source = text
    root = _QUOTED_ENTITY.search(text)
    original_roots = {match.group("name").strip() for match in _QUOTED_ENTITY.finditer(run.original_input)}
    if not _NODE_COUNT.search(text) and root is not None and original_roots == {root.group("name").strip()}:
        limit_source = run.original_input
    try:
        node_limit = journey_node_limit(limit_source)
    except ValueError:
        return clarification(task, "invalid_journey_node_count", "请给出一个明确、正整数的旅程节点数量。")
    owner = owner_key(view, runtime)
    if is_branch_followup(text):
        if owner is None:
            return clarification(
                task,
                "explorer_session_missing",
                "当前没有可恢复的对话探索图，请先指定歌曲、歌手或专辑；我不会另建图冒充刚才的分支。",
            )
        try:
            response, snapshots, mode = await (
                sessions or get_explorer_sessions()
            ).follow(text, owner=owner, graph=graph, runtime=runtime)
        except WorldLineExecutionError as exc:
            return clarification(task, exc.code, str(exc))
        world = session_world(response)
        result = TaskResult(
            task_id=task.task_id,
            route_key=RouteKey.WORLDLINE,
            status=TaskStatus.SUCCESS,
            data={
                "worldline_result": world.model_dump(mode="json"),
                "explorer_session": response.model_dump(mode="json"),
                "explorer_followup_mode": mode,
            },
        )
        result = await decorate_journey(result, world, snapshots, runtime, node_limit=node_limit)
        if mode == "explain_selected":
            node = next(
                (
                    item
                    for item in result.data["listening_journey"]["nodes"]
                    if item["node_id"] == response.current_node
                ),
                None,
            )
            detail = (
                (
                    node["narrative"]
                    + "；来源："
                    + ", ".join(node["narration"]["source_ids"])
                )
                if node
                else "该节点暂无可核实的详细说明，不补写知识。"
            )
            result = result.model_copy(
                update={
                    "content": result.content
                    + "\n沿刚才选中的节点讲解："
                    + detail
                    + "。本次复用原图与来源，未重复扩展或重建。"
                }
            )
        return result.model_copy(
            update={"content": result.content + branch_note(response)}
        )
    guidance = root_guidance(text)
    if guidance:
        return clarification(task, "explorer_topic_root_guidance", guidance)
    target = None
    requested_song_id = explicit_journey_song_id(text, run.original_input)
    routing = view["routing_context"]
    if requested_song_id is not None and routing.has_current_song and routing.current_song_id == requested_song_id:
        read = SongContextRead.model_validate_json(await invoke_song_context(context_tool, {}))
        if read.status is not TaskStatus.SUCCESS:
            return TaskResult(task_id=task.task_id, route_key=RouteKey.WORLDLINE, status=read.status,
                              content=read.message, error=read.message if read.status is TaskStatus.FAILED else None,
                              data={"reason": read.reason})
        root = _QUOTED_ENTITY.search(text).group("name")
        if read.song.song_id != requested_song_id or _normalized_name(read.song.name) != _normalized_name(root):
            return clarification(task, "explicit_song_target_mismatch", "本轮起点与歌曲资料不一致，请核对歌曲名称和 ID。")
        target = ResolvedEntity(entity_id=catalog_entity_id(EntityType.SONG, requested_song_id),
                                entity_type=EntityType.SONG, name=read.song.name, artist=read.song.artist or None,
                                album=read.song.album or None, source="netease")
    # Explicit titles follow the existing catalog/query-resolution workflow.
    if _CURRENT_SONG_REFERENCE.search(text) and not _QUOTED_ENTITY.search(text):
        played_id, pending = _previous_action_target(list(run.results))
        references = latest_entities(run.results)
        songs = {item.song_id: item for item in references.songs}
        explicit_player = re.search(r"当前|正在播放", text) is not None
        if songs and played_id is None and not pending and not explicit_player:
            if len(songs) != 1:
                return TaskResult(
                    task_id=task.task_id,
                    route_key=RouteKey.WORLDLINE,
                    status=TaskStatus.NEEDS_INPUT,
                    content="搜索结果中有多首歌曲，请明确要探索的歌曲名称。",
                    data={"reason": "ambiguous_song_reference"},
                )
            selected = next(iter(songs.values()))
            song_id, name = selected.song_id, selected.name
            artist, album = " / ".join(selected.artists), selected.album
        else:
            read = SongContextRead.model_validate_json(
                await invoke_song_context(context_tool, {})
            )
            if read.status is not TaskStatus.SUCCESS:
                return TaskResult(
                    task_id=task.task_id,
                    route_key=RouteKey.WORLDLINE,
                    status=read.status,
                    content=read.message,
                    error=read.message if read.status is TaskStatus.FAILED else None,
                    data={"reason": read.reason},
                )
            song_id, name = read.song.song_id, read.song.name
            artist, album = read.song.artist, read.song.album
        target = ResolvedEntity(
            entity_id=catalog_entity_id(EntityType.SONG, song_id),
            entity_type=EntityType.SONG,
            name=name,
            artist=artist or None,
            album=album or None,
            source="netease",
        )
        # This is a child query projection; the original RouteTask is untouched.
        text = _CURRENT_SONG_REFERENCE.sub(lambda _: f"歌曲《{name}》", text)
    private = create_worldline_state(
        journey_query(text),
        task.task_id,
        messages=view["messages"],
        routing_context=view["routing_context"],
    )
    private.update(
        trusted_target=target,
        continuity_context=view.get("continuity_context", {}),
    )
    invoke_options = (
        {"durability": "async"} if getattr(graph, "checkpointer", None) is False else {}
    )
    if isinstance(graph, CompiledStateGraph):
        # Programmatic private read: reuse the same verified Java facts that the
        # WorldLine workflow just loaded. No additional tool/API or data source.
        invoke_options["output_keys"] = ["task_result", "entity_facts"]
    output = await graph.ainvoke(private, context=runtime.context, **invoke_options)
    result = TaskResult.model_validate(output.get("task_result"))
    if result.task_id != task.task_id or result.route_key != RouteKey.WORLDLINE:
        raise ValueError("Explorer result does not match its assigned task")
    if result.status is TaskStatus.SUCCESS:
        world = WorldLineResult.model_validate(result.data["worldline_result"])
        if target is not None and world.root_entity.entity_id != target.entity_id:
            raise ValueError("Explorer changed the trusted song target")
        if requested_song_id is not None and world.root_entity.entity_id != catalog_entity_id(EntityType.SONG, requested_song_id):
            return clarification(task, "explicit_song_target_mismatch", "目录结果与本轮指定的歌曲 ID 不一致，请核对起点。")
        raw_facts = output.get("entity_facts")
        facts = (
            MusicEntityFacts.model_validate(raw_facts)
            if raw_facts is not None
            else None
        )
        response = None
        if owner is not None:
            response = await (sessions or get_explorer_sessions()).adopt(
                result, facts, owner=owner, graph=graph, runtime=runtime
            )
            result = result.model_copy(
                update={
                    "data": {
                        **result.data,
                        "explorer_session": response.model_dump(mode="json"),
                    }
                }
            )
        result = await decorate_journey(
            result, world, [facts] if facts else [], runtime, node_limit=node_limit
        )
        if response is not None:
            result = result.model_copy(
                update={"content": result.content + branch_note(response)}
            )
    else:
        journey = ListeningJourney(
            root_entity_id=None,
            evidence_state=EvidenceState.UNVERIFIED,
            nodes=(),
            limitations=("实体或知识未核实，未生成路径或播放队列。",),
        )
        result = result.model_copy(
            update={
                "data": {
                    **result.data,
                    "listening_journey": journey.model_dump(mode="json"),
                    "journey_queue": [],
                }
            }
        )
    return result


def branch_note(response):
    names = "、".join(branch.target_entity.name for branch in response.branches)
    current = next(
        item.name for item in response.graph.nodes if item.id == response.current_node
    )
    outcome = (
        "；此次未发现新的有来源关联" if response.outcome == "no_new_connections" else ""
    )
    return f"\n当前探索节点：{current}{outcome}。" + (
        f"可继续指定分支：{names}。" if names else "当前没有新的可探索分支。"
    )

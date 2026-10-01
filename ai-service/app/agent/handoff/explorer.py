"""Explorer reuses the grounded WorldLine workflow, with trusted song binding."""

import re
from functools import lru_cache

from app.agent.foundation.adapters import _ground_same_turn_artist_reference
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.current_song.graph import _previous_action_target
from app.agent.foundation.subgraphs.worldline.entity_resolver import _QUOTED_ENTITY
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    ResolvedEntity,
    WorldLineResult,
)
from app.agent.foundation.subgraphs.worldline.music_service import catalog_entity_id
from app.agent.foundation.subgraphs.worldline.state import create_worldline_state
from app.agent.handoff.context import latest_entities
from app.agent.handoff.song_context import SongContextRead
from app.rag.query_understanding.context import _CURRENT_SONG_REFERENCE


@lru_cache
def get_worldline_subgraph():
    # Instantiate the existing retrieval/generation runtime only when needed.
    return create_worldline_subgraph()


async def execute_exploration(view, run, runtime, *, graph, context_tool) -> TaskResult:
    task = run.pending_tasks[0]
    text = _ground_same_turn_artist_reference(view, task)
    target = None
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
            read = SongContextRead.model_validate_json(await context_tool.ainvoke({}))
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
        text,
        task.task_id,
        messages=view["messages"],
        routing_context=view["routing_context"],
    )
    private.update(
        trusted_target=target,
        continuity_context=view.get("continuity_context", {}),
    )
    output = await graph.ainvoke(private, context=runtime.context)
    result = TaskResult.model_validate(output.get("task_result"))
    if result.task_id != task.task_id or result.route_key != RouteKey.WORLDLINE:
        raise ValueError("Explorer result does not match its assigned task")
    if result.status is TaskStatus.SUCCESS:
        world = WorldLineResult.model_validate(result.data["worldline_result"])
        if target is not None and world.root_entity.entity_id != target.entity_id:
            raise ValueError("Explorer changed the trusted song target")
    return result

"""Resolve a seed-only child task from verified current-song facts."""
import re

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.subgraphs.tool_calling.models import ReferencedEntities, SongReference
from app.agent.foundation.subgraphs.worldline.entity_resolver import _QUOTED_ENTITY, _normalized_name
from app.agent.handoff.explorer import explicit_journey_song_id
from app.agent.handoff.song_context import SongContextRead, invoke_song_context


async def read_journey_seed(task, original_input, routing_context, context_tool):
    text = task.input_text.strip()
    if not re.search(r"旅程|世界线|探索路径", original_input):
        return None
    if not re.search(r"以.+为起点[，,。]*$", text):
        return None
    if re.search(r"搜索|查询|读取|播放|收藏|喜欢|添加|创建|生成|删除|记住|规划", text):
        return None
    if re.search(r"不读|不要读|不查|不要查", original_input):
        return None
    song_id = explicit_journey_song_id(text, original_input)
    if song_id is None or not routing_context.has_current_song or routing_context.current_song_id != song_id:
        return None
    read = SongContextRead.model_validate_json(await invoke_song_context(context_tool, {}))
    if read.status is not TaskStatus.SUCCESS:
        return TaskResult(task_id=task.task_id, route_key=task.route_key, status=read.status,
                          content=read.message, error=read.message if read.status is TaskStatus.FAILED else None,
                          data={"reason": read.reason})
    root = _QUOTED_ENTITY.search(text).group("name")
    if read.song.song_id != song_id or _normalized_name(read.song.name) != _normalized_name(root):
        return TaskResult(task_id=task.task_id, route_key=task.route_key, status=TaskStatus.NEEDS_INPUT,
                          content="本轮起点与歌曲资料不一致，请核对歌曲名称和 ID。",
                          data={"reason": "explicit_song_target_mismatch"})
    reference = SongReference(position=1, song_id=song_id, name=read.song.name,
                              artists=[read.song.artist] if read.song.artist else [], album=read.song.album)
    return TaskResult(task_id=task.task_id, route_key=task.route_key, status=TaskStatus.SUCCESS,
                      content=f"已核实起点《{read.song.name}》的目录资料，交给 Explorer 按本轮要求规划。",
                      data={"entities": ReferencedEntities(songs=[reference]).model_dump(mode="json"),
                            "song": {"song_id": song_id, "name": read.song.name, "artist": read.song.artist, "album": read.song.album},
                            "target_source": read.target_source, "tool_calls": 1,
                            "tool_phase_counts": {"retrieval": 1, "execution": 0, "total": 1}})

import json
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, ValidationError

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.tool_calling.assistant import (
    ToolCallingAssistant,
    all_tool_calling_tools,
    answer_tool_calling_task,
    bind_tool_calling_tools,
)
from app.agent.foundation.subgraphs.tool_calling.client_tools import (
    CLIENT_ACTION_TOOL_NAMES,
)
from app.agent.foundation.subgraphs.tool_calling.direct_actions import (
    make_action_id,
    resolve_direct_actions,
)
from app.agent.foundation.subgraphs.tool_calling.models import (
    ClientAction,
    PendingClientActionPayload,
    PlaylistReference,
    ReferencedEntities,
    SongReference,
    ToolExecutionRecord,
    ToolSelection,
)
from app.agent.structured_output import (
    SemanticValidationError,
    StructuredOutputError,
    StructuredOutputService,
)
from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState
from app.schemas.context import MusicAgentContext
from app.schemas.music import MusicSearchResult, MusicTrack, ToolFailure, UserPlaylistsResult
from app.tools.music import handle_music_tool_error

MAX_TOOL_CALLS = 5
MAX_VISIBLE_RESPONSE_CHARACTERS = 2_000
TOOL_LIMIT_MESSAGE = (
    "这次任务需要的工具调用次数超过安全上限，我已停止继续操作。"
    "请缩小请求范围或分成两次完成。"
)
INVALID_TOOL_OUTPUT_MESSAGE = "工具选择或参数格式无效，本次没有执行工具。"

# Tool output and conversation history can supply entity references, but only
# the assigned user clause can authorize a player or library mutation.
_ACTION_REQUESTS = {
    "pause_player": r"暂停|停一下|停住",
    "resume_player": r"继续播放|恢复播放|接着播放",
    "play_next": r"下一首|下一曲|切歌|换歌",
    "play_previous": r"上一首|上一曲|切回上一首",
    "set_volume": r"音量|调大|调小|关小|声音大|声音小",
    "set_muted": r"静音|取消静音",
    "seek_to": r"进度|跳到|定位到|拖到|快进|快退",
    "seek_by": r"快进|快退|前进|后退|进度",
    "set_play_mode": r"播放模式|单曲循环|列表循环|随机播放|顺序播放",
    "play_song": r"播放|放一首|来一首|听一下|听这首|听它",
    "set_liked": r"收藏|喜欢|点赞|点个喜欢",
    "open_lyrics": r"打开歌词|显示歌词|查看歌词|展开歌词",
    "add_song_to_playlist": r"加入.{0,8}歌单|添加到.{0,8}歌单|放进.{0,8}歌单",
}
_ACTION_CLAUSE = re.compile(r"[,，.。!！?？;；]|然后|接着|随后|并且|同时|以及|但是|不过|但")
_ACTION_NEGATION = re.compile(r"(?:别|不要|不用|无需|不再|不要再)[^,，.。!！?？;；]{0,8}$")


def _action_intent_errors(state: ToolCallingState) -> dict[str, str]:
    text = state["current_input"]
    clauses = _ACTION_CLAUSE.split(text)
    errors: dict[str, str] = {}
    for call in _latest_tool_calls(state):
        name = call.get("name")
        pattern = _ACTION_REQUESTS.get(name)
        if pattern is None:
            continue
        args = call.get("args") or {}
        if name == "set_liked" and args.get("liked") is False:
            pattern = r"取消收藏|取消喜欢|不喜欢|移除收藏"
        allowed = False
        for clause in clauses:
            for match in re.finditer(pattern, clause, re.IGNORECASE):
                if _ACTION_NEGATION.search(clause[:match.start()]):
                    continue
                if name == "set_liked" and args.get("liked") is True and re.search(
                    r"(?:取消|不)\s*$", clause[:match.start()]
                ):
                    continue
                allowed = True
                break
            if allowed:
                break
        if not allowed:
            errors[str(call.get("id") or "")] = "当前任务没有明确请求这个操作；请只执行本轮用户明确要求的动作。"
    return errors


def _route_after_direct(state: ToolCallingState) -> str:
    return END if state.get("direct_complete") else "agent"


def _route_after_agent(state: ToolCallingState) -> str:
    return tools_condition(state)


def _read_json(content: Any) -> dict[str, Any] | None:
    if not isinstance(content, str):
        return None
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _extract_entities(tool_results: list[dict[str, Any]]) -> ReferencedEntities:
    songs: list[SongReference] = []
    playlists: list[PlaylistReference] = []
    seen_song_ids: set[int] = set()
    seen_playlist_ids: set[int] = set()
    for result in tool_results:
        content = result.get("content")
        if result.get("name") == "search_music" and isinstance(content, str):
            try:
                parsed = MusicSearchResult.model_validate_json(content)
            except ValueError:
                continue
            for song in parsed.songs:
                if song.song_id in seen_song_ids:
                    continue
                seen_song_ids.add(song.song_id)
                songs.append(
                    SongReference(
                        position=len(songs) + 1,
                        **song.model_dump(mode="json"),
                    )
                )
        elif result.get("name") == "get_user_playlists" and isinstance(content, str):
            try:
                parsed = UserPlaylistsResult.model_validate_json(content)
            except ValueError:
                continue
            for playlist in parsed.playlists:
                if playlist.playlist_id in seen_playlist_ids:
                    continue
                seen_playlist_ids.add(playlist.playlist_id)
                playlists.append(
                    PlaylistReference(
                        position=len(playlists) + 1,
                        **playlist.model_dump(mode="json"),
                    )
                )
    return ReferencedEntities(songs=songs, playlists=playlists)


def _extract_client_actions(
    state: ToolCallingState,
) -> list[ClientAction]:
    actions: list[ClientAction] = []
    for index, result in enumerate(state.get("tool_results", [])):
        if result.get("name") not in CLIENT_ACTION_TOOL_NAMES:
            continue
        payload = _read_json(result.get("content"))
        if payload is None or payload.get("status") != "pending_client_execution":
            continue
        pending = PendingClientActionPayload.model_validate(payload)
        actions.append(
            ClientAction(
                action_id=make_action_id(
                    state["task_id"],
                    str(result.get("tool_call_id") or f"tool:{index}"),
                ),
                task_id=state["task_id"],
                **pending.client_action,
            )
        )
    return actions


def _latest_tool_calls(state: ToolCallingState) -> list[dict[str, Any]]:
    for message in reversed(state["messages"]):
        if isinstance(message, AIMessage) and message.tool_calls:
            return [dict(call) for call in message.tool_calls]
    return []


def _known_references(
    state: ToolCallingState,
) -> tuple[dict[int, SongReference], dict[int, PlaylistReference]]:
    current = _extract_entities(state.get("tool_results", []))
    groups = [current, *state.get("prior_entities", [])]
    songs = {
        song.song_id: song
        for group in groups
        for song in group.songs
    }
    playlists = {
        playlist.playlist_id: playlist
        for group in groups
        for playlist in group.playlists
    }
    return songs, playlists


def _track_matches_reference(track: MusicTrack, reference: SongReference) -> bool:
    return (
        track.song_id == reference.song_id
        and track.name == reference.name
        and track.artists == reference.artists
        and track.album == reference.album
        and track.cover_url == reference.cover_url
        and track.duration == reference.duration
    )


def _reference_errors(state: ToolCallingState) -> dict[str, str]:
    songs, playlists = _known_references(state)
    current_song_id = state["routing_context"].current_song_id
    known_song_ids = {*songs, *([current_song_id] if current_song_id else [])}
    errors: dict[str, str] = {}
    for call in _latest_tool_calls(state):
        call_id = str(call.get("id", ""))
        name = call.get("name")
        args = call.get("args")
        if not call_id or not isinstance(args, dict):
            continue
        if name in {"get_song_lyrics", "set_liked"}:
            song_id = args.get("song_id")
            if isinstance(song_id, int) and song_id not in known_song_ids:
                errors[call_id] = "歌曲 ID 没有可信来源；请先搜索歌曲或请用户明确选择。"
        elif name == "play_song":
            try:
                track = MusicTrack.model_validate(args.get("song"))
            except ValueError:
                continue
            reference = songs.get(track.song_id)
            if reference is None or not _track_matches_reference(track, reference):
                errors[call_id] = "播放歌曲必须完整使用 search_music 或历史结果；请先搜索。"
        elif name == "add_song_to_playlist":
            song_id = args.get("song_id")
            playlist_id = args.get("playlist_id")
            if isinstance(song_id, int) and song_id not in known_song_ids:
                errors[call_id] = "歌曲 ID 没有可信来源；请先搜索歌曲。"
            if isinstance(playlist_id, int):
                playlist = playlists.get(playlist_id)
                if playlist is None:
                    errors[call_id] = "歌单 ID 没有可信来源；请先读取当前用户歌单。"
                elif not playlist.editable:
                    errors[call_id] = "目标歌单不可编辑；请让用户选择可编辑歌单。"
    return errors


def _duplicate_call_errors(state: ToolCallingState) -> dict[str, str]:
    latest = _latest_tool_calls(state)
    if not latest:
        return {}
    prior_calls = state.get("tool_calls", [])[:-len(latest)]
    attempted_ids = {
        str(item.get("tool_call_id"))
        for item in state.get("tool_results", [])
        if (_read_json(item.get("content")) or {}).get("error")
        not in {"duplicate_tool_call", "batch_rejected", "untrusted_entity_reference"}
    }

    def signature(call: dict[str, Any]) -> tuple[str, str] | None:
        name, args = call.get("name"), call.get("args")
        if not isinstance(name, str) or not isinstance(args, dict):
            return None
        return name, json.dumps(args, ensure_ascii=False, sort_keys=True)

    seen = {
        item for call in prior_calls
        if str(call.get("id")) in attempted_ids
        if (item := signature(call)) is not None
    }
    errors: dict[str, str] = {}
    for call in latest:
        item = signature(call)
        if item is None:
            continue
        if item in seen:
            errors[str(call.get("id"))] = "相同参数的工具请求已经在本次任务中执行，不能重复执行。"
        else:
            seen.add(item)
    return errors


def _final_task_result(
    state: ToolCallingState,
    message: AIMessage,
) -> tuple[str, TaskResult, list[ClientAction]]:
    content = str(message.text).strip()
    if len(content) > MAX_VISIBLE_RESPONSE_CHARACTERS:
        content = content[: MAX_VISIBLE_RESPONSE_CHARACTERS - 1].rstrip() + "…"
    actions = _extract_client_actions(state)
    entities = _extract_entities(state.get("tool_results", []))
    failures = [
        payload
        for result in state.get("tool_results", [])
        if (payload := _read_json(result.get("content"))) is not None
        and payload.get("success") is False
    ]
    all_tools_failed = bool(state.get("tool_results")) and len(failures) == len(
        state.get("tool_results", [])
    )
    limit_reached = content == TOOL_LIMIT_MESSAGE
    status = (
        TaskStatus.FAILED
        if all_tools_failed or limit_reached
        else TaskStatus.SUCCESS
    )
    error = None
    if limit_reached:
        error = TOOL_LIMIT_MESSAGE
    elif all_tools_failed:
        error = str(failures[0].get("message") or "工具调用失败，请稍后重试。")
    return (
        content,
        TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.TOOL_CALLING,
            status=status,
            content=content,
            data={
                "client_actions": [
                    action.model_dump(mode="json") for action in actions
                ],
                "entities": entities.model_dump(mode="json"),
                "tool_calls": state.get("tool_call_count", 0),
                "tool_call_details": [
                    {"name": str(item.get("name") or "unknown"),
                     "status": str(item.get("status") or "unknown")}
                    for item in state.get("tool_results", [])
                ],
            },
            error=error,
        ),
        actions,
    )


def _fail_invalid_tool_output(
    state: ToolCallingState,
    reason: str,
) -> dict[str, Any]:
    return {
        "messages": [AIMessage(content=INVALID_TOOL_OUTPUT_MESSAGE)],
        "answer": INVALID_TOOL_OUTPUT_MESSAGE,
        "client_actions": [],
        "task_result": TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.TOOL_CALLING,
            status=TaskStatus.FAILED,
            content=INVALID_TOOL_OUTPUT_MESSAGE,
            data={"reason": reason, "client_actions": [], "tool_call_details": []},
            error=INVALID_TOOL_OUTPUT_MESSAGE,
        ),
    }


def build_tool_calling_subgraph(
    *,
    assistant: ToolCallingAssistant | None = None,
    model: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
) -> StateGraph:
    graph_tools = list(tools or all_tool_calling_tools())
    tools_by_name = {item.name: item for item in graph_tools}
    bound_model = bind_tool_calling_tools(model, graph_tools) if model is not None else None

    def _argument_errors(state: ToolCallingState) -> dict[str, tuple[str, str]]:
        errors: dict[str, tuple[str, str]] = {}
        for call in _latest_tool_calls(state):
            call_id = str(call.get("id") or "")
            if not call_id:
                continue

            def allowlisted(selection: ToolSelection) -> None:
                if selection.name not in tools_by_name:
                    raise SemanticValidationError("unknown_tool")

            try:
                selected = StructuredOutputService(
                    "tool_selection", ToolSelection,
                    semantic_validator=allowlisted, max_attempts=1,
                ).validate(call)
            except StructuredOutputError as exc:
                code = (
                    "unknown_tool" if exc.failure_type.value == "semantic_validation_error"
                    else "invalid_tool_selection"
                )
                errors[call_id] = (code, INVALID_TOOL_OUTPUT_MESSAGE)
                continue
            # LangChain removes injected ToolRuntime fields from this public schema.
            schema = tools_by_name[selected.name].tool_call_schema
            if not isinstance(schema, type) or not issubclass(schema, BaseModel):
                errors[call_id] = ("invalid_tool_schema", INVALID_TOOL_OUTPUT_MESSAGE)
                continue
            if set(selected.args).difference(schema.model_fields):
                errors[call_id] = ("invalid_tool_arguments", INVALID_TOOL_OUTPUT_MESSAGE)
                continue
            try:
                StructuredOutputService(
                    f"tool_arguments.{selected.name}", schema, max_attempts=1,
                ).validate(selected.args)
            except StructuredOutputError:
                errors[call_id] = ("invalid_tool_arguments", INVALID_TOOL_OUTPUT_MESSAGE)
        return errors

    async def agent(state: ToolCallingState) -> dict[str, Any]:
        if assistant is not None:
            result = await assistant(
                state["messages"],
                state["current_input"],
                state["routing_context"],
                state.get("prior_entities", []),
            )
        else:
            result = await answer_tool_calling_task(
                state["messages"],
                state["current_input"],
                state["routing_context"],
                state.get("prior_entities", []),
                model=bound_model,
                continuity_context=state.get("continuity_context"),
            )

        if result.invalid_tool_calls or any(
            not isinstance(call.get("id"), str) or not call["id"].strip()
            for call in result.tool_calls
        ):
            return _fail_invalid_tool_output(state, "malformed_tool_call")

        tool_call_count = state.get("tool_call_count", 0)
        requested_calls = len(result.tool_calls)
        if requested_calls and tool_call_count + requested_calls > MAX_TOOL_CALLS:
            result = AIMessage(content=TOOL_LIMIT_MESSAGE)
            requested_calls = 0

        update: dict[str, Any] = {
            "messages": [result],
            "tool_call_count": tool_call_count + requested_calls,
            "tool_calls": [
                *state.get("tool_calls", []),
                *[dict(call) for call in result.tool_calls],
            ],
        }
        if not result.tool_calls:
            answer, task_result, client_actions = _final_task_result(state, result)
            update.update(
                answer=answer,
                task_result=task_result,
                client_actions=client_actions,
            )
        return update

    def capture_tool_results(state: ToolCallingState) -> dict[str, Any]:
        captured_ids = {
            str(item.get("tool_call_id"))
            for item in state.get("tool_results", [])
        }
        results = list(state.get("tool_results", []))
        calls_by_id = {
            str(call.get("id")): call
            for call in state.get("tool_calls", [])
            if call.get("id") is not None
        }
        for message in state["messages"]:
            if not isinstance(message, ToolMessage):
                continue
            call_id = str(message.tool_call_id)
            if call_id in captured_ids or call_id not in calls_by_id:
                continue
            captured_ids.add(call_id)
            try:
                record = ToolExecutionRecord.model_validate({
                    "tool_call_id": call_id,
                    "name": message.name or calls_by_id[call_id].get("name", ""),
                    "content": message.content,
                    "status": getattr(message, "status", "success"),
                })
            except ValidationError:
                record = ToolExecutionRecord(
                    tool_call_id=call_id,
                    name=str(calls_by_id[call_id].get("name") or "unknown"),
                    content=ToolFailure(
                        error="invalid_tool_result",
                        message="工具结果格式无效，本次未使用该结果。",
                    ).model_dump_json(),
                    status="error",
                )
            results.append(record.model_dump(mode="json"))
        return {"tool_results": results}

    def reject_invalid_tool_calls(state: ToolCallingState) -> dict[str, Any]:
        argument_errors = _argument_errors(state)
        reference_errors = _reference_errors(state)
        duplicate_errors = _duplicate_call_errors(state)
        action_errors = _action_intent_errors(state)
        messages: list[ToolMessage] = []
        for call in _latest_tool_calls(state):
            call_id = str(call.get("id", ""))
            if call_id in argument_errors:
                error_code, message = argument_errors[call_id]
            elif call_id in action_errors:
                error_code = "unauthorized_action"
                message = action_errors[call_id]
            elif call_id in reference_errors:
                error_code = "untrusted_entity_reference"
                message = reference_errors[call_id]
            elif call_id in duplicate_errors:
                error_code = "duplicate_tool_call"
                message = duplicate_errors[call_id]
            else:
                error_code = "batch_rejected"
                message = "同一批工具调用含有重复或无效请求，本次未执行；请调整后重试。"
            messages.append(
                ToolMessage(
                    content=ToolFailure(
                        error=error_code,
                        message=message,
                    ).model_dump_json(),
                    tool_call_id=call_id,
                    name=str(call.get("name") or "unknown"),
                    status="error",
                )
            )
        return {"messages": messages}

    def route_validated_calls(state: ToolCallingState) -> str:
        return (
            "reject_tool_calls"
            if (_argument_errors(state) or _reference_errors(state)
                or _duplicate_call_errors(state) or _action_intent_errors(state))
            else "tools"
        )

    builder = StateGraph(
        ToolCallingState,
        context_schema=MusicAgentContext,
        output_schema=SubGraphOutput,
    )
    builder.add_node("direct_actions", resolve_direct_actions)
    builder.add_node("agent", agent)
    builder.add_node("validate_tool_calls", lambda _: {})
    builder.add_node(
        "tools",
        ToolNode(graph_tools, handle_tool_errors=handle_music_tool_error),
    )
    builder.add_node("capture_tool_results", capture_tool_results)
    builder.add_node("reject_tool_calls", reject_invalid_tool_calls)
    builder.add_edge(START, "direct_actions")
    builder.add_conditional_edges(
        "direct_actions",
        _route_after_direct,
        {"agent": "agent", END: END},
    )
    builder.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "validate_tool_calls", END: END},
    )
    builder.add_conditional_edges(
        "validate_tool_calls",
        route_validated_calls,
        {"tools": "tools", "reject_tool_calls": "reject_tool_calls"},
    )
    builder.add_edge("tools", "capture_tool_results")
    builder.add_edge("reject_tool_calls", "capture_tool_results")
    builder.add_edge("capture_tool_results", "agent")
    return builder


def create_tool_calling_subgraph(
    *,
    assistant: ToolCallingAssistant | None = None,
    model: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
) -> CompiledStateGraph:
    return build_tool_calling_subgraph(
        assistant=assistant,
        model=model,
        tools=tools,
    ).compile(checkpointer=False)

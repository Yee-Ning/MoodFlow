import json
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.tool_calling.assistant import (
    ToolCallingAssistant,
    all_tool_calling_tools,
    answer_tool_calling_task,
    bind_tool_calling_tools,
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
)
from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState
from app.schemas.context import MusicAgentContext
from app.schemas.music import MusicSearchResult, UserPlaylistsResult
from app.tools.music import handle_music_tool_error

MAX_TOOL_CALLS = 5
MAX_VISIBLE_RESPONSE_CHARACTERS = 2_000
TOOL_LIMIT_MESSAGE = (
    "这次任务需要的工具调用次数超过安全上限，我已停止继续操作。"
    "请缩小请求范围或分成两次完成。"
)


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
        payload = _read_json(result.get("content"))
        if payload is None or payload.get("status") != "pending_client_execution":
            continue
        try:
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
        except ValueError:
            continue
    return actions


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
            },
            error=error,
        ),
        actions,
    )


def build_tool_calling_subgraph(
    *,
    assistant: ToolCallingAssistant | None = None,
    model: BaseChatModel | None = None,
    tools: Sequence[BaseTool] | None = None,
) -> StateGraph:
    graph_tools = list(tools or all_tool_calling_tools())
    bound_model = bind_tool_calling_tools(model, graph_tools) if model is not None else None

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
            )

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
            results.append(
                {
                    "tool_call_id": call_id,
                    "name": message.name or calls_by_id[call_id].get("name", ""),
                    "content": message.content,
                    "status": getattr(message, "status", "success"),
                }
            )
        return {"tool_results": results}

    builder = StateGraph(
        ToolCallingState,
        context_schema=MusicAgentContext,
        output_schema=SubGraphOutput,
    )
    builder.add_node("direct_actions", resolve_direct_actions)
    builder.add_node("agent", agent)
    builder.add_node(
        "tools",
        ToolNode(graph_tools, handle_tool_errors=handle_music_tool_error),
    )
    builder.add_node("capture_tool_results", capture_tool_results)
    builder.add_edge(START, "direct_actions")
    builder.add_conditional_edges(
        "direct_actions",
        _route_after_direct,
        {"agent": "agent", END: END},
    )
    builder.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "tools", END: END},
    )
    builder.add_edge("tools", "capture_tool_results")
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
    ).compile()

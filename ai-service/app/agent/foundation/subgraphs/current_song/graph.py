import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.current_song.assistant import (
    CurrentSongAssistant,
    answer_current_song_task,
)
from app.agent.foundation.subgraphs.current_song.state import (
    CurrentSongState,
    SongTargetSource,
)
from app.context.music import MusicContextProvider
from app.schemas.client_actions import (
    ClientAction,
    ClientActionName,
    JumpToQueueItemArguments,
    PlayAlbumArguments,
    PlayPlaylistArguments,
    PlaySongArguments,
)

logger = logging.getLogger(__name__)

MAX_VISIBLE_RESPONSE_CHARACTERS = 2_000
NO_CURRENT_SONG_MESSAGE = "请先选择并播放一首歌曲，我才能分析当前歌曲。"
PENDING_QUEUE_CHANGE_MESSAGE = (
    "切歌指令还在等待播放器执行，请在新歌曲开始播放后再让我分析。"
)
CONTEXT_UNAVAILABLE_MESSAGE = "当前歌曲资料暂时无法加载，请稍后再试。"


def _previous_action_target(
    results: list[TaskResult],
) -> tuple[int | None, bool]:
    """Return a selected song id, or whether an unresolved queue change exists."""
    for result in reversed(results):
        if result.status is not TaskStatus.SUCCESS:
            continue
        raw_actions = result.data.get("client_actions", [])
        if not isinstance(raw_actions, list):
            raise ValueError("client_actions must be a list")
        for raw_action in reversed(raw_actions):
            action = ClientAction.model_validate(raw_action)
            if action.name is ClientActionName.PLAY_SONG:
                if not isinstance(action.arguments, PlaySongArguments):
                    raise ValueError("play_song action has invalid arguments")
                return action.arguments.song.song_id, False
            if action.name is ClientActionName.PLAY_ALBUM:
                if not isinstance(action.arguments, PlayAlbumArguments):
                    raise ValueError("play_album action has invalid arguments")
                return action.arguments.tracks[0].song_id, False
            if action.name is ClientActionName.PLAY_PLAYLIST:
                if not isinstance(action.arguments, PlayPlaylistArguments):
                    raise ValueError("play_playlist action has invalid arguments")
                return action.arguments.tracks[0].song_id, False
            if action.name is ClientActionName.JUMP_TO_QUEUE_ITEM:
                if not isinstance(action.arguments, JumpToQueueItemArguments):
                    raise ValueError("jump_to_queue_item action has invalid arguments")
                if action.arguments.song_id is not None:
                    return action.arguments.song_id, False
                return None, True
            if action.name in {
                ClientActionName.PLAY_NEXT,
                ClientActionName.PLAY_PREVIOUS,
            }:
                return None, True
    return None, False


def _route_after_resolution(state: CurrentSongState) -> str:
    return END if state.get("task_result") is not None else "load_song_context"


def _route_after_context(state: CurrentSongState) -> str:
    return END if state.get("task_result") is not None else "agent"


def _visible_text(message: AIMessage) -> str:
    content = str(message.text).strip()
    if not content:
        raise RuntimeError("Current-song assistant returned an empty response")
    if len(content) > MAX_VISIBLE_RESPONSE_CHARACTERS:
        content = content[: MAX_VISIBLE_RESPONSE_CHARACTERS - 1].rstrip() + "…"
    return content


def build_current_song_subgraph(
    *,
    provider: MusicContextProvider | None = None,
    assistant: CurrentSongAssistant | None = None,
    model: BaseChatModel | None = None,
) -> StateGraph:
    context_provider = provider or MusicContextProvider()

    def resolve_current_song(state: CurrentSongState) -> dict[str, Any]:
        prior_song_id, pending_queue_change = _previous_action_target(
            state.get("prior_task_results", [])
        )
        if prior_song_id is not None:
            return {
                "target_song_id": prior_song_id,
                "target_source": SongTargetSource.PRIOR_PLAY_ACTION,
            }
        if pending_queue_change:
            return {
                "answer": PENDING_QUEUE_CHANGE_MESSAGE,
                "task_result": TaskResult(
                    task_id=state["task_id"],
                    route_key=RouteKey.CURRENT_SONG,
                    status=TaskStatus.NEEDS_INPUT,
                    content=PENDING_QUEUE_CHANGE_MESSAGE,
                    data={"reason": "pending_queue_change"},
                ),
            }

        context = state["routing_context"]
        if not context.has_current_song or context.current_song_id is None:
            return {
                "answer": NO_CURRENT_SONG_MESSAGE,
                "task_result": TaskResult(
                    task_id=state["task_id"],
                    route_key=RouteKey.CURRENT_SONG,
                    status=TaskStatus.NEEDS_INPUT,
                    content=NO_CURRENT_SONG_MESSAGE,
                    data={"reason": "no_current_song"},
                ),
            }
        return {
            "target_song_id": context.current_song_id,
            "target_source": SongTargetSource.PLAYER_CONTEXT,
        }

    async def load_song_context(state: CurrentSongState) -> dict[str, Any]:
        song_id = state.get("target_song_id")
        if song_id is None:
            raise RuntimeError("CurrentSong target was not resolved")
        try:
            song_context = await context_provider.get_song(song_id)
        except Exception:
            logger.warning(
                "CurrentSong context resolution failed: song_id=%d",
                song_id,
                exc_info=True,
            )
            return {
                "answer": CONTEXT_UNAVAILABLE_MESSAGE,
                "task_result": TaskResult(
                    task_id=state["task_id"],
                    route_key=RouteKey.CURRENT_SONG,
                    status=TaskStatus.FAILED,
                    content=CONTEXT_UNAVAILABLE_MESSAGE,
                    error=CONTEXT_UNAVAILABLE_MESSAGE,
                    data={
                        "song_id": song_id,
                        "target_source": (
                            state["target_source"].value
                            if state.get("target_source") is not None
                            else None
                        ),
                    },
                ),
            }
        return {"song_context": song_context}

    async def agent(state: CurrentSongState) -> dict[str, Any]:
        song_context = state.get("song_context")
        if song_context is None:
            raise RuntimeError("CurrentSong context was not loaded")
        if assistant is not None:
            response = await assistant(
                state["messages"],
                state["current_input"],
                song_context,
                state["routing_context"],
            )
        else:
            response = await answer_current_song_task(
                state["messages"],
                state["current_input"],
                song_context,
                state["routing_context"],
                model=model,
                continuity_context=state.get("continuity_context"),
            )
        if not isinstance(response, AIMessage):
            raise RuntimeError("Current-song assistant returned a non-AI message")
        if response.tool_calls:
            raise RuntimeError("Current-song assistant attempted to call a tool")
        content = _visible_text(response)
        return {
            "messages": [response],
            "answer": content,
            "task_result": TaskResult(
                task_id=state["task_id"],
                route_key=RouteKey.CURRENT_SONG,
                status=TaskStatus.SUCCESS,
                content=content,
                data={
                    "song": {
                        "song_id": song_context.song_id,
                        "name": song_context.name,
                        "artist": song_context.artist,
                        "album": song_context.album,
                        "lyrics_status": song_context.lyrics_status,
                    },
                    "target_source": (
                        state["target_source"].value
                        if state.get("target_source") is not None
                        else None
                    ),
                    "rag_used": False,
                },
            ),
        }

    builder = StateGraph(CurrentSongState, output_schema=SubGraphOutput)
    builder.add_node("resolve_current_song", resolve_current_song)
    builder.add_node("load_song_context", load_song_context)
    builder.add_node("agent", agent)
    builder.add_edge(START, "resolve_current_song")
    builder.add_conditional_edges(
        "resolve_current_song",
        _route_after_resolution,
        {"load_song_context": "load_song_context", END: END},
    )
    builder.add_conditional_edges(
        "load_song_context",
        _route_after_context,
        {"agent": "agent", END: END},
    )
    builder.add_edge("agent", END)
    return builder


def create_current_song_subgraph(
    *,
    provider: MusicContextProvider | None = None,
    assistant: CurrentSongAssistant | None = None,
    model: BaseChatModel | None = None,
) -> CompiledStateGraph:
    return build_current_song_subgraph(
        provider=provider,
        assistant=assistant,
        model=model,
    ).compile(checkpointer=False)

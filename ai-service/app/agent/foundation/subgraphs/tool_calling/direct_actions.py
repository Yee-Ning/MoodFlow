"""Deterministic fast path for rule-routed player commands."""

from hashlib import sha256
from typing import Any

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.tool_calling.models import (
    ClientAction,
    ClientActionName,
    EmptyActionArguments,
    ReferencedEntities,
    SetLikedArguments,
    SetVolumeArguments,
)
from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState


DIRECT_SUB_INTENTS = frozenset(
    {
        "pause_music",
        "resume_music",
        "next_music",
        "previous_music",
        "favorite_song",
        "lower_volume",
    }
)

_PLAYER_ACTIONS = {
    "pause_music": ClientActionName.PAUSE_PLAYER,
    "resume_music": ClientActionName.RESUME_PLAYER,
    "next_music": ClientActionName.PLAY_NEXT,
    "previous_music": ClientActionName.PLAY_PREVIOUS,
}

_ACTION_CONTENT = {
    "pause_music": "已向播放器发送暂停指令。",
    "resume_music": "已向播放器发送继续播放指令。",
    "next_music": "已向播放器发送播放下一首的指令。",
    "previous_music": "已向播放器发送播放上一首的指令。",
    "favorite_song": "已向播放器发送收藏当前歌曲的指令。",
    "lower_volume": "已向播放器发送降低音量的指令。",
}


def make_action_id(task_id: str, token: str) -> str:
    candidate = f"{task_id}:{token}"
    if len(candidate) <= 128:
        return candidate
    digest = sha256(candidate.encode("utf-8")).hexdigest()
    return f"action-{digest}"


def _needs_player(sub_intents: list[str]) -> str | None:
    if any(item in _PLAYER_ACTIONS or item == "favorite_song" for item in sub_intents):
        return "请先选择并播放一首歌曲，再执行这个操作。"
    return None


def resolve_direct_actions(state: ToolCallingState) -> dict[str, Any]:
    intents = state.get("routing_intents", [])
    sub_intents = list(dict.fromkeys(item.sub_intent for item in intents))
    if not sub_intents or any(item not in DIRECT_SUB_INTENTS for item in sub_intents):
        return {"direct_complete": False}

    context = state["routing_context"]
    missing_player = _needs_player(sub_intents)
    if missing_player and (
        not context.has_current_song or context.current_song_id is None
    ):
        return {
            "direct_complete": True,
            "answer": missing_player,
            "task_result": TaskResult(
                task_id=state["task_id"],
                route_key=RouteKey.TOOL_CALLING,
                status=TaskStatus.NEEDS_INPUT,
                content=missing_player,
                data={"client_actions": [], "entities": ReferencedEntities().model_dump(mode="json")},
            ),
        }

    actions: list[ClientAction] = []
    for index, sub_intent in enumerate(sub_intents):
        action_id = make_action_id(state["task_id"], f"direct:{index}")
        if sub_intent in _PLAYER_ACTIONS:
            actions.append(
                ClientAction(
                    action_id=action_id,
                    task_id=state["task_id"],
                    name=_PLAYER_ACTIONS[sub_intent],
                    arguments=EmptyActionArguments(),
                )
            )
        elif sub_intent == "favorite_song":
            actions.append(
                ClientAction(
                    action_id=action_id,
                    task_id=state["task_id"],
                    name=ClientActionName.SET_LIKED,
                    arguments=SetLikedArguments(
                        song_id=context.current_song_id,
                        liked=True,
                    ),
                )
            )
        elif sub_intent == "lower_volume":
            actions.append(
                ClientAction(
                    action_id=action_id,
                    task_id=state["task_id"],
                    name=ClientActionName.SET_VOLUME,
                    arguments=SetVolumeArguments(
                        volume=round(max(0.0, context.volume - 0.15), 2)
                    ),
                )
            )

    content = "\n".join(_ACTION_CONTENT[item] for item in sub_intents)
    entities = ReferencedEntities()
    return {
        "direct_complete": True,
        "client_actions": actions,
        "answer": content,
        "task_result": TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.TOOL_CALLING,
            status=TaskStatus.SUCCESS,
            content=content,
            data={
                "client_actions": [
                    action.model_dump(mode="json") for action in actions
                ],
                "entities": entities.model_dump(mode="json"),
                "tool_calls": 0,
            },
        ),
    }

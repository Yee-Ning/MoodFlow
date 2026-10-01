"""Deterministic player commands and cloud likes without model calls."""

import json
import re
from hashlib import sha256
from typing import Any

from langgraph.runtime import Runtime

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import IntentType, RouteKey
from app.agent.foundation.rule_routing import RuleRouter
from app.agent.foundation.subgraphs.tool_calling.models import (
    ClientAction,
    ClientActionName,
    EmptyActionArguments,
    JumpToQueueItemArguments,
    ReferencedEntities,
    SetLikedArguments,
    SetVolumeArguments,
)
from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState
from app.agent.foundation.rule_routing.normalizer import normalize_text
from app.schemas.context import MusicAgentContext
from app.tools.music import MusicServiceClient, handle_music_tool_error


DIRECT_SUB_INTENTS = frozenset(
    {
        "pause_music",
        "resume_music",
        "next_music",
        "previous_music",
        "favorite_song",
        "unfavorite_song",
        "open_lyrics",
        "clear_queue",
        "jump_to_queue_index",
        "lower_volume",
    }
)

_PLAYER_ACTIONS = {
    "pause_music": ClientActionName.PAUSE_PLAYER,
    "resume_music": ClientActionName.RESUME_PLAYER,
    "next_music": ClientActionName.PLAY_NEXT,
    "previous_music": ClientActionName.PLAY_PREVIOUS,
    "open_lyrics": ClientActionName.OPEN_LYRICS,
    "clear_queue": ClientActionName.CLEAR_QUEUE,
}

_ACTION_CONTENT = {
    "pause_music": "已向播放器发送暂停指令。",
    "resume_music": "已向播放器发送继续播放指令。",
    "next_music": "已向播放器发送播放下一首的指令。",
    "previous_music": "已向播放器发送播放上一首的指令。",
    "lower_volume": "已向播放器发送降低音量的指令。",
    "open_lyrics": "已向播放器发送展开歌词的指令。",
    "clear_queue": "已向播放器发送清空队列并停止播放的指令。",
    "jump_to_queue_index": "已向播放器发送跳到指定队列位置的指令。",
}


def make_action_id(task_id: str, token: str) -> str:
    candidate = f"{task_id}:{token}"
    if len(candidate) <= 128:
        return candidate
    digest = sha256(candidate.encode("utf-8")).hexdigest()
    return f"action-{digest}"


def _needs_player(sub_intents: list[str]) -> str | None:
    needs_player = {*_PLAYER_ACTIONS, "favorite_song", "unfavorite_song"} - {"clear_queue"}
    if any(item in needs_player for item in sub_intents):
        return "请先选择并播放一首歌曲，再执行这个操作。"
    return None


def queue_index_from_text(text: str) -> int | None:
    match = re.search(r"第\s*([0-9]+|[一二三四五六七八九十百]+)\s*首", normalize_text(text).text)
    if match is None:
        return None
    value = match.group(1)
    if value.isdecimal():
        return int(value)
    if not re.fullmatch(r"[一二三四五六七八九]|[一二三四五六七八九]?十[一二三四五六七八九]?|一百", value):
        return None
    digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
    total, digit = 0, 0
    for char in value:
        if char in {"十", "百"}:
            total += (digit or 1) * (10 if char == "十" else 100)
            digit = 0
        else:
            digit = digits[char]
    return total + digit


async def resolve_direct_actions(
    state: ToolCallingState, runtime: Runtime[MusicAgentContext],
) -> dict[str, Any]:
    intents = state.get("routing_intents", [])
    # Semantic routing may identify the domain without a sub-intent. Refine
    # only this assigned clause, and only when existing rules cover it fully.
    # History, prior Explorer output and transfer summaries grant no authority.
    if intents and all(
        item.intent is IntentType.TOOL_CALLING and item.sub_intent is None
        for item in intents
    ):
        decision = RuleRouter().route(state["current_input"], state["routing_context"])
        if decision.is_complete and all(
            item.sub_intent in DIRECT_SUB_INTENTS for item in decision.intents
        ):
            intents = decision.intents
    sub_intents = list(dict.fromkeys(item.sub_intent for item in intents))
    if not sub_intents or any(item not in DIRECT_SUB_INTENTS for item in sub_intents):
        return {"direct_complete": False}
    if sum(item.sub_intent == "jump_to_queue_index" for item in intents) > 1:
        return {"direct_complete": False}
    queue_target = None
    if "jump_to_queue_index" in sub_intents:
        evidence = next(item.evidence for item in intents if item.sub_intent == "jump_to_queue_index")
        queue_target = queue_index_from_text(evidence)
        if queue_target is None:
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
    records: list[dict[str, Any]] = []
    contents: list[str] = []
    failures: list[str] = []
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
        elif sub_intent in {"favorite_song", "unfavorite_song"}:
            liked = sub_intent == "favorite_song"
            session_cookie = runtime.context.session_cookie if runtime.context else None
            try:
                result = await MusicServiceClient(session_cookie).set_song_liked(context.current_song_id, liked)
            except Exception as exc:
                failure = handle_music_tool_error(exc)
                message = json.loads(failure)["message"]
                failures.append(message)
                contents.append(message)
                records.append({"tool_call_id": action_id, "name": "set_song_liked_tool",
                                "content": failure, "status": "error"})
                continue
            records.append({"tool_call_id": action_id, "name": "set_song_liked_tool",
                            "content": result.model_dump_json(), "status": "success"})
            actions.append(
                ClientAction(
                    action_id=action_id,
                    task_id=state["task_id"],
                    name=ClientActionName.SET_LIKED,
                    arguments=SetLikedArguments(
                        song_id=context.current_song_id,
                        liked=liked,
                    ),
                )
            )
            contents.append(result.message + "，并发送播放器红心更新。")
            continue
        elif sub_intent == "jump_to_queue_index":
            target = queue_target
            assert target is not None
            if target < 1 or target > context.queue_size:
                failures.append("目标位置超出当前播放队列范围，请选择队列内的歌曲序号。")
                contents.append(failures[-1])
                continue
            actions.append(ClientAction(
                action_id=action_id, task_id=state["task_id"],
                name=ClientActionName.JUMP_TO_QUEUE_ITEM,
                arguments=JumpToQueueItemArguments(index=target),
            ))
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

        contents.append(_ACTION_CONTENT[sub_intent])

    content = "\n".join(contents)
    entities = ReferencedEntities()
    return {
        "direct_complete": True,
        "client_actions": actions,
        "tool_results": records,
        "answer": content,
        "task_result": TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.TOOL_CALLING,
            status=TaskStatus.FAILED if failures else TaskStatus.SUCCESS,
            content=content,
            data={
                "client_actions": [
                    action.model_dump(mode="json") for action in actions
                ],
                "entities": entities.model_dump(mode="json"),
                "tool_calls": len(records),
                "tool_call_details": [{"name": item["name"], "status": item["status"]} for item in records],
            },
            error=failures[0] if failures else None,
        ),
    }

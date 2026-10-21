"""Narrow, fail-closed authority for additions to this task's new playlist."""

import json
import re
from contextvars import ContextVar

from langchain_core.messages import AIMessage, ToolMessage

from app.agent.foundation.agent_history_source import SCOPE_FIELDS, invocation_scope
from app.agent.foundation.agent_pack import AgentPackInputs
from app.agent.foundation.business_pack import business_invocation
from app.agent.foundation.routing import RouteKey


# Producer context is available to explicit assistant implementations. Merely
# injecting an assistant does NOT bind its output or any incoming history.
producer_state = ContextVar("tool_calling_producer_state", default=None)


def current_invocation(state):
    return business_invocation(
        RouteKey.TOOL_CALLING, state["current_input"], phase="tool_step",
        prompt_refs=("curator-tools-base/v1",), route_task_id=state["task_id"],
        invocation_run_id=state.get("pack_run_id"),
        inputs=AgentPackInputs(continuity_context=state.get("continuity_context")),
    )


def _owns(metadata, scope):
    # An explicit anonymous user_id=None is the existing invocation contract for
    # local callers. Missing user_id is NOT equivalent to that explicit value.
    return all(key in metadata and metadata[key] == scope[key] for key in SCOPE_FIELDS) and all(
        isinstance(metadata[key], str) and metadata[key] for key in SCOPE_FIELDS[:3]
    )


def capture_created_receipts(state):
    """Preserve the original bound carrier, never backfill unknown attribution."""
    receipts = []
    for message in state["messages"]:
        if not isinstance(message, ToolMessage) or message.name != "create_playlist_tool":
            continue
        receipts.append({
            "tool_call_id": message.tool_call_id, "content": message.content,
            "status": message.status,
            "executed_create": message.additional_kwargs.get("executed_create") is True,
            "scope": {key: message.additional_kwargs[key] for key in SCOPE_FIELDS
                      if key in message.additional_kwargs},
        })
    return receipts


def inherited_playlist_id(state, call):
    """Require a uniquely paired create receipt AND an independent current anchor."""
    if call.get("name") != "add_song_to_playlist" or not state.get("pack_run_id"):
        return None
    playlist_id = (call.get("args") or {}).get("playlist_id")
    if type(playlist_id) is not int or playlist_id <= 0:
        return None
    scope = invocation_scope(current_invocation(state))
    latest = state["messages"][-1]
    if not isinstance(latest, AIMessage) or not _owns(latest.additional_kwargs, scope):
        return None
    if call not in latest.tool_calls:
        return None
    successful = []
    for receipt in state.get("created_playlist_receipts", []):
        if (receipt.get("executed_create") is not True or receipt.get("status") != "success"
                or not _owns(receipt.get("scope", {}), scope)):
            continue
        creators = [message for message in state["messages"] if isinstance(message, AIMessage)
                    and _owns(message.additional_kwargs, scope)
                    for creation in message.tool_calls
                    if creation.get("name") == "create_playlist_tool"
                    and creation.get("id") == receipt.get("tool_call_id")]
        if len(creators) != 1:
            return None
        try:
            body = json.loads(receipt["content"])
        except (KeyError, TypeError, ValueError):
            return None
        if not isinstance(body, dict) or body.get("status") != "created" or body.get("error"):
            return None
        new_id = body.get("playlist_id")
        if type(new_id) is not int or new_id <= 0:
            return None
        successful.append(new_id)
    return playlist_id if successful == [playlist_id] else None


_QUOTED = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|《[^》]*》|"[^"\n]*"|\'[^\'\n]*\'|`[^`]*`')
_ADD = re.compile(r"(?:加入|添加|放进|加上).{0,30}(?:歌曲|音乐|首|曲)")
_UNSAFE = re.compile(
    r"[?？]|如果|若|假如|假设|要是|倘若|是否|能否|能不能|可不可以|可以吗|好吗|行吗|要不要|"
    r"但|不过|然而|只是|只(?:查看|看|讨论|搜索)|引用|标题|讨论|"
    r"不要|不用|无需|不再|别|取消|先不|暂不|不加|不创建|不新建|"
    r"等(?:到|待)?|待.+(?:后|时)|当.+(?:后|时)|有空|如有|前提|才能|的话|之后再"
)


def requests_inherited_add(text, create_pattern, clause_splitter, negation):
    """Only adjacent explicit create/add requests, with one inert modifier at most."""
    if _UNSAFE.search(text):
        return False
    quoted = _QUOTED.findall(text)
    if any(_ADD.search(value) for value in quoted):
        return False
    masked = _QUOTED.sub("名称值", text)
    # An unmatched quote is not an actionable instruction either.
    if re.search(r'[“”「」『』《》"\'`]', masked):
        return False
    clauses = [part.strip() for part in clause_splitter.split(masked) if part.strip()]
    creates = [(i, match) for i, clause in enumerate(clauses)
               for match in re.finditer(create_pattern, clause)
               if not negation.search(clause[:match.start()])]
    adds = [i for i, clause in enumerate(clauses) if _ADD.search(clause)]
    if len(creates) != 1 or len(adds) != 1:
        return False
    start, end = creates[0][0], adds[0]
    if end - start not in {1, 2}:
        return False
    if sum(clause.count("歌单") for clause in clauses[:end + 1]) != 1:
        return False
    if re.search(r"另一|其他|其它|分别|两个|两份|多个|这些|那些|各个", masked):
        return False
    addition = clauses[end]
    if not re.fullmatch(r"(?:请|帮我|为我|并|再|给我)*\s*(?:加入|添加|放进|加上).{0,30}(?:歌曲|音乐|首歌|曲)", addition):
        return False
    if re.search(r"群|队列|列表|专辑|收藏|到|至(?!少)|给", addition):
        return False
    if end == start + 2:
        modifier = clauses[start + 1]
        if re.search(r"搜索|播放|创建|新建|加入|添加|删除|移除|收藏|打开|清空|取消", modifier):
            return False
        if not re.fullmatch(
            r"(?:名称(?:为|是)|命名为|名叫|叫)(?:名称值|[\w\-]{1,40})|(?:设为|设置为)?(?:私密|公开)(?:歌单)?",
            modifier,
        ):
            return False
    return True

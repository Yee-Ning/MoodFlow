"""Curator consumes the frozen Journey queue through existing guarded music tools."""

import re

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.foundation.long_horizon.operations import journal_tool_call
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.tool_calling.graph import (
    _action_intent_errors,
    _extract_client_actions,
    _extract_entities,
    _reference_errors,
)
from app.agent.foundation.subgraphs.tool_calling.state import ToolCallingState
from app.agent.handoff.chain_receivers import accepted_envelope, explorer_result
from app.agent.handoff.journey import JourneyTransferPayload, ListeningJourney
from app.agent.handoff.registry import AgentKey
from app.schemas.context import MusicAgentContext
from app.schemas.music import MusicSearchResult
from app.tools.music import handle_music_tool_error


async def consume_journey(state, run, runtime, *, tools):
    source = explorer_result(run)
    if source is None:
        return None
    envelope = accepted_envelope(state, run)
    if envelope is None or envelope.source_agent not in {
        AgentKey.EXPLORER,
        AgentKey.GENERAL_CHAT,
    }:
        return None
    task = run.pending_tasks[0]
    journey = ListeningJourney.model_validate(source.data["listening_journey"])
    previous = [
        r.data["journey_consumption"]
        for r in run.results
        if r.data.get("journey_consumption", {}).get("explorer_task_id")
        == source.task_id
    ]
    if envelope.source_agent is AgentKey.GENERAL_CHAT and not previous:
        return None  # unrelated later playback is not a Journey continuation
    if not re.search(
        r"Journey|聆听|路径|旅程", task.input_text, re.IGNORECASE
    ) and not (
        previous and re.search(r"继续.*播|接着.*播|下一.*节点", task.input_text)
    ):
        return None
    records, processed = [], []
    start = sum(len(row["node_ids"]) for row in previous)

    def finish(content, status=TaskStatus.SUCCESS, reason=None):
        actions = _extract_client_actions(
            {"task_id": task.task_id, "tool_results": records}
        )
        return TaskResult(
            task_id=task.task_id,
            route_key=RouteKey.TOOL_CALLING,
            status=status,
            content=content,
            error=content if status is TaskStatus.FAILED else None,
            data={
                "journey_consumption": {
                    "explorer_task_id": source.task_id,
                    "node_ids": [n.node_id for n in processed],
                    "knowledge_node_ids": [
                        n.node_id for n in processed if n.song_id is None
                    ],
                    "next_index": start + len(processed),
                    "total": len(journey.nodes),
                },
                "client_actions": [a.model_dump(mode="json") for a in actions],
                "entities": _extract_entities(records).model_dump(mode="json"),
                "tool_calls": len(records),
                "tool_call_details": [
                    {"name": r["name"], "status": r["status"]} for r in records
                ],
                "reason": reason,
            },
        )

    if not journey.nodes:
        return finish(
            "Journey 队列为空，没有生成播放指令；请先提供有来源的探索节点。",
            TaskStatus.NEEDS_INPUT,
            "empty_journey",
        )
    if envelope.source_agent is AgentKey.EXPLORER:
        try:
            payload = JourneyTransferPayload.model_validate_json(
                envelope.request.context_summary
            )
            if payload != journey.transfer_payload():
                raise ValueError("Journey queue differs from Explorer result")
        except ValueError:
            return finish(
                "Journey 转交队列无法与 Explorer 原结果核对，尚未生成播放指令。",
                TaskStatus.NEEDS_INPUT,
                "invalid_journey_payload",
            )
    expected_prefix = [node.node_id for node in journey.nodes[:start]]
    if [node_id for row in previous for node_id in row["node_ids"]] != expected_prefix:
        return finish(
            "Journey 消费进度无法核对，未重复生成播放指令。",
            TaskStatus.NEEDS_INPUT,
            "invalid_journey_progress",
        )
    if start >= len(journey.nodes):
        return finish(
            "Journey 队列已消费完毕，没有重复生成播放指令。",
            TaskStatus.NEEDS_INPUT,
            "journey_exhausted",
        )
    # Existing planned tasks define the alternation. Never add tasks or change routing.
    later_curator = any(
        t.route_key is RouteKey.TOOL_CALLING for t in run.pending_tasks[1:]
    )
    segment = (
        journey.nodes[start : start + 1] if later_curator else journey.nodes[start:]
    )
    scoped = [tool for tool in tools if tool.name in {"search_music", "play_song"}]
    if any(node.song_id for node in segment) and {tool.name for tool in scoped} != {
        "search_music",
        "play_song",
    }:
        return finish(
            "Journey 播放所需的现有目录搜索/播放工具不可用，未生成播放指令。",
            TaskStatus.FAILED,
            "missing_journey_tools",
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

    async def invoke(name, args):
        call = {
            "name": name,
            "args": args,
            "id": f"{task.task_id}:journey:{len(records)}",
            "type": "tool_call",
        }
        tool_state = {
            "messages": [AIMessage(content="", tool_calls=[call])],
            "current_input": task.input_text,
            "routing_context": state["routing_context"],
            "prior_entities": [],
            "tool_results": records,
        }
        if _action_intent_errors(tool_state) or _reference_errors(tool_state):
            raise ValueError(
                "Journey tool lacks current authorization or catalog reference"
            )
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
        if response.status != "success":
            raise ValueError("Journey tool failed")
        return response.content

    try:
        tracks = {}
        # Validate this segment before generating any action. Knowledge IDs never become music IDs.
        for node in segment:
            if node.song_id is None:
                continue
            raw = await invoke(
                "search_music", {"keyword": node.song_title, "limit": 50}
            )
            result = MusicSearchResult.model_validate_json(raw)
            if result.keyword != node.song_title:
                raise ValueError("catalog query mismatch")
            matches = [
                song
                for song in result.songs
                if song.song_id == node.song_id and song.name == node.song_title
            ]
            if len(matches) != 1:
                raise ValueError("Journey song unavailable or ambiguous in catalog")
            tracks[node.node_id] = matches[0]
        for node in segment:
            if node.song_id is not None:
                await invoke(
                    "play_song", {"song": tracks[node.node_id].model_dump(mode="json")}
                )
            processed.append(node)
    except Exception:
        return finish(
            "Journey 的目录核对或播放指令生成失败，已停止后续节点；已有指令仍待客户端执行/确认。",
            TaskStatus.FAILED,
            "journey_tool_failed",
        )
    playable = sum(node.song_id is not None for node in processed)
    knowledge = len(processed) - playable
    return finish(
        f"已按 Journey 顺序生成 {playable} 条播放指令，待客户端执行/确认；{knowledge} 个纯知识节点仅记录讲解，没有播放动作。"
    )

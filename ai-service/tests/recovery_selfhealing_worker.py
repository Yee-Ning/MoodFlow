"""Test subprocess: real PG saver/coordinator, controlled HTTP music and models."""

import asyncio
import json
import os
import sys

from langchain_core.messages import AIMessage

from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator, ContinuityCheckpointer
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.runner import invoke
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.graph import create_handoff_graph
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import HandoffRun
from tests.test_handoff_network import transfers, worldline
from tests.test_handoff_runtime import Router, ToolModel, call, player_context


async def execute():
    mode, point, thread = sys.argv[1:4]
    store = PostgresContinuityStore(os.environ["CONTEXT_LOOP_TEST_DATABASE"])
    coordinator = ContinuityCoordinator(LongHorizonContextService(store))
    raw = AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer())
    task = store.active_task("handoff:v1:" + thread)
    session = store.active_session(task.task_id) if task else None
    config = {"configurable": {"thread_id": "handoff:v1:" + thread, "session_id": session.session_id}} if session else None
    row = await raw.aget_tuple(config) if config else None
    saved = HandoffRun.model_validate(row.checkpoint["channel_values"]["handoff_run"]) if row else None
    target = RouteKey.WORLDLINE if point == "before_journal_committed" else RouteKey.TOOL_CALLING
    model = transfers((AgentKey.GENERAL_CHAT, AgentKey.CURATOR, AgentKey.EXPLORER))
    if saved:
        model.responses = model.responses[len(saved.hops):] or model.responses[-1:]
    if point == "after_write_before_result":
        tool_name, args = "create_playlist_tool", {"name": "MoodFlowTest-process-fixture", "privacy": 10}
    elif point == "after_add_before_result":
        tool_name, args = "add_song_to_playlist", {"playlist_id": 18445771229, "song_id": 2652820720}
    else:
        tool_name, args = "pause_player", {}
    steps = [call("get_user_playlists", args={}, call_id="read-owned")] if point == "after_add_before_result" else []
    curator = ToolModel(responses=[*steps, call(tool_name, args=args, call_id="stable-write"), AIMessage(content="已处理")])
    graph = create_handoff_graph(checkpointer=ContinuityCheckpointer(raw, coordinator), transfer_journal=coordinator.journal,
        intent_router=Router(target), transfer_model=model, curator_model=curator,
        worldline_subgraph=worldline())
    if mode == "crash":
        append = coordinator.writer.append_public_event
        def kill_before_receipt(identity, key, kind, body, **kwargs):
            if (point in {"after_write_before_result", "after_add_before_result"}
                and kind == HistoryEventType.TOOL_RESULT and body.effect == "write" and body.status == "success"):
                os._exit(73)
            if point == "before_action_ack" and kind == HistoryEventType.ACTION_ACK:
                os._exit(73)
            return append(identity, key, kind, body, **kwargs)
        coordinator.writer.append_public_event = kill_before_receipt
        transfer = coordinator.writer.transfer
        def kill_before_commit(identity, key, body):
            if point == "before_journal_committed" and body.phase == "committed":
                os._exit(73)
            return transfer(identity, key, body)
        coordinator.writer.transfer = kill_before_commit
    text = {"after_write_before_result": "新建私密歌单 MoodFlowTest-process-fixture",
        "after_add_before_result": "把当前歌曲加入歌单 MoodFlowTest-existing-fixture",
        "before_action_ack": "暂停播放", "before_journal_committed": "探索音乐世界线"}[point]
    context = player_context().model_copy(update={"current_song_id": 2652820720})
    response = await invoke(graph=graph, coordinator=coordinator, graph_mode="handoff", thread_id=thread,
        message=text if mode == "crash" else "",
        routing_context=context, request_id="original" if mode == "crash" else "recover",
        resume=mode != "crash", user_id="test-user", session_cookie="JSESSIONID=test-process")
    task = store.active_task("handoff:v1:" + thread)
    session = store.active_session(task.task_id)
    if mode == "crash" and point == "before_action_ack":
        actions = store.query_task_history(task.task_id, event_type=HistoryEventType.CLIENT_ACTION)
        assert len(actions) == 1
        # Controlled player applies once outside the dying ACK transaction.
        import httpx
        httpx.post(os.environ["SPRING_BOOT_BASE_URL"] + "/test/player-applied", timeout=5)
        await coordinator.acknowledge(graph_mode="handoff", thread_id=thread, user_id="test-user",
            action_id=actions[0].payload["action_id"], status="applied")
    latest = await raw.aget_tuple({"configurable": {"thread_id": "handoff:v1:" + thread, "session_id": session.session_id}})
    run = HandoffRun.model_validate(latest.checkpoint["channel_values"]["handoff_run"])
    view = coordinator.recovery_status(graph_mode="handoff", thread_id=thread, user_id="test-user")
    print(json.dumps({"response_status": str(response.status), "message": response.message,
        "run_id": run.run_id, "started_at": run.started_at.isoformat(), "limits": run.limits.model_dump(mode="json"),
        "usage": [item.model_dump(mode="json") for item in run.usage], "diagnostics": view}, ensure_ascii=False))
    store.close()


asyncio.run(execute())

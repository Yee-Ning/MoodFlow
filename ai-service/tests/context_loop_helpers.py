"""Real local persistence and graph transitions for context-loop fault tests."""

from datetime import UTC, datetime
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import empty_checkpoint

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.models import ContinuityIdentity
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.agent.handoff.continuity import AgentTransferJournal, JournalCheckpointer, snapshot_from_row
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import HandoffRun, new_run
from app.agent.handoff.tools import execute_transfer


def harness(path, *, routes=(RouteKey.WORLDLINE,), suffix="one"):
    store = SQLiteContinuityStore(path)
    service = LongHorizonContextService(store)
    session = service.open_conversation(f"handoff:v1:{suffix}")
    text = "探索音乐世界线并暂停播放"
    run = new_run(
        f"run:{suffix}", text,
        RouteDecision(is_complete=True, tasks=[
            RouteTask(task_id=f"task:{index}", route_key=route, input_text=text)
            for index, route in enumerate(routes)
        ]), started_at=datetime.now(UTC), entry_agent=AgentKey.GENERAL_CHAT,
    )
    identity = ContinuityIdentity(
        conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id=f"request:{suffix}", graph_mode="handoff",
        run_id=run.run_id,
    )
    writer = HistoryWriter(store)
    journal = AgentTransferJournal(writer)
    saver = JournalCheckpointer(create_memory_checkpointer(), journal)
    config = {"configurable": {"thread_id": identity.conversation_id, "session_id": identity.session_id}}
    return SimpleNamespace(
        store=store, service=service, session=session, run=run, identity=identity,
        writer=writer, journal=journal, saver=saver, config=config, messages=[],
    )


async def transfer(h, target, *, call_id="call:one", guard=None, summary="公开的任务进度"):
    message = AIMessage(content="private intermediate model reasoning", tool_calls=[
        {"id": call_id, "name": f"transfer_to_{target}_agent",
         "args": {"reason": "转交下一能力", "context_summary": summary}},
    ])
    command = await execute_transfer(
        message, h.run, journal=h.journal, identity=h.identity, guard=guard,
    )
    h.messages.extend(command.update["messages"])
    h.run = HandoffRun.model_validate(command.update["handoff_run"])
    h.envelope = command.update.get("transfer_envelope")
    return command


def checkpoint(h, *, saver=None):
    row = empty_checkpoint()
    row["channel_values"] = {
        "handoff_run": h.run.model_dump(mode="json"), "messages": h.messages,
        "transfer_envelope": getattr(h, "envelope", None),
        "continuity_context": {
            "conversation_id": h.identity.conversation_id, "task_id": h.identity.continuity_task_id,
            "session_id": h.identity.session_id, "request_id": h.identity.request_id,
        },
    }
    versions = {key: 1 for key in row["channel_values"]}
    row["channel_versions"] = versions
    h.config = (saver or h.saver).put(h.config, row, {"source": "loop", "step": 1}, versions)
    saved = h.saver.get_tuple(h.config)
    ancestors, parent = [], saved.parent_config
    while parent:
        prior = h.saver.get_tuple(parent)
        ancestors.append(prior.checkpoint["id"])
        parent = prior.parent_config
    return snapshot_from_row(saved, h.identity, ancestors=ancestors)

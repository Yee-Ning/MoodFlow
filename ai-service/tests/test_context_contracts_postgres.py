"""Contract storage stays single-source across a real PostgreSQL reopen."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.journal_contracts import JournalReader
from app.agent.foundation.long_horizon.models import ContinuityIdentity, EventPointer, HandoffDraftV2, HistoryEventType
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.agent.handoff.continuity import AgentTransferJournal, JournalCheckpointer
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import new_run
from tests.context_loop_helpers import checkpoint, transfer
from tests.test_context_loop_postgres_recovery import BASE, database

__all__ = ["database"]
pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


@pytest.mark.asyncio
async def test_postgres_reference_archives_and_receipts_survive_reopen(database):
    store = PostgresContinuityStore(database)
    service = LongHorizonContextService(store)
    session = service.open_conversation("phase3-contracts")
    run = new_run("run:contracts", "探索音乐世界线", RouteDecision(is_complete=True, tasks=[
        RouteTask(task_id="route:one", route_key=RouteKey.WORLDLINE, input_text="探索音乐世界线")]),
        started_at=datetime.now(UTC), entry_agent=AgentKey.GENERAL_CHAT)
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="request:contracts", graph_mode="handoff", run_id=run.run_id)
    writer = HistoryWriter(store)
    journal = AgentTransferJournal(writer)
    h = SimpleNamespace(store=store, service=service, session=session, run=run, identity=identity, writer=writer,
        journal=journal, saver=JournalCheckpointer(create_memory_checkpointer(), journal), messages=[], envelope=None,
        config={"configurable": {"thread_id": session.conversation_id, "session_id": session.session_id}})
    await transfer(h, "curator")
    checkpoint(h)
    events = store.query_task_history(session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)
    assert "summary" not in events[1].payload
    assert JournalReader(store).transfer(events[1]).summary == JournalReader(store).transfer(events[0]).summary
    projection = service.build_handoff(session.session_id, HandoffDraftV2(goal="探索音乐", current_stage="待执行", summary="有界公开摘要"))
    store.put_handoff(projection)
    with store._pool.connection() as connection:
        raw = connection.execute("SELECT body FROM agent_handoff WHERE handoff_id=%s", (projection.handoff_id,)).fetchone()["body"]
        assert "summary" not in raw and raw["storage_schema"] == "history-pointer:v1"
    store.close()
    reopened = PostgresContinuityStore(database)
    try:
        assert reopened.get_handoff(projection.handoff_id).model_dump() == projection.model_dump()
        event = JournalReader(reopened).resolve(EventPointer(task_id=session.task_id, event_id=events[1].event_id))
        assert JournalReader(reopened).transfer(event).summary.payload_digest is not None
        assert len(reopened.query_task_history(session.task_id, event_type=HistoryEventType.SESSION_PROJECTION)) == 1
    finally:
        reopened.close()

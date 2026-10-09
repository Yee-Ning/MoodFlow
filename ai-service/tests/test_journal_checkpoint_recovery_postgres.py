"""Actual saver round trips in the existing disposable loopback PG fixture."""

import json

import pytest

from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.journal_rebuild import JournalCheckpointRebuilder
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.handoff.continuity import AgentTransferJournal, JournalCheckpointer
from tests.context_loop_helpers import checkpoint, harness, transfer
from tests.test_context_loop_postgres_recovery import BASE, database

__all__ = ["database"]
pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


def postgres_harness(tmp_path, database):
    h = harness(tmp_path / "fixture.db")
    store = PostgresContinuityStore(database)
    store.create_task(h.store.get_task(h.session.task_id))
    store.create_session(h.session)
    h.store = store
    h.service = LongHorizonContextService(store)
    h.writer = HistoryWriter(store)
    h.journal = AgentTransferJournal(h.writer)
    h.saver = JournalCheckpointer(AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer()), h.journal)
    return h


@pytest.mark.asyncio
async def test_checkpoint_loss_and_prefix_versions_round_trip_without_duplicate_receipts(tmp_path, database):
    h = postgres_harness(tmp_path, database)
    try:
        await transfer(h, "curator")
        checkpoint(h)
        h.saver.saver.delete_thread(h.session.conversation_id)
        assert h.saver.get_tuple(h.config) is None
        builder = JournalCheckpointRebuilder(h.writer, h.saver, task_id=h.session.task_id)
        first = builder.rebuild(h.run.run_id, page_size=1)
        first_row = h.saver.get_tuple(first.checkpoint_config)
        receipts_before = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)
        repeat = builder.rebuild(h.run.run_id, through_seq=first.checkpoint.rebuilt_from.journal_head_event_seq)
        repeat_row = h.saver.get_tuple(repeat.checkpoint_config)
        assert json.dumps(first_row.checkpoint, sort_keys=True).encode() == json.dumps(repeat_row.checkpoint, sort_keys=True).encode()
        assert h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER) == receipts_before
        # Use a later proposal, without manufacturing new native execution
        # state after its store was lost. Different prefixes need distinct blobs.
        from tests.test_journal_checkpoint_recovery import audit
        audit(h, "explorer", "later-proposal")
        later = builder.rebuild(h.run.run_id)
        assert later.checkpoint.transfer_phase == "proposed"
        assert h.saver.get_tuple(later.checkpoint_config).checkpoint["channel_values"]["recovery_checkpoint"] == later.checkpoint.model_dump(mode="json")
        assert h.saver.get_tuple(first.checkpoint_config).checkpoint == first_row.checkpoint
        history_config = {"configurable": {key: value for key, value in later.checkpoint_config["configurable"].items()
                                          if key != "checkpoint_id"}}
        assert len(list(h.saver.list(history_config))) == 2
        assert h.saver.get_tuple({"configurable": {"thread_id": h.session.conversation_id, "session_id": h.session.session_id}}) is None
    finally:
        h.store.close()

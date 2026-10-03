"""Real isolated PostgreSQL namespace and receipt-gap tampering acceptance."""

from uuid import uuid4

import pytest

from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.handoff.continuity import AgentTransferJournal, snapshot_from_row
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.models import ContinuityIdentity
from tests.test_context_loop_payload_integrity import taint
from tests.test_context_loop_postgres_recovery import BASE, database, worker

# Reuse the existing loopback-only disposable database fixture.
__all__ = ["database"]
pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


def persist_tampered(saver, row, field):
    values = row.checkpoint["channel_values"]
    messages, envelope = taint(values["messages"], values["transfer_envelope"], field)
    values["messages"], values["transfer_envelope"] = list(messages), envelope
    versions = {channel: saver.get_next_version(row.checkpoint["channel_versions"].get(channel), None)
        for channel in ("messages", "transfer_envelope")}
    row.checkpoint["channel_versions"].update(versions)
    parent = row.parent_config or {"configurable": {
        key: value for key, value in row.config["configurable"].items() if key != "checkpoint_id"
    }}
    saver.put(parent, row.checkpoint, row.metadata, versions)
    return envelope


@pytest.mark.parametrize("field", ["reason", "context_summary", "mood"])
@pytest.mark.parametrize("path", ["receipt_gap", "namespace"])
def test_tampered_persisted_payload_blocks_recovery_without_new_receipt(database, field, path):
    thread = "roguehandoff-" + uuid4().hex
    worker(database, "crash", "after_checkpoint" if path == "receipt_gap" else "target_started", thread)
    if path == "namespace":
        assert worker(database, "migrate", "none", thread)["migrated"]
    store = PostgresContinuityStore(database)
    try:
        task = store.active_task("handoff:v1:" + thread)
        session = store.active_session(task.task_id)
        saver = AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer())
        config = {"configurable": {"thread_id": task.conversation_id, "session_id": session.session_id}}
        row = saver.get_tuple(config)
        before = store.query_task_history(task.task_id, session_id=session.session_id,
            event_type=HistoryEventType.AGENT_TRANSFER, limit=100)
        # Simulate a router/storage change after the sender's immutable proposal.
        envelope = persist_tampered(saver, row, field)
        result = worker(database, "resume", "none", thread)
        assert result["status"] == "needs_input" and result["result_count"] == 0
        after = store.query_task_history(task.task_id, session_id=session.session_id,
            event_type=HistoryEventType.AGENT_TRANSFER, limit=100)
        assert [event.event_id for event in before] == [event.event_id for event in after]
        errors = store.query_task_history(task.task_id, session_id=session.session_id, event_type=HistoryEventType.ERROR)
        assert len(errors) == 1 and errors[0].payload["code"] == "integrity_conflict"
        assert "注入" not in str(errors[0].payload)
        reread = saver.get_tuple(config)
        assert reread.checkpoint["channel_values"]["transfer_envelope"] == envelope
    finally:
        store.close()


@pytest.mark.parametrize("field", ["reason", "context_summary", "mood"])
def test_namespace_gap_rejects_tampered_source_before_copying_target(database, field):
    thread = "roguehandoff-source-" + uuid4().hex
    worker(database, "crash", "target_started", thread)
    store = PostgresContinuityStore(database)
    try:
        task = store.active_task("handoff:v1:" + thread)
        source = store.active_session(task.task_id)
        worker(database, "migration_crash", "none", thread)
        target = store.active_session(task.task_id)
        saver = AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer())
        config = {"configurable": {"thread_id": task.conversation_id, "session_id": source.session_id}}
        row = saver.get_tuple(config)
        values = row.checkpoint["channel_values"]
        persist_tampered(saver, row, field)
        identity = ContinuityIdentity(conversation_id=task.conversation_id, continuity_task_id=task.task_id,
            session_id=source.session_id, request_id="test", graph_mode="handoff", run_id=values["handoff_run"]["run_id"])
        from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
        from app.agent.foundation.long_horizon.migration import NamespaceMigrator
        from app.agent.foundation.long_horizon.service import LongHorizonContextService
        from app.agent.foundation.long_horizon.public_events import PublicMigration
        from app.agent.handoff.integrity import TransferIntegrityConflict
        from types import SimpleNamespace
        import asyncio

        coordinator = ContinuityCoordinator(LongHorizonContextService(store))
        intent = next(event for event in store.query_task_history(task.task_id,
            event_type=HistoryEventType.NAMESPACE_MIGRATION) if event.payload["phase"] == "proposed")
        lease = SimpleNamespace(check=lambda: None)
        with pytest.raises(TransferIntegrityConflict):
            asyncio.run(NamespaceMigrator(coordinator).reconcile(SimpleNamespace(checkpointer=saver), identity,
                PublicMigration.model_validate(intent.payload), lease))
        assert saver.get_tuple({"configurable": {"thread_id": task.conversation_id, "session_id": target.session_id}}) is None
        errors = store.query_task_history(task.task_id, session_id=source.session_id, event_type=HistoryEventType.ERROR)
        assert len(errors) == 1 and errors[0].payload["code"] == "integrity_conflict"
        snapshot = snapshot_from_row(saver.get_tuple(config), identity)
        with pytest.raises(TransferIntegrityConflict):
            AgentTransferJournal(HistoryWriter(store)).confirm_checkpoint(snapshot, reconcile=True)
    finally:
        store.close()

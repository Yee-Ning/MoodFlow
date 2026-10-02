"""Explicit namespace migration; a crash leaves a replayable public intent.

Copy execution channels inside the private checkpointer, not into public
artifacts. Agent receipts are re-confirmed against the actual target snapshot.
"""

import copy

from langgraph.checkpoint.base import empty_checkpoint

from app.agent.handoff.continuity import snapshot_from_row

from .execution import ContinuityConflict
from .history_writer import digest
from .models import HistoryEventType, RolloverReason, TransferJournalEvent
from .public_events import PublicMigration


class NamespaceMigrator:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    async def migrate(self, call, *, migration_id):
        call.lease.check()
        coordinator = self.coordinator
        if not getattr(coordinator.store, "supports_atomic_rollover", False):
            raise ContinuityConflict("namespace migration requires PostgreSQL")
        source = await coordinator.snapshot(call.graph, call.config, call.identity)
        if source is None or not source.next_nodes:
            raise ContinuityConflict("migration requires a saved pending run")
        row = await call.graph.checkpointer.aget_tuple(call.config)
        if row.pending_writes:
            raise ContinuityConflict("cached writes must recover in their original namespace first")
        # The explicit copy covers parent execution channels. Never discard a
        # persisted child execution by restarting it under a new namespace.
        scanned = 0
        async for child in call.graph.checkpointer.alist(
            {"configurable": {"thread_id": call.identity.conversation_id}}, limit=1001
        ):
            scanned += 1
            if scanned > 1000:
                raise ContinuityConflict("migration lineage needs original-namespace drilldown")
            config = child.config["configurable"]
            if (config.get("session_id") == call.identity.session_id and config.get("checkpoint_ns")
                and child.metadata.get("parents", {}).get("") == source.ref.checkpoint_id):
                raise ContinuityConflict("child execution must recover in its original namespace first")
        context = row.checkpoint["channel_values"]["continuity_context"]
        draft = coordinator.draft(call.session, source, context.get("live_snapshot"), source.run.original_input)
        projection = coordinator.service.build_handoff(call.session.session_id, draft)
        plan, _, _ = await coordinator.recovery.execute(projection, call.live, source, operations=call.operations)
        if plan.action not in {"resume_source", "resume_target"} or coordinator.pending_actions(call.identity):
            raise ContinuityConflict("migration recovery evidence incomplete")
        target_session = "session:migrate:" + digest([call.identity.continuity_task_id, migration_id])[7:]
        key = "migration:" + digest(migration_id)[7:]
        existing = coordinator.store.get_history_event(coordinator.writer.event_id(call.identity, key + ":proposed"))
        body = PublicMigration.model_validate(existing.payload) if existing else PublicMigration(
            run_id=source.run.run_id, source_session=call.identity.session_id, target_session=target_session,
            source_checkpoint=source.ref.checkpoint_id, target_checkpoint=empty_checkpoint()["id"], phase="proposed")
        if body.source_checkpoint != source.ref.checkpoint_id:
            raise ContinuityConflict("migration source revision changed")
        coordinator.writer.append_public_event(call.identity, key + ":proposed", HistoryEventType.NAMESPACE_MIGRATION, body)
        if coordinator.store.get_session(target_session) is None:
            coordinator.service.rollover(call.identity.session_id, draft, RolloverReason.EXPLICIT_CHECKPOINT,
                automatic=True, new_session_id=target_session, writer=coordinator.writer, identity=call.identity)
        return await self.reconcile(call.graph, call.identity, body, call.lease)

    async def reconcile(self, graph, source_identity, body, lease):
        lease.check()
        coordinator = self.coordinator
        source_config = {"configurable": {"thread_id": source_identity.conversation_id,
            "session_id": body.source_session, "checkpoint_id": body.source_checkpoint}}
        row = await graph.checkpointer.aget_tuple(source_config)
        if row is None or row.checkpoint["channel_values"]["handoff_run"]["run_id"] != body.run_id:
            raise ContinuityConflict("migration source unavailable")
        target_identity = source_identity.model_copy(update={"session_id": body.target_session, "run_id": body.run_id})
        target_session = coordinator.store.require_session(body.target_session)
        if target_session.previous_session_id != body.source_session or target_session.task_id != source_identity.continuity_task_id:
            raise ContinuityConflict("migration session lineage mismatch")
        target_config = {"configurable": {"thread_id": source_identity.conversation_id,
                                          "session_id": body.target_session, "checkpoint_id": body.target_checkpoint}}
        saved = await graph.checkpointer.aget_tuple(target_config)
        if saved is None:
            checkpoint = copy.deepcopy(row.checkpoint)
            checkpoint["id"] = body.target_checkpoint
            values = checkpoint["channel_values"]
            values["continuity_context"].update({"session_id": body.target_session, "execution_fence": lease.fence,
                                                "state_revision": lease.revision, "source_checkpoint_id": body.source_checkpoint})
            # Bypass hook adapters only for the explicit copy; confirm below using
            # a saver read. Control versions/triggers preserve the original nodes.
            raw_saver = graph.checkpointer
            while hasattr(raw_saver, "saver"):
                raw_saver = raw_saver.saver
            config = {"configurable": {"thread_id": source_identity.conversation_id, "session_id": body.target_session}}
            await raw_saver.aput(config, checkpoint, row.metadata, checkpoint["channel_versions"])
            saved = await graph.checkpointer.aget_tuple(target_config)
        # A new namespace has distinct journal keys, but retains references to the
        # original immutable proposals. Only the verified target state commits them.
        cursor = 0
        while True:
            events = coordinator.store.query_task_history(source_identity.continuity_task_id,
                session_id=body.source_session, event_type=HistoryEventType.AGENT_TRANSFER, after_seq=cursor, limit=100)
            if not events:
                break
            for event in events:
                transfer = TransferJournalEvent.model_validate(event.payload)
                if transfer.summary.run_id == body.run_id and transfer.phase == "proposed":
                    coordinator.writer.transfer(target_identity, transfer.transfer_id + ":proposed", transfer)
                cursor = event.event_seq
        snapshot = snapshot_from_row(saved, target_identity)
        coordinator.journal.confirm_checkpoint(snapshot, reconcile=True)
        key = "migration:" + digest([body.source_session, body.target_session])[7:]
        coordinator.writer.append_public_event(target_identity, key + ":committed", HistoryEventType.NAMESPACE_MIGRATION,
                                               body.model_copy(update={"phase": "committed"}))
        lease.advance()
        return target_config

    async def recover_missing_target(self, graph, identity, lease):
        cursor = 0
        while True:
            rows = self.coordinator.store.query_task_history(identity.continuity_task_id,
                event_type=HistoryEventType.NAMESPACE_MIGRATION, after_seq=cursor, limit=100)
            if not rows:
                raise ContinuityConflict("resume checkpoint unavailable")
            for event in rows:
                body = PublicMigration.model_validate(event.payload)
                if body.phase == "proposed" and body.target_session == identity.session_id:
                    source_identity = identity.model_copy(update={"session_id": body.source_session, "run_id": body.run_id})
                    return await self.reconcile(graph, source_identity, body, lease)
                cursor = event.event_seq

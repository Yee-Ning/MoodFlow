"""Read-only contract adaptation; canonical documents remain append-only."""

from datetime import datetime

from .models import (
    ContinuityIdentity, EventPointer, HistoryEventType, HistoryPointer,
    SessionProjectionArchive, SessionProjectionPointer, TransferJournalEvent,
    TransferJournalReference, TransferProposalReference, read_handoff_projection,
)


class JournalReader:
    def __init__(self, store):
        self.store = store

    def resolve(self, pointer: EventPointer):
        """Return the whole fact; offset is only an optional caller locator."""
        self.store.validate_pointers(
            [HistoryPointer(kind="event", target_id=pointer.event_id)], task_id=pointer.task_id,
        )
        event = self.store.get_history_event(pointer.event_id)
        if event is None or event.task_id != pointer.task_id:
            raise ValueError("unresolvable event pointer")
        return event

    def query(self, task_id, *, start: datetime | None = None, end: datetime | None = None,
              after_seq=0, limit=20, event_type=None):
        """Filter across pages rather than silently losing late time matches."""
        if limit < 1 or limit > 100 or after_seq < 0:
            raise ValueError("invalid journal query bounds")
        for bound in (start, end):
            if bound is not None and (bound.tzinfo is None or bound.utcoffset() is None):
                raise ValueError("journal time bounds require a timezone")
        if start is not None and end is not None and start > end:
            raise ValueError("journal time range is reversed")
        result, cursor = [], after_seq
        while len(result) < limit:
            rows = self.store.query_task_history(task_id, after_seq=cursor, limit=100,
                                                  event_type=event_type)
            if not rows:
                break
            for event in rows:
                cursor = event.event_seq
                if (start is None or event.timestamp >= start) and (end is None or event.timestamp <= end):
                    result.append(event)
                    if len(result) == limit:
                        break
        return result

    def transfer(self, event):
        if event.event_type is not HistoryEventType.AGENT_TRANSFER:
            raise ValueError("journal reference is not a transfer event")
        if not isinstance(event.payload, dict):
            raise ValueError("transfer event requires a typed public payload")
        if event.payload.get("schema_version", 1) == 1:
            return TransferJournalEvent.model_validate(event.payload)
        if event.payload.get("schema_version") == 3:
            first = TransferProposalReference.model_validate(event.payload)
            current, seen = event, set()
            while current.payload.get("schema_version") == 3:
                if current.event_id in seen or len(seen) >= 1000:
                    raise ValueError("proposal lineage requires bounded history drilldown")
                seen.add(current.event_id)
                alias = TransferProposalReference.model_validate(current.payload)
                if alias.transfer_id != first.transfer_id:
                    raise ValueError("proposal lineage transfer identity mismatch")
                origin = self.resolve(EventPointer(task_id=event.task_id, event_id=alias.summary_ref.target_id))
                target_session = self.store.require_session(current.session_id)
                source_session = self.store.require_session(origin.session_id)
                if (origin.event_type is not HistoryEventType.AGENT_TRANSFER
                    or target_session.previous_session_id != origin.session_id
                    or target_session.conversation_id != source_session.conversation_id):
                    raise ValueError("proposal migration lineage mismatch")
                current = origin
                if not isinstance(current.payload, dict):
                    raise ValueError("proposal lineage requires a public payload")
            canonical = TransferJournalEvent.model_validate(current.payload)
            if canonical.phase != "proposed" or canonical.transfer_id != first.transfer_id:
                raise ValueError("proposal lineage has no canonical anchor")
            return TransferJournalEvent(phase="proposed", transfer_id=first.transfer_id,
                summary=canonical.summary, summary_ref=first.summary_ref)
        receipt = TransferJournalReference.model_validate(event.payload)
        proposal = self.resolve(EventPointer(task_id=event.task_id, event_id=receipt.summary_ref.target_id))
        if proposal.session_id != event.session_id or proposal.event_type is not HistoryEventType.AGENT_TRANSFER:
            raise ValueError("transfer proposal scope or type mismatch")
        canonical = self.transfer(proposal)
        if canonical.phase != "proposed" or canonical.transfer_id != receipt.transfer_id:
            raise ValueError("transfer reference is not its canonical proposal")
        if receipt.checkpoint_ref is not None:
            if (receipt.checkpoint_ref.session_id, receipt.checkpoint_ref.checkpoint_ns) != (event.session_id, event.session_id):
                raise ValueError("transfer checkpoint scope mismatch")
            session = self.store.require_session(event.session_id)
            if receipt.checkpoint_ref.execution_thread_id != session.conversation_id:
                raise ValueError("transfer checkpoint conversation mismatch")
        # Compatibility view only; never write this hydrated summary back.
        return TransferJournalEvent(
            phase=receipt.phase, transfer_id=receipt.transfer_id,
            summary=(canonical.summary.model_copy(update={"reason_code": receipt.reason_code})
                     if receipt.phase == "rejected" else canonical.summary),
            summary_ref=receipt.summary_ref, checkpoint_ref=receipt.checkpoint_ref,
            result_refs=(canonical.summary.completed_result_refs
                         if receipt.phase in {"committed", "reconciled"} else receipt.result_refs or ()),
            retry_allowed=receipt.retry_allowed or False,
        )


def transfer_document(store, identity, body):
    """Validate the caller's view and write only a canonical proposal pointer."""
    values = body.model_dump(mode="json") if hasattr(body, "model_dump") else body
    if values.get("schema_version") == 3:
        receipt = TransferProposalReference.model_validate(values)
    elif values.get("schema_version", 1) == 2:
        receipt = TransferJournalReference.model_validate(values)
    else:
        view = TransferJournalEvent.model_validate(values)
        if view.phase == "proposed":
            return view.model_dump(mode="json", exclude_none=True)
        reference_pointer = view.summary_ref
        if view.phase == "rejected" and reference_pointer is None:
            from .history_writer import HistoryWriter
            reference_pointer = HistoryPointer(kind="event", target_id=HistoryWriter.event_id(
                identity, view.transfer_id + ":proposed"))
        if reference_pointer is None:
            raise ValueError("transfer receipt requires canonical summary provenance")
        reader = JournalReader(store)
        reference = reader.resolve(EventPointer(task_id=identity.continuity_task_id,
                                               event_id=reference_pointer.target_id))
        origin = reader.transfer(reference)
        excluded = {"reason_code"} if view.phase == "rejected" else set()
        if reference.session_id != identity.session_id or origin.summary.model_dump(exclude=excluded) != view.summary.model_dump(exclude=excluded):
            raise ValueError("transfer summary provenance mismatch")
        canonical_ref = (reference_pointer if origin.phase == "proposed" else origin.summary_ref)
        if canonical_ref is None:
            raise ValueError("transfer canonical proposal missing")
        if view.phase in {"committed", "reconciled"} and view.result_refs != view.summary.completed_result_refs:
            raise ValueError("commit results disagree with canonical proposal")
        if view.retry_allowed and view.phase != "rejected":
            raise ValueError("committed transfer cannot authorize a retry")
        receipt = TransferJournalReference(
            phase=view.phase, transfer_id=view.transfer_id, summary_ref=canonical_ref,
            checkpoint_ref=view.checkpoint_ref,
            result_refs=view.result_refs if view.phase.startswith("target_") else None,
            reason_code=view.summary.reason_code if view.phase == "rejected" else None,
            retry_allowed=view.retry_allowed if view.phase == "rejected" else None,
        )
    # The same validation applies even to directly supplied v2 documents.
    from .models import HistoryEvent
    from datetime import UTC
    probe = HistoryEvent(event_id="validation", task_id=identity.continuity_task_id,
        session_id=identity.session_id, event_type=HistoryEventType.AGENT_TRANSFER,
        timestamp=datetime.now(UTC), node="validation", source="validation",
        payload=receipt.model_dump(mode="json", exclude_none=True))
    validated = JournalReader(store).transfer(probe)
    if identity.run_id is not None and validated.summary.run_id != identity.run_id:
        raise ValueError("public history run identity mismatch")
    return probe.payload


def archive_projection(store, projection):
    """Storage index contains a pointer; its document goes through HistoryWriter."""
    from .history_writer import HistoryWriter
    session = store.require_session(projection.session_id)
    identity = ContinuityIdentity(conversation_id=session.conversation_id,
        continuity_task_id=session.task_id, session_id=session.session_id,
        request_id=projection.handoff_id, graph_mode="foundation")
    event = HistoryWriter(store).append_public_event(identity, "projection:" + projection.handoff_id,
        HistoryEventType.SESSION_PROJECTION, SessionProjectionArchive(projection=projection))
    return SessionProjectionPointer(handoff_id=projection.handoff_id, task_id=projection.task_id,
        session_id=projection.session_id, event_ref=HistoryPointer(kind="event", target_id=event.event_id))


def resolve_projection(store, body):
    if body.get("storage_schema") != "history-pointer:v1":
        return read_handoff_projection(body)
    index = SessionProjectionPointer.model_validate(body)
    if index.event_ref.kind != "event":
        raise ValueError("projection must reference a journal event")
    event = JournalReader(store).resolve(EventPointer(task_id=index.task_id, event_id=index.event_ref.target_id))
    if event.event_type is not HistoryEventType.SESSION_PROJECTION or event.session_id != index.session_id:
        raise ValueError("projection archive scope or type mismatch")
    projection = SessionProjectionArchive.model_validate(event.payload).projection
    if (projection.handoff_id, projection.task_id, projection.session_id) != (index.handoff_id, index.task_id, index.session_id):
        raise ValueError("projection archive identity mismatch")
    return projection

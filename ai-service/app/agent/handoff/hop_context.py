"""Ephemeral, server-owned current-hop input; never a checkpoint/continuity field."""

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
from types import SimpleNamespace

from app.agent.foundation.context_contracts import EnvelopeManifest, SessionManifest
from app.agent.foundation.context_pointers import EventPointer
from app.agent.foundation.long_horizon.journal_contracts import JournalReader
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.handoff.continuity import transfer_identity, validate_transfer_messages
from app.agent.handoff.contracts import HandoffEnvelope
from app.agent.handoff.integrity import payload_digest, transfer_payload
from app.agent.handoff.state import HandoffRun


@dataclass
class HopLease:
    active: bool = True


@dataclass(frozen=True)
class CurrentHopSnapshot:
    run_id: str
    owner_agent: str
    task_json: str
    envelope_json: str | None = field(repr=False)
    envelope_digest: str | None
    envelope_audit: EnvelopeManifest | None
    chain: tuple[str, ...]
    committed: bool
    lease: HopLease = field(repr=False, compare=False)
    result_refs: tuple[EventPointer, ...] = ()
    identity: object = field(default=None, repr=False, compare=False)
    store: object = field(default=None, repr=False, compare=False)

    def require_current(self):
        if not self.lease.active:
            raise SourceIntegrityError()

    def envelope(self):
        self.require_current()
        if self.envelope_json is None:
            return None
        if not self.committed or self.envelope_audit is None:
            raise SourceIntegrityError()
        try:
            envelope = HandoffEnvelope.model_validate_json(self.envelope_json)
            if payload_digest(envelope.model_dump(mode="json")) != self.envelope_digest or self.envelope_digest != self.envelope_audit.payload_digest:
                raise SourceIntegrityError()
            if (envelope.run_id, envelope.request.target_agent.value, envelope.active_task.model_dump_json()) != (
                self.run_id, self.owner_agent, self.task_json
            ):
                raise SourceIntegrityError()
            reader = JournalReader(self.store)
            proposed = reader.transfer(reader.resolve(self.envelope_audit.summary_ref))
            committed = reader.transfer(reader.resolve(self.envelope_audit.commit_ref))
            if proposed.phase != "proposed" or committed.phase not in {"committed", "reconciled"}:
                raise SourceIntegrityError()
            if proposed.summary.payload_digest != self.envelope_digest or committed.summary.payload_digest != self.envelope_digest:
                raise SourceIntegrityError()
            if (committed.transfer_id, committed.summary.run_id, committed.summary.target_agent) != (
                self.envelope_audit.transfer_id, self.run_id, self.owner_agent
            ):
                raise SourceIntegrityError()
            return envelope
        except (ValueError, TypeError):
            raise SourceIntegrityError() from None

    @property
    def session_audit(self):
        return SessionManifest(session_id=self.identity.session_id) if self.identity is not None else None


active_hop: ContextVar[CurrentHopSnapshot | None] = ContextVar("agent_pack_current_hop", default=None)


def retired_for_guard_retry(state, run, journal, identity):
    """The original rejected Command clears its already-consumed input.

    Prove that transition from the immutable outgoing rejection; a client flag
    or missing incoming Envelope alone cannot establish consumption.
    """
    if not state.get("guard_retry"):
        return False
    from langchain_core.messages import AIMessage, ToolMessage
    from app.agent.handoff.messages import parse_transfer_call
    messages = state.get("messages", ())
    call = next((message for message in reversed(messages) if isinstance(message, AIMessage) and message.tool_calls), None)
    if call is None:
        return False
    request = parse_transfer_call(call, run.current_agent)
    call_id = call.tool_calls[0]["id"]
    reply = next((message for message in reversed(messages) if isinstance(message, ToolMessage)
                  and message.tool_call_id == call_id), None)
    rejected = journal._event(identity, transfer_identity(run.run_id, call_id), "rejected")
    if reply is None or rejected is None:
        return False
    receipt = JournalReader(journal.writer.store).transfer(rejected)
    return (receipt.phase == "rejected" and receipt.retry_allowed and receipt.summary.reason_code == "guard_rejected"
        and (receipt.summary.run_id, receipt.summary.source_agent, receipt.summary.hop_index, receipt.summary.route_task_id) == (
            run.run_id, run.current_agent.value, run.handoff_count, run.pending_tasks[0].task_id)
        and json.loads(reply.content) == {"accepted": False, "code": "guard_rejected"}
        and receipt.summary.payload_digest == payload_digest(transfer_payload(
            run, request, source_agent=run.current_agent, tool_call_id=call_id)))


def capture_current_hop(state, run: HandoffRun, *, journal=None, identity=None) -> CurrentHopSnapshot:
    """Read after the existing await_committed/before_agent gate; do not commit.

    Without a Journal the original graph remains usable, but its Envelope is
    deliberately not consumable by the formal Source.
    """
    raw = state.get("transfer_envelope")
    task = run.pending_tasks[0]
    try:
        envelope_json = (raw.model_dump_json() if isinstance(raw, HandoffEnvelope)
                         else json.dumps(raw, ensure_ascii=False, allow_nan=False)) if raw is not None else None
    except (ValueError, TypeError):
        raise SourceIntegrityError() from None
    audit, expected, result_refs = None, None, ()
    if journal is not None:
        try:
            if identity is None or identity.run_id != run.run_id or identity.graph_mode != "handoff":
                raise SourceIntegrityError()
            session = journal.writer.store.require_session(identity.session_id)
            if (session.task_id, session.conversation_id) != (identity.continuity_task_id, identity.conversation_id):
                raise SourceIntegrityError()
            reader = JournalReader(journal.writer.store)
            digests = validate_transfer_messages(SimpleNamespace(run=run, messages=tuple(state.get("messages", ())), envelope=raw))
            last_receipt, last_summary = None, None
            for index, hop in enumerate(run.hops):
                tid = transfer_identity(run.run_id, hop.tool_call_id)
                proposal = journal._event(identity, tid, "proposed")
                receipt = journal._event(identity, tid, "committed") or journal._event(identity, tid, "reconciled")
                if proposal is None or receipt is None:
                    raise SourceIntegrityError()
                for event in (proposal, receipt):
                    if (event.task_id, event.session_id) != (identity.continuity_task_id, identity.session_id):
                        raise SourceIntegrityError()
                canonical, accepted = reader.transfer(proposal), reader.transfer(receipt)
                summary = accepted.summary
                if accepted.phase not in {"committed", "reconciled"} or canonical.phase != "proposed":
                    raise SourceIntegrityError()
                if (summary.run_id, summary.hop_index, summary.source_agent, summary.target_agent, summary.route_task_id) != (
                    run.run_id, index, hop.source_agent.value, hop.target_agent.value, hop.task_id
                ) or summary.payload_digest is None or summary.payload_digest != digests[hop.tool_call_id]:
                    raise SourceIntegrityError()
                if canonical.summary.payload_digest != summary.payload_digest:
                    raise SourceIntegrityError()
                last_receipt, last_summary = receipt, summary
            current_received = bool(run.hops and run.hops[-1].task_id == task.task_id
                                    and run.hops[-1].completed_count == len(run.results))
            if current_received and raw is None and not retired_for_guard_retry(state, run, journal, identity):
                raise SourceIntegrityError()
            if raw is not None:
                envelope = HandoffEnvelope.model_validate_json(envelope_json)
                if not current_received or envelope.request.target_agent != run.current_agent:
                    raise SourceIntegrityError()
                expected = last_summary.payload_digest
                # The receipt points at the canonical proposal (or its validated
                # migrated alias). Never invent an event ID from received text.
                accepted = reader.transfer(last_receipt)
                if accepted.summary_ref is None:
                    raise SourceIntegrityError()
                audit = EnvelopeManifest(transfer_id=last_summary.transfer_id, run_id=run.run_id,
                    tool_call_id=envelope.tool_call_id, schema_version=1, payload_digest=expected,
                    summary_ref=EventPointer(task_id=identity.continuity_task_id, event_id=accepted.summary_ref.target_id),
                    commit_ref=EventPointer(task_id=identity.continuity_task_id, event_id=last_receipt.event_id))
                result_refs = tuple(EventPointer(task_id=identity.continuity_task_id, event_id=ref.target_id)
                                    for ref in last_summary.completed_result_refs)
        except (ValueError, KeyError, TypeError):
            raise SourceIntegrityError() from None
    return CurrentHopSnapshot(run_id=run.run_id, owner_agent=run.current_agent.value, task_json=task.model_dump_json(),
        envelope_json=envelope_json, envelope_digest=expected, envelope_audit=audit,
        chain=tuple(agent.value for agent in run.handoff_chain), committed=journal is not None, lease=HopLease(),
        result_refs=result_refs,
        identity=identity if journal is not None else None, store=journal.writer.store if journal is not None else None)


@asynccontextmanager
async def hop_source_scope(state, run, *, journal=None, identity=None):
    snapshot = capture_current_hop(state, run, journal=journal, identity=identity)
    token = active_hop.set(snapshot)
    try:
        yield snapshot
    finally:
        # Child tasks inherit ContextVars; revoke their copy too at hop end.
        snapshot.lease.active = False
        active_hop.reset(token)

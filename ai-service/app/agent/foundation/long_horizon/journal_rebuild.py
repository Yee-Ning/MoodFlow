"""Bounded, explicit scheme-A recovery through the existing saver interface."""

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from types import SimpleNamespace

from langgraph.checkpoint.base import empty_checkpoint

from .history_writer import HistoryWriter, digest
from .journal_contracts import JournalReader
from .models import (
    ContinuityIdentity, HistoryEvent, HistoryEventType, HistoryPointer,
    PublicHistoryError, RunProgressRecord,
)
from .public_events import PublicUsage
from .recovery_checkpoint import (
    RecoveryBudgetRef, RecoveryCheckpoint, RecoveryHop, RecoveryMissing, RecoveryProvenance,
)

RECOVERY_NAMESPACE = "journal-recovery"
UNAVAILABLE_EXECUTION = (
    ("original_input", "not_recorded_in_public_journal"),
    ("plan", "public_plan_digest_is_not_an_execution_plan"),
    ("hops", "original_tool_call_ids_not_recorded"),
    ("usage", "existing_ledger_reference_only"),
    ("messages", "not_recorded_in_public_journal"),
    ("transfer_envelope", "original_payload_not_recorded"),
    ("execution_checkpoint", "recovery_evidence_cannot_execute_graph"),
)


@dataclass(frozen=True)
class JournalRebuildReport:
    checkpoint: RecoveryCheckpoint
    checkpoint_config: dict
    events_consumed: int
    events_skipped: int
    missing_markers: int


class _PrefixStore:
    """References may precede the replay start, but never exceed its frozen head."""

    def __init__(self, store, head):
        self.store, self.head = store, head

    def __getattr__(self, name):
        return getattr(self.store, name)

    def get_history_event(self, event_id):
        try:
            event = self.store.get_history_event(event_id)
        except (ValueError, TypeError):
            return None
        return event if event is not None and event.event_seq <= self.head else None


class _Fold:
    def __init__(self, run_id, after_seq):
        self.run_id = run_id
        self.hops, self.signatures = [], {}
        self.usage_refs = deque(maxlen=64)
        self.missing = dict.fromkeys(UNAVAILABLE_EXECUTION)
        self.last_agent = self.last_run_id = self.run_status = self.phase = None
        self.recent_chain = ()
        self.complete_chain = after_seq == 0
        self.consumed = self.skipped = 0
        if after_seq:
            self.mark("journal", "replay_start_excludes_earlier_events")

    def mark(self, field, reason):
        # Reasons are fixed categories, never unbounded per-event payloads.
        self.missing[(field, reason)] = None

    def transfer(self, event, view, anchored):
        from app.agent.handoff.registry import AGENT_REGISTRY, AgentKey

        self.phase, self.last_run_id = view.phase, view.summary.run_id
        self.mark("payload_digest", "original_payload_unavailable_for_sha256_recomputation")
        if view.phase != "committed":
            if view.phase in {"target_started", "target_finished", "reconciled"} and view.transfer_id not in self.signatures:
                self.mark("handoff_chain", "observation_without_committed_event")
            return
        if not anchored:
            self.mark("handoff_chain", "committed_digest_anchor_unavailable")
            return
        summary = view.summary
        if view.transfer_id in self.signatures:
            self.skipped += 1
            self.consumed -= 1
            return
        if len(self.hops) == 5:
            self.mark("handoff_chain", "committed_hop_limit_exceeded")
            return
        if AgentKey(summary.target_agent) not in AGENT_REGISTRY[AgentKey(summary.source_agent)].transfer_targets:
            self.mark("handoff_chain", "invalid_committed_edge")
            return
        if summary.hop_index != len(self.hops) or (self.hops and self.hops[-1].to_agent != summary.source_agent):
            self.complete_chain = False
            self.mark("handoff_chain", "committed_chain_gap")
        self.hops.append(RecoveryHop(from_agent=summary.source_agent, to_agent=summary.target_agent,
                                     transfer_id=view.transfer_id, event_seq=event.event_seq))
        self.signatures[view.transfer_id] = summary.model_dump(exclude={"reason_code"})
        self.last_agent = summary.target_agent
        self.recent_chain = ((self.hops[0].from_agent, *(hop.to_agent for hop in self.hops))
                             if self.complete_chain else ())

    def progress(self, record, prefix):
        self.last_run_id, self.run_status = record.run.run_id, record.run.status
        proven = len(record.transfer_refs) == record.run.accepted_hop_count == len(self.hops)
        for ref in record.transfer_refs:
            receipt = prefix.get_history_event(ref.target_id)
            if receipt is None or receipt.event_type is not HistoryEventType.AGENT_TRANSFER:
                proven = False
                continue
            if receipt.payload.get("phase") != "committed" or receipt.payload.get("transfer_id") not in self.signatures:
                proven = False
        if record.run.accepted_hop_count == 0 and not self.hops and proven:
            self.last_agent = record.run.current_agent
            self.recent_chain = record.run.chain if self.complete_chain else ()
        elif not proven or tuple(record.run.chain) != self.recent_chain:
            self.mark("handoff_chain", "run_progress_has_unproven_committed_chain")
        if self.last_agent is not None and record.run.current_agent != self.last_agent:
            self.mark("last_agent", "run_progress_disagrees_with_committed_owner")

    def finish(self, session, head, task_id):
        for field, value in (("run_id", self.run_id), ("last_run_id", self.last_run_id),
                             ("last_agent", self.last_agent), ("run_status", self.run_status),
                             ("transfer_phase", self.phase)):
            if value is None:
                self.mark(field, "no_proven_journal_value")
        if not self.usage_refs:
            self.mark("budget_ref", "no_model_usage_events_in_replay_window")
        return RecoveryCheckpoint(
            run_id=self.run_id, session_id=session.session_id, conversation_id=session.conversation_id,
            handoff_chain=tuple(self.hops), accepted_hop_count=len(self.hops), last_agent=self.last_agent,
            last_run_id=self.last_run_id, run_status=self.run_status, transfer_phase=self.phase,
            recent_run_chain=self.recent_chain, model_usage_refs=tuple(self.usage_refs),
            budget_ref=RecoveryBudgetRef(continuity_task_id=task_id, run_id=self.run_id) if self.run_id else None,
            missing=tuple(RecoveryMissing(field=field, reason=reason) for field, reason in self.missing),
            rebuilt_from=RecoveryProvenance(journal_head_event_seq=head),
        )


class JournalCheckpointRebuilder:
    def __init__(self, writer: HistoryWriter, checkpointer, *, task_id: str | None = None):
        self.writer, self.store, self.checkpointer = writer, writer.store, checkpointer
        # A run ID is not a global history scope. Bind the existing continuity
        # task when selecting by run; selecting by session resolves it itself.
        self.task_id = task_id

    def _rows(self, task_id, *, session_id=None, after_seq=0, limit=100, descending=False):
        try:
            return self.store.query_task_history(task_id, session_id=session_id, after_seq=after_seq,
                                                 limit=limit, descending=descending)
        except ValueError:
            # Future/legacy malformed envelopes cannot pass HistoryEvent's
            # closed enum. Only recovery gets an opaque, bounded read adapter;
            # neither Journal's schema nor its public write gate is widened.
            from .postgres_store import PostgresContinuityStore
            from .store import SQLiteContinuityStore

            if isinstance(self.store, SQLiteContinuityStore):
                table, marker = "history_events", "?"
            elif isinstance(self.store, PostgresContinuityStore):
                table, marker = "agent_history_event", "%s"
            else:
                raise
            clauses, values = [f"task_id={marker}", f"event_seq>{marker}"], [task_id, after_seq]
            if session_id is not None:
                clauses.append(f"session_id={marker}")
                values.append(session_id)
            rows = self.store._all(
                f"SELECT body, event_seq FROM {table} WHERE {' AND '.join(clauses)} "
                f"ORDER BY event_seq {'DESC' if descending else 'ASC'} LIMIT {marker}",
                tuple([*values, limit]),
            )
            result = []
            for row in rows:
                body = json.loads(row["body"]) if isinstance(row["body"], str) else row["body"]
                try:
                    result.append(HistoryEvent.model_validate({**body, "event_seq": row["event_seq"]}))
                except (ValueError, TypeError):
                    result.append(SimpleNamespace(
                        event_seq=row["event_seq"], event_id=body.get("event_id"), task_id=task_id,
                        session_id=body.get("session_id"), event_type=body.get("event_type"),
                        timestamp=None, payload=body.get("payload"), opaque=True,
                    ))
            return result

    def _pages(self, task_id, session_id, after_seq, head, page_size):
        cursor = after_seq
        while cursor < head:
            rows = self._rows(task_id, session_id=session_id, after_seq=cursor, limit=page_size)
            if not rows:
                break
            for event in rows:
                if event.event_seq > head:
                    return
                if event.event_seq <= cursor:
                    raise ValueError("journal cursor did not advance")
                cursor = event.event_seq
                yield event

    @staticmethod
    def _run_id(event, reader):
        payload = event.payload
        if not isinstance(payload, dict):
            return None
        if event.event_type is HistoryEventType.AGENT_TRANSFER:
            try:
                return reader.transfer(event).summary.run_id
            except (ValueError, TypeError, KeyError):
                summary = payload.get("summary")
                return summary.get("run_id") if isinstance(summary, dict) else None
        if not isinstance(event.event_type, HistoryEventType):
            return None
        run = payload.get("run")
        return payload.get("run_id") or (run.get("run_id") if isinstance(run, dict) else None)

    def _conflict(self, event, run_id):
        from app.agent.handoff.integrity import TransferIntegrityConflict

        session = self.store.require_session(event.session_id)
        identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
                                      session_id=session.session_id, request_id="journal-rebuild", graph_mode="handoff",
                                      run_id=run_id)
        related = digest(event.event_id)
        self.writer.append_public_event(identity, "rebuild:integrity:" + related[7:], HistoryEventType.ERROR,
                                        PublicHistoryError(code="integrity_conflict", related_key_digest=related))
        raise TransferIntegrityConflict()

    def _transfer(self, event, reader, fold):
        raw_summary = event.payload.get("summary", {})
        raw_digest = raw_summary.get("payload_digest") if isinstance(raw_summary, dict) else None
        if raw_digest is not None and (not isinstance(raw_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", raw_digest)):
            self._conflict(event, fold.run_id)
        try:
            view = reader.transfer(event)
        except (ValueError, KeyError, TypeError):
            fold.mark("handoff_chain", "transfer_reference_missing_or_unreadable")
            return None, False
        session = self.store.require_session(event.session_id)
        checkpoint_ref = view.checkpoint_ref
        if checkpoint_ref is not None and (
            checkpoint_ref.session_id, checkpoint_ref.checkpoint_ns, checkpoint_ref.execution_thread_id,
        ) != (session.session_id, session.session_id, session.conversation_id):
            fold.mark("handoff_chain", "transfer_checkpoint_scope_mismatch")
            return None, False
        if view.phase == "proposed":
            canonical = view
        else:
            ref = view.summary_ref
            session = self.store.require_session(event.session_id)
            identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
                session_id=session.session_id, request_id="journal-rebuild", graph_mode="handoff", run_id=fold.run_id)
            proposal_id = ref.target_id if ref else HistoryWriter.event_id(identity, f"{view.transfer_id}:proposed")
            proposal = reader.store.get_history_event(proposal_id)
            if proposal is None:
                fold.mark("payload_digest", "canonical_proposal_missing_from_prefix")
                return view, False
            try:
                canonical = reader.transfer(proposal)
            except (ValueError, KeyError, TypeError):
                fold.mark("payload_digest", "canonical_proposal_unreadable")
                return view, False
            if (proposal.task_id, proposal.session_id, canonical.phase) != (event.task_id, event.session_id, "proposed"):
                self._conflict(event, fold.run_id)
        expected, actual = canonical.summary.payload_digest, view.summary.payload_digest
        core = ("transfer_id", "run_id", "hop_index", "source_agent", "target_agent", "route_task_id")
        if any(getattr(canonical.summary, field) != getattr(view.summary, field) for field in core):
            self._conflict(event, fold.run_id)
        if expected != actual:
            self._conflict(event, fold.run_id)
        previous = fold.signatures.get(view.transfer_id)
        if previous is not None and previous != view.summary.model_dump(exclude={"reason_code"}):
            self._conflict(event, fold.run_id)
        if actual is None:
            fold.mark("payload_digest", "legacy_digest_missing")
        return view, actual is not None

    def rebuild(self, run_id=None, *, session_id=None, after_seq=0, through_seq=None, page_size=100):
        """Replay (after_seq, through_seq], freezing the head before either pass.

        Session-only selection uses the last run-bearing journal event. Run-only
        selection reads the bound continuity task across its existing sessions.
        """
        if run_id is None and session_id is None:
            raise ValueError("run_id or session_id is required")
        if (not isinstance(after_seq, int) or isinstance(after_seq, bool) or after_seq < 0
                or not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 100
                or (through_seq is not None and (not isinstance(through_seq, int) or isinstance(through_seq, bool)
                                                or through_seq < after_seq))):
            raise ValueError("invalid journal replay bounds")
        session = self.store.require_session(session_id) if session_id else None
        task_id = session.task_id if session else self.task_id
        if not task_id or (session and self.task_id is not None and self.task_id != task_id):
            raise ValueError("run recovery requires the matching continuity task scope")
        latest = self._rows(task_id, session_id=session_id, limit=1, descending=True)
        fence = latest[0].event_seq if latest else 0
        if through_seq is not None:
            fence = min(fence, through_seq)
        reader = JournalReader(_PrefixStore(self.store, fence))
        selected, head, timestamp = run_id, 0, datetime(1970, 1, 1, tzinfo=UTC)
        for event in self._pages(task_id, session_id, 0, fence, page_size):
            head = event.event_seq
            if event.timestamp is not None:
                timestamp = event.timestamp
            candidate = self._run_id(event, reader)
            if candidate and (run_id is None or candidate == run_id):
                selected = candidate
                if session_id is None:
                    session = self.store.require_session(event.session_id)
        if session is None:
            session = self.store.active_session(task_id)
        if session is None:
            raise ValueError("no server session available for recovery")
        reader = JournalReader(_PrefixStore(self.store, head))
        fold = _Fold(selected, after_seq)
        for event in self._pages(task_id, session_id, after_seq, head, page_size):
            if not isinstance(event.event_type, HistoryEventType) or getattr(event, "opaque", False):
                fold.mark("journal", "unknown_event_type_or_invalid_envelope")
                fold.skipped += 1
                continue
            candidate = self._run_id(event, reader)
            if candidate is not None and candidate != selected:
                fold.skipped += 1
                continue
            if event.event_type not in {HistoryEventType.AGENT_TRANSFER, HistoryEventType.RUN_PROGRESS, HistoryEventType.MODEL_USAGE}:
                fold.skipped += 1
                continue
            source_session = self.store.require_session(event.session_id)
            if (source_session.task_id, source_session.conversation_id) != (task_id, session.conversation_id):
                raise ValueError("journal recovery conversation scope mismatch")
            if event.event_type is HistoryEventType.AGENT_TRANSFER:
                if not isinstance(event.payload, dict):
                    fold.mark("handoff_chain", "transfer_payload_missing")
                    fold.skipped += 1
                    continue
                view, anchored = self._transfer(event, reader, fold)
                if view is None or view.summary.run_id != selected:
                    fold.skipped += 1
                    continue
                fold.consumed += 1
                fold.transfer(event, view, anchored)
            else:
                try:
                    body = (RunProgressRecord if event.event_type is HistoryEventType.RUN_PROGRESS else PublicUsage).model_validate(event.payload)
                except (ValueError, TypeError):
                    fold.mark("journal", "known_event_payload_unreadable")
                    fold.skipped += 1
                    continue
                if candidate != selected or selected is None:
                    fold.skipped += 1
                    continue
                fold.consumed += 1
                if event.event_type is HistoryEventType.RUN_PROGRESS:
                    ref = body.checkpoint_ref
                    if (ref.session_id, ref.checkpoint_ns, ref.execution_thread_id) != (
                        event.session_id, event.session_id, source_session.conversation_id,
                    ):
                        fold.mark("run_status", "run_progress_checkpoint_scope_mismatch")
                        fold.consumed -= 1
                        fold.skipped += 1
                        continue
                    fold.progress(body, reader.store)
                else:
                    if len(fold.usage_refs) == fold.usage_refs.maxlen:
                        fold.mark("model_usage_refs", "reference_window_truncated_use_existing_ledger")
                    fold.usage_refs.append(HistoryPointer(kind="event", target_id=event.event_id))
        checkpoint = fold.finish(session, head, task_id)
        state = checkpoint.model_dump(mode="json")
        state_hash = digest(state)[7:]
        # Saver blobs are keyed by channel version across checkpoint IDs. A
        # constant version would alias different journal prefixes in Postgres.
        version = f"{head:020d}.{state_hash}"
        row = empty_checkpoint()
        row.update(id=f"recovery:{head:020d}:{state_hash}", ts=timestamp.isoformat(),
                   channel_values={"recovery_checkpoint": state}, channel_versions={"recovery_checkpoint": version},
                   updated_channels=["recovery_checkpoint"])
        config = {"configurable": {"thread_id": session.conversation_id, "session_id": session.session_id,
                                    "checkpoint_ns": RECOVERY_NAMESPACE}}
        stored = self.checkpointer.put(config, row, {"source": "update", "step": 0}, row["channel_versions"])
        return JournalRebuildReport(checkpoint, stored, fold.consumed, fold.skipped, len(checkpoint.missing))

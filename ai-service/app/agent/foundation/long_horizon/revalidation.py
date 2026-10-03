"""Source-aware reality checks and checkpoint/journal transfer reconciliation."""

import hashlib
import json
from datetime import UTC, datetime

from app.agent.handoff.continuity import (
    AgentTransferJournal, public_state_digest, transfer_identity,
)
from app.agent.handoff.registry import AGENT_REGISTRY, next_agent_for_task
from app.agent.handoff.state import HandoffRun

from .history_writer import HistoryWriter
from .models import HistoryEventType, HistoryPointer, LiveSnapshot, PublicTaskResult, RevalidationReport, TransferJournalEvent

STATE_SOURCES = {
    "current_song_id": {"client_player"}, "player_state": {"client_player"},
    "queue_size": {"client_player"}, "current_index": {"client_player"},
    "user_id": {"server_auth"}, "logged_in": {"server_auth"},
    "write_permission": {"server_auth", "music_service"},
    "index_version": {"retrieval_manifest"}, "release_version": {"retrieval_manifest"},
    "catalog_song": {"music_service"},
}
MUSIC_WRITES = {"create_playlist_tool", "add_song_to_playlist", "remove_song_from_playlist_tool", "set_song_liked_tool"}


class RealityRevalidator:
    def __init__(self, store, *, now=None, max_age_seconds=300, expected_sources=None):
        self.store = store
        self.now = now or (lambda: datetime.now(UTC))
        self.max_age_seconds = max_age_seconds
        self.expected_sources = STATE_SOURCES if expected_sources is None else expected_sources

    def _fresh(self, observation, scope):
        age = (self.now() - observation.observed_at).total_seconds()
        return (observation.scope == scope and -5 <= age <= self.max_age_seconds
                and observation.status != "unknown")

    def check_fields(self, projection, live_snapshot: LiveSnapshot, scope):
        """Check historical fields without inferring an execution owner."""
        field_results = {}
        observations = {item.field: item for item in live_snapshot.observations}
        for field in projection.revalidate_fields:
            observation = observations.get(field)
            if field not in projection.last_known_state or observation is None or not self._fresh(observation, scope):
                field_results[field] = "unknown"
                continue
            if observation.source not in self.expected_sources.get(field, set()):
                field_results[field] = "unknown"
                continue
            historical = projection.last_known_state.get(field)
            if isinstance(historical, dict) and "value" in historical and "revision" in historical:
                old_value, old_revision = historical["value"], historical["revision"]
            else:
                old_value, old_revision = historical, None
            if old_revision is not None and observation.revision is None:
                field_results[field] = "unknown"
            elif observation.status == "changed" or observation.value != old_value or (old_revision is not None and observation.revision != old_revision):
                field_results[field] = "changed"
            else:
                field_results[field] = "valid"
        return field_results

    def revalidate(self, projection, live_snapshot: LiveSnapshot, checkpoint_snapshot, refs=()):
        snapshot = checkpoint_snapshot
        if snapshot is None:
            return RevalidationReport(reasons=("checkpoint_missing",))
        identity = snapshot.identity
        field_results = self.check_fields(projection, live_snapshot, identity.conversation_id)
        reasons = []
        observations = {item.field: item for item in live_snapshot.observations}
        changed = tuple(field for field, status in field_results.items() if status == "changed")
        if any(field in changed and observations[field].value is False for field in ("logged_in", "write_permission")):
            reasons.append("current_authority_lost")
        if "user_id" in changed:
            reasons.append("current_authority_scope_changed")
        try:
            run = HandoffRun.model_validate(snapshot.run.model_dump(mode="json"))
            if (run.started_at - self.now()).total_seconds() > 5:
                raise ValueError("run starts in the future")
            if snapshot.guard_retry_deadline is not None and snapshot.guard_retry_deadline.tzinfo is None:
                raise ValueError("guard retry deadline must include its time zone")
            session = self.store.require_session(identity.session_id)
            projection_session = self.store.require_session(projection.session_id)
            if projection.task_id != identity.continuity_task_id or (projection_session.task_id, projection_session.conversation_id) != (
                identity.continuity_task_id, identity.conversation_id
            ):
                raise ValueError("projection scope mismatch")
            if (session.task_id, session.conversation_id, identity.run_id, identity.graph_mode) != (
                identity.continuity_task_id, identity.conversation_id, run.run_id, "handoff"
            ):
                raise ValueError("checkpoint identity mismatch")
            if (snapshot.ref.session_id, snapshot.ref.checkpoint_ns, snapshot.ref.execution_thread_id) != (
                identity.session_id, identity.session_id, identity.conversation_id
            ):
                raise ValueError("checkpoint scope or digest mismatch")
            AgentTransferJournal(HistoryWriter(self.store)).verify_payloads(
                identity, run, snapshot.messages, snapshot.envelope,
            )
            if snapshot.ref.public_state_digest != public_state_digest(run):
                raise ValueError("checkpoint scope or digest mismatch")
            source_ref = getattr(projection, "source_checkpoint_ref", None)
            if source_ref and (source_ref.session_id, source_ref.execution_thread_id, source_ref.checkpoint_ns) != (
                identity.session_id, identity.conversation_id, identity.session_id
            ):
                raise ValueError("projection checkpoint scope mismatch")
            if source_ref and source_ref.checkpoint_id not in {
                snapshot.ref.checkpoint_id, *snapshot.ancestor_checkpoint_ids
            }:
                raise ValueError("projection checkpoint lineage mismatch")
            if snapshot.next_nodes:
                expected = AGENT_REGISTRY[run.current_agent].node_name if run.status.value == "running" else "final_response"
                if any(node != expected for node in snapshot.next_nodes):
                    raise ValueError("checkpoint next node disagrees with run")
        except (ValueError, TypeError, KeyError):
            return RevalidationReport(
                field_results=field_results, checkpoint_integrity="conflict", transfer_integrity="conflict",
                reasons=("checkpoint_integrity_conflict",), invalidated_dependencies=changed,
            )
        ref_status = "valid"
        progress = projection.agent_progress
        pointers = [*refs, *projection.key_message_refs, *projection.evidence_refs,
                    *progress.progress_refs, *progress.earlier_chain_refs]
        pointers.extend(ref for ref in (progress.run_state_ref, progress.pending_transfer_ref) if ref)
        pointers.extend(ref for items in projection.constraint_refs.values() for ref in items)
        pointers.extend(ref for items in projection.decision_refs.values() for ref in items)
        try:
            self.store.validate_pointers(pointers, task_id=identity.continuity_task_id)
            for ref in projection.artifact_refs:
                artifact = self.store.get_artifact(ref)
                if artifact is None or artifact.task_id != identity.continuity_task_id:
                    raise ValueError("artifact scope mismatch")
                body = json.dumps(artifact.payload, ensure_ascii=False, sort_keys=True, default=str).encode()
                if artifact.content_hash != "sha256:" + hashlib.sha256(body).hexdigest():
                    raise ValueError("artifact content hash mismatch")
        except (ValueError, TypeError):
            ref_status = "conflict"
            reasons.append("reference_integrity_conflict")
        journal = []
        cursor = 0
        while True:
            batch = self.store.query_task_history(
                identity.continuity_task_id, session_id=identity.session_id,
                event_type=HistoryEventType.AGENT_TRANSFER.value, after_seq=cursor, limit=100,
            )
            if not batch:
                break
            journal.extend(batch)
            cursor = batch[-1].event_seq
            if len(journal) > 1000:
                return RevalidationReport(reasons=("journal_window_requires_drilldown",))
        receipt_map, proposed, rejected, journal_refs = {}, {}, {}, []
        integrity = "valid"
        try:
            for event in journal:
                receipt = TransferJournalEvent.model_validate(event.payload)
                if receipt.summary.run_id != run.run_id:
                    continue
                journal_refs.append(HistoryPointer(kind="event", target_id=event.event_id))
                if receipt.phase in {"committed", "reconciled"}:
                    if receipt.transfer_id in receipt_map:
                        raise ValueError("duplicate committed transfer")
                    receipt_map[receipt.transfer_id] = receipt
                    if receipt.summary_ref is None or receipt.summary_ref.kind != "event":
                        raise ValueError("committed transfer summary provenance missing")
                    proposal_event = self.store.get_history_event(receipt.summary_ref.target_id)
                    if proposal_event is None or (proposal_event.task_id, proposal_event.session_id) != (event.task_id, event.session_id):
                        raise ValueError("committed summary provenance scope mismatch")
                    original = TransferJournalEvent.model_validate(proposal_event.payload)
                    if original.phase != "proposed" or original.summary != receipt.summary:
                        raise ValueError("committed summary provenance mismatch")
                    cp = receipt.checkpoint_ref
                    if (cp.session_id, cp.execution_thread_id, cp.checkpoint_ns) != (
                        identity.session_id, identity.conversation_id, identity.session_id
                    ) or cp.checkpoint_id not in {snapshot.ref.checkpoint_id, *snapshot.ancestor_checkpoint_ids}:
                        raise ValueError("journal checkpoint lineage mismatch")
                elif receipt.phase == "proposed":
                    proposed[receipt.transfer_id] = receipt
                elif receipt.phase == "rejected":
                    rejected[receipt.transfer_id] = receipt
            expected_ids = set()
            for index, hop in enumerate(run.hops):
                transfer_id = transfer_identity(run.run_id, hop.tool_call_id)
                expected_ids.add(transfer_id)
                receipt = receipt_map.get(transfer_id)
                if receipt is None:
                    integrity = "missing_receipt"
                    continue
                summary = receipt.summary
                if (summary.hop_index, summary.source_agent, summary.target_agent, summary.route_task_id) != (
                    index, hop.source_agent.value, hop.target_agent.value, hop.task_id
                ):
                    raise ValueError("journal accepted hop mismatch")
            if set(receipt_map) - expected_ids:
                raise ValueError("journal commits absent from checkpoint")
            for transfer_id, proposal in proposed.items():
                if transfer_id in expected_ids:
                    continue
                summary = proposal.summary
                denial = rejected.get(transfer_id)
                # Earlier proposals are audit evidence once a consistent
                # committed checkpoint+journal proves the winning hop.
                if summary.hop_index < run.handoff_count and integrity == "valid":
                    continue
                if not run.pending_tasks or (summary.source_agent, summary.hop_index, summary.route_task_id) != (
                    run.current_agent.value, run.handoff_count, run.pending_tasks[0].task_id
                ) or summary.target_agent != next_agent_for_task(run.current_agent, run.pending_tasks[0].route_key):
                    raise ValueError("uncommitted proposal disagrees with source checkpoint")
                if integrity == "valid":
                    integrity = "uncommitted"
                if denial and not denial.retry_allowed:
                    reasons.append("terminal_transfer_rejection")
            for result in run.results:
                event_id = HistoryWriter.event_id(identity, f"result:{run.run_id}:{result.task_id}")
                result_event = self.store.get_history_event(event_id)
                if result_event is None:
                    integrity = "missing_receipt"
                    reasons.append("result_receipt_reconciliation_required")
                    continue
                expected = PublicTaskResult(run_id=run.run_id, route_task_id=result.task_id,
                                            route_key=str(result.route_key), status=str(result.status), content=(result.content or "")[:1000])
                if result_event.event_type is not HistoryEventType.STRUCTURED_OUTPUT or PublicTaskResult.model_validate(result_event.payload) != expected:
                    raise ValueError("checkpoint result receipt mismatch")
        except (ValueError, TypeError, KeyError):
            integrity = "conflict"
            reasons.append("transfer_integrity_conflict")
        remaining = max(0, run.limits.run_timeout_seconds - (self.now() - run.started_at).total_seconds())
        if snapshot.guard_retry_deadline is not None:
            remaining = min(remaining, max(0, (snapshot.guard_retry_deadline - self.now()).total_seconds()))
        exhausted = (remaining <= 0 or run.total_tokens >= run.limits.run_token_budget
                     or any(item.tokens >= run.limits.agent_token_budget for item in run.usage))
        write_status = "none"
        for result in run.results:
            for action in result.data.get("client_actions", []):
                ack = observations.get("action_ack:" + str(action.get("action_id", "")))
                confirmed = (ack is not None and ack.source == "client_action_ack"
                             and self._fresh(ack, identity.conversation_id) and ack.value == "applied")
                if not confirmed:
                    write_status = "unknown"
                elif write_status == "none":
                    write_status = "confirmed"
            for detail in result.data.get("tool_call_details", []):
                if detail.get("name") in MUSIC_WRITES:
                    if detail.get("status") != "success":
                        write_status = "unknown"
                    elif write_status == "none":
                        write_status = "confirmed"
        if snapshot.pending_writes:
            write_status = "unknown"
            reasons.append("checkpoint_pending_writes_unresolved")
        return RevalidationReport(
            field_results=field_results, transfer_integrity=integrity, ref_integrity=ref_status,
            checkpoint_integrity="valid", budget_state="exhausted" if exhausted else "valid",
            invalidated_dependencies=changed, reasons=tuple(reasons), run_id=run.run_id,
            current_agent=run.current_agent.value, remaining_deadline=remaining,
            run_status=run.status.value, write_status=write_status,
            required_drilldown=tuple([*pointers, *journal_refs][:64]) if integrity in {"conflict", "missing_receipt"} else (),
        )

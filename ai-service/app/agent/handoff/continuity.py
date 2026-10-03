"""Opt-in transfer journal at Command and confirmed checkpoint boundaries."""

from dataclasses import dataclass
import asyncio
import hashlib
import json
from datetime import datetime
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.foundation.long_horizon.history_writer import HistoryWriter, digest
from app.agent.foundation.long_horizon.models import (
    AgentTransferSummaryV1,
    AgentProgress,
    CheckpointRef,
    ContinuityIdentity,
    HistoryPointer,
    HistoryEventType,
    HandoffDraftV2,
    PublicRunMetadata,
    PublicTaskResult,
    PublicHistoryError,
    RunProgressRecord,
    TransferJournalEvent,
)
from app.agent.handoff.contracts import HandoffCode, HandoffEnvelope
from app.agent.handoff.messages import parse_transfer_call
from app.agent.handoff.integrity import TransferIntegrityConflict, payload_digest, transfer_payload
from app.agent.handoff.registry import transfer_tool_name
from app.agent.handoff.state import HandoffRun


def transfer_identity(run_id: str, call_id: str) -> str:
    return "transfer:" + digest([run_id, call_id])[7:]


def pointer(event) -> HistoryPointer:
    return HistoryPointer(kind="event", target_id=event.event_id)


def public_run(run: HandoffRun) -> PublicRunMetadata:
    return PublicRunMetadata(
        run_id=run.run_id, entry_agent=run.entry_agent.value,
        current_agent=run.current_agent.value, status=run.status.value,
        started_at=run.started_at, chain=run.handoff_chain,
        accepted_hop_count=run.handoff_count,
        pending_task_ids=tuple(task.task_id for task in run.pending_tasks),
        plan_digest=digest(run.plan.model_dump(mode="json")),
        usage=tuple(item.model_dump(mode="json") for item in run.usage),
        limits=run.limits.model_dump(mode="json"),
    )


def public_state_digest(run: HandoffRun) -> str:
    def effects(result):
        return {
            field: [{key: item[key] for key in ("action_id", "operation_id", "tool_call_id", "name", "type", "status") if key in item}
                    for item in result.data.get(field, [])]
            for field in ("client_actions", "tool_call_details")
        }
    return digest({
        "run": public_run(run).model_dump(mode="json"),
        "original_input_digest": digest(run.original_input),
        "hops": [
            {**hop.model_dump(mode="json"), "tool_call_id": transfer_identity(run.run_id, hop.tool_call_id)}
            for hop in run.hops
        ],
        "results": [
            {"task_id": result.task_id, "route_key": str(result.route_key), "status": result.status.value,
             "content_digest": digest(result.content or ""), "effects": effects(result)}
            for result in run.results
        ],
    })


@dataclass(frozen=True)
class CheckpointSnapshot:
    """Internal evidence obtained from a saver read, not from client resume input."""

    identity: ContinuityIdentity
    ref: CheckpointRef
    run: HandoffRun
    messages: tuple
    envelope: HandoffEnvelope | dict | None
    ancestor_checkpoint_ids: frozenset[str] = frozenset()
    next_nodes: tuple[str, ...] = ()
    pending_writes: bool = False
    guard_retry_deadline: datetime | None = None


def checkpoint_envelope(raw):
    """Retain malformed private data so the journal can audit and reject it."""
    if raw is None:
        return None
    try:
        return HandoffEnvelope.model_validate(raw)
    except (ValueError, TypeError):
        return raw


def snapshot_from_row(row, identity, *, ancestors=(), next_nodes=()) -> CheckpointSnapshot:
    config = row.config["configurable"]
    if config.get("session_id", config.get("checkpoint_ns")) != identity.session_id:
        raise ValueError("checkpoint session scope mismatch")
    if config.get("thread_id") != identity.conversation_id:
        raise ValueError("checkpoint conversation scope mismatch")
    values = row.checkpoint["channel_values"]
    run = HandoffRun.model_validate(values["handoff_run"])
    if identity.run_id is not None and identity.run_id != run.run_id:
        raise ValueError("checkpoint run identity mismatch")
    ref = CheckpointRef(
        execution_thread_id=config["thread_id"], session_id=identity.session_id,
        checkpoint_ns=identity.session_id, checkpoint_id=row.checkpoint["id"],
        public_state_digest=public_state_digest(run),
    )
    return CheckpointSnapshot(
        identity=identity, ref=ref, run=run,
        messages=tuple(values.get("messages", [])),
        envelope=checkpoint_envelope(values.get("transfer_envelope")),
        ancestor_checkpoint_ids=frozenset(ancestors), next_nodes=tuple(next_nodes),
        pending_writes=bool(row.pending_writes),
        guard_retry_deadline=datetime.fromisoformat(values["guard_retry_deadline"])
        if values.get("guard_retry_deadline") else None,
    )


def validate_transfer_messages(snapshot: CheckpointSnapshot) -> dict[str, str]:
    calls, replies, pending = {}, {}, {}
    for message in snapshot.messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if str(call.get("name", "")).startswith("transfer_to_"):
                    if call.get("id") in pending:
                        raise ValueError("duplicate checkpoint transfer call")
                    pending[call.get("id")] = (call, message.id)
        elif isinstance(message, ToolMessage):
            pair = pending.pop(message.tool_call_id, None)
            if pair is None:
                if str(message.name or "").startswith("transfer_to_"):
                    raise ValueError("orphan checkpoint transfer reply")
                continue
            call, assistant_id = pair
            # Transfer message IDs use json.dumps's default separators.
            identity = hashlib.sha256(json.dumps([snapshot.run.run_id, message.tool_call_id]).encode()).hexdigest()
            if message.id == f"handoff-result:{identity}" or assistant_id == f"handoff-call:{identity}":
                if message.tool_call_id in replies:
                    raise ValueError("duplicate checkpoint transfer reply")
                calls[message.tool_call_id] = call
                replies[message.tool_call_id] = message
    if pending:
        raise ValueError("unpaired checkpoint transfer call")
    for message in snapshot.messages:
        if isinstance(message, ToolMessage) and str(message.name or "").startswith("transfer_to_"):
            if message.tool_call_id not in calls and not str(message.id or "").startswith("handoff-result:"):
                raise ValueError("orphan checkpoint transfer reply")
    digests = {}
    for hop in snapshot.run.hops:
        call = calls.get(hop.tool_call_id)
        reply = replies.get(hop.tool_call_id)
        if call is None or reply is None or call["name"] != transfer_tool_name(hop.target_agent):
            raise ValueError("checkpoint hop has no matching transfer pair")
        request = parse_transfer_call(AIMessage(content="", tool_calls=[call]), hop.source_agent)
        if request.target_agent != hop.target_agent:
            raise ValueError("checkpoint transfer target mismatch")
        if json.loads(reply.content) != {"accepted": True, "code": "accepted"}:
            raise ValueError("checkpoint hop has no accepted reply")
        digests[hop.tool_call_id] = payload_digest(transfer_payload(
            snapshot.run, request, source_agent=hop.source_agent, tool_call_id=hop.tool_call_id,
            completed_count=hop.completed_count,
        ))
    if snapshot.envelope is not None:
        raw = snapshot.envelope
        envelope = HandoffEnvelope.model_validate(raw.model_dump(mode="json") if isinstance(raw, HandoffEnvelope) else raw)
        if not snapshot.run.hops:
            raise ValueError("checkpoint envelope has no accepted hop")
        hop = snapshot.run.hops[-1]
        if (envelope.run_id, envelope.tool_call_id, envelope.source_agent,
            envelope.request.target_agent, envelope.active_task.task_id) != (
            snapshot.run.run_id, hop.tool_call_id, hop.source_agent, hop.target_agent, hop.task_id
        ):
            raise ValueError("checkpoint envelope disagrees with accepted hop")
        if tuple(envelope.pending_tasks) != snapshot.run.pending_tasks:
            raise ValueError("checkpoint envelope pending tasks disagree")
        if envelope.original_input != snapshot.run.original_input:
            raise ValueError("checkpoint envelope changed original authority")
        if [(item.task_id, str(item.route_key), item.content) for item in envelope.completed_results] != [
            (item.task_id, str(item.route_key), (item.content or "")[:1000]) for item in snapshot.run.results
        ]:
            raise ValueError("checkpoint envelope completed results disagree")
        if payload_digest(envelope.model_dump(mode="json")) != digests[hop.tool_call_id]:
            raise ValueError("checkpoint transfer envelope payload mismatch")
    return digests


class AgentTransferJournal:
    def __init__(self, writer: HistoryWriter):
        self.writer = writer
        self._commit_signals = {}

    async def await_committed(self, identity, run, *, messages, envelope, timeout):
        """Opt-in graph nodes cannot act on an in-memory accepted Command."""
        if not run.hops:
            return
        transfer_id = transfer_identity(run.run_id, run.hops[-1].tool_call_id)
        key = (identity.session_id, transfer_id)
        loop = asyncio.get_running_loop()
        signal = asyncio.Event()
        self._commit_signals[key] = (loop, signal)
        try:
            if self._event(identity, transfer_id, "committed") or self._event(identity, transfer_id, "reconciled"):
                signal.set()
            async with asyncio.timeout(timeout):
                await signal.wait()
            receipt = self._event(identity, transfer_id, "committed") or self._event(identity, transfer_id, "reconciled")
            if receipt is None:
                raise ValueError("transfer commit receipt unavailable")
            summary = TransferJournalEvent.model_validate(receipt.payload).summary
            hop = run.hops[-1]
            if (summary.run_id, summary.source_agent, summary.target_agent, summary.route_task_id, summary.hop_index) != (
                run.run_id, hop.source_agent.value, hop.target_agent.value, hop.task_id, len(run.hops) - 1
            ):
                raise ValueError("transfer commit disagrees with target execution")
            # Check the actual node input, even if its persisted checkpoint was valid.
            self.verify_payloads(identity, run, messages, envelope, require_receipts=True)
        finally:
            self._commit_signals.pop(key, None)

    def _result_refs(self, identity, run):
        return tuple(pointer(self.writer.task_result(identity, PublicTaskResult(
            run_id=run.run_id, route_task_id=result.task_id,
            route_key=str(result.route_key), status=str(result.status),
            content=(result.content or "")[:1000],
        ))) for result in run.results)

    def _event(self, identity, transfer_id, phase):
        return self.writer.store.get_history_event(self.writer.event_id(identity, f"{transfer_id}:{phase}"))

    def verify_payloads(self, identity, run, messages, envelope, *, require_receipts=False):
        """Compare actual payloads with sender proposals and committed receipts.

        Missing receipts may be reconciled only from matching immutable proposals.
        A missing legacy digest is never generated from the received payload.
        """
        self.writer.require_identity(identity)
        if identity.graph_mode != "handoff" or identity.run_id != run.run_id:
            raise ValueError("transfer payload identity mismatch")
        if not run.hops:
            validate_transfer_messages(SimpleNamespace(run=run, messages=tuple(messages), envelope=envelope))
            return
        related = transfer_identity(run.run_id, run.hops[-1].tool_call_id)
        try:
            snapshot = SimpleNamespace(run=run, messages=tuple(messages), envelope=envelope)
            actual = validate_transfer_messages(snapshot)
            for hop in run.hops:
                related = transfer_identity(run.run_id, hop.tool_call_id)
                proposal = self._event(identity, related, "proposed")
                receipt = self._event(identity, related, "committed") or self._event(identity, related, "reconciled")
                if proposal is None or (require_receipts and receipt is None):
                    raise ValueError("transfer payload digest has no immutable anchor")
                expected = TransferJournalEvent.model_validate(proposal.payload).summary.payload_digest
                if expected is None or actual[hop.tool_call_id] != expected:
                    raise ValueError("transfer payload digest mismatch")
                if receipt and TransferJournalEvent.model_validate(receipt.payload).summary.payload_digest != expected:
                    raise ValueError("transfer receipt payload digest mismatch")
        except (ValueError, KeyError, TypeError):
            self.writer.append_public_event(
                identity, f"{related}:integrity_conflict", HistoryEventType.ERROR,
                PublicHistoryError(code="integrity_conflict", related_key_digest=digest(related)),
            )
            raise TransferIntegrityConflict() from None

    def observe_target(self, snapshot, *, phase):
        """Explicit execution hook; a saved task result proves target_finished."""
        if phase not in {"target_started", "target_finished"} or not snapshot.run.hops:
            raise ValueError("target observation requires an accepted transfer")
        self.confirm_checkpoint(snapshot)
        run, identity = snapshot.run, snapshot.identity
        hop = run.hops[-1]
        if run.current_agent != hop.target_agent:
            raise ValueError("target observation owner mismatch")
        finished = any(result.task_id == hop.task_id for result in run.results)
        if (phase == "target_finished") != finished:
            raise ValueError("target phase disagrees with checkpoint task result")
        transfer_id = transfer_identity(run.run_id, hop.tool_call_id)
        existing = self._event(identity, transfer_id, phase)
        if existing:
            return existing
        receipt = self._event(identity, transfer_id, "committed") or self._event(identity, transfer_id, "reconciled")
        summary = TransferJournalEvent.model_validate(receipt.payload).summary
        return self.writer.transfer(identity, f"{transfer_id}:{phase}", TransferJournalEvent(
            phase=phase, transfer_id=transfer_id, summary=summary, summary_ref=pointer(receipt),
            checkpoint_ref=snapshot.ref, result_refs=self._result_refs(identity, run),
        ))

    def observe_transfer(self, identity, run_before, command_after, *, phase="proposed"):
        """A Command is a proposal; committed is only emitted after a saver read."""
        self.writer.require_identity(identity)
        if identity.graph_mode != "handoff" or identity.run_id != run_before.run_id:
            raise ValueError("transfer identity must match handoff run")
        if phase not in {"proposed", "rejected"}:
            raise ValueError("Command observation cannot confirm checkpoint commit")
        update = command_after if isinstance(command_after, dict) else command_after.update
        message = next((item for item in update.get("messages", []) if isinstance(item, AIMessage)), None)
        if message is None:
            raise ValueError("transfer proposal requires its control call")
        request = parse_transfer_call(message, run_before.current_agent)
        call_id = message.tool_calls[0]["id"]
        reason_code = "capability_owner"
        if phase == "rejected":
            reply = next(item for item in update["messages"] if isinstance(item, ToolMessage) and item.tool_call_id == call_id)
            reason_code = HandoffCode(json.loads(reply.content)["code"]).value
        summary = AgentTransferSummaryV1(
            transfer_id=transfer_identity(run_before.run_id, call_id),
            run_id=run_before.run_id, hop_index=run_before.handoff_count,
            source_agent=run_before.current_agent.value, target_agent=request.target_agent.value,
            route_task_id=run_before.pending_tasks[0].task_id,
            reason_code=reason_code, candidate_summary=request.context_summary,
            payload_digest=payload_digest(transfer_payload(
                run_before, request, source_agent=run_before.current_agent, tool_call_id=call_id,
            )),
            completed_result_refs=self._result_refs(identity, run_before),
            pending_task_refs=tuple(task.task_id for task in run_before.pending_tasks),
        )
        return self.writer.transfer(identity, f"{summary.transfer_id}:{phase}", TransferJournalEvent(
            phase=phase, transfer_id=summary.transfer_id, summary=summary,
            retry_allowed=bool(update.get("guard_retry", False)) if phase == "rejected" else False,
        ))

    def confirm_checkpoint(self, snapshot: CheckpointSnapshot, *, reconcile=False):
        identity, run = snapshot.identity, snapshot.run
        self.writer.require_identity(identity)
        if identity.graph_mode != "handoff" or identity.run_id != run.run_id:
            raise ValueError("checkpoint run identity mismatch")
        if (snapshot.ref.session_id, snapshot.ref.checkpoint_ns, snapshot.ref.execution_thread_id) != (
            identity.session_id, identity.session_id, identity.conversation_id
        ):
            raise ValueError("checkpoint journal scope mismatch")
        self.verify_payloads(identity, run, snapshot.messages, snapshot.envelope)
        if snapshot.ref.public_state_digest != public_state_digest(run):
            raise ValueError("checkpoint public state digest mismatch")
        transfer_refs = []
        for index, hop in enumerate(run.hops):
            transfer_id = transfer_identity(run.run_id, hop.tool_call_id)
            existing = self._event(identity, transfer_id, "committed") or self._event(identity, transfer_id, "reconciled")
            if existing:
                receipt = TransferJournalEvent.model_validate(existing.payload)
                if (receipt.summary.run_id, receipt.summary.hop_index, receipt.summary.source_agent,
                    receipt.summary.target_agent, receipt.summary.route_task_id) != (
                    run.run_id, index, hop.source_agent.value, hop.target_agent.value, hop.task_id
                ):
                    raise ValueError("committed journal disagrees with checkpoint")
                if receipt.checkpoint_ref.checkpoint_id not in {
                    snapshot.ref.checkpoint_id, *snapshot.ancestor_checkpoint_ids
                }:
                    raise ValueError("journal checkpoint is outside current lineage")
                transfer_refs.append(pointer(existing))
                continue
            proposal = self._event(identity, transfer_id, "proposed")
            if proposal is None:
                raise ValueError("checkpoint transfer proposal missing; reconciliation requires history")
            summary = TransferJournalEvent.model_validate(proposal.payload).summary
            if (summary.run_id, summary.hop_index, summary.source_agent, summary.target_agent,
                summary.route_task_id) != (run.run_id, index, hop.source_agent.value, hop.target_agent.value, hop.task_id):
                raise ValueError("transfer proposal disagrees with checkpoint")
            phase = "reconciled" if reconcile else "committed"
            receipt = self.writer.transfer(identity, f"{transfer_id}:{phase}", TransferJournalEvent(
                phase=phase, transfer_id=transfer_id, summary=summary, summary_ref=pointer(proposal),
                checkpoint_ref=snapshot.ref, result_refs=summary.completed_result_refs,
            ))
            transfer_refs.append(pointer(receipt))
            signal = self._commit_signals.get((identity.session_id, transfer_id))
            if signal:
                signal[0].call_soon_threadsafe(signal[1].set)
        progress = self.writer.run_progress(identity, RunProgressRecord(
            run=public_run(run), checkpoint_ref=snapshot.ref,
            result_refs=self._result_refs(identity, run), transfer_refs=tuple(transfer_refs),
        ))
        return pointer(progress)


class ProgressProjector:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    def _validate_record(self, event):
        record = RunProgressRecord.model_validate(event.payload)
        run = record.run
        if record.checkpoint_ref.session_id != event.session_id:
            raise ValueError("run progress checkpoint session mismatch")
        if len(record.transfer_refs) != run.accepted_hop_count:
            raise ValueError("run progress lacks committed hop receipts")
        for index, ref in enumerate(record.transfer_refs):
            receipt_event = self.store.get_history_event(ref.target_id)
            if receipt_event is None or (receipt_event.task_id, receipt_event.session_id) != (event.task_id, event.session_id):
                raise ValueError("run progress transfer scope mismatch")
            if receipt_event.semantic_type is not HistoryEventType.AGENT_TRANSFER:
                raise ValueError("run progress reference is not a transfer")
            receipt = TransferJournalEvent.model_validate(receipt_event.payload)
            summary = receipt.summary
            if receipt.phase not in {"committed", "reconciled"}:
                raise ValueError("uncommitted transfer cannot count as accepted")
            if (summary.run_id, summary.hop_index, summary.source_agent, summary.target_agent) != (
                run.run_id, index, run.chain[index], run.chain[index + 1]
            ):
                raise ValueError("run progress committed chain mismatch")
        results = []
        for ref in record.result_refs:
            result_event = self.store.get_history_event(ref.target_id)
            if result_event is None or (result_event.task_id, result_event.session_id) != (event.task_id, event.session_id):
                raise ValueError("run progress result scope mismatch")
            if result_event.event_type is not HistoryEventType.STRUCTURED_OUTPUT:
                raise ValueError("run progress reference is not a public result")
            result = PublicTaskResult.model_validate(result_event.payload)
            if result.run_id != run.run_id or result.route_task_id in run.pending_task_ids:
                raise ValueError("run progress result disagrees with pending tasks")
            results.append(result)
        if len({result.route_task_id for result in results}) != len(results):
            raise ValueError("run progress contains duplicate results")
        return record, results

    def project_progress(self, session, event_cursor=0, projection_budget=None, *, draft):
        """Project only persisted receipts; proposal text never marks work done."""
        session = self.store.require_session(session if isinstance(session, str) else session.session_id)
        cursor, all_events = event_cursor, []
        while True:
            batch = self.store.query_task_history(
                session.task_id, session_id=session.session_id, after_seq=cursor, limit=100,
            )
            if not batch:
                break
            all_events.extend(batch)
            cursor = batch[-1].event_seq
            if len(all_events) > 1000:
                raise ValueError("progress event window requires a bounded cursor")
        records = {}
        if isinstance(draft, HandoffDraftV2) and draft.agent_progress.run_state_ref:
            previous_event = self.store.get_history_event(draft.agent_progress.run_state_ref.target_id)
            if previous_event is None or previous_event.task_id != session.task_id:
                raise ValueError("incremental progress pointer scope mismatch")
            if previous_event.session_id == session.session_id:
                record, results = self._validate_record(previous_event)
                records[record.run.run_id] = (previous_event, record, results)
        audit_refs = []
        latest_transfer = None
        for event in all_events:
            if event.semantic_type is HistoryEventType.AGENT_TRANSFER:
                transfer = TransferJournalEvent.model_validate(event.payload)
                latest_transfer = (event, transfer)
                if transfer.phase in {"proposed", "rejected"}:
                    audit_refs.append(pointer(event))
            elif event.event_type is HistoryEventType.RUN_PROGRESS:
                record, results = self._validate_record(event)
                records.pop(record.run.run_id, None)
                records[record.run.run_id] = (event, record, results)
        prior = self.store.get_handoff(session.inherited_handoff_id) if session.inherited_handoff_id else None
        inherited_progress = draft.agent_progress if isinstance(draft, HandoffDraftV2) and draft.agent_progress.last_run_id else (
            prior.agent_progress if prior else AgentProgress()
        )
        completed, source_checkpoint = [], getattr(prior, "source_checkpoint_ref", None)
        progress = inherited_progress
        if records:
            event, record, _ = list(records.values())[-1]
            run = record.run
            phase = "committed" if run.accepted_hop_count else "unknown"
            if latest_transfer and latest_transfer[1].summary.run_id == run.run_id:
                phase = latest_transfer[1].phase
            earlier = [pointer(item[0]) for item in list(records.values())[:-1]]
            earlier = [*inherited_progress.earlier_chain_refs, *earlier]
            if inherited_progress.run_state_ref and inherited_progress.last_run_id != run.run_id:
                earlier.append(inherited_progress.run_state_ref)
            earlier = list(dict.fromkeys(earlier))
            progress = AgentProgress(
                last_run_id=run.run_id, last_agent=run.current_agent, run_status=run.status,
                last_route_task_id=run.pending_task_ids[0] if run.pending_task_ids else None,
                recent_run_chain=run.chain, accepted_hop_count=run.accepted_hop_count,
                transfer_phase=phase, run_state_ref=pointer(event),
                progress_refs=record.result_refs, earlier_chain_refs=tuple(earlier),
                pending_transfer_ref=pointer(latest_transfer[0])
                if latest_transfer and latest_transfer[1].phase in {"proposed", "rejected"} else None,
            )
            source_checkpoint = record.checkpoint_ref
            for _, _, results in records.values():
                completed.extend(
                    f"{result.run_id}/{result.route_task_id}: {result.content or result.route_key}"
                    for result in results if result.status == "success"
                )
        elif latest_transfer:
            if latest_transfer[1].phase in {"committed", "reconciled", "target_finished", "target_started"}:
                raise ValueError("confirmed transfer is missing its run progress checkpoint")
            progress = inherited_progress.model_copy(update={
                "transfer_phase": latest_transfer[1].phase,
                "pending_transfer_ref": pointer(latest_transfer[0]),
            })
        values = draft.model_dump(mode="json")
        for field in ("schema_version", "projection_version", "agent_progress", "source_event_cursor", "source_checkpoint_ref", "counter_name"):
            values.pop(field, None)
        pending = []
        if records:
            pending = list(list(records.values())[-1][1].run.pending_task_ids)
        projected = HandoffDraftV2(
            **{key: value for key, value in values.items() if key not in {"summary", "completed_steps", "pending_items", "next_actions", "key_message_refs"}},
            agent_progress=progress, source_event_cursor=cursor,
            source_checkpoint_ref=source_checkpoint,
            counter_name=getattr(self.service.counter, "name", "custom"),
            summary="\n".join(completed)[:12000], completed_steps=list(dict.fromkeys(completed)),
            pending_items=pending, next_actions=[f"继续待办 {task}" for task in pending],
            key_message_refs=list(dict.fromkeys([*draft.key_message_refs, *audit_refs]))[:32],
        )
        # Use the real service's complete-envelope budgeting, without storing a projection yet.
        if projection_budget is None:
            bounded_projection = self.service.build_handoff(session.session_id, projected)
        else:
            from app.agent.foundation.long_horizon.service import LongHorizonContextService

            bounded = LongHorizonContextService(
                self.store, policy=self.service.policy.model_copy(update={"handoff_token_budget": projection_budget}),
                counter=self.service.counter, now=self.service._now,
            )
            bounded_projection = bounded.build_handoff(session.session_id, projected)
        return HandoffDraftV2.model_validate({
            key: value for key, value in bounded_projection.model_dump(mode="json").items()
            if key in HandoffDraftV2.model_fields
        })


class JournalCheckpointer(BaseCheckpointSaver):
    """Explicit adapter: writes receipts only after the delegated saver succeeds."""

    def __init__(self, saver, journal):
        super().__init__(serde=saver.serde)
        self.saver = saver
        self.journal = journal

    @property
    def config_specs(self):
        return self.saver.config_specs

    def get_tuple(self, config):
        return self.saver.get_tuple(config)

    async def aget_tuple(self, config):
        return await self.saver.aget_tuple(config)

    def list(self, config, **kwargs):
        yield from self.saver.list(config, **kwargs)

    async def alist(self, config, **kwargs):
        async for row in self.saver.alist(config, **kwargs):
            yield row

    def put_writes(self, config, writes, task_id, task_path=""):
        return self.saver.put_writes(config, writes, task_id, task_path)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await self.saver.aput_writes(config, writes, task_id, task_path)

    @staticmethod
    def _identity(row):
        values = row.checkpoint["channel_values"]
        raw_run = values.get("handoff_run")
        if raw_run is None:
            return None
        context = values.get("continuity_context", {})
        return ContinuityIdentity(
            conversation_id=context["conversation_id"], continuity_task_id=context["task_id"],
            session_id=context["session_id"], request_id=context["request_id"],
            graph_mode="handoff", run_id=raw_run["run_id"],
        )

    def put(self, config, checkpoint, metadata, new_versions):
        stored = self.saver.put(config, checkpoint, metadata, new_versions)
        row = self.saver.get_tuple(stored)
        identity = self._identity(row)
        if identity:
            ancestors, parent = [], row.parent_config
            while parent:
                ancestor = self.saver.get_tuple(parent)
                if ancestor is None:
                    raise ValueError("checkpoint ancestor unavailable")
                if ancestor.checkpoint["id"] in ancestors or len(ancestors) >= 1000:
                    raise ValueError("checkpoint lineage requires bounded history drilldown")
                ancestors.append(ancestor.checkpoint["id"])
                parent = ancestor.parent_config
            self.journal.confirm_checkpoint(snapshot_from_row(row, identity, ancestors=ancestors))
        return stored

    async def aput(self, config, checkpoint, metadata, new_versions):
        stored = await self.saver.aput(config, checkpoint, metadata, new_versions)
        row = await self.saver.aget_tuple(stored)
        identity = self._identity(row)
        if identity:
            ancestors, parent = [], row.parent_config
            while parent:
                ancestor = await self.saver.aget_tuple(parent)
                if ancestor is None:
                    raise ValueError("checkpoint ancestor unavailable")
                if ancestor.checkpoint["id"] in ancestors or len(ancestors) >= 1000:
                    raise ValueError("checkpoint lineage requires bounded history drilldown")
                ancestors.append(ancestor.checkpoint["id"])
                parent = ancestor.parent_config
            self.journal.confirm_checkpoint(snapshot_from_row(row, identity, ancestors=ancestors))
        return stored

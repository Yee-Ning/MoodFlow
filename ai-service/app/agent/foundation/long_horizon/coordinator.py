"""One server-owned lifecycle for JSON and SSE, independent of consumers."""

import asyncio
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from dataclasses import replace
from datetime import UTC, datetime
from functools import lru_cache
from threading import RLock
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RouteDecision
from app.agent.foundation.state.main import create_main_state
from app.agent.handoff.continuity import AgentTransferJournal, ProgressProjector, snapshot_from_row
from app.agent.handoff.state import HandoffRun, RunStatus

from .accounting import DurableAccounting
from .drilldown import HistoryDrilldown
from .execution import ContinuityConflict, ExecutionLocks
from .history_writer import HistoryWriter, digest
from .models import ContinuityIdentity, HandoffDraftV2, HistoryEventType, LiveSnapshot, PublicTaskResult, StateObservation
from .operations import OperationJournal
from .public_events import PublicAction, PublicActionAck, PublicCheckpoint, PublicRoute
from .recovery_executor import RecoveryExecutor
from .recovery_receipts import RecoveryReceiptRepair, is_recovery_query, latest_recovery_status, recovery_message
from .runtime import get_long_horizon_service

logger = logging.getLogger(__name__)
active_call = ContextVar("continuity_active_call", default=None)


def index_observations(conversation_id):
    """Read the configured immutable manifest without creating a retriever/LLM."""
    import json
    from pathlib import Path
    from app.rag.indexing.models import IndexBuildManifest
    from app.agent.foundation.subgraphs.worldline.retrieval import RetrievalRuntimeSettings
    now = datetime.now(UTC)
    settings = RetrievalRuntimeSettings()
    mode = settings.retrieval_mode
    values, status = {"index_version": "disabled", "release_version": "disabled"}, "valid"
    if mode != "empty":
        try:
            root = Path(__file__).resolve().parents[4]
            path = (root / (settings.index_manifest or "")).resolve()
            path.relative_to(root)
            if path.stat().st_size > 1_048_576:
                raise ValueError("manifest byte limit")
            manifest = IndexBuildManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
            if not manifest.ready_for_retrieval:
                raise ValueError("manifest not ready")
            values = {"index_version": digest([manifest.collection_name, manifest.config_fingerprint,
                manifest.source_fingerprint, manifest.projection_fingerprint]),
                "release_version": manifest.source_release_id}
        except (OSError, ValueError):
            values, status = {"index_version": None, "release_version": None}, "unknown"
    return tuple(StateObservation(field=key, value=value, source="retrieval_manifest", observed_at=now,
        scope=conversation_id, revision=digest(value), status=status) for key, value in values.items())


def server_snapshot(conversation_id, routing_context, user_id):
    now = datetime.now(UTC)
    values = {"user_id": user_id, "logged_in": user_id is not None}
    observations = [StateObservation(field=key, value=value, source="server_auth", observed_at=now,
                                    scope=conversation_id, revision=digest([user_id, value])) for key, value in values.items()]
    for key in ("current_song_id", "player_state", "queue_size", "current_index"):
        value = routing_context.model_dump(mode="json").get(key)
        observations.append(StateObservation(field=key, value=value, source="client_player", observed_at=now,
                                            scope=conversation_id, revision=digest(value)))
    return LiveSnapshot(observations=(*observations, *index_observations(conversation_id)))


@dataclass
class ContinuityCall:
    coordinator: object
    identity: ContinuityIdentity
    session: object
    config: dict
    graph_input: dict | None
    live: LiveSnapshot
    lease: object
    accounting: object
    operations: object
    graph: object
    blocked_response: str | None = None
    revalidation: dict | None = None
    checkpoint_lock: object = field(default_factory=RLock)
    action_sink: object = None

    async def close(self):
        await asyncio.to_thread(self.lease.release)


class ContinuityCoordinator:
    def __init__(self, service, *, read_facts=None, read_effects=None):
        self.service, self.store = service, service.store
        self.writer = HistoryWriter(self.store)
        self.journal = AgentTransferJournal(self.writer)
        self.projector = ProgressProjector(service)
        self.history = HistoryDrilldown(self.writer)
        self.locks = ExecutionLocks(self.store)
        self.recovery = RecoveryExecutor(self.writer, self.journal,
            read_facts=read_facts or self.refresh_facts, read_effects=read_effects or self.refresh_effects)
        self.receipt_repair = RecoveryReceiptRepair(self.writer, self.journal)

    async def refresh_effects(self, snapshot, live):
        return self.with_acks(snapshot.identity, live)

    async def refresh_facts(self, fields, snapshot, live):
        observations = {item.field: item for item in live.observations}
        if set(fields) & {"index_version", "release_version"}:
            for item in await asyncio.to_thread(index_observations, snapshot.identity.conversation_id):
                observations[item.field] = item
        # Auth/player facts came from this request's resolved login and client
        # snapshot. Do not invent fresh observations for unavailable sources.
        return LiveSnapshot(observations=tuple(observations.values()))

    async def snapshot(self, graph, config, identity):
        row = await graph.checkpointer.aget_tuple(config)
        if row is None or not row.checkpoint["channel_values"].get("handoff_run"):
            return None
        run_id = row.checkpoint["channel_values"]["handoff_run"]["run_id"]
        identity = identity.model_copy(update={"run_id": run_id})
        ancestors, parent = [], row.parent_config
        while parent:
            prior = await graph.checkpointer.aget_tuple(parent)
            if prior is None or prior.checkpoint["id"] in ancestors or len(ancestors) >= 1000:
                raise ContinuityConflict("checkpoint lineage unproven")
            ancestors.append(prior.checkpoint["id"])
            parent = prior.parent_config
        pinned = {**config, "configurable": {**config["configurable"], "checkpoint_id": row.checkpoint["id"]}}
        state = await graph.aget_state(pinned)
        snapshot = snapshot_from_row(row, identity, ancestors=ancestors, next_nodes=state.next)
        if row.pending_writes and self.safe_pending_writes(row, snapshot):
            snapshot = replace(snapshot, pending_writes=False)
        return snapshot

    def safe_pending_writes(self, row, snapshot):
        """A cached pure Command/result can be applied by LangGraph, not executed
        again. It is still uncommitted: target gating waits for a real new saver
        checkpoint and receipt. Unknown external effects never enter this path.
        """
        from types import SimpleNamespace
        if OperationJournal(self.writer, SimpleNamespace(identity=snapshot.identity)).unknown_writes(snapshot.run.run_id):
            return False
        values = dict(row.checkpoint["channel_values"])
        for _, channel, value in row.pending_writes:
            if channel.startswith("branch:to:") or channel == "__no_writes__":
                continue
            if channel not in {"handoff_run", "messages", "transfer_envelope", "handoff_trace", "guard_retry",
                "guard_retry_deadline", "task_results", "final_response", "handoff_failed", "handoff_needs_input"}:
                return False
            if channel == "messages":
                from langgraph.graph.message import add_messages
                values[channel] = add_messages(values.get(channel, []), value)
            else:
                values[channel] = value
        try:
            candidate = HandoffRun.model_validate(values["handoff_run"])
            if (candidate.run_id, candidate.started_at, candidate.limits) != (snapshot.run.run_id, snapshot.run.started_at, snapshot.run.limits):
                return False
            if (candidate.original_input, candidate.plan, candidate.entry_agent) != (
                snapshot.run.original_input, snapshot.run.plan, snapshot.run.entry_agent):
                return False
            if candidate.results[:len(snapshot.run.results)] != snapshot.run.results:
                return False
            for old, new in zip(snapshot.run.usage, candidate.usage, strict=True):
                if any(getattr(new, field) < getattr(old, field) for field in (
                    "tokens", "retries", "reported_tokens", "estimated_tokens", "model_calls")):
                    return False
            if candidate.hops[:len(snapshot.run.hops)] != snapshot.run.hops or len(candidate.hops) > len(snapshot.run.hops) + 1:
                return False
            for result in candidate.results:
                if result.data.get("client_actions"):
                    return False
            from app.agent.handoff.continuity import checkpoint_envelope
            pending = replace(snapshot, run=candidate, messages=tuple(values.get("messages", [])),
                envelope=checkpoint_envelope(values.get("transfer_envelope")))
            self.journal.verify_payloads(pending.identity, pending.run, pending.messages, pending.envelope)
            return True
        except (ValueError, KeyError, TypeError):
            return False

    async def begin_call(self, *, graph, graph_mode, thread_id, message, routing_context, user_id=None,
                         request_id=None, resume=False, new_session=False, decisions=(), session_cookie=None):
        conversation_id = f"handoff:v1:{thread_id}" if graph_mode == "handoff" else thread_id
        lease = await asyncio.to_thread(self.locks.acquire, conversation_id, digest([graph_mode, user_id]))
        try:
            session = self.service.open_conversation(conversation_id)
            identity = ContinuityIdentity(conversation_id=conversation_id, continuity_task_id=session.task_id,
                session_id=session.session_id, request_id=request_id or "request:" + uuid4().hex, graph_mode=graph_mode)
            config = {"configurable": {"thread_id": conversation_id, "session_id": session.session_id}, "recursion_limit": 40}
            live = server_snapshot(conversation_id, routing_context, user_id)
            accounting = DurableAccounting(self.writer, identity, lease)
            from app.tools.music import MusicServiceClient
            operations = OperationJournal(self.writer, accounting,
                read_client=MusicServiceClient(session_cookie) if session_cookie and user_id is not None else None)
            blocked, graph_input, report_body = None, None, None
            snapshot = await self.snapshot(graph, config, identity) if graph_mode == "handoff" else None
            saved_row = await graph.checkpointer.aget_tuple(config)
            state_config = {**config, "configurable": {**config["configurable"], "checkpoint_id": saved_row.checkpoint["id"]}} if saved_row else config
            state = await graph.aget_state(state_config)
            previous = state.values or {}
            if resume and not previous and session.previous_session_id:
                from .migration import NamespaceMigrator
                config = await NamespaceMigrator(self).recover_missing_target(graph, identity, lease)
                state = await graph.aget_state(config)
                previous = state.values or {}
                snapshot = await self.snapshot(graph, config, identity) if graph_mode == "handoff" else None
            context = previous.get("continuity_context", {})
            previous_run = snapshot.run.run_id if snapshot else context.get("run_id")
            if is_recovery_query(message):
                view = latest_recovery_status(self.store, session.task_id, session_id=session.session_id)
                return ContinuityCall(self, identity, session, config, None, live, lease, accounting,
                    operations, graph, recovery_message(view), view)
            from .write_reconciliation import is_write_status_query
            if context.get("auth_scope") and context["auth_scope"] != lease.scope and (
                is_write_status_query(message) or (previous_run and operations.unknown_writes(previous_run))
            ):
                raise ContinuityConflict("write reconciliation auth scope changed")
            recovering = resume or bool(request_id and context.get("request_id") == request_id)
            gaps = ()
            # A completed transport replay must return the original response;
            # an ACK gap alone is diagnosed only by explicit recovery, not replay.
            repair_needed = snapshot and (resume or state.next or any(
                gap.kind != "action_ack" for gap in self.receipt_repair.scan(snapshot, operations)))
            if recovering and repair_needed:
                accounting.identity = snapshot.identity
                gaps = await self.receipt_repair.repair(snapshot, operations,
                    authority_matches=not context.get("auth_scope") or context["auth_scope"] == lease.scope)
                if gaps:
                    self.receipt_repair.record(snapshot, gaps)
                    snapshot = await self.snapshot(graph, config, identity)
                    if any(gap.status == "blocked" for gap in gaps):
                        blocked = recovery_message(latest_recovery_status(self.store, session.task_id,
                            session_id=session.session_id))
            elif previous_run and operations.reconciler is not None and operations.unknown_writes(previous_run):
                await operations.reconcile_run(previous_run)
                if snapshot:
                    snapshot = await self.snapshot(graph, config, identity)
            # Same request replay uses the original run and checkpoint; a transport
            # identifier is never treated as permission to restart business effects.
            replay = bool(request_id and context.get("request_id") == request_id)
            resume = resume or replay
            if resume and new_session:
                raise ContinuityConflict("resume and new session are separate operations")
            if snapshot and not state.next and operations.unknown_writes(snapshot.run.run_id):
                blocked = await operations.answer_query(message) if is_write_status_query(message) else (
                    "上次写操作的结果尚未确认，需要先查询状态，不能重复执行。")
            if not resume and state.next:
                raise ContinuityConflict("pending run requires empty-input resume")
            if replay and message and message != previous.get("current_input"):
                raise ContinuityConflict("request id reused with a different user request")
            from .models import RolloverReason
            rollover_reason = RolloverReason.EXPLICIT_CHECKPOINT if new_session else None
            if not resume and not new_session and previous and not state.next and not blocked and getattr(self.store, "supports_atomic_rollover", False):
                pressure = sum(self.service.counter.count(str(item.content)) for item in previous.get("messages", []))
                if not self.pending_actions(identity):
                    rollover_reason = self.service.should_rollover(active_context_tokens=pressure)
            if rollover_reason:
                if not getattr(self.store, "supports_atomic_rollover", False):
                    raise ContinuityConflict("SQLite automatic rollover is disabled")
                if snapshot and snapshot.run.status not in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.NEEDS_INPUT}:
                    raise ContinuityConflict("pending run must recover in its original namespace")
                if blocked:
                    raise ContinuityConflict("unknown write effect prevents rollover")
                if self.pending_actions(identity):
                    raise ContinuityConflict("unconfirmed client actions prevent rollover")
                draft = self.draft(session, snapshot, context.get("live_snapshot"), previous.get("current_input") or message)
                rollover = self.service.rollover(session.session_id, draft, rollover_reason,
                                                 automatic=True, writer=self.writer, identity=identity)
                session = rollover.new_session
                identity = identity.model_copy(update={"session_id": session.session_id})
                accounting.identity = identity
                config["configurable"]["session_id"] = session.session_id
                previous, context = {}, {}
            if resume:
                if not previous:
                    raise ContinuityConflict("resume checkpoint unavailable")
                if context.get("graph_mode") != graph_mode:
                    raise ContinuityConflict("checkpoint graph mode mismatch")
                if snapshot and not state.next and not any(gap.status == "blocked" for gap in gaps):
                    self.journal.confirm_checkpoint(snapshot, reconcile=True)
                    if previous.get("final_response"):
                        self.history.public_text(snapshot.identity, "final:" + snapshot.run.run_id,
                                                 HistoryEventType.ASSISTANT_MESSAGE, previous["final_response"])
                if snapshot and state.next and blocked is None:
                    identity = snapshot.identity
                    accounting.identity = identity
                    live = self.with_acks(identity, live)
                    draft = self.draft(session, snapshot, context.get("live_snapshot"), snapshot.run.original_input)
                    projection = self.service.build_handoff(session.session_id, draft)
                    plan, report, live = await self.recovery.execute(projection, live, snapshot, operations=operations, receipt_gaps=gaps)
                    report_body = report.model_dump(mode="json")
                    totals = accounting.totals(snapshot.run.run_id, [item.model_dump(mode="json") for item in snapshot.run.usage])
                    if (sum(item["tokens"] for item in totals) >= snapshot.run.limits.run_token_budget
                        or any(item["tokens"] >= snapshot.run.limits.agent_token_budget or item["retries"] > snapshot.run.limits.max_retries_per_agent for item in totals)):
                        blocked = recovery_message(latest_recovery_status(self.store, session.task_id, session_id=session.session_id))
                    elif plan.action not in {"resume_target", "resume_source"}:
                        blocked = recovery_message(latest_recovery_status(self.store, session.task_id, session_id=session.session_id))
                    else:
                        # The exclusive fence protects the saved source namespace.
                        # An unpinned empty-input resume lets LangGraph apply only
                        # its already persisted cached writes, then save/confirm.
                        config = {**config, "configurable": {key: value for key, value in config["configurable"].items() if key != "checkpoint_id"}}
                        # The next model pack receives source-aware results. Do not
                        # mutate the original goal, owner, run usage, or start time.
                        context["revalidation"] = report.model_dump(mode="json")
                elif graph_mode == "foundation" and state.next:
                    started = context.get("started_at")
                    if not started or (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds() >= 120:
                        blocked = "原运行截止时间已到，恢复已停止。"
                    elif context.get("auth_scope") != lease.scope or operations.unknown_writes(context.get("run_id")):
                        blocked = "原运行权限或写操作状态尚未确认。"
                if snapshot and gaps and (not state.next or blocked):
                    if not state.next:
                        reason = "receipt_evidence_insufficient" if any(gap.status == "blocked" for gap in gaps) else "run_terminal"
                        self.receipt_repair.record(snapshot, gaps, action="stop", reason=reason)
                    view = latest_recovery_status(self.store, session.task_id, session_id=session.session_id)
                    report_body = view
                    if any(gap.status == "blocked" for gap in gaps):
                        blocked = recovery_message(view)
                # Empty input resumes saved pending nodes. For a completed run,
                # runner returns the saved visible response without invoking again.
            elif blocked is None:
                run_id = "foundation:" + digest(identity.request_id)[7:]
                history = self.history.read(identity)
                inherited = self.store.get_handoff(session.inherited_handoff_id) if session.inherited_handoff_id else None
                if inherited:
                    inherited = self.service.apply_revocations(inherited, session.task_id)
                field_results = self.recovery.validator.check_fields(inherited, live, conversation_id) if inherited else {}
                current_context = {"schema_version": 1, "conversation_id": conversation_id,
                    "task_id": session.task_id, "session_id": session.session_id, "request_id": identity.request_id,
                    "graph_mode": graph_mode, "run_id": run_id, "started_at": datetime.now(UTC).isoformat(),
                    "auth_scope": lease.scope, "live_snapshot": live.model_dump(mode="json"),
                    "execution_fence": lease.fence, "state_revision": lease.revision,
                    "handoff": inherited.model_dump(mode="json") if inherited else None,
                    "revalidation": {"field_results": field_results},
                    "changed_fields": [field for field, status in field_results.items() if status == "changed"],
                    "key_events": list(history.items), "artifacts": [],
                    "effective_decisions": [item.model_dump(mode="json") for item in self.history.effective_decisions(identity)]}
                self.history.public_text(identity, "user:" + identity.request_id, HistoryEventType.USER_MESSAGE, message)
                for decision in decisions:
                    # Validate supersedes before writing, never after accepting it.
                    for ref in decision.supersedes:
                        event = self.store.get_history_event(ref.target_id) if ref.kind == "event" else None
                        if event is None or event.task_id != session.task_id or event.event_type != HistoryEventType.USER_DECISION:
                            raise ValueError("invalid superseded decision reference")
                    self.writer.append_public_event(identity, "decision:" + decision.decision_id, HistoryEventType.USER_DECISION, decision)
                current_context["effective_decisions"] = [item.model_dump(mode="json") for item in self.history.effective_decisions(identity)]
                if inherited:
                    current_context["handoff"] = self.service.apply_revocations(inherited, session.task_id).model_dump(mode="json")
                graph_input = create_main_state(message, messages=[HumanMessage(content=message)],
                                               routing_context=routing_context, continuity_context=current_context)
            return ContinuityCall(self, identity, session, config, graph_input, live, lease, accounting, operations, graph, blocked, report_body)
        except BaseException:
            await asyncio.to_thread(lease.release)
            raise

    def with_acks(self, identity, live):
        cursor, observations = 0, {item.field: item for item in live.observations}
        while True:
            rows = self.store.query_task_history(identity.continuity_task_id, event_type=HistoryEventType.ACTION_ACK, after_seq=cursor, limit=100)
            if not rows:
                break
            for event in rows:
                ack = PublicActionAck.model_validate(event.payload)
                observations["action_ack:" + ack.action_id] = StateObservation(field="action_ack:" + ack.action_id,
                    value=ack.status, source="client_action_ack", observed_at=event.timestamp, scope=identity.conversation_id)
                cursor = event.event_seq
        return LiveSnapshot(observations=tuple(list(observations.values())[-64:]))

    def recovery_status(self, *, graph_mode, thread_id, user_id):
        conversation = f"handoff:v1:{thread_id}" if graph_mode == "handoff" else thread_id
        self.locks.require_scope(conversation, digest([graph_mode, user_id]))
        task = self.store.active_task(conversation)
        if task is None:
            return {"status": "unavailable", "reason": "recovery_not_observed", "receipt_gaps": []}
        session = self.store.active_session(task.task_id)
        return latest_recovery_status(self.store, task.task_id, session_id=session.session_id)

    def draft(self, session, snapshot, live_body, goal):
        live = LiveSnapshot.model_validate(live_body) if live_body else LiveSnapshot()
        known = {item.field: {"value": item.value, "revision": item.revision} for item in live.observations}
        draft = HandoffDraftV2(goal=(goal or "继续原任务")[:4000], current_stage="checkpoint",
            revalidate_fields=list(known), last_known_state=known)
        from .models import HistoryPointer
        identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
            session_id=session.session_id, request_id="projection", graph_mode="handoff")
        for event, decision in self.history.effective_decision_entries(identity):
            target, refs = (draft.constraints, draft.constraint_refs) if decision.kind == "constraint" else (draft.decisions, draft.decision_refs)
            if decision.text not in target:
                target.append(decision.text)
            refs.setdefault(decision.text, []).append(HistoryPointer(kind="event", target_id=event.event_id))
        if snapshot:
            # Project this run's bounded event window; earlier session chains are
            # available through immutable archive cursors and explicit refs.
            cursor, start, earlier = 0, 0, {}
            while True:
                rows = self.store.query_task_history(session.task_id, session_id=session.session_id,
                    event_type=HistoryEventType.RUN_PROGRESS, after_seq=cursor, limit=100)
                if not rows:
                    break
                for row in rows:
                    if row.payload["run"]["run_id"] == snapshot.run.run_id and not start:
                        start = max(0, row.event_seq - 1)
                    if row.payload["run"]["run_id"] != snapshot.run.run_id:
                        earlier[row.payload["run"]["run_id"]] = HistoryPointer(kind="event", target_id=row.event_id)
                    cursor = row.event_seq
            from .models import AgentProgress
            draft = draft.model_copy(update={"agent_progress": AgentProgress(last_run_id=snapshot.run.run_id,
                earlier_chain_refs=tuple(list(earlier.values())[-32:]))})
            draft = self.projector.project_progress(session.session_id, event_cursor=start, draft=draft)
        return draft

    def after_checkpoint(self, row):
        call = active_call.get()
        if call is None:
            return
        with call.checkpoint_lock:
            call.lease.advance()
            values = row.checkpoint["channel_values"]
            context = values.get("continuity_context", {})
            if not context or context.get("request_id") != call.identity.request_id:
                # Recovery keeps the original request id in the checkpoint.
                if context.get("task_id") != call.identity.continuity_task_id:
                    return
            raw = values.get("handoff_run")
            run_id = raw["run_id"] if raw else values.get("run_id") or context.get("run_id")
            if not run_id:
                return
            identity = call.identity.model_copy(update={"run_id": run_id})
            call.accounting.identity = identity
            self.writer.append_public_event(identity, "checkpoint:" + row.checkpoint["id"], HistoryEventType.CHECKPOINT,
                PublicCheckpoint(checkpoint_id=row.checkpoint["id"], run_id=run_id, fence=call.lease.fence, revision=call.lease.revision))
            route = values.get("route_decision")
            if route:
                route = route if isinstance(route, RouteDecision) else RouteDecision.model_validate(route)
                self.writer.append_public_event(identity, "route:" + run_id, HistoryEventType.ROUTE_DECISION,
                    PublicRoute(run_id=run_id, tasks=tuple((task.task_id, str(task.route_key)) for task in route.tasks),
                                needs_clarification=route.needs_clarification))
            results = HandoffRun.model_validate(raw).results if raw else values.get("task_results", [])
            active_ids = {task.task_id for task in route.tasks} if route else set()
            for result in results:
                result = result if isinstance(result, TaskResult) else TaskResult.model_validate(result)
                if not raw and result.task_id not in active_ids:
                    continue
                self.writer.task_result(identity, PublicTaskResult(run_id=run_id, route_task_id=result.task_id,
                    route_key=str(result.route_key), status=str(result.status), content=(result.content or "")[:1000]))
                from app.schemas.client_actions import ClientAction
                for action in result.data.get("client_actions", []):
                    action = ClientAction.model_validate(action)
                    self.writer.append_public_event(identity, "action:" + action.action_id, HistoryEventType.CLIENT_ACTION,
                        PublicAction(action_id=action.action_id, name=str(action.name), arguments_digest=digest(action.arguments.model_dump(mode="json"))))
                    if call.action_sink is not None:
                        call.action_sink(action)
            final = values.get("final_response")
            if final and (raw is None or HandoffRun.model_validate(raw).terminal):
                self.history.public_text(identity, "final:" + run_id, HistoryEventType.ASSISTANT_MESSAGE, final)

    async def before_agent(self, state, run):
        call = active_call.get()
        if call is None:
            return
        call.lease.check()
        latest_config = {**call.config, "configurable": {key: value for key, value in call.config["configurable"].items() if key != "checkpoint_id"}}
        snapshot = await self.snapshot(call.graph, latest_config, call.identity)
        if snapshot and snapshot.run.hops and snapshot.run.current_agent == snapshot.run.hops[-1].target_agent:
            if not any(result.task_id == snapshot.run.hops[-1].task_id for result in snapshot.run.results):
                self.journal.observe_target(snapshot, phase="target_started")

    def pending_actions(self, identity):
        actions, acknowledgements, cursor = set(), set(), 0
        while True:
            rows = self.store.query_task_history(identity.continuity_task_id, session_id=identity.session_id,
                                                 after_seq=cursor, limit=100)
            if not rows:
                return actions - acknowledgements
            for event in rows:
                if event.event_type == HistoryEventType.CLIENT_ACTION:
                    actions.add(event.payload["action_id"])
                elif event.event_type == HistoryEventType.ACTION_ACK and event.payload["status"] == "applied":
                    acknowledgements.add(event.payload["action_id"])
                cursor = event.event_seq

    async def acknowledge(self, *, graph_mode=None, thread_id, user_id, action_id, status):
        from .action_ack import resolve_action_owner
        from .models import HistoryPointer

        identity, action = await asyncio.to_thread(resolve_action_owner, self.store, self.locks,
            graph_mode=graph_mode, thread_id=thread_id, user_id=user_id, action_id=action_id)
        # Retain the original action's task/session and the Writer's immutable deduplication key.
        event = await asyncio.to_thread(self.writer.append_public_event, identity, "ack:" + action_id,
            HistoryEventType.ACTION_ACK, PublicActionAck(action_id=action_id, status=status,
                action_ref=HistoryPointer(kind="event", target_id=action.event_id)))
        return {"action_id": action_id, "status": event.payload["status"]}


class ContinuityCheckpointer(BaseCheckpointSaver):
    def __init__(self, saver, coordinator):
        super().__init__(serde=saver.serde)
        self.saver, self.coordinator = saver, coordinator

    @property
    def config_specs(self):
        return self.saver.config_specs

    def get_tuple(self, config):
        return self.restore(self.saver.get_tuple(config))

    async def aget_tuple(self, config):
        return self.restore(await self.saver.aget_tuple(config))

    @staticmethod
    def restore(row):
        if row is None or not row.config["configurable"].get("checkpoint_ns"):
            return row
        from .checkpoint_models import decode
        return row._replace(checkpoint={**row.checkpoint, "channel_values": decode(row.checkpoint["channel_values"])},
                            pending_writes=decode(row.pending_writes))

    @staticmethod
    def project(config, checkpoint):
        if not config["configurable"].get("checkpoint_ns"):
            call = active_call.get()
            values = checkpoint["channel_values"]
            context = values.get("continuity_context")
            if call and context:
                return {**checkpoint, "channel_values": {**values, "continuity_context": {**context,
                    "execution_fence": call.lease.fence, "state_revision": call.lease.revision + 1}}}
            return checkpoint
        from .checkpoint_models import encode
        return {**checkpoint, "channel_values": encode(checkpoint["channel_values"])}

    def list(self, config, **kwargs):
        for row in self.saver.list(config, **kwargs):
            yield self.restore(row)

    async def alist(self, config, **kwargs):
        async for row in self.saver.alist(config, **kwargs):
            yield self.restore(row)

    @staticmethod
    def check():
        call = active_call.get()
        if call is not None:
            call.lease.check()

    def put(self, config, checkpoint, metadata, new_versions):
        self.check()
        stored = self.saver.put(config, self.project(config, checkpoint), metadata, new_versions)
        if not config["configurable"].get("checkpoint_ns"):
            self.coordinator.after_checkpoint(self.saver.get_tuple(stored))
        return stored

    async def aput(self, config, checkpoint, metadata, new_versions):
        self.check()
        stored = await self.saver.aput(config, self.project(config, checkpoint), metadata, new_versions)
        if config["configurable"].get("checkpoint_ns"):
            return stored
        self.coordinator.after_checkpoint(await self.saver.aget_tuple(stored))
        call = active_call.get()
        if call:
            snapshot = await self.coordinator.snapshot(call.graph, stored, call.identity)
            if snapshot and snapshot.run.hops and snapshot.run.current_agent == snapshot.run.hops[-1].target_agent:
                if any(result.task_id == snapshot.run.hops[-1].task_id for result in snapshot.run.results):
                    self.coordinator.journal.observe_target(snapshot, phase="target_finished")
        return stored

    def put_writes(self, config, writes, task_id, task_path=""):
        self.check()
        if config["configurable"].get("checkpoint_ns"):
            from .checkpoint_models import encode
            writes = encode(list(writes))
        return self.saver.put_writes(config, writes, task_id, task_path)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        self.check()
        if config["configurable"].get("checkpoint_ns"):
            from .checkpoint_models import encode
            writes = encode(list(writes))
        return await self.saver.aput_writes(config, writes, task_id, task_path)


@lru_cache
def get_continuity_coordinator():
    return ContinuityCoordinator(get_long_horizon_service())

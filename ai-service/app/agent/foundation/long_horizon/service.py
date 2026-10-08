from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agent.foundation.context_engineering import ContextPack, build_context
from app.agent.foundation.routing import RouteKey
from app.agent.session_cache import read_session_projection
from app.rag.chunk.token_counter import TokenCounter, UnicodeHeuristicTokenCounter

from .models import (
    ArtifactRecord,
    ColdStartContext,
    HandoffDraft,
    HandoffDraftV2,
    HandoffProjection,
    HandoffProjectionV2,
    HistoryEvent,
    HistoryEventType,
    HistoryPointer,
    RolloverReason,
    RolloverResult,
    SessionRecord,
    TaskRecord,
)
from .store import ContinuityStore


class RolloverPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_context_threshold: int = Field(default=5200, gt=0)
    handoff_token_budget: int = Field(default=1200, gt=0)
    rollover_on_artifact: bool = False


def _unique(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        key = json.dumps(
            value.model_dump(mode="json") if isinstance(value, BaseModel) else value,
            ensure_ascii=False, sort_keys=True, default=str,
        )
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


class LongHorizonContextService:
    def __init__(self, store: ContinuityStore, *, policy: RolloverPolicy | None = None,
                 counter: TokenCounter | None = None,
                 now: Callable[[], datetime] | None = None):
        self.store = store
        self.policy = policy or RolloverPolicy()
        self.counter = counter or UnicodeHeuristicTokenCounter()
        self._now = now or (lambda: datetime.now(UTC))

    def start_task(
        self,
        conversation_id: str,
        *,
        task_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> SessionRecord:
        resolved_task_id = task_id or f"task:{uuid4().hex}"
        if self.store.get_task(resolved_task_id) is not None:
            raise ValueError(f"task already exists:{resolved_task_id}")
        self.store.create_task(
            TaskRecord(
                task_id=resolved_task_id,
                conversation_id=conversation_id,
                created_at=self._now(),
            )
        )
        return self.open_task(
            resolved_task_id,
            conversation_id=conversation_id,
            parent_session_id=parent_session_id,
        )

    def open_conversation(self, conversation_id: str) -> SessionRecord:
        task = self.store.active_task(conversation_id)
        if task is None:
            return self.start_task(conversation_id)
        return self.open_task(task.task_id, conversation_id=conversation_id)

    def open_task(
        self,
        task_id: str,
        *,
        conversation_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> SessionRecord:
        task = self.store.get_task(task_id)
        if task is None:
            resolved_conversation_id = conversation_id or f"conversation:{uuid4().hex}"
            task = TaskRecord(
                task_id=task_id,
                conversation_id=resolved_conversation_id,
                created_at=self._now(),
            )
            self.store.create_task(task)
        elif conversation_id is not None and task.conversation_id != conversation_id:
            raise ValueError("task belongs to a different conversation")
        active = self.store.active_session(task_id)
        if active is not None:
            return active
        session = SessionRecord(
            task_id=task_id,
            conversation_id=task.conversation_id,
            session_id=f"session:{uuid4().hex}",
            parent_session_id=parent_session_id, created_at=self._now(),
        )
        self.store.create_session(session)
        return session

    def get_history_event(self, event_id: str) -> HistoryEvent | None:
        return self.store.get_history_event(event_id)

    def query_task_history(self, task_id: str, *, event_type: HistoryEventType | None = None,
                           session_id: str | None = None, keyword: str | None = None,
                           limit: int = 100) -> list[HistoryEvent]:
        return self.store.query_task_history(
            task_id, event_type=event_type.value if event_type else None,
            session_id=session_id, keyword=keyword, limit=limit,
        )

    def get_artifact(self, ref: str) -> ArtifactRecord | None:
        return self.store.get_artifact(ref)

    def record_event(self, *, task_id: str, session_id: str, event_type: HistoryEventType,
                     node: str, source: str, payload: dict[str, Any] | None = None,
                     storage_ref: str | None = None, references: list[HistoryPointer] | None = None,
                     metadata: dict[str, Any] | None = None, request_id: str | None = None) -> HistoryEvent:
        event = HistoryEvent(
            event_id=f"evt:{uuid4().hex}", task_id=task_id, session_id=session_id,
            event_type=event_type, timestamp=self._now(), node=node, source=source,
            payload=payload, storage_ref=storage_ref, references=references or [],
            metadata=metadata or {}, request_id=request_id,
        )
        self.store.append_event(event)
        return event

    def create_artifact(self, *, task_id: str, session_id: str, artifact_type: str,
                        payload: Any, projection: dict[str, Any],
                        metadata: dict[str, Any] | None = None) -> ArtifactRecord:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
        artifact = ArtifactRecord(
            artifact_id=f"artifact-{uuid4().hex}", task_id=task_id, session_id=session_id,
            artifact_type=artifact_type, created_at=self._now(),
            content_hash="sha256:" + hashlib.sha256(serialized).hexdigest(),
            payload=payload, projection=projection, metadata=metadata or {},
        )
        self.store.put_artifact(artifact)
        self.record_event(
            task_id=task_id, session_id=session_id,
            event_type=HistoryEventType.ARTIFACT_CREATED,
            node="artifact_store", source=artifact_type,
            storage_ref=artifact.ref,
            references=[HistoryPointer(kind="artifact", target_id=artifact.ref)],
            metadata={"projection": projection, "content_hash": artifact.content_hash},
        )
        return artifact

    def should_rollover(self, *, active_context_tokens: int,
                        explicit_checkpoint: bool = False,
                        artifact_created: bool = False) -> RolloverReason | None:
        if explicit_checkpoint:
            return RolloverReason.EXPLICIT_CHECKPOINT
        if active_context_tokens >= self.policy.active_context_threshold:
            return RolloverReason.TOKEN_THRESHOLD
        if artifact_created and self.policy.rollover_on_artifact:
            return RolloverReason.ARTIFACT_CREATED
        return None

    def _inherit(self, session: SessionRecord, draft: HandoffDraft) -> HandoffDraft:
        if not session.inherited_handoff_id:
            return self.apply_revocations(draft, session.task_id)
        prior = self.store.get_handoff(session.inherited_handoff_id)
        if prior is None:
            raise ValueError("session inherited handoff cannot be resolved")
        return self.apply_revocations(draft.model_copy(update={
            "constraints": _unique([*prior.constraints, *draft.constraints]),
            "constraint_refs": {**prior.constraint_refs, **draft.constraint_refs},
            "preferences": _unique([*prior.preferences, *draft.preferences]),
            "decisions": _unique([*prior.decisions, *draft.decisions]),
            "decision_refs": {**prior.decision_refs, **draft.decision_refs},
            "important_entities": _unique([*prior.important_entities, *draft.important_entities]),
            "key_message_refs": _unique([*prior.key_message_refs, *draft.key_message_refs]),
            "artifact_refs": _unique([*prior.artifact_refs, *draft.artifact_refs]),
            "evidence_refs": _unique([*prior.evidence_refs, *draft.evidence_refs]),
            "revalidate_fields": _unique([*prior.revalidate_fields, *draft.revalidate_fields]),
            "last_known_state": {**prior.last_known_state, **draft.last_known_state},
        }), session.task_id)

    def apply_revocations(self, projection, task_id):
        from .public_events import PublicDecision
        revoked, cursor = set(), 0
        while True:
            rows = self.store.query_task_history(task_id, event_type=HistoryEventType.USER_DECISION,
                after_seq=cursor, limit=100)
            if not rows:
                break
            for row in rows:
                body = PublicDecision.model_validate(row.payload)
                revoked.update(ref.target_id for ref in body.supersedes)
                cursor = row.event_seq
        updates = {}
        for field, ref_field in (("constraints", "constraint_refs"), ("decisions", "decision_refs")):
            refs = getattr(projection, ref_field)
            removed = {text for text, pointers in refs.items() if pointers and all(ref.target_id in revoked for ref in pointers)}
            updates[field] = [text for text in getattr(projection, field) if text not in removed]
            updates[ref_field] = {text: [ref for ref in pointers if ref.target_id not in revoked]
                for text, pointers in refs.items() if text not in removed}
        return projection.model_copy(update=updates)

    def build_handoff(self, session_id: str, draft: HandoffDraft | HandoffDraftV2) -> HandoffProjection | HandoffProjectionV2:
        session = self.store.require_session(session_id)
        if session.closed_at is not None:
            raise ValueError("cannot hand off a closed session")
        draft = self._inherit(session, draft)
        linked = [pointer for values in [*draft.constraint_refs.values(),
                                         *draft.decision_refs.values()] for pointer in values]
        self.store.validate_pointers([*draft.key_message_refs, *draft.evidence_refs, *linked],
                                     task_id=session.task_id)
        if isinstance(draft, HandoffDraftV2):
            progress = draft.agent_progress
            extra_refs = [*progress.progress_refs, *progress.earlier_chain_refs]
            extra_refs.extend(ref for ref in (progress.pending_transfer_ref, progress.run_state_ref) if ref)
            self.store.validate_pointers(extra_refs, task_id=session.task_id)
            if draft.source_checkpoint_ref:
                source = self.store.require_session(draft.source_checkpoint_ref.session_id)
                if (source.task_id, source.conversation_id) != (session.task_id, session.conversation_id):
                    raise ValueError("projection source checkpoint scope mismatch")
                if (draft.source_checkpoint_ref.execution_thread_id, draft.source_checkpoint_ref.checkpoint_ns) != (source.conversation_id, source.session_id):
                    raise ValueError("projection source checkpoint namespace mismatch")
        for ref in draft.artifact_refs:
            artifact = self.store.get_artifact(ref)
            if artifact is None or artifact.task_id != session.task_id:
                raise ValueError(f"unresolvable artifact pointer:{ref}")
        values = draft.model_dump(mode="json")
        dropped: list[str] = []
        envelope = dict(
            handoff_id=f"handoff:{uuid4().hex}", task_id=session.task_id,
            session_id=session.session_id, created_at=self._now(),
            token_count=0, token_budget=self.policy.handoff_token_budget,
            dropped_fields=dropped,
        )
        projection_type = HandoffProjectionV2 if isinstance(draft, HandoffDraftV2) else HandoffProjection

        def tokens() -> int:
            if projection_type is HandoffProjectionV2:
                candidate = HandoffProjectionV2(**values, **envelope)
                return self.counter.count(candidate.model_dump_json(exclude_none=True))
            return self.counter.count(json.dumps(values, ensure_ascii=False, separators=(",", ":")))

        # Handoff remains structured. Trim low-value projections before required state.
        for field in ("summary", "key_message_refs", "completed_steps", "important_entities", "preferences"):
            while tokens() > self.policy.handoff_token_budget and values[field]:
                if field == "summary":
                    values[field] = values[field][: max(0, len(values[field]) // 2)]
                    if len(values[field]) < 32:
                        values[field] = ""
                else:
                    values[field].pop()
                if field not in dropped:
                    dropped.append(field)
        if tokens() > self.policy.handoff_token_budget:
            raise ValueError("required handoff fields exceed token budget")
        envelope["token_count"] = tokens()
        if projection_type is HandoffProjectionV2:
            # Count the actual complete envelope, including the final counter value.
            for _ in range(4):
                count = tokens()
                if count == envelope["token_count"]:
                    break
                envelope["token_count"] = count
            if tokens() > self.policy.handoff_token_budget:
                raise ValueError("required handoff fields exceed token budget")
        return projection_type(**values, **envelope)

    def rollover(self, session_id: str, draft: HandoffDraft,
                 reason: RolloverReason, *, automatic: bool = False, new_session_id=None, writer=None, identity=None) -> RolloverResult:
        if automatic and not getattr(self.store, "supports_atomic_rollover", False):
            raise ValueError("automatic rollover requires an atomic persistence backend")
        with self.store.transaction():
            session = self.store.require_session(session_id)
            handoff = self.build_handoff(session_id, draft)
            self.store.put_handoff(handoff)
            target_id = new_session_id or f"session:{uuid4().hex}"
            if writer is None:
                checkpoint = self.record_event(task_id=session.task_id, session_id=session_id,
                    event_type=HistoryEventType.CHECKPOINT, node="rollover", source=reason.value,
                    payload={"reason": reason.value, "handoff_id": handoff.handoff_id})
                self.record_event(task_id=session.task_id, session_id=session_id,
                    event_type=HistoryEventType.SESSION_HANDOFF, node="rollover", source="handoff_projection",
                    payload={"handoff_id": handoff.handoff_id, "token_count": handoff.token_count},
                    references=[HistoryPointer(kind="event", target_id=checkpoint.event_id)])
            else:
                from .public_events import PublicSessionHandoff
                writer.append_public_event(identity, "rollover:" + target_id, HistoryEventType.SESSION_HANDOFF,
                    PublicSessionHandoff(handoff_id=handoff.handoff_id, target_session=target_id, reason=reason.value))
            closed = self.store.close_session(session_id, self._now().isoformat())
            new_session = SessionRecord(
                task_id=session.task_id,
                conversation_id=session.conversation_id,
                session_id=target_id,
                previous_session_id=session.session_id,
                parent_session_id=session.parent_session_id or session.session_id,
                inherited_handoff_id=handoff.handoff_id, created_at=self._now(),
            )
            self.store.create_session(new_session)
        return RolloverResult(
            closed_session=closed, handoff=handoff,
            new_session=new_session, reason=reason,
        )

    def cold_start(self, session_id: str, *, live_state: dict[str, Any]) -> ColdStartContext:
        session = self.store.require_session(session_id)
        handoff = (read_session_projection(self.store, session_id, session.inherited_handoff_id)
                   if session.inherited_handoff_id else None)
        if handoff is None:
            return ColdStartContext(task_id=session.task_id, session_id=session_id,
                                    revalidated_state=live_state)
        changed = [field for field in handoff.revalidate_fields
                   if handoff.last_known_state.get(field) != live_state.get(field)]
        linked_pointers = _unique([
            *handoff.key_message_refs, *handoff.evidence_refs,
            *(pointer for values in handoff.constraint_refs.values() for pointer in values),
            *(pointer for values in handoff.decision_refs.values() for pointer in values),
        ])
        key_events = [event for pointer in linked_pointers
                      if pointer.kind == "event"
                      if (event := self.store.get_history_event(pointer.target_id)) is not None]
        artifacts = [artifact for ref in handoff.artifact_refs
                     if (artifact := self.store.get_artifact(ref)) is not None]
        self.record_event(
            task_id=session.task_id, session_id=session_id,
            event_type=HistoryEventType.REALITY_REVALIDATION,
            node="cold_start", source="live_state",
            payload={"historical": handoff.last_known_state,
                     "current": live_state, "changed_fields": changed},
            references=handoff.evidence_refs,
        )
        return ColdStartContext(
            task_id=session.task_id, session_id=session_id, handoff=handoff,
            key_events=key_events, artifacts=artifacts,
            revalidated_state=live_state, historical_state=handoff.last_known_state,
            changed_fields=changed,
        )

    def drill_down(self, *, task_id: str, pointers: list[HistoryPointer] | None = None,
                   event_type: HistoryEventType | None = None,
                   session_id: str | None = None, keyword: str | None = None,
                   limit: int = 20) -> list[HistoryEvent | ArtifactRecord]:
        if pointers:
            self.store.validate_pointers(pointers, task_id=task_id)
            return [target for pointer in pointers if (target := (
                self.store.get_history_event(pointer.target_id) if pointer.kind == "event"
                else self.store.get_artifact(pointer.target_id))) is not None]
        return self.store.query_task_history(
            task_id, event_type=event_type.value if event_type else None,
            session_id=session_id, keyword=keyword, limit=limit,
        )

    def build_active_context(self, node: RouteKey, *, session_id: str, query: str,
                             system: str, live_state: dict[str, Any],
                             drilldown: list[HistoryEvent | ArtifactRecord] | None = None,
                             recent_messages: list[Any] | None = None, **sources: Any) -> ContextPack:
        cold = self.cold_start(session_id, live_state=live_state)
        history_rows = [item.model_dump(mode="json", exclude_none=True)
                        for item in (drilldown or []) if isinstance(item, HistoryEvent)]
        artifact_rows = [
            {"ref": item.ref, "artifact_type": item.artifact_type, "projection": item.projection}
            for item in [*cold.artifacts, *(drilldown or [])]
            if isinstance(item, ArtifactRecord)
        ]
        pack = build_context(
            node, query=query, system=system, messages=recent_messages,
            trace_context={"task_id": cold.task_id, "session_id": session_id},
            business_state={"live": cold.revalidated_state,
                            "historical": cold.historical_state,
                            "changed_fields": cold.changed_fields},
            handoff_projection=(cold.handoff.model_dump(mode="json", exclude_none=True)
                                if cold.handoff else None),
            history_drilldown=history_rows,
            artifact_projections=artifact_rows,
            **sources,
        )
        self.record_event(
            task_id=cold.task_id, session_id=session_id,
            event_type=HistoryEventType.CONTEXT_TRACE,
            node=node.value, source="phase1_context_engineering",
            payload={"total_tokens": pack.total_tokens, "budget": pack.budget,
                     "trace": pack.trace.model_dump(mode="json")},
        )
        return pack

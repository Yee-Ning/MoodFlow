"""Typed, bounded, replay-safe writes to the append-only public history."""

import hashlib
import json
import re
from datetime import UTC, datetime

from pydantic import BaseModel

from .models import (
    ContinuityIdentity,
    HistoryEvent,
    HistoryEventType,
    HistoryPointer,
    PublicHistoryError,
    PublicTaskResult,
    RunProgressRecord,
    TransferJournalEvent,
)
from .store import ContinuityStore
from .public_events import (
    PublicText, PublicDecision, PublicRoute, PublicTool, PublicActionAck,
    PublicRevalidation, PublicUsage, PublicCheckpoint, PublicContextTrace,
    PublicArchive, PublicMigration, PublicAction, PublicSessionHandoff,
)

PUBLIC_SCHEMAS = {
    HistoryEventType.AGENT_TRANSFER: TransferJournalEvent,
    HistoryEventType.RUN_PROGRESS: RunProgressRecord,
    HistoryEventType.STRUCTURED_OUTPUT: PublicTaskResult,
    HistoryEventType.ERROR: PublicHistoryError,
    HistoryEventType.USER_MESSAGE: PublicText,
    HistoryEventType.ASSISTANT_MESSAGE: PublicText,
    HistoryEventType.USER_DECISION: PublicDecision,
    HistoryEventType.ROUTE_DECISION: PublicRoute,
    HistoryEventType.TOOL_CALL: PublicTool,
    HistoryEventType.TOOL_RESULT: PublicTool,
    HistoryEventType.ACTION_ACK: PublicActionAck,
    HistoryEventType.CLIENT_ACTION: PublicAction,
    HistoryEventType.REALITY_REVALIDATION: PublicRevalidation,
    HistoryEventType.MODEL_USAGE: PublicUsage,
    HistoryEventType.CHECKPOINT: PublicCheckpoint,
    HistoryEventType.CONTEXT_TRACE: PublicContextTrace,
    HistoryEventType.HISTORY_ARCHIVE: PublicArchive,
    HistoryEventType.NAMESPACE_MIGRATION: PublicMigration,
    HistoryEventType.SESSION_HANDOFF: PublicSessionHandoff,
}
PRIVATE_TEXT = re.compile(
    r"scratchpad|<\s*(?:analysis|reasoning)\b|(?:raw|system)[_ ]?prompt|"
    r"-----BEGIN[^\n]*PRIVATE KEY|\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{12,}|"
    r"\b(?:api[_ -]?key|password|token|cookie)[\"']?\s*[:=]",
    re.IGNORECASE,
)


def digest(value) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()


def _check_public_text(value) -> None:
    if isinstance(value, str) and PRIVATE_TEXT.search(value):
        raise ValueError("private or credential-like content is not public history")
    if isinstance(value, dict):
        for item in value.values():
            _check_public_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_public_text(item)


class HistoryWriter:
    def __init__(self, store: ContinuityStore, *, max_bytes=16_384, now=None):
        self.store = store
        self.max_bytes = max_bytes
        self.now = now or (lambda: datetime.now(UTC))

    def require_identity(self, identity: ContinuityIdentity) -> None:
        session = self.store.require_session(identity.session_id)
        if (session.task_id, session.conversation_id) != (
            identity.continuity_task_id, identity.conversation_id
        ):
            raise ValueError("history identity scope mismatch")

    @staticmethod
    def event_id(identity, event_key):
        return "evt:" + digest([identity.continuity_task_id, identity.session_id, event_key])[7:]

    def append_public_event(
        self, identity: ContinuityIdentity, event_key: str,
        event_kind: HistoryEventType, body: BaseModel | dict,
        *, refs: tuple[HistoryPointer, ...] = (),
    ) -> HistoryEvent:
        self.require_identity(identity)
        if not event_key or len(event_key) > 160:
            raise ValueError("invalid public event key")
        schema = PUBLIC_SCHEMAS.get(event_kind)
        if schema is None:
            raise ValueError("public event type has no allowlisted schema")
        values = body.model_dump(mode="json") if isinstance(body, BaseModel) else body
        payload = schema.model_validate(values).model_dump(mode="json", exclude_none=True)
        body_run_id = payload.get("run_id") or payload.get("summary", {}).get("run_id") or payload.get("run", {}).get("run_id")
        if identity.run_id is not None and body_run_id is not None and identity.run_id != body_run_id:
            raise ValueError("public history run identity mismatch")
        _check_public_text(payload)
        _check_public_text(identity.model_dump(mode="json"))
        if len(json.dumps(payload, ensure_ascii=False).encode()) > self.max_bytes:
            raise ValueError("public event exceeds byte budget")
        if len(refs) > 64:
            raise ValueError("public event reference budget exceeded")
        # Nested references must receive the same task ownership checks as top-level refs.
        all_refs = list(refs)
        if isinstance(body, BaseModel):
            values = body.model_dump(mode="json")
        else:
            values = payload

        def collect(value):
            if isinstance(value, dict):
                if set(value) == {"kind", "target_id"}:
                    all_refs.append(HistoryPointer.model_validate(value))
                else:
                    for item in value.values():
                        collect(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    collect(item)

        collect(values)
        all_refs = list({(ref.kind, ref.target_id): ref for ref in all_refs}.values())
        if len(all_refs) > 64:
            raise ValueError("public event reference budget exceeded")
        _check_public_text([ref.model_dump(mode="json") for ref in all_refs])
        self.store.validate_pointers(all_refs, task_id=identity.continuity_task_id)
        key = "public:" + digest([identity.continuity_task_id, identity.session_id, event_key])[7:]
        event = HistoryEvent(
            event_id="evt:" + key[7:], event_key=key,
            task_id=identity.continuity_task_id, session_id=identity.session_id,
            request_id=identity.request_id, event_type=event_kind,
            timestamp=self.now(), node="public_history", source="typed_writer:v1",
            payload=payload, references=all_refs,
        )
        if len(event.model_dump_json().encode()) > self.max_bytes:
            raise ValueError("public event envelope exceeds byte budget")
        try:
            return self.store.append_event_once(event)
        except ValueError as exc:
            if str(exc) == "history event key conflict" and event_kind is not HistoryEventType.ERROR:
                self.append_public_event(
                    identity, "conflict:" + key[7:], HistoryEventType.ERROR,
                    PublicHistoryError(code="idempotency_conflict", related_key_digest=digest(key)),
                )
            raise

    def transfer(self, identity, event_key, body: TransferJournalEvent) -> HistoryEvent:
        return self.append_public_event(identity, event_key, HistoryEventType.AGENT_TRANSFER, body)

    def task_result(self, identity, result: PublicTaskResult) -> HistoryEvent:
        return self.append_public_event(
            identity, f"result:{result.run_id}:{result.route_task_id}",
            HistoryEventType.STRUCTURED_OUTPUT, result,
        )

    def run_progress(self, identity, body: RunProgressRecord) -> HistoryEvent:
        return self.append_public_event(
            identity, "progress:" + digest([body.run.run_id, body.checkpoint_ref.checkpoint_id])[7:],
            HistoryEventType.RUN_PROGRESS, body,
        )

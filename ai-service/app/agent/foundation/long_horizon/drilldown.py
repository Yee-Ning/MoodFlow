"""Bounded lazy history reads and incremental, append-only archival indexes."""

import json
from datetime import UTC, datetime

from pydantic import Field

from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter

from .history_writer import _check_public_text, digest
from .models import ArtifactRecord, ContinuityContract, HistoryEventType, HistoryPointer
from .public_events import PublicArchive, PublicDecision, PublicText, SafeTextArtifact
from .query_drilldown import QuerySelection, is_query_cursor


class DrilldownPack(ContinuityContract):
    items: tuple[dict, ...] = ()
    next_cursor: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    bytes: int = Field(default=0, ge=0)
    truncated: bool = False
    missing_refs: tuple[HistoryPointer, ...] = ()


class HistoryDrilldown:
    def __init__(self, writer):
        self.writer, self.store = writer, writer.store
        self.counter = UnicodeHeuristicTokenCounter()

    def read(self, identity, *, pointers=(), cursor=0, count=20, tokens=800, bytes_limit=8192, expand_artifacts=False, query=None):
        self.writer.require_identity(identity)
        if not (0 < count <= 20 and 0 < tokens <= 800 and 0 < bytes_limit <= 8192 and cursor >= 0):
            raise ValueError("drilldown budget exceeds policy")
        if query is not None and not isinstance(query, str):
            raise ValueError("drilldown query must be text")
        if query and query.strip():
            selection = QuerySelection(self, identity, query, cursor, pointers=pointers, expand_artifacts=expand_artifacts)
            if len(selection.pointers) > count:
                raise ValueError("drilldown pointer budget exceeded")
            return self.read_query(selection, count=count, tokens=tokens, bytes_limit=bytes_limit,
                                   expand_artifacts=expand_artifacts)
        if is_query_cursor(cursor):
            raise ValueError("query drilldown cursor requires a nonblank query")
        if len(pointers) > count:
            raise ValueError("drilldown pointer budget exceeded")
        # Explicit pointers and cursors share every budget, including the envelope.
        rows = pointers or self.store.query_task_history(identity.continuity_task_id, after_seq=cursor, limit=count + 1)
        items, missing, next_cursor, truncated = [], [], cursor, False
        for row in rows:
            pointer = row if isinstance(row, HistoryPointer) else HistoryPointer(kind="event", target_id=row.event_id)
            record, payload = self.resolve_public(identity, pointer, explicit=bool(pointers),
                                                   expand_artifacts=expand_artifacts)
            if record is None:
                missing.append(pointer)
                continue
            candidate = {"ref": pointer.model_dump(), "projection": payload}
            trial = DrilldownPack(items=tuple([*items, candidate]), next_cursor=getattr(record, "event_seq", None) or next_cursor)
            serialized = trial.model_dump_json()
            # Reserve room for the counters/truncated/missing fields themselves.
            if len(items) >= count or self.counter.count(serialized) + 80 > tokens or len(serialized.encode()) + 256 > bytes_limit:
                truncated = True
                break
            items.append(candidate)
            next_cursor = getattr(record, "event_seq", None) or next_cursor
        result = DrilldownPack(items=tuple(items), next_cursor=next_cursor, truncated=truncated, missing_refs=tuple(missing))
        for _ in range(3):
            serialized = result.model_dump_json()
            result = result.model_copy(update={"tokens": self.counter.count(serialized), "bytes": len(serialized.encode())})
        if result.tokens > tokens or result.bytes > bytes_limit:
            raise ValueError("drilldown envelope exceeds budget")
        return result

    def resolve_public(self, identity, pointer, *, explicit, expand_artifacts):
        """Shared original public reader; selection cannot bypass scope/schema checks."""
        record = (self.store.get_history_event(pointer.target_id) if pointer.kind == "event"
                  else self.store.get_artifact(pointer.target_id))
        if record is None:
            return None, None
        if record.task_id != identity.continuity_task_id:
            raise ValueError("drilldown reference belongs to another task")
        session = self.store.require_session(record.session_id)
        if session.conversation_id != identity.conversation_id:
            raise ValueError("drilldown conversation scope mismatch")
        if pointer.kind == "artifact":
            if record.artifact_type not in {"public_text:v1", "history_archive:v1", "public_tool:v1"}:
                raise ValueError("artifact lacks a safe public schema")
            if record.artifact_type == "public_tool:v1":
                from .operations import safe_tool_result
                safe = safe_tool_result(record.payload["name"], json.dumps(record.payload["result"], ensure_ascii=False))
            else:
                schema = SafeTextArtifact if record.artifact_type == "public_text:v1" else PublicArchive
                safe = schema.model_validate(record.payload).model_dump(mode="json")
            if digest(record.payload) != record.content_hash:
                raise ValueError("artifact content hash changed")
            payload = safe if expand_artifacts else record.projection
        elif record.source != "typed_writer:v1":
            if explicit:
                raise ValueError("legacy untyped event requires explicit safe migration")
            if record.event_type in {HistoryEventType.USER_MESSAGE, HistoryEventType.ASSISTANT_MESSAGE} and set(record.payload or {}) == {"text"}:
                payload = PublicText.model_validate(record.payload).model_dump(mode="json")
            else:
                payload = {"legacy_status": "requires_public_migration", "event_type": record.semantic_type.value}
        else:
            payload = record.payload
        _check_public_text(payload)
        return record, payload

    def read_query(self, selection, *, count, tokens, bytes_limit, expand_artifacts):
        candidates = selection.ranked(expand_artifacts)
        items, position = [], selection.position

        def fits(values, offset):
            trial = DrilldownPack(items=tuple(values), next_cursor=selection.cursor(offset))
            serialized = trial.model_dump_json()
            return self.counter.count(serialized) + 80 <= tokens and len(serialized.encode()) + 256 <= bytes_limit

        if not fits([], position):
            raise ValueError("query drilldown envelope exceeds budget")
        while position < len(candidates) and len(items) < count:
            candidate = candidates[position]
            if not fits([candidate], position + 1):
                # This candidate cannot fit even on an empty page. Consume its
                # rank so it cannot trap every following read on an empty page.
                position += 1
                continue
            if not fits([*items, candidate], position + 1):
                break  # Fits alone: leave it for the next page, never lose it.
            items.append(candidate)
            position += 1
        result = DrilldownPack(items=tuple(items), next_cursor=selection.cursor(position),
                              truncated=position < len(candidates))
        for _ in range(3):
            serialized = result.model_dump_json()
            result = result.model_copy(update={"tokens": self.counter.count(serialized), "bytes": len(serialized.encode())})
        if result.tokens > tokens or result.bytes > bytes_limit:
            raise ValueError("drilldown envelope exceeds budget")
        return result

    def archive(self, identity, *, cursor=None, count=40):
        self.writer.require_identity(identity)
        if not 0 < count <= 40:
            raise ValueError("archive count exceeds policy")
        if cursor is None:
            cursor, scanned = 0, 0
            while True:
                rows = self.store.query_task_history(identity.continuity_task_id,
                    event_type=HistoryEventType.HISTORY_ARCHIVE, after_seq=scanned, limit=100)
                if not rows:
                    break
                for row in rows:
                    cursor = max(cursor, PublicArchive.model_validate(row.payload).last_cursor)
                    scanned = row.event_seq
        events = self.store.query_task_history(identity.continuity_task_id, after_seq=cursor, limit=count)
        # Archive is an immutable index. Original events remain the authority.
        events = [event for event in events if event.event_seq and event.event_type is not HistoryEventType.HISTORY_ARCHIVE]
        if not events:
            return cursor
        body = PublicArchive(first_cursor=cursor, last_cursor=events[-1].event_seq, count=len(events),
                             event_refs=tuple(HistoryPointer(kind="event", target_id=event.event_id) for event in events))
        self.writer.append_public_event(identity, f"archive:{cursor}:{body.last_cursor}", HistoryEventType.HISTORY_ARCHIVE, body)
        return body.last_cursor

    def public_text(self, identity, key, kind, text):
        _check_public_text(text)
        if len(text.encode()) <= 8000:
            return self.writer.append_public_event(identity, key, kind, PublicText(text=text))
        body = SafeTextArtifact(text=text).model_dump(mode="json")
        if len(json.dumps(body, ensure_ascii=False).encode()) > 256000:
            raise ValueError("public artifact byte budget exceeded")
        artifact_id = digest([identity.continuity_task_id, identity.session_id, key, body])[7:]
        ref = "artifact:" + artifact_id
        artifact = self.store.get_artifact(ref)
        if artifact is None:
            artifact = ArtifactRecord(artifact_id=artifact_id, task_id=identity.continuity_task_id,
                                      session_id=identity.session_id, artifact_type="public_text:v1",
                                      created_at=datetime.now(UTC), content_hash=digest(body), payload=body,
                                      projection={"preview": text[:500], "characters": len(text)}, metadata={})
            self.store.put_artifact(artifact)
        return self.writer.append_public_event(identity, key, kind, PublicText(artifact_ref=HistoryPointer(kind="artifact", target_id=ref)))

    def effective_decisions(self, identity):
        return tuple(body for _, body in self.effective_decision_entries(identity))

    def effective_decision_entries(self, identity):
        active, cursor = {}, 0
        while True:
            rows = self.store.query_task_history(identity.continuity_task_id, event_type=HistoryEventType.USER_DECISION,
                                                 after_seq=cursor, limit=100)
            if not rows:
                return tuple(active.values())
            for event in rows:
                body = PublicDecision.model_validate(event.payload)
                for ref in body.supersedes:
                    superseded = self.store.get_history_event(ref.target_id) if ref.kind == "event" else None
                    if superseded is None or superseded.task_id != identity.continuity_task_id or superseded.event_type != HistoryEventType.USER_DECISION:
                        raise ValueError("decision supersedes must reference the same task's decision")
                    active.pop(ref.target_id, None)
                if body.effect != "revoke":
                    active[event.event_id] = (event, body)
                cursor = event.event_seq

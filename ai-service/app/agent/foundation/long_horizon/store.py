from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .models import ArtifactRecord, HandoffProjection, HistoryEvent, HistoryPointer, SessionRecord


class SQLiteContinuityStore:
    """Small durable store; history, artifacts and handoffs are append-only."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if str(self.path) == ":memory:":
            if self._connection is None:
                self._connection = sqlite3.connect(":memory:", check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
                self._connection.execute("PRAGMA foreign_keys=ON")
            return self._connection
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    previous_session_id TEXT, parent_session_id TEXT,
                    inherited_handoff_id TEXT, created_at TEXT NOT NULL, closed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS sessions_task ON sessions(task_id, created_at);
                CREATE TABLE IF NOT EXISTS history_events (
                    event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL, timestamp TEXT NOT NULL, node TEXT NOT NULL,
                    source TEXT NOT NULL, body TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS history_task ON history_events(task_id, timestamp);
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, body TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );
                CREATE TABLE IF NOT EXISTS handoffs (
                    handoff_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, body TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                );
                CREATE TRIGGER IF NOT EXISTS immutable_history_update BEFORE UPDATE ON history_events
                BEGIN SELECT RAISE(ABORT, 'history is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_history_delete BEFORE DELETE ON history_events
                BEGIN SELECT RAISE(ABORT, 'history is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_artifact_update BEFORE UPDATE ON artifacts
                BEGIN SELECT RAISE(ABORT, 'artifacts are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_artifact_delete BEFORE DELETE ON artifacts
                BEGIN SELECT RAISE(ABORT, 'artifacts are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_handoff_update BEFORE UPDATE ON handoffs
                BEGIN SELECT RAISE(ABORT, 'handoffs are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_handoff_delete BEFORE DELETE ON handoffs
                BEGIN SELECT RAISE(ABORT, 'handoffs are immutable'); END;
            """)
            connection.commit()
        finally:
            if self._connection is None:
                connection.close()

    @staticmethod
    def _json(model) -> str:
        return model.model_dump_json(exclude_none=True)

    def create_session(self, session: SessionRecord) -> None:
        if session.previous_session_id and not self.get_session(session.previous_session_id):
            raise ValueError("previous session does not exist")
        if session.parent_session_id and not self.get_session(session.parent_session_id):
            raise ValueError("parent session does not exist")
        if session.inherited_handoff_id and not self.get_handoff(session.inherited_handoff_id):
            raise ValueError("inherited handoff does not exist")
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?,?)",
                (session.session_id, session.task_id, session.previous_session_id,
                 session.parent_session_id, session.inherited_handoff_id,
                 session.created_at.isoformat(), None),
            )
            connection.commit()
        finally:
            if self._connection is None:
                connection.close()

    def close_session(self, session_id: str, closed_at: str) -> SessionRecord:
        session = self.require_session(session_id)
        if session.closed_at is not None:
            raise ValueError("session is already closed")
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE sessions SET closed_at=? WHERE session_id=? AND closed_at IS NULL",
                (closed_at, session_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("session close failed")
            connection.commit()
        finally:
            if self._connection is None:
                connection.close()
        return self.require_session(session_id)

    def get_session(self, session_id: str) -> SessionRecord | None:
        row = self._one("SELECT * FROM sessions WHERE session_id=?", (session_id,))
        return SessionRecord.model_validate(dict(row)) if row else None

    def require_session(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown session:{session_id}")
        return session

    def active_session(self, task_id: str) -> SessionRecord | None:
        row = self._one(
            "SELECT * FROM sessions WHERE task_id=? AND closed_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        )
        return SessionRecord.model_validate(dict(row)) if row else None

    def sessions_for_task(self, task_id: str) -> list[SessionRecord]:
        return [SessionRecord.model_validate(dict(row)) for row in self._all(
            "SELECT * FROM sessions WHERE task_id=? ORDER BY created_at", (task_id,))]

    def append_event(self, event: HistoryEvent) -> None:
        session = self.require_session(event.session_id)
        if session.task_id != event.task_id:
            raise ValueError("event task/session mismatch")
        self.validate_pointers(event.references, task_id=event.task_id)
        if event.storage_ref and event.storage_ref.startswith("artifact:"):
            artifact = self.get_artifact(event.storage_ref)
            if artifact is None or artifact.task_id != event.task_id:
                raise ValueError(f"unresolvable storage_ref:{event.storage_ref}")
        self._insert("history_events", (event.event_id, event.task_id, event.session_id,
                     event.event_type.value, event.timestamp.isoformat(), event.node,
                     event.source, self._json(event)))

    def get_history_event(self, event_id: str) -> HistoryEvent | None:
        row = self._one("SELECT body FROM history_events WHERE event_id=?", (event_id,))
        return HistoryEvent.model_validate_json(row["body"]) if row else None

    def query_task_history(self, task_id: str, *, event_type: str | None = None,
                           session_id: str | None = None, keyword: str | None = None,
                           limit: int = 100) -> list[HistoryEvent]:
        clauses, values = ["task_id=?"], [task_id]
        if event_type:
            clauses.append("event_type=?")
            values.append(event_type)
        if session_id:
            clauses.append("session_id=?")
            values.append(session_id)
        if keyword:
            clauses.append("body LIKE ?")
            values.append(f"%{keyword}%")
        values.append(str(limit))
        rows = self._all(
            f"SELECT body FROM history_events WHERE {' AND '.join(clauses)} ORDER BY timestamp LIMIT ?",
            tuple(values),
        )
        return [HistoryEvent.model_validate_json(row["body"]) for row in rows]

    def put_artifact(self, artifact: ArtifactRecord) -> None:
        session = self.require_session(artifact.session_id)
        if session.task_id != artifact.task_id:
            raise ValueError("artifact task/session mismatch")
        self._insert("artifacts", (artifact.artifact_id, artifact.task_id, artifact.session_id,
                     artifact.created_at.isoformat(), self._json(artifact)))

    def get_artifact(self, ref: str) -> ArtifactRecord | None:
        artifact_id = ref.removeprefix("artifact:")
        row = self._one("SELECT body FROM artifacts WHERE artifact_id=?", (artifact_id,))
        return ArtifactRecord.model_validate_json(row["body"]) if row else None

    def put_handoff(self, handoff: HandoffProjection) -> None:
        session = self.require_session(handoff.session_id)
        if session.task_id != handoff.task_id:
            raise ValueError("handoff task/session mismatch")
        linked = [pointer for values in [*handoff.constraint_refs.values(),
                                         *handoff.decision_refs.values()] for pointer in values]
        self.validate_pointers([*handoff.key_message_refs, *handoff.evidence_refs, *linked],
                               task_id=handoff.task_id)
        for ref in handoff.artifact_refs:
            artifact = self.get_artifact(ref)
            if artifact is None or artifact.task_id != handoff.task_id:
                raise ValueError(f"unresolvable artifact pointer:{ref}")
        self._insert("handoffs", (handoff.handoff_id, handoff.task_id, handoff.session_id,
                     handoff.created_at.isoformat(), self._json(handoff)))

    def get_handoff(self, handoff_id: str) -> HandoffProjection | None:
        row = self._one("SELECT body FROM handoffs WHERE handoff_id=?", (handoff_id,))
        return HandoffProjection.model_validate_json(row["body"]) if row else None

    def validate_pointers(self, pointers: Iterable[HistoryPointer], *, task_id: str) -> None:
        for pointer in pointers:
            target = (self.get_history_event(pointer.target_id) if pointer.kind == "event"
                      else self.get_artifact(pointer.target_id))
            if target is None or target.task_id != task_id:
                raise ValueError(f"unresolvable {pointer.kind} pointer:{pointer.target_id}")

    def _insert(self, table: str, values: tuple) -> None:
        placeholders = ",".join("?" for _ in values)
        connection = self._connect()
        try:
            connection.execute(f"INSERT INTO {table} VALUES({placeholders})", values)
            connection.commit()
        finally:
            if self._connection is None:
                connection.close()

    def _one(self, sql: str, values: tuple) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return connection.execute(sql, values).fetchone()
        finally:
            if self._connection is None:
                connection.close()

    def _all(self, sql: str, values: tuple) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return list(connection.execute(sql, values).fetchall())
        finally:
            if self._connection is None:
                connection.close()

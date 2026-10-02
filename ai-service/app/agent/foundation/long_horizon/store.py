from __future__ import annotations

import json

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import ContextManager, Protocol

from .models import (
    ArtifactRecord,
    HandoffProjection,
    HandoffProjectionV2,
    HistoryEvent,
    HistoryPointer,
    SessionRecord,
    TaskRecord,
    read_handoff_projection,
    same_event_content,
)


class ContinuityStore(Protocol):
    """Persistence boundary; business services do not depend on SQLite."""

    def create_task(self, task: TaskRecord) -> None: ...
    def get_task(self, task_id: str) -> TaskRecord | None: ...
    def active_task(self, conversation_id: str) -> TaskRecord | None: ...
    def tasks_for_conversation(self, conversation_id: str) -> list[TaskRecord]: ...
    def close_task(self, task_id: str, closed_at: str) -> TaskRecord: ...
    def create_session(self, session: SessionRecord) -> None: ...
    def close_session(self, session_id: str, closed_at: str) -> SessionRecord: ...
    def get_session(self, session_id: str) -> SessionRecord | None: ...
    def require_session(self, session_id: str) -> SessionRecord: ...
    def active_session(self, task_id: str) -> SessionRecord | None: ...
    def sessions_for_task(self, task_id: str) -> list[SessionRecord]: ...
    def append_event(self, event: HistoryEvent) -> None: ...
    def append_event_once(self, event: HistoryEvent) -> HistoryEvent: ...
    def get_history_event(self, event_id: str) -> HistoryEvent | None: ...
    def query_task_history(
        self,
        task_id: str,
        *,
        event_type: str | None = None,
        session_id: str | None = None,
        keyword: str | None = None,
        limit: int = 100,
        after_seq: int | None = None,
    ) -> list[HistoryEvent]: ...
    def put_artifact(self, artifact: ArtifactRecord) -> None: ...
    def get_artifact(self, ref: str) -> ArtifactRecord | None: ...
    def put_handoff(self, handoff: HandoffProjection | HandoffProjectionV2) -> None: ...
    def get_handoff(self, handoff_id: str) -> HandoffProjection | HandoffProjectionV2 | None: ...
    def validate_pointers(
        self, pointers: Iterable[HistoryPointer], *, task_id: str
    ) -> None: ...
    def transaction(self) -> ContextManager[None]: ...


class SQLiteContinuityStore:
    """Local/test adapter; history, artifacts and handoffs are append-only."""

    supports_atomic_rollover = False

    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: sqlite3.Connection | None = None
        self._event_lock = RLock()
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
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, closed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS tasks_conversation
                    ON tasks(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
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
            history_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(history_events)")
            }
            for column, sql_type in (("event_key", "TEXT"), ("event_seq", "INTEGER")):
                if column not in history_columns:
                    connection.execute(f"ALTER TABLE history_events ADD COLUMN {column} {sql_type}")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS history_event_key ON history_events(event_key)"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS history_event_seq ON history_events(event_seq)"
            )
            session_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "conversation_id" not in session_columns:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN conversation_id TEXT"
                )
            connection.execute("""
                INSERT OR IGNORE INTO tasks(task_id, conversation_id, created_at, closed_at)
                SELECT task_id, 'legacy-conversation:' || task_id, MIN(created_at),
                       CASE WHEN SUM(closed_at IS NULL) = 0 THEN MAX(closed_at) ELSE NULL END
                FROM sessions
                GROUP BY task_id
            """)
            connection.execute("""
                UPDATE sessions
                SET conversation_id = (
                    SELECT tasks.conversation_id FROM tasks
                    WHERE tasks.task_id = sessions.task_id
                )
                WHERE conversation_id IS NULL
            """)
            connection.commit()
        finally:
            if self._connection is None:
                connection.close()

    @staticmethod
    def _json(model) -> str:
        return model.model_dump_json(exclude_none=True)

    @contextmanager
    def transaction(self):
        """Compatibility boundary; PostgreSQL provides atomic rollover."""
        yield

    def create_task(self, task: TaskRecord) -> None:
        self._insert(
            "tasks",
            (
                task.task_id,
                task.conversation_id,
                task.created_at.isoformat(),
                task.closed_at.isoformat() if task.closed_at else None,
            ),
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        row = self._one("SELECT * FROM tasks WHERE task_id=?", (task_id,))
        return TaskRecord.model_validate(dict(row)) if row else None

    def active_task(self, conversation_id: str) -> TaskRecord | None:
        row = self._one(
            "SELECT * FROM tasks WHERE conversation_id=? AND closed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        )
        return TaskRecord.model_validate(dict(row)) if row else None

    def tasks_for_conversation(self, conversation_id: str) -> list[TaskRecord]:
        return [
            TaskRecord.model_validate(dict(row))
            for row in self._all(
                "SELECT * FROM tasks WHERE conversation_id=? ORDER BY created_at",
                (conversation_id,),
            )
        ]

    def close_task(self, task_id: str, closed_at: str) -> TaskRecord:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"unknown task:{task_id}")
        if task.closed_at is not None:
            raise ValueError("task is already closed")
        if self.active_session(task_id) is not None:
            raise ValueError("cannot close a task with an active session")
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE tasks SET closed_at=? WHERE task_id=? AND closed_at IS NULL",
                (closed_at, task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("task close failed")
            connection.commit()
        finally:
            if self._connection is None:
                connection.close()
        resolved = self.get_task(task_id)
        if resolved is None:
            raise RuntimeError("closed task disappeared")
        return resolved

    def create_session(self, session: SessionRecord) -> None:
        task = self.get_task(session.task_id)
        if task is None:
            raise ValueError("task does not exist")
        if task.closed_at is not None:
            raise ValueError("cannot create a session for a closed task")
        if task.conversation_id != session.conversation_id:
            raise ValueError("task/session conversation mismatch")
        for field, related_id in (
            ("previous", session.previous_session_id),
            ("parent", session.parent_session_id),
        ):
            if not related_id:
                continue
            related = self.get_session(related_id)
            if related is None:
                raise ValueError(f"{field} session does not exist")
            if (
                related.task_id != session.task_id
                or related.conversation_id != session.conversation_id
            ):
                raise ValueError(f"{field} session lineage mismatch")
        if session.inherited_handoff_id:
            handoff = self.get_handoff(session.inherited_handoff_id)
            if handoff is None:
                raise ValueError("inherited handoff does not exist")
            if handoff.task_id != session.task_id:
                raise ValueError("inherited handoff task mismatch")
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO sessions("
                "session_id, task_id, conversation_id, previous_session_id, "
                "parent_session_id, inherited_handoff_id, created_at, closed_at"
                ") VALUES(?,?,?,?,?,?,?,?)",
                (
                    session.session_id,
                    session.task_id,
                    session.conversation_id,
                    session.previous_session_id,
                    session.parent_session_id,
                    session.inherited_handoff_id,
                    session.created_at.isoformat(),
                    session.closed_at.isoformat() if session.closed_at else None,
                ),
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
        self._append_event(event, idempotent=False)

    def append_event_once(self, event: HistoryEvent) -> HistoryEvent:
        if event.event_key is None:
            raise ValueError("idempotent history requires event_key")
        return self._append_event(event, idempotent=True)

    def _append_event(self, event: HistoryEvent, *, idempotent: bool) -> HistoryEvent:
        with self._event_lock:
            return self._append_locked_event(event, idempotent=idempotent)

    def _append_locked_event(self, event: HistoryEvent, *, idempotent: bool) -> HistoryEvent:
        session = self.require_session(event.session_id)
        if session.task_id != event.task_id:
            raise ValueError("event task/session mismatch")
        self.validate_pointers(event.references, task_id=event.task_id)
        if event.storage_ref and event.storage_ref.startswith("artifact:"):
            artifact = self.get_artifact(event.storage_ref)
            if artifact is None or artifact.task_id != event.task_id:
                raise ValueError(f"unresolvable storage_ref:{event.storage_ref}")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if idempotent:
                row = connection.execute(
                    "SELECT body FROM history_events WHERE event_key=?", (event.event_key,)
                ).fetchone()
                if row:
                    existing = HistoryEvent.model_validate_json(row["body"])
                    if not same_event_content(existing, event):
                        raise ValueError("history event key conflict")
                    connection.commit()
                    return existing
            seq = connection.execute(
                "SELECT COALESCE(MAX(event_seq), 0) + 1 FROM history_events"
            ).fetchone()[0]
            stored = event.model_copy(update={"event_seq": seq})
            connection.execute(
                "INSERT INTO history_events(event_id, task_id, session_id, event_type, "
                "timestamp, node, source, body, event_key, event_seq) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (stored.event_id, stored.task_id, stored.session_id, stored.event_type.value,
                 stored.timestamp.isoformat(), stored.node, stored.source, self._json(stored),
                 stored.event_key, seq),
            )
            connection.commit()
            return stored
        except BaseException:
            connection.rollback()
            raise
        finally:
            if self._connection is None:
                connection.close()

    def get_history_event(self, event_id: str) -> HistoryEvent | None:
        row = self._one("SELECT body FROM history_events WHERE event_id=?", (event_id,))
        return HistoryEvent.model_validate_json(row["body"]) if row else None

    def query_task_history(self, task_id: str, *, event_type: str | None = None,
                           session_id: str | None = None, keyword: str | None = None,
                           limit: int = 100, after_seq: int | None = None) -> list[HistoryEvent]:
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
        if after_seq is not None:
            clauses.append("event_seq > ?")
            values.append(after_seq)
        values.append(str(limit))
        rows = self._all(
            f"SELECT body FROM history_events WHERE {' AND '.join(clauses)} "
            "ORDER BY event_seq, timestamp, event_id LIMIT ?",
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

    def put_handoff(self, handoff: HandoffProjection | HandoffProjectionV2) -> None:
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

    def get_handoff(self, handoff_id: str) -> HandoffProjection | HandoffProjectionV2 | None:
        row = self._one("SELECT body FROM handoffs WHERE handoff_id=?", (handoff_id,))
        return read_handoff_projection(json.loads(row["body"])) if row else None

    def validate_pointers(self, pointers: Iterable[HistoryPointer], *, task_id: str) -> None:
        for pointer in pointers:
            target = (self.get_history_event(pointer.target_id) if pointer.kind == "event"
                      else self.get_artifact(pointer.target_id))
            if target is None or target.task_id != task_id:
                raise ValueError(f"unresolvable {pointer.kind} pointer:{pointer.target_id}")

    def _insert(self, table: str, values: tuple) -> None:
        with self._event_lock:
            self._insert_locked(table, values)

    def _insert_locked(self, table: str, values: tuple) -> None:
        placeholders = ",".join("?" for _ in values)
        connection = self._connect()
        try:
            connection.execute(f"INSERT INTO {table} VALUES({placeholders})", values)
            connection.commit()
        finally:
            if self._connection is None:
                connection.close()

    def _one(self, sql: str, values: tuple) -> sqlite3.Row | None:
        with self._event_lock:
            return self._one_locked(sql, values)

    def _one_locked(self, sql: str, values: tuple) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return connection.execute(sql, values).fetchone()
        finally:
            if self._connection is None:
                connection.close()

    def _all(self, sql: str, values: tuple) -> list[sqlite3.Row]:
        with self._event_lock:
            return self._all_locked(sql, values)

    def _all_locked(self, sql: str, values: tuple) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return list(connection.execute(sql, values).fetchall())
        finally:
            if self._connection is None:
                connection.close()

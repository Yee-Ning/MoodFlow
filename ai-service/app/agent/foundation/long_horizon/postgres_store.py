from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

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


class PostgresContinuityStore:
    """PostgreSQL adapter for durable long-horizon continuity."""

    supports_atomic_rollover = True

    def __init__(self, database_url: str, *, max_pool_size: int = 10):
        if not database_url.strip():
            raise ValueError("database_url is required")
        self._pool = ConnectionPool(
            database_url,
            min_size=1,
            max_size=max_pool_size,
            open=True,
            kwargs={"row_factory": dict_row},
        )
        self._transaction_connection: ContextVar[Any | None] = ContextVar(
            "postgres_continuity_transaction", default=None
        )
        row = self._one(
            "SELECT to_regclass('public.agent_task') AS table_name", ()
        )
        if row is None or row["table_name"] is None:
            self.close()
            raise RuntimeError(
                "Agent continuity schema is missing; run "
                "python -m scripts.migrate_agent_postgres first"
            )

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def transaction(self):
        if self._transaction_connection.get() is not None:
            yield
            return
        with self._pool.connection() as connection:
            with connection.transaction():
                token = self._transaction_connection.set(connection)
                try:
                    yield
                finally:
                    self._transaction_connection.reset(token)

    @staticmethod
    def _body(model) -> Jsonb:
        return Jsonb(model.model_dump(mode="json", exclude_none=True))

    @staticmethod
    def _task(row: dict[str, Any] | None) -> TaskRecord | None:
        return TaskRecord.model_validate(row) if row else None

    @staticmethod
    def _session(row: dict[str, Any] | None) -> SessionRecord | None:
        return SessionRecord.model_validate(row) if row else None

    def create_task(self, task: TaskRecord) -> None:
        self._execute(
            """
            INSERT INTO agent_task(task_id, conversation_id, created_at, closed_at)
            VALUES(%s, %s, %s, %s)
            """,
            (task.task_id, task.conversation_id, task.created_at, task.closed_at),
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._task(
            self._one("SELECT * FROM agent_task WHERE task_id=%s", (task_id,))
        )

    def active_task(self, conversation_id: str) -> TaskRecord | None:
        return self._task(
            self._one(
                """
                SELECT * FROM agent_task
                WHERE conversation_id=%s AND closed_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (conversation_id,),
            )
        )

    def tasks_for_conversation(self, conversation_id: str) -> list[TaskRecord]:
        return [
            TaskRecord.model_validate(row)
            for row in self._all(
                """
                SELECT * FROM agent_task
                WHERE conversation_id=%s ORDER BY created_at
                """,
                (conversation_id,),
            )
        ]

    def close_task(self, task_id: str, closed_at: str) -> TaskRecord:
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    "SELECT * FROM agent_task WHERE task_id=%s FOR UPDATE",
                    (task_id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown task:{task_id}")
                if row["closed_at"] is not None:
                    raise ValueError("task is already closed")
                active = connection.execute(
                    """
                    SELECT 1 FROM agent_session
                    WHERE task_id=%s AND closed_at IS NULL LIMIT 1
                    """,
                    (task_id,),
                ).fetchone()
                if active is not None:
                    raise ValueError("cannot close a task with an active session")
                resolved = connection.execute(
                    """
                    UPDATE agent_task SET closed_at=%s
                    WHERE task_id=%s AND closed_at IS NULL
                    RETURNING *
                    """,
                    (closed_at, task_id),
                ).fetchone()
        if resolved is None:
            raise ValueError("task close failed")
        return TaskRecord.model_validate(resolved)

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
        self._execute(
            """
            INSERT INTO agent_session(
                session_id, task_id, conversation_id, previous_session_id,
                parent_session_id, inherited_handoff_id, created_at, closed_at
            ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session.session_id,
                session.task_id,
                session.conversation_id,
                session.previous_session_id,
                session.parent_session_id,
                session.inherited_handoff_id,
                session.created_at,
                session.closed_at,
            ),
        )

    def close_session(self, session_id: str, closed_at: str) -> SessionRecord:
        row = self._one(
            """
            UPDATE agent_session SET closed_at=%s
            WHERE session_id=%s AND closed_at IS NULL
            RETURNING *
            """,
            (closed_at, session_id),
            write=True,
        )
        if row is None:
            session = self.get_session(session_id)
            if session is None:
                raise KeyError(f"unknown session:{session_id}")
            raise ValueError("session is already closed")
        return SessionRecord.model_validate(row)

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self._session(
            self._one(
                "SELECT * FROM agent_session WHERE session_id=%s", (session_id,)
            )
        )

    def require_session(self, session_id: str) -> SessionRecord:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"unknown session:{session_id}")
        return session

    def active_session(self, task_id: str) -> SessionRecord | None:
        return self._session(
            self._one(
                """
                SELECT * FROM agent_session
                WHERE task_id=%s AND closed_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (task_id,),
            )
        )

    def sessions_for_task(self, task_id: str) -> list[SessionRecord]:
        return [
            SessionRecord.model_validate(row)
            for row in self._all(
                """
                SELECT * FROM agent_session
                WHERE task_id=%s ORDER BY created_at
                """,
                (task_id,),
            )
        ]

    def append_event(self, event: HistoryEvent) -> None:
        self._append_event(event, idempotent=False)

    def append_event_once(self, event: HistoryEvent) -> HistoryEvent:
        if event.event_key is None:
            raise ValueError("idempotent history requires event_key")
        return self._append_event(event, idempotent=True)

    def _append_event(self, event: HistoryEvent, *, idempotent: bool) -> HistoryEvent:
        with self.transaction():
            # Allocate sequence numbers in task commit order so a paginated
            # reader cannot pass an earlier, still-uncommitted history row.
            self._one("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked", (event.task_id,))
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
        row = self._one(
            """
            INSERT INTO agent_history_event(
                event_id, task_id, session_id, event_type, event_timestamp,
                node, source, body, event_key
            ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            + (" ON CONFLICT DO NOTHING" if idempotent else "")
            + " RETURNING event_seq",
            (
                event.event_id,
                event.task_id,
                event.session_id,
                event.event_type.value,
                event.timestamp,
                event.node,
                event.source,
                self._body(event),
                event.event_key,
            ),
            write=True,
        )
        if row is not None:
            return event.model_copy(update={"event_seq": row["event_seq"]})
        existing_row = self._one(
            "SELECT body, event_seq FROM agent_history_event WHERE event_key=%s",
            (event.event_key,),
        )
        if existing_row is None:
            raise ValueError("history event receipt unavailable")
        existing = HistoryEvent.model_validate(existing_row["body"]).model_copy(
            update={"event_seq": existing_row["event_seq"]}
        )
        if not same_event_content(existing, event):
            raise ValueError("history event key conflict")
        return existing

    def get_history_event(self, event_id: str) -> HistoryEvent | None:
        row = self._one(
            "SELECT body, event_seq FROM agent_history_event WHERE event_id=%s", (event_id,)
        )
        return HistoryEvent.model_validate(row["body"]).model_copy(
            update={"event_seq": row["event_seq"]}
        ) if row else None

    def query_task_history(
        self,
        task_id: str,
        *,
        event_type: str | None = None,
        session_id: str | None = None,
        keyword: str | None = None,
        limit: int = 100,
        after_seq: int | None = None,
        descending: bool = False,
    ) -> list[HistoryEvent]:
        clauses = ["task_id=%s"]
        values: list[Any] = [task_id]
        if event_type:
            clauses.append("event_type=%s")
            values.append(event_type)
        if session_id:
            clauses.append("session_id=%s")
            values.append(session_id)
        if keyword:
            clauses.append("body::text ILIKE %s")
            values.append(f"%{keyword}%")
        if after_seq is not None:
            clauses.append("event_seq > %s")
            values.append(after_seq)
        values.append(limit)
        order = "event_seq DESC, event_timestamp DESC, event_id DESC" if descending else "event_seq, event_timestamp, event_id"
        rows = self._all(
            "SELECT body, event_seq FROM agent_history_event WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY {order} LIMIT %s",
            tuple(values),
        )
        return [HistoryEvent.model_validate(row["body"]).model_copy(
            update={"event_seq": row["event_seq"]}
        ) for row in rows]

    def put_artifact(self, artifact: ArtifactRecord) -> None:
        session = self.require_session(artifact.session_id)
        if session.task_id != artifact.task_id:
            raise ValueError("artifact task/session mismatch")
        self._execute(
            """
            INSERT INTO agent_artifact(
                artifact_id, task_id, session_id, created_at, body
            ) VALUES(%s, %s, %s, %s, %s)
            """,
            (
                artifact.artifact_id,
                artifact.task_id,
                artifact.session_id,
                artifact.created_at,
                self._body(artifact),
            ),
        )

    def get_artifact(self, ref: str) -> ArtifactRecord | None:
        artifact_id = ref.removeprefix("artifact:")
        row = self._one(
            "SELECT body FROM agent_artifact WHERE artifact_id=%s", (artifact_id,)
        )
        return ArtifactRecord.model_validate(row["body"]) if row else None

    def put_handoff(self, handoff: HandoffProjection | HandoffProjectionV2) -> None:
        session = self.require_session(handoff.session_id)
        if session.task_id != handoff.task_id:
            raise ValueError("handoff task/session mismatch")
        linked = [
            pointer
            for values in [
                *handoff.constraint_refs.values(),
                *handoff.decision_refs.values(),
            ]
            for pointer in values
        ]
        self.validate_pointers(
            [*handoff.key_message_refs, *handoff.evidence_refs, *linked],
            task_id=handoff.task_id,
        )
        for ref in handoff.artifact_refs:
            artifact = self.get_artifact(ref)
            if artifact is None or artifact.task_id != handoff.task_id:
                raise ValueError(f"unresolvable artifact pointer:{ref}")
        self._execute(
            """
            INSERT INTO agent_handoff(
                handoff_id, task_id, session_id, created_at, body
            ) VALUES(%s, %s, %s, %s, %s)
            """,
            (
                handoff.handoff_id,
                handoff.task_id,
                handoff.session_id,
                handoff.created_at,
                self._body(handoff),
            ),
        )

    def get_handoff(self, handoff_id: str) -> HandoffProjection | HandoffProjectionV2 | None:
        row = self._one(
            "SELECT body FROM agent_handoff WHERE handoff_id=%s", (handoff_id,)
        )
        return read_handoff_projection(row["body"]) if row else None

    def validate_pointers(
        self, pointers: Iterable[HistoryPointer], *, task_id: str
    ) -> None:
        for pointer in pointers:
            target = (
                self.get_history_event(pointer.target_id)
                if pointer.kind == "event"
                else self.get_artifact(pointer.target_id)
            )
            if target is None or target.task_id != task_id:
                raise ValueError(
                    f"unresolvable {pointer.kind} pointer:{pointer.target_id}"
                )

    def _execute(self, sql: str, values: tuple[Any, ...]) -> None:
        if connection := self._transaction_connection.get():
            connection.execute(sql, values)
            return
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(sql, values)

    def _one(
        self,
        sql: str,
        values: tuple[Any, ...],
        *,
        write: bool = False,
    ) -> dict[str, Any] | None:
        if connection := self._transaction_connection.get():
            return connection.execute(sql, values).fetchone()
        with self._pool.connection() as connection:
            if write:
                with connection.transaction():
                    return connection.execute(sql, values).fetchone()
            return connection.execute(sql, values).fetchone()

    def _all(self, sql: str, values: tuple[Any, ...]) -> list[dict[str, Any]]:
        if connection := self._transaction_connection.get():
            return list(connection.execute(sql, values).fetchall())
        with self._pool.connection() as connection:
            return list(connection.execute(sql, values).fetchall())

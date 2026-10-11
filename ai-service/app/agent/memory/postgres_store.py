from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .models import MemoryEvidence, MemoryJob, MemoryPreferences, MemoryRecord, MemoryType


def _vector_text(values: list[float] | None) -> str | None:
    if values is None:
        return None
    return "[" + ",".join(format(value, ".9g") for value in values) + "]"


def _parse_vector(value: Any) -> list[float] | None:
    if value is None or isinstance(value, list):
        return value
    text = str(value).strip().strip("[]")
    return [float(item) for item in text.split(",")] if text else []


class PostgresMemoryStore:
    """Dedicated long-term memory repository backed by PostgreSQL + pgvector."""

    def __init__(self, database_url: str, *, max_pool_size: int = 10) -> None:
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
            "postgres_memory_transaction", default=None
        )
        row = self._one(
            """
            SELECT
                to_regclass('public.agent_long_term_memory') AS table_name,
                EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector') AS vector_enabled
            """,
            (),
        )
        if row is None or row["table_name"] is None or not row["vector_enabled"]:
            self.close()
            raise RuntimeError(
                "Long-term memory schema/pgvector is missing; run "
                "python -m scripts.migrate_agent_postgres on a pgvector-enabled PostgreSQL"
            )

    def close(self) -> None:
        self._pool.close()

    def mark_profile_dirty(self, user_id, revision):
        self._execute(
            """INSERT INTO agent_memory_profile_projection(user_id,pending_revision)
               VALUES(%s,%s) ON CONFLICT(user_id) DO UPDATE SET
               pending_revision=EXCLUDED.pending_revision, updated_at=NOW()""", (user_id, revision))

    def pending_profile_revision(self, user_id):
        row = self._one(
            "SELECT pending_revision FROM agent_memory_profile_projection WHERE user_id=%s FOR UPDATE", (user_id,))
        return row["pending_revision"] if row else None

    def profile_records(self, user_id):
        rows = self._all(
            f"SELECT {self._columns()} FROM agent_long_term_memory WHERE user_id=%s AND memory_type='profile'",
            (user_id,))
        return [record for row in rows if (record := self._record(row)) is not None]

    def complete_profile_revision(self, user_id, revision, omitted_count):
        row = self._one(
            """UPDATE agent_memory_profile_projection SET pending_revision=NULL, projected_revision=%s,
               omitted_count=%s, updated_at=NOW() WHERE user_id=%s AND pending_revision=%s
               RETURNING user_id""", (revision, omitted_count, user_id, revision))
        return row is not None

    def get_record(self, user_id, memory_id):
        return self._record(self._one(
            f"SELECT {self._columns()} FROM agent_long_term_memory WHERE user_id=%s AND memory_id=%s",
            (user_id, memory_id)))

    def get_evidence(self, evidence_id):
        row = self._one("SELECT body FROM agent_memory_evidence WHERE evidence_id=%s", (evidence_id,))
        return MemoryEvidence.model_validate(row["body"]) if row else None

    def add_evidence(self, evidence):
        self._execute(
            """INSERT INTO agent_memory_evidence(evidence_id,user_id,source_message_id,memory_type,memory_key,body)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (evidence.evidence_id, evidence.user_id, evidence.source_message_id, evidence.memory_type.value,
             evidence.memory_key, Jsonb(evidence.model_dump(mode="json"))))

    def prepare_message(self, user_id, message_id, message_digest):
        with self.transaction(user_id, "message:" + message_id):
            self._execute(
                """INSERT INTO agent_memory_source_message(user_id,message_id,message_digest)
                   VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""", (user_id, message_id, message_digest))
            row = self._one(
                "SELECT message_digest,completed FROM agent_memory_source_message WHERE user_id=%s AND message_id=%s",
                (user_id, message_id))
            if row["message_digest"] != message_digest:
                raise ValueError("memory message idempotency conflict")
            return row["completed"]

    def complete_message(self, user_id, message_id):
        self._execute("UPDATE agent_memory_source_message SET completed=TRUE WHERE user_id=%s AND message_id=%s",
                      (user_id, message_id))

    def preferences(self, user_id):
        row = self._one("SELECT enabled,generation FROM agent_memory_preferences WHERE user_id=%s", (user_id,))
        return MemoryPreferences.model_validate(row) if row else MemoryPreferences()

    def set_enabled(self, user_id, enabled):
        row = self._one(
            """INSERT INTO agent_memory_preferences(user_id,enabled,generation) VALUES(%s,%s,1)
               ON CONFLICT(user_id) DO UPDATE SET enabled=EXCLUDED.enabled,
               generation=agent_memory_preferences.generation+1 RETURNING enabled,generation""", (user_id, enabled))
        return MemoryPreferences.model_validate(row)

    def list_records(self, user_id, *, after_id="", limit=50):
        rows = self._all(f"SELECT {self._columns()} FROM agent_long_term_memory "
                         "WHERE user_id=%s AND memory_id>%s ORDER BY memory_id LIMIT %s", (user_id, after_id, limit))
        return [self._record(row) for row in rows]

    def forget_fact(self, user_id, memory_key):
        from .projection import profile_key
        from app.agent.foundation.long_horizon.history_writer import digest
        self._execute("DELETE FROM agent_memory_evidence WHERE user_id=%s AND memory_key=%s", (user_id, memory_key))
        self._execute("DELETE FROM agent_long_term_memory WHERE user_id=%s AND memory_key=%s", (user_id, memory_key))
        self._execute(
            """INSERT INTO agent_memory_profile_revocation(user_id,key_digest,revoked_at) VALUES(%s,%s,NOW())
               ON CONFLICT(user_id,key_digest) DO UPDATE SET revoked_at=EXCLUDED.revoked_at""",
            (user_id, digest(profile_key(memory_key))))
        self.set_enabled(user_id, self.preferences(user_id).enabled)

    def profile_revocations(self, user_id):
        return {row["key_digest"]: row["revoked_at"] for row in self._all(
            "SELECT key_digest,revoked_at FROM agent_memory_profile_revocation WHERE user_id=%s", (user_id,))}

    def enqueue_job(self, job):
        with self.transaction(job.user_id, "job:" + job.job_id):
            previous = self._one("SELECT source_digest FROM agent_memory_job WHERE job_id=%s", (job.job_id,))
            if previous:
                if previous["source_digest"] != job.source_digest:
                    raise ValueError("memory job idempotency conflict")
                return False
            self._execute("INSERT INTO agent_memory_job(job_id,user_id,source_digest,payload) VALUES(%s,%s,%s,%s)",
                          (job.job_id, job.user_id, job.source_digest, Jsonb(job.model_dump(mode="json"))))
            return True

    def get_job(self, user_id, job_id):
        return self._job(self._one("SELECT * FROM agent_memory_job WHERE job_id=%s AND user_id=%s",
                                   (job_id, user_id)))

    def _job(self, row):
        if row is None:
            return None
        payload = dict(row["payload"])
        payload.update({key: row[key] for key in ("status", "owner", "lease_until", "attempts")})
        return MemoryJob.model_validate(payload)

    def claim_job(self, owner):
        with self._pool.connection() as connection, connection.transaction():
            row = connection.execute(
                """SELECT job_id FROM agent_memory_job WHERE status='pending'
                   OR (status='running' AND lease_until<=NOW()) ORDER BY created_at
                   LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
            if row is None:
                return None
            claimed = connection.execute(
                """UPDATE agent_memory_job SET status='running',owner=%s,lease_until=NOW()+INTERVAL '120 seconds',
                   attempts=attempts+1 WHERE job_id=%s RETURNING *""", (owner, row["job_id"])).fetchone()
        return self._job(claimed)

    def _owned_job(self, connection, job_id, owner):
        row = connection.execute(
            """SELECT * FROM agent_memory_job WHERE job_id=%s AND owner=%s
               AND status='running' AND lease_until>NOW() FOR UPDATE""", (job_id, owner)).fetchone()
        if row is None:
            raise ValueError("memory job lease lost")
        return self._job(row)

    def dispatch_job(self, job_id, owner):
        with self._pool.connection() as connection, connection.transaction():
            job = self._owned_job(connection, job_id, owner)
            if job.dispatches >= 2:
                return False
            job = job.model_copy(update={"dispatches": job.dispatches + 1})
            connection.execute("UPDATE agent_memory_job SET payload=%s WHERE job_id=%s",
                               (Jsonb(job.model_dump(mode="json")), job_id))
            return True

    def observe_job(self, job_id, owner, tokens):
        with self._pool.connection() as connection, connection.transaction():
            job = self._owned_job(connection, job_id, owner)
            job = job.model_copy(update={"reported_tokens": job.reported_tokens + (tokens or 0),
                                        "observed_dispatches": job.observed_dispatches + 1,
                                        "usage_known": job.usage_known and tokens is not None})
            connection.execute("UPDATE agent_memory_job SET payload=%s WHERE job_id=%s",
                               (Jsonb(job.model_dump(mode="json")), job_id))

    def finish_job(self, job_id, owner, status):
        with self._pool.connection() as connection, connection.transaction():
            row = self._owned_job(connection, job_id, owner)
            job = row.model_copy(update={"status": status, "user_message": row.user_message if status == "pending" else "",
                "extraction": row.extraction if status == "pending" else None, "owner": None, "lease_until": None})
            connection.execute(
                "UPDATE agent_memory_job SET status=%s,owner=NULL,lease_until=NULL,payload=%s WHERE job_id=%s",
                (status, Jsonb(job.model_dump(mode="json")), job_id))

    def prepare_job(self, job_id, owner, extraction):
        with self._pool.connection() as connection, connection.transaction():
            job = self._owned_job(connection, job_id, owner).model_copy(update={"extraction": extraction})
            connection.execute("UPDATE agent_memory_job SET payload=%s WHERE job_id=%s",
                               (Jsonb(job.model_dump(mode="json")), job_id))

    def cancel_pending_jobs(self, user_id):
        self._execute(
            """UPDATE agent_memory_job SET payload=payload || %s::jsonb
               WHERE user_id=%s AND status='pending'""",
            (Jsonb({"cancelled": True, "user_message": "", "extraction": None}), user_id))

    @contextmanager
    def transaction(self, user_id: str, memory_key: str) -> Iterator[None]:
        if self._transaction_connection.get() is not None:
            yield
            return
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{len(user_id)}:{user_id}{memory_key}",),
                )
                token = self._transaction_connection.set(connection)
                try:
                    yield
                finally:
                    self._transaction_connection.reset(token)

    @staticmethod
    def _record(row: dict[str, Any] | None) -> MemoryRecord | None:
        if row is None:
            return None
        payload = dict(row)
        payload.pop("similarity", None)
        payload["embedding"] = _parse_vector(payload.get("embedding"))
        return MemoryRecord.model_validate(payload)

    @staticmethod
    def _columns(*, include_similarity: bool = False) -> str:
        columns = """
            memory_id, user_id, memory_type, memory_key, content,
            structured_content, source_thread_id, confidence, importance,
            evidence_strength, confirmations, created_at, updated_at, status,
            version, superseded_by, embedding::text AS embedding, source_message_id, evidence_quote, valid_until
        """
        if include_similarity:
            columns += ", 1 - (embedding <=> %s::vector) AS similarity"
        return columns

    def get_active(
        self, user_id: str, memory_type: MemoryType, memory_key: str
    ) -> MemoryRecord | None:
        return self._record(
            self._one(
                f"""
                SELECT {self._columns()} FROM agent_long_term_memory
                WHERE user_id=%s AND memory_type=%s AND memory_key=%s
                  AND status='active'
                LIMIT 1
                """,
                (user_id, memory_type.value, memory_key),
            )
        )

    def insert(self, memory: MemoryRecord) -> None:
        self._execute(
            """
            INSERT INTO agent_long_term_memory(
                memory_id, user_id, memory_type, memory_key, content,
                structured_content, source_thread_id, confidence, importance,
                evidence_strength, confirmations, created_at, updated_at, status,
                version, superseded_by, embedding, source_message_id, evidence_quote, valid_until
            ) VALUES(
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s::vector, %s, %s, %s
            )
            """,
            self._values(memory),
        )

    def replace(self, memory: MemoryRecord) -> None:
        values = self._values(memory)
        self._execute(
            """
            UPDATE agent_long_term_memory SET
                user_id=%s, memory_type=%s, memory_key=%s, content=%s,
                structured_content=%s, source_thread_id=%s, confidence=%s,
                importance=%s, evidence_strength=%s, confirmations=%s,
                created_at=%s, updated_at=%s, status=%s, version=%s,
                superseded_by=%s, embedding=%s::vector, source_message_id=%s, evidence_quote=%s, valid_until=%s
            WHERE memory_id=%s
            """,
            (*values[1:], values[0]),
        )

    @staticmethod
    def _values(memory: MemoryRecord) -> tuple[Any, ...]:
        return (
            memory.memory_id,
            memory.user_id,
            memory.memory_type.value,
            memory.memory_key,
            memory.content,
            Jsonb(memory.structured_content),
            memory.source_thread_id,
            memory.confidence,
            memory.importance,
            memory.evidence_strength.value,
            memory.confirmations,
            memory.created_at,
            memory.updated_at,
            memory.status.value,
            memory.version,
            memory.superseded_by,
            _vector_text(memory.embedding),
            memory.source_message_id,
            memory.evidence_quote,
            memory.valid_until,
        )

    def list_active_profiles(
        self, user_id: str, *, scene: str | None = None, limit: int = 50, query: str = ""
    ) -> list[MemoryRecord]:
        clauses = ["user_id=%s", "memory_type='profile'", "status='active'"]
        clauses.append("(valid_until IS NULL OR valid_until>NOW())")
        values: list[Any] = [user_id]
        if scene is not None:
            clauses.append(
                "(structured_content->>'scene' IS NULL OR structured_content->>'scene'=%s)"
            )
            values.append(scene)
        else:
            clauses.append("structured_content->>'scene' IS NULL")
        import re
        normalized = re.sub(r"\W+", "", query.casefold())
        values.extend([normalized, limit])
        subject = "regexp_replace(lower(COALESCE(structured_content->>'subject','')), '[^[:alnum:]_]', '', 'g')"
        rows = self._all(
            f"""
            SELECT {self._columns()} FROM agent_long_term_memory
            WHERE {' AND '.join(clauses)}
            ORDER BY (LENGTH({subject}) >= 2 AND POSITION({subject} IN %s)>0) DESC,
                     importance DESC, updated_at DESC LIMIT %s
            """,
            tuple(values),
        )
        return [record for row in rows if (record := self._record(row)) is not None]

    def search_active_episodes(
        self,
        user_id: str,
        embedding: list[float],
        *,
        scene: str | None = None,
        limit: int = 20,
    ) -> list[tuple[MemoryRecord, float]]:
        clauses = [
            "user_id=%s",
            "memory_type='episodic'",
            "status='active'",
            "embedding IS NOT NULL",
            "(valid_until IS NULL OR valid_until>NOW())",
        ]
        vector = _vector_text(embedding)
        values: list[Any] = [vector, user_id]
        if scene is not None:
            clauses.append(
                "(structured_content->>'scene' IS NULL OR structured_content->>'scene'=%s)"
            )
            values.append(scene)
        else:
            clauses.append("structured_content->>'scene' IS NULL")
        values.extend([vector, limit])
        rows = self._all(
            f"""
            SELECT {self._columns(include_similarity=True)}
            FROM agent_long_term_memory
            WHERE {' AND '.join(clauses)}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            tuple(values),
        )
        results: list[tuple[MemoryRecord, float]] = []
        for row in rows:
            record = self._record(row)
            if record is not None:
                results.append((record, max(0.0, min(1.0, float(row["similarity"])))))
        return results

    def has_active_episodes(self, user_id: str) -> bool:
        row = self._one(
            """
            SELECT EXISTS(
                SELECT 1 FROM agent_long_term_memory
                WHERE user_id=%s AND memory_type='episodic'
                  AND status='active' AND embedding IS NOT NULL
            ) AS found
            """,
            (user_id,),
        )
        return bool(row and row["found"])

    def _execute(self, statement: str, values: tuple[Any, ...]) -> None:
        if connection := self._transaction_connection.get():
            connection.execute(statement, values)
            return
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(statement, values)

    def _one(
        self, statement: str, values: tuple[Any, ...]
    ) -> dict[str, Any] | None:
        if connection := self._transaction_connection.get():
            return connection.execute(statement, values).fetchone()
        with self._pool.connection() as connection:
            return connection.execute(statement, values).fetchone()

    def _all(
        self, statement: str, values: tuple[Any, ...]
    ) -> list[dict[str, Any]]:
        if connection := self._transaction_connection.get():
            return list(connection.execute(statement, values).fetchall())
        with self._pool.connection() as connection:
            return list(connection.execute(statement, values).fetchall())

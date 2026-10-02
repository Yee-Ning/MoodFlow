"""A conversation lease with a persistent fence and checkpoint revision.

Postgres also holds a session advisory lock: lease expiry cannot let a second
worker execute while a living first worker owns the connection. SQLite uses
BEGIN IMMEDIATE for CAS, and remains ineligible for automatic rollover.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from time import time
from uuid import uuid4
from threading import BoundedSemaphore


class ContinuityConflict(RuntimeError):
    """Public conflict; deliberately contains no state or authentication value."""


@dataclass
class ExecutionLease:
    locks: object
    key: str
    scope: str
    owner: str
    fence: int
    revision: int
    connection: object = None
    released: bool = False

    def check(self):
        if self.released:
            raise ContinuityConflict("execution lease released")
        if self.connection is not None:
            self.connection.execute("SELECT 1")
        with self.locks.transaction() as connection:
            row = connection.execute(self.locks.sql(
                "SELECT * FROM agent_execution WHERE execution_key=?"
            ), (self.key,)).fetchone()
            if (row is None or row["owner"] != self.owner or row["fence"] != self.fence
                    or row["expires_at"] <= time()):
                raise ContinuityConflict("execution fence lost")
            if row["revision"] != self.revision:
                raise ContinuityConflict("checkpoint revision changed")

    def renew(self):
        self.check()
        with self.locks.transaction() as connection:
            cursor = connection.execute(self.locks.sql(
                "UPDATE agent_execution SET expires_at=? WHERE execution_key=? AND owner=? AND fence=?"
            ), (time() + self.locks.ttl, self.key, self.owner, self.fence))
            if cursor.rowcount != 1:
                raise ContinuityConflict("execution fence lost")

    def advance(self):
        self.check()
        with self.locks.transaction() as connection:
            cursor = connection.execute(self.locks.sql(
                "UPDATE agent_execution SET revision=revision+1 WHERE execution_key=? AND owner=? AND fence=? AND revision=?"
            ), (self.key, self.owner, self.fence, self.revision))
            if cursor.rowcount != 1:
                raise ContinuityConflict("checkpoint revision changed")
            self.revision += 1

    def release(self):
        if self.released:
            return
        try:
            with self.locks.transaction() as connection:
                connection.execute(self.locks.sql(
                    "UPDATE agent_execution SET owner=NULL, expires_at=0 WHERE execution_key=? AND owner=? AND fence=?"
                ), (self.key, self.owner, self.fence))
        finally:
            self.released = True
            if self.connection is not None:
                try:
                    self.connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (self.key,))
                    self.connection.commit()
                finally:
                    try:
                        self.locks.store._pool.putconn(self.connection)
                    finally:
                        self.locks._slots.release()


class ExecutionLocks:
    def __init__(self, store, *, ttl=90):
        self.store, self.ttl = store, ttl
        self.postgres = getattr(store, "supports_atomic_rollover", False)
        if self.postgres:
            if store._pool.max_size < 2:
                raise ValueError("continuity requires at least two PostgreSQL pool connections")
            # Advisory owners retain a connection for the entire call. Reserve
            # at least half the pool for checkpoint/history/ACK queries.
            self._slots = BoundedSemaphore(max(1, store._pool.max_size // 2))
            row = store._one("SELECT to_regclass('public.agent_execution') AS name", ())
            if not row or not row["name"]:
                raise RuntimeError("continuity execution schema missing; approve manual migration 004 first")
        else:
            with self.transaction() as connection:
                connection.execute("""CREATE TABLE IF NOT EXISTS agent_execution (
                    execution_key TEXT PRIMARY KEY, auth_scope TEXT NOT NULL,
                    owner TEXT, fence INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0, expires_at REAL NOT NULL DEFAULT 0
                )""")

    def sql(self, value):
        return value.replace("?", "%s") if self.postgres else value

    @contextmanager
    def transaction(self):
        if self.postgres:
            with self.store._pool.connection() as connection, connection.transaction():
                yield connection
        else:
            with self.store._event_lock:
                connection = self.store._connect()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    if self.store._connection is None:
                        connection.close()

    def acquire(self, key, scope):
        owner, advisory = uuid4().hex, None
        if self.postgres:
            if not self._slots.acquire(blocking=False):
                raise ContinuityConflict("continuity execution capacity reached")
            try:
                advisory = self.store._pool.getconn()
                row = advisory.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS locked", (key,)).fetchone()
                advisory.commit()
                if not row["locked"]:
                    raise ContinuityConflict("conversation already executing")
            except BaseException:
                if advisory is not None:
                    self.store._pool.putconn(advisory)
                self._slots.release()
                raise
        try:
            with self.transaction() as connection:
                connection.execute(self.sql(
                    "INSERT INTO agent_execution(execution_key, auth_scope) VALUES(?, ?) ON CONFLICT(execution_key) DO NOTHING"
                ), (key, scope))
                row = connection.execute(self.sql("SELECT * FROM agent_execution WHERE execution_key=?"), (key,)).fetchone()
                if row["auth_scope"] != scope:
                    raise ContinuityConflict("conversation authentication scope changed")
                if not self.postgres and row["owner"] and row["expires_at"] > time():
                    raise ContinuityConflict("conversation already executing")
                fence = row["fence"] + 1
                connection.execute(self.sql(
                    "UPDATE agent_execution SET owner=?, fence=?, expires_at=? WHERE execution_key=?"
                ), (owner, fence, time() + self.ttl, key))
                return ExecutionLease(self, key, scope, owner, fence, row["revision"], advisory)
        except BaseException:
            if advisory is not None:
                advisory.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (key,))
                advisory.commit()
                self.store._pool.putconn(advisory)
                self._slots.release()
            raise

    def require_scope(self, key, scope):
        with self.transaction() as connection:
            row = connection.execute(self.sql("SELECT auth_scope FROM agent_execution WHERE execution_key=?"), (key,)).fetchone()
            if row is None or row["auth_scope"] != scope:
                raise ContinuityConflict("conversation authentication scope changed")

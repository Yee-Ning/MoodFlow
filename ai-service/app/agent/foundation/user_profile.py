"""Versioned user documents; writes share the public HistoryWriter gate."""

import json
import sqlite3
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.foundation.long_horizon.history_writer import validate_public_document
from app.agent.foundation.routing import RouteKey


class ProfileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=1000)
    source: str = Field(min_length=1, max_length=160)
    updated_at: AwareDatetime
    confidence: float = Field(ge=0, le=1)
    intents: tuple[RouteKey, ...] = Field(min_length=1, max_length=4)
    scene: str | None = Field(default=None, max_length=120)
    valid_until: AwareDatetime | None = None
    memory_id: str | None = Field(default=None, max_length=160)
    memory_key: str | None = Field(default=None, max_length=400)

    @field_validator("key", "value", "source")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("profile fields must not be blank")
        return value.strip()


class ProfileDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    updated_at: AwareDatetime
    entries: tuple[ProfileEntry, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def unique_keys(self):
        if len({entry.key for entry in self.entries}) != len(self.entries):
            raise ValueError("profile entry keys must be unique")
        return self


class ProfileStore(Protocol):
    def read(self, user_id: str, *, version: int | None = None) -> ProfileDocument | None: ...
    def write(self, document: ProfileDocument, *, expected_version: int) -> ProfileDocument: ...


class SQLiteProfileStore:
    """Local adapter with immutable snapshots and optimistic concurrency."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS user_profile_document (
                user_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(user_id, version)
            );
            CREATE TRIGGER IF NOT EXISTS immutable_profile_update BEFORE UPDATE ON user_profile_document
            BEGIN SELECT RAISE(ABORT, 'profile versions are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_profile_delete BEFORE DELETE ON user_profile_document
            BEGIN SELECT RAISE(ABORT, 'profile versions are immutable'); END;
        """)
        from threading import RLock
        self.lock = RLock()

    def read(self, user_id, *, version=None):
        with self.lock:
            row = self.connection.execute(
                "SELECT body FROM user_profile_document WHERE user_id=? "
                + ("AND version=? " if version is not None else "")
                + "ORDER BY version DESC LIMIT 1",
                (user_id, version) if version is not None else (user_id,),
            ).fetchone()
        return ProfileDocument.model_validate_json(row[0]) if row else None

    def write(self, document, *, expected_version):
        payload = validate_public_document(ProfileDocument, document)
        parsed = ProfileDocument.model_validate(payload)
        with self.lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            previous = self.read(parsed.user_id)
            if (previous.version if previous else 0) != expected_version or parsed.version != expected_version + 1:
                raise ValueError("profile version conflict")
            self.connection.execute(
                "INSERT INTO user_profile_document VALUES (?, ?, ?)",
                (parsed.user_id, parsed.version, json.dumps(payload, ensure_ascii=False)),
            )
        return parsed

    def close(self):
        self.connection.close()


class ProfileWriter:
    def __init__(self, store: ProfileStore, *, now=None):
        self.store = store
        self.now = now or (lambda: datetime.now(UTC))

    def update(self, user_id: str, entries: list[ProfileEntry], *, expected_version: int):
        previous = self.store.read(user_id)
        if (previous.version if previous else 0) != expected_version:
            raise ValueError("profile version conflict")
        merged = {entry.key: entry for entry in previous.entries} if previous else {}
        for entry in entries:
            parsed = ProfileEntry.model_validate(entry)
            if parsed.key in merged and parsed.updated_at < merged[parsed.key].updated_at:
                raise ValueError("profile update is older than stored entry")
            merged[parsed.key] = parsed
        document = ProfileDocument(user_id=user_id, version=expected_version + 1,
                                   updated_at=self.now(), entries=tuple(merged.values()))
        validate_public_document(ProfileDocument, document)
        return self.store.write(document, expected_version=expected_version)

    def project(self, user_id, entries, *, managed_keys, expected_version):
        """Replace only Memory-owned entries, preserving independently authored facts."""
        previous = self.store.read(user_id)
        if (previous.version if previous else 0) != expected_version:
            raise ValueError("profile version conflict")
        retained = tuple(entry for entry in previous.entries if entry.key not in managed_keys) if previous else ()
        values = (*retained, *entries)
        if previous is not None and previous.entries == values:
            return previous
        document = ProfileDocument(user_id=user_id, version=expected_version + 1,
                                   updated_at=self.now(), entries=values)
        validate_public_document(ProfileDocument, document)
        return self.store.write(document, expected_version=expected_version)


class PostgresProfileStore:
    """Uses the existing persistence backend; migration is applied by operators."""

    def __init__(self, database_url):
        from psycopg_pool import ConnectionPool
        self.pool = ConnectionPool(database_url, min_size=1, max_size=2)

    def read(self, user_id, *, version=None):
        with self.pool.connection() as connection:
            row = connection.execute(
                "SELECT body FROM agent_user_profile_document WHERE user_id=%s "
                + ("AND version=%s " if version is not None else "")
                + "ORDER BY version DESC LIMIT 1",
                (user_id, version) if version is not None else (user_id,),
            ).fetchone()
        return ProfileDocument.model_validate(row[0]) if row else None

    def write(self, document, *, expected_version):
        from psycopg.types.json import Jsonb
        payload = validate_public_document(ProfileDocument, document)
        parsed = ProfileDocument.model_validate(payload)
        with self.pool.connection() as connection, connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (parsed.user_id,))
            row = connection.execute("SELECT MAX(version) FROM agent_user_profile_document WHERE user_id=%s",
                                     (parsed.user_id,)).fetchone()
            if (row[0] or 0) != expected_version or parsed.version != expected_version + 1:
                raise ValueError("profile version conflict")
            connection.execute("INSERT INTO agent_user_profile_document VALUES (%s, %s, %s)",
                               (parsed.user_id, parsed.version, Jsonb(payload)))
        return parsed

    def close(self):
        self.pool.close()


@lru_cache
def get_profile_store() -> ProfileStore:
    import os
    from app.agent.foundation.long_horizon.runtime import persistence_backend
    if persistence_backend() == "postgres":
        return PostgresProfileStore(os.environ["DATABASE_URL"])
    return SQLiteProfileStore(Path(__file__).resolve().parents[3] / "tmp" / "user_profiles.sqlite")


def close_profile_store():
    if get_profile_store.cache_info().currsize:
        get_profile_store().close()
    get_profile_store.cache_clear()

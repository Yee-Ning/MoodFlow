from __future__ import annotations

import os
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg import Connection
from psycopg.rows import dict_row

from app.agent.checkpointing import create_checkpoint_serializer

MIGRATION_ROOT = Path(__file__).resolve().parents[2] / "migrations" / "postgresql"


def require_database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for PostgreSQL persistence")
    return database_url


def postgres_supports_pgvector(database_url: str) -> bool:
    with Connection.connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            """
            SELECT EXISTS(
                SELECT 1 FROM pg_available_extensions WHERE name='vector'
            ) AS available
            """
        ).fetchone()
    return bool(row and row["available"])


def run_business_migrations(
    database_url: str, *, include_memory: bool = True
) -> list[str]:
    applied_now: list[str] = []
    with Connection.connect(database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("moodflow_agent_schema_migrations",),
            )
            connection.execute("""
                CREATE TABLE IF NOT EXISTS agent_schema_migration (
                    version VARCHAR(255) PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            applied = {
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM agent_schema_migration"
                ).fetchall()
            }
            for path in sorted(MIGRATION_ROOT.glob("*.sql")):
                if path.name == "002_long_term_memory.sql" and not include_memory:
                    continue
                if path.name in applied:
                    continue
                connection.execute(path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO agent_schema_migration(version) VALUES(%s)",
                    (path.name,),
                )
                applied_now.append(path.name)
    return applied_now


def run_langgraph_migrations(database_url: str) -> None:
    with Connection.connect(
        database_url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    ) as connection:
        PostgresSaver(
            connection,
            serde=create_checkpoint_serializer(),
        ).setup()


def run_all_migrations(
    database_url: str | None = None, *, include_memory: bool = True
) -> list[str]:
    resolved_url = database_url or require_database_url()
    if include_memory and not postgres_supports_pgvector(resolved_url):
        raise RuntimeError(
            "PostgreSQL does not provide the pgvector extension required by Memory V1"
        )
    applied = run_business_migrations(resolved_url, include_memory=include_memory)
    run_langgraph_migrations(resolved_url)
    return applied

"""Memory and Redis P3 use distinct migration names with the same prefix."""

import pytest
from psycopg import Connection

from app.agent.postgres_migrations import run_business_migrations
from tests.test_context_loop_postgres_recovery import BASE, database

__all__ = ["database"]
pytestmark = pytest.mark.skipif(not BASE, reason="requires isolated PostgreSQL")


def test_memory_and_redis_migrations_coexist_and_remain_idempotent(database):
    added = run_business_migrations(database, include_memory=True)
    assert "008_memory_profile_projection.sql" in added
    assert run_business_migrations(database, include_memory=True) == []
    with Connection.connect(database) as connection:
        versions = {row[0] for row in connection.execute("SELECT version FROM agent_schema_migration")}
        assert {"008_memory_profile_projection.sql", "008_redis_events.sql"} <= versions
        tables = ["agent_memory_profile_projection", "agent_song_playcount", "agent_progress_event", "agent_memory_job"]
        for table in tables:
            assert connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0] == table

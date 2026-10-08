"""Profile persistence acceptance uses only the existing disposable loopback DB."""

import pytest
from psycopg import Error

from app.agent.foundation.user_profile import PostgresProfileStore, ProfileWriter
from tests.test_context_entry_phase1 import entry
from tests.test_context_loop_postgres_recovery import BASE, database

__all__ = ["database"]
pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


def test_profile_versions_persist_and_sql_mutation_is_rejected(database):
    store = PostgresProfileStore(database)
    writer = ProfileWriter(store)
    first = writer.update("alice", [entry()], expected_version=0)
    second = writer.update("alice", [entry(value="请使用详细的回答")], expected_version=1)
    assert store.read("bob") is None
    assert store.read("alice", version=1) == first
    with pytest.raises(ValueError, match="version conflict"):
        writer.update("alice", [entry()], expected_version=0)
    with pytest.raises(Error, match="immutable"):
        with store.pool.connection() as connection:
            connection.execute("DELETE FROM agent_user_profile_document WHERE user_id='alice'")
    store.close()
    reopened = PostgresProfileStore(database)
    assert reopened.read("alice") == second
    reopened.close()

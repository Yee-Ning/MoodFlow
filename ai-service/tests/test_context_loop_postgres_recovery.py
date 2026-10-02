"""Isolated PostgreSQL process deaths; never connect to an application DB."""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from psycopg import Connection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.agent.foundation.long_horizon.execution import ContinuityConflict, ExecutionLocks
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.handoff.state import HandoffRun
from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer
from app.agent.postgres_migrations import run_all_migrations

BASE = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


@pytest.fixture
def database():
    name = "moodflow_p1_test_" + uuid4().hex[:16]
    parameters = conninfo_to_dict(BASE)
    assert parameters.get("host") in {"127.0.0.1", "localhost"}, "acceptance only targets the loopback test server"
    with Connection.connect(BASE, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(**{**parameters, "dbname": name})
    run_all_migrations(url, include_memory=False)
    try:
        yield url
    finally:
        with Connection.connect(BASE, autocommit=True) as connection:
            connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def worker(url, mode, point, thread):
    result = subprocess.run([sys.executable, "-m", "tests.context_loop_process_worker", mode, point, thread],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, "CONTEXT_LOOP_TEST_DATABASE": url},
        capture_output=True, text=True, timeout=80, encoding="utf-8")
    # Outputs contain only controlled test fixture diagnostics, never credentials.
    if mode in {"crash", "migration_crash"}:
        assert result.returncode == 73, result.stderr[-4000:]
        return None
    assert result.returncode == 0, result.stderr[-4000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("point", ["after_proposal", "before_checkpoint", "after_checkpoint", "target_started", "after_result", "after_final", "model_dispatch"])
def test_process_death_recovers_original_run_without_repeat_hops_or_reset(database, point):
    thread = "p1-death-" + uuid4().hex
    worker(database, "crash", point, thread)
    store = PostgresContinuityStore(database)
    task = store.active_task("handoff:v1:" + thread)
    session = store.active_session(task.task_id)
    saver = AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer())
    row = saver.get_tuple({"configurable": {"thread_id": task.conversation_id, "session_id": session.session_id}})
    original_id = row.checkpoint["channel_values"]["run_id"]
    original_start = row.checkpoint["channel_values"]["run_started_at"]
    result = worker(database, "resume", point, thread)
    assert result["status"] == "success", result
    assert result["run_id"] == original_id and result["started_at"] == original_start
    assert result["hops"] == 2 and result["result_count"] == 1
    events = store.query_task_history(task.task_id, event_type=HistoryEventType.AGENT_TRANSFER, limit=100)
    accepted = [event for event in events if event.payload["phase"] in {"committed", "reconciled"}]
    assert len(accepted) == 2
    final = store.query_task_history(task.task_id, event_type=HistoryEventType.ASSISTANT_MESSAGE)
    assert len(final) == 1
    if point == "model_dispatch":
        assert result["tokens"] > 200  # Unknown pre-dispatch reserve is retained.
    store.close()


def test_postgres_advisory_lock_survives_lease_expiry_and_fences_connection_loss(database):
    one, two = PostgresContinuityStore(database), PostgresContinuityStore(database)
    first = ExecutionLocks(one).acquire("task-lock", "scope")
    with pytest.raises(ContinuityConflict):
        ExecutionLocks(two).acquire("task-lock", "scope")
    first.advance()
    one._one("UPDATE agent_execution SET expires_at=0 WHERE execution_key=%s RETURNING fence", ("task-lock",))
    with pytest.raises(ContinuityConflict):
        ExecutionLocks(two).acquire("task-lock", "scope")
    first.release()
    second = ExecutionLocks(two).acquire("task-lock", "scope")
    assert second.fence > first.fence and second.revision == 1
    second.release()
    one.close()
    two.close()


def test_explicit_namespace_migration_preserves_run_and_reconciles_target(database):
    thread = "p1-migrate-" + uuid4().hex
    worker(database, "crash", "target_started", thread)
    store = PostgresContinuityStore(database)
    task = store.active_task("handoff:v1:" + thread)
    old_session = store.active_session(task.task_id)
    saver = AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer())
    row = saver.get_tuple({"configurable": {"thread_id": task.conversation_id, "session_id": old_session.session_id}})
    original = HandoffRun.model_validate(row.checkpoint["channel_values"]["handoff_run"])
    migrated = worker(database, "migrate", "none", thread)
    assert migrated["migrated"] and migrated["session_id"] != old_session.session_id
    result = worker(database, "resume", "none", thread)
    assert result["status"] == "success" and result["run_id"] == original.run_id
    assert result["started_at"] == original.started_at.isoformat() and result["hops"] == 2
    assert result["tokens"] >= original.total_tokens
    new_session = store.active_session(task.task_id)
    assert new_session.previous_session_id == old_session.session_id
    store.close()


def test_migration_process_death_after_lineage_commit_reconciles_missing_target(database):
    thread = "p1-migration-gap-" + uuid4().hex
    worker(database, "crash", "target_started", thread)
    store = PostgresContinuityStore(database)
    task = store.active_task("handoff:v1:" + thread)
    before = store.active_session(task.task_id)
    worker(database, "migration_crash", "none", thread)
    after = store.active_session(task.task_id)
    assert after.previous_session_id == before.session_id
    saver = AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer())
    assert saver.get_tuple({"configurable": {"thread_id": task.conversation_id, "session_id": after.session_id}}) is None
    result = worker(database, "resume", "none", thread)
    assert result["status"] == "success" and result["hops"] == 2 and result["session_id"] == after.session_id
    store.close()


def test_lost_advisory_connection_fails_closed_before_new_executor_acquires(database):
    one, two = PostgresContinuityStore(database), PostgresContinuityStore(database)
    first = ExecutionLocks(one).acquire("connection-loss", "scope")
    pid = first.connection.info.backend_pid
    with Connection.connect(database, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
    from psycopg import OperationalError
    with pytest.raises(OperationalError):
        first.check()
    second = ExecutionLocks(two).acquire("connection-loss", "scope")
    assert second.fence > first.fence
    second.release()
    with pytest.raises(OperationalError):
        first.release()
    one.close()
    two.close()


def test_bounded_concurrency_keeps_connections_for_history_and_ack(database):
    from concurrent.futures import ThreadPoolExecutor
    store = PostgresContinuityStore(database, max_pool_size=6)
    locks = ExecutionLocks(store)
    with ThreadPoolExecutor(max_workers=3) as executor:
        leases = list(executor.map(lambda index: locks.acquire(f"parallel:{index}", "scope"), range(3)))
    with pytest.raises(ContinuityConflict, match="capacity"):
        locks.acquire("overflow", "scope")
    assert store._one("SELECT COUNT(*) AS count FROM agent_execution", ())["count"] == 3
    for lease in leases:
        lease.advance()
        lease.release()
    again = locks.acquire("parallel:0", "scope")
    assert again.fence == 2 and again.revision == 1
    again.release()
    store.close()

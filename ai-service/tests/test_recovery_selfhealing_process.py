"""Native exit 73 against isolated PG, production resume, and a separate HTTP fixture."""

from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
from time import sleep
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from langgraph.checkpoint.base import empty_checkpoint

import pytest
from psycopg import Connection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.agent.checkpointing import AsyncCompatiblePostgresSaver, create_checkpoint_serializer
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.postgres_migrations import run_all_migrations
from app.agent.handoff.state import HandoffRun

BASE = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not BASE, reason="recovery process acceptance requires isolated DATABASE_URL")


@pytest.fixture
def recovery_database():
    params = conninfo_to_dict(BASE)
    assert params.get("host") in {"localhost", "127.0.0.1"}
    name = "moodflow_d_test_" + uuid4().hex[:16]
    with Connection.connect(BASE, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(**{**params, "dbname": name})
    run_all_migrations(url, include_memory=False)
    try:
        yield url
    finally:
        with Connection.connect(BASE, autocommit=True) as conn:
            conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()", (name,))
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


@pytest.fixture
def external_effect():
    state = {"requests": [], "songs": [], "created": False, "player_applied": 0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def dispatch(self):
            url = urlsplit(self.path)
            state["requests"].append((self.command, url.path))
            playlist = {"id": 18445771229, "name": "MoodFlowTest-existing-fixture", "creator": "test-user",
                "subscribed": False, "trackCount": len(state["songs"]), "coverImgUrl": ""}
            if self.command == "POST":
                if url.path == "/test/player-applied":
                    state["player_applied"] += 1
                    data = {}
                elif url.path == "/api/playlists":
                    state["created"] = True
                    data = {"playlist_id": 99, "name": parse_qs(url.query)["name"][0], "status": "created", "message": "已创建"}
                else:
                    state["songs"].append({"id": 2652820720, "name": "晴天", "artists": ["测试艺人"], "album": "测试"})
                    data = {"status": "added", "message": "已添加"}
            elif url.path == "/api/user/playlists":
                data = [playlist]
                if state["created"]:
                    data.append({**playlist, "id": 99, "name": "MoodFlowTest-process-fixture", "trackCount": 0})
            else:
                data = {"playlist": playlist, "description": "", "songs": state["songs"]}
            payload = json.dumps({"code": 200, "message": "成功", "data": data}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        do_GET = dispatch
        do_POST = dispatch
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def worker(database, url, mode, point, thread):
    result = subprocess.run([sys.executable, "-m", "tests.recovery_selfhealing_worker", mode, point, thread],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, "CONTEXT_LOOP_TEST_DATABASE": database,
            "SPRING_BOOT_BASE_URL": url, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
        capture_output=True, text=True, encoding="utf-8", timeout=80)
    assert result.returncode == (73 if mode == "crash" else 0), result.stderr[-6000:] + result.stdout[-2000:]
    return None if mode == "crash" else json.loads(result.stdout.strip().splitlines()[-1])


def saved_run(database, thread):
    store = PostgresContinuityStore(database)
    task = store.active_task("handoff:v1:" + thread)
    session = store.active_session(task.task_id)
    saver = AsyncCompatiblePostgresSaver(store._pool, serde=create_checkpoint_serializer())
    row = saver.get_tuple({"configurable": {"thread_id": task.conversation_id, "session_id": session.session_id}})
    return store, task, session, saver, row, HandoffRun.model_validate(row.checkpoint["channel_values"]["handoff_run"])


def archive(case, result, requests, *, checkpoint_id=None):
    directory = os.getenv("RECOVERY_TEST_EVIDENCE_DIR")
    if directory:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        (root / (case + ".json")).write_text(json.dumps({"native_exit_code": 73, "case": case,
            "saved_checkpoint_id": checkpoint_id, "recovery_http_requests": requests, "result": result},
            ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("point", ["after_write_before_result", "after_add_before_result", "before_action_ack", "before_journal_committed"])
def test_native_exit_recovers_receipts_or_structured_block_without_external_write(recovery_database, external_effect, point):
    url, effect = external_effect
    thread = "d-native-" + uuid4().hex
    worker(recovery_database, url, "crash", point, thread)
    store, task, session, saver, row, original = saved_run(recovery_database, thread)
    before = list(effect["requests"])
    result = worker(recovery_database, url, "resume", point, thread)
    assert all(method == "GET" for method, _ in effect["requests"][len(before):])
    assert result["run_id"] == original.run_id and result["started_at"] == original.started_at.isoformat()
    assert result["limits"] == original.limits.model_dump(mode="json")
    gaps = result["diagnostics"]["receipt_gaps"]
    assert gaps
    if point == "after_write_before_result":
        assert gaps[0]["status"] == "blocked" and gaps[0]["reason"] == "creation_privacy_unobservable"
    elif point == "before_action_ack":
        assert any(g["reason"] == "action_ack_missing" for g in gaps)
        assert effect["player_applied"] == 1
        assert not store.query_task_history(task.task_id, event_type=HistoryEventType.ACTION_ACK)
    else:
        assert all(g["status"] == "repaired" for g in gaps)
        assert any(g["kind"] == ("write_success" if point == "after_add_before_result" else "transfer_committed") for g in gaps)
        if point == "after_add_before_result":
            results = store.query_task_history(task.task_id, event_type=HistoryEventType.TOOL_RESULT, limit=100)
            assert sum(e.payload["effect"] == "write" and e.payload["status"] == "success" for e in results) == 1
    archive(point, result, effect["requests"][len(before):], checkpoint_id=row.checkpoint["id"])
    before_replay = len(effect["requests"])
    worker(recovery_database, url, "resume", point, thread)
    assert all(method == "GET" for method, _ in effect["requests"][before_replay:])
    if point == "after_add_before_result":
        results = store.query_task_history(task.task_id, event_type=HistoryEventType.TOOL_RESULT, limit=100)
        assert sum(e.payload["effect"] == "write" and e.payload["status"] == "success" for e in results) == 1
    store.close()


def test_native_exit_transfer_receipt_repairs_after_original_deadline(recovery_database, external_effect):
    url, effect = external_effect
    thread = "d-deadline-" + uuid4().hex
    worker(recovery_database, url, "crash", "before_journal_committed", thread)
    store, task, session, saver, row, original = saved_run(recovery_database, thread)
    # Test fixture moves the saved original start into the past, never recovery.
    run = original.model_copy(update={"started_at": datetime.now(UTC) - timedelta(seconds=121)})
    values = {**row.checkpoint["channel_values"], "handoff_run": run.model_dump(mode="json"), "run_started_at": run.started_at.isoformat()}
    versions = {key: saver.get_next_version(row.checkpoint["channel_versions"][key], None)
        for key in ("handoff_run", "run_started_at")}
    checkpoint = {**row.checkpoint, "id": empty_checkpoint()["id"], "channel_values": values,
        "channel_versions": {**row.checkpoint["channel_versions"], **versions}}
    saver.put(row.config, checkpoint, row.metadata, versions)
    verified = saver.get_tuple({"configurable": {"thread_id": task.conversation_id, "session_id": session.session_id}})
    assert HandoffRun.model_validate(verified.checkpoint["channel_values"]["handoff_run"]).started_at == run.started_at
    result = worker(recovery_database, url, "resume", "before_journal_committed", thread)
    assert result["diagnostics"]["reason"] == "original_run_limit_exhausted"
    assert result["diagnostics"]["receipt_gaps"]
    assert all(g["status"] == "repaired" for g in result["diagnostics"]["receipt_gaps"])
    assert result["started_at"] == run.started_at.isoformat()
    assert not effect["requests"]
    archive("expired-transfer", result, [], checkpoint_id=row.checkpoint["id"])
    store.close()


@pytest.mark.parametrize("point", ["after_add_before_result", "before_journal_committed"])
def test_dedicated_postgres_restart_preserves_gap_and_repairs_without_write(recovery_database, external_effect, point):
    container = os.getenv("RECOVERY_TEST_PG_CONTAINER")
    if not container:
        pytest.skip("dedicated test PG container not configured for restart")
    assert container == "moodflow-recovery-selfhealing-20261003-pg"
    assert conninfo_to_dict(recovery_database).get("port") == "15467"
    url, effect = external_effect
    thread = "d-pg-restart-" + uuid4().hex
    worker(recovery_database, url, "crash", point, thread)
    store, task, session, saver, row, original = saved_run(recovery_database, thread)
    checkpoint_id = row.checkpoint["id"]
    store.close()
    restart = subprocess.run(["docker", "restart", "--time", "2", container], capture_output=True, text=True, timeout=30)
    assert restart.returncode == 0, restart.stderr
    for _ in range(20):
        ready = subprocess.run(["docker", "exec", container, "pg_isready", "-U", "postgres"],
            capture_output=True, timeout=5)
        if ready.returncode == 0:
            break
        sleep(0.5)
    else:
        pytest.fail("dedicated PostgreSQL did not become ready after restart")
    store, task, session, saver, row, restored = saved_run(recovery_database, thread)
    assert row.checkpoint["id"] == checkpoint_id
    assert restored.model_dump(mode="json") == original.model_dump(mode="json")
    store.close()
    before = len(effect["requests"])
    result = worker(recovery_database, url, "resume", point, thread)
    assert all(method == "GET" for method, _ in effect["requests"][before:])
    assert result["run_id"] == original.run_id and result["started_at"] == original.started_at.isoformat()
    assert result["diagnostics"]["receipt_gaps"]
    assert all(g["status"] == "repaired" for g in result["diagnostics"]["receipt_gaps"])
    archive("postgres-restart-" + point, result, effect["requests"][before:], checkpoint_id=checkpoint_id)

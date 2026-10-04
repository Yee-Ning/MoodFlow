"""Safety contracts for the production updater; no production mutation."""
from __future__ import annotations

import json
import os

import pytest

from deploy import update, update_smoke


def test_update_rebuilds_only_affected_images():
    assert update.changed_services(["docs/ops.md", "deploy/update.py", "deploy/backup.py"]) == []
    assert update.changed_services(["music-backend/src/App.java", "soul-music-room/src/App.tsx"]) == [
        "java", "frontend"]
    assert update.changed_services(["netease-music-api", "ai-service/app/main.py"]) == [
        "netease", "ai-service"]
    assert update.changed_services(["deploy/qdrant-health.sh"]) == ["qdrant"]
    assert update.changed_services(["ai-service/evals/config/jev_multi_frozen_strategy_v3.json"]) == [
        "ai-service"]


def test_reviewed_ai_observability_only_release_keeps_contract_and_submodule(monkeypatch):
    monkeypatch.setattr(update, 'command', lambda *args: 'unchanged')
    paths = ['ai-service/app/agent/handoff/observability.py','ai-service/app/api/chat.py',
             'deploy/runtime.py','deploy/handoff_observer.py','docs/reports/current/report.md']
    assert update.reviewed_services('old','new',paths,['ai-service'],True)==['ai-service']
    with pytest.raises(update.UpdateError, match='requires_explicit_review'):
        update.reviewed_services('old','new',paths,['ai-service'],False)


@pytest.mark.parametrize('path', ['docker-compose.production.yml','music-backend/src/App.java',
                                'ai-service/app/agent/handoff/state.py','ai-service/app/llm/runtime.py',
                                'ai-service/migrations/005.sql','.env.example','netease-music-api'])
def test_ai_observability_release_rejects_other_paths(monkeypatch, path):
    monkeypatch.setattr(update, 'command', lambda *args: 'unchanged')
    with pytest.raises(update.UpdateError,match='unreviewed_path'):
        update.reviewed_services('old','new',[path],['ai-service'],True)


@pytest.mark.parametrize('changed', ['compose','submodule'])
def test_ai_observability_release_rejects_contract_or_pinned_submodule_drift(monkeypatch, changed):
    def command(*args):
        if changed=='compose' and args[-1]=='new:docker-compose.production.yml':
            return 'different'
        if changed=='submodule' and args[-1]=='new:netease-music-api':
            return 'different'
        return 'unchanged'
    monkeypatch.setattr(update,'command',command)
    with pytest.raises(update.UpdateError,match='must_be_unchanged'):
        update.reviewed_services('old','new',['deploy/runtime.py'],['ai-service'],True)


@pytest.mark.parametrize("path", ["docker-compose.production.yml", ".env.example", "deploy/nginx-host.conf"])
def test_update_blocks_sensitive_contract_changes(path):
    with pytest.raises(update.UpdateError, match="manual_review_required"):
        update.changed_services([path])


def test_update_requires_explicit_contract_review_and_rebuilds_all_images():
    assert update.changed_services(["docker-compose.production.yml", ".env.example"],
                                   approve_contract=True) == list(update.BUILDABLE)
    with pytest.raises(update.UpdateError, match="manual_review_required"):
        update.changed_services(["deploy/nginx-host.conf"], approve_contract=True)


def test_preflight_requires_typesafe_secret_path_without_reading_value(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "ROOT", tmp_path)
    names = ("POSTGRES_PASSWORD_FILE", "LLM_API_KEY_FILE", "QDRANT_API_KEY_FILE",
             "TYPESAFE_API_KEY_FILE")
    paths = {}
    for name in names:
        path = tmp_path / name.lower()
        path.write_text("fixture-only", encoding="utf-8")
        path.chmod(0o600)
        paths[name] = str(path)
    env = tmp_path / ".env.production"
    env.write_text("\n".join(f"{name}={path}" for name, path in paths.items()), encoding="utf-8")
    env.chmod(0o600)
    # Windows chmod cannot represent POSIX 0600; the updater itself runs on Linux.
    original_stat = type(env).stat

    def private_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        return os.stat_result([result.st_mode & ~0o077, *list(result)[1:]])

    monkeypatch.setattr(type(env), "stat", private_stat)
    assert update.read_env_paths() == paths
    env.write_text("\n".join(f"{name}={path}" for name, path in paths.items()
                             if name != "TYPESAFE_API_KEY_FILE"), encoding="utf-8")
    with pytest.raises(update.UpdateError, match="secret_file_missing_or_empty:TYPESAFE_API_KEY_FILE"):
        update.read_env_paths()


def test_update_requires_explicit_migration_review():
    paths = ["ai-service/app/agent/postgres_migrations/009_add.sql", "ai-service/app/main.py"]
    assert update.migration_changes(paths) == paths[:1]
    assert update.changed_services(paths) == ["ai-service"]


def test_wait_state_fails_on_unhealthy_container(monkeypatch):
    monkeypatch.setattr(update, "compose", lambda *args: "test-container")
    monkeypatch.setattr(update, "command", lambda *args: json.dumps({"Status": "running",
                                                                    "Health": {"Status": "unhealthy"}}))
    with pytest.raises(update.UpdateError, match="service_unhealthy:java"):
        update.wait_state("java", "healthy", timeout=1)


def test_preflight_rejects_dirty_tree_before_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_git(*args):
        calls.append(args)
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if args[:2] == ("branch", "--show-current"):
            return "main"
        if args[:2] == ("remote", "get-url"):
            return "git@github.com:Yee-Ning/MoodFlow.git"
        if args[:2] == ("rev-parse", "--abbrev-ref"):
            return "origin/main"
        if args[0] == "status":
            return "?? local-config"
        raise AssertionError(args)

    monkeypatch.setattr(update, "git", fake_git)
    monkeypatch.setattr(update, "__file__", str(tmp_path / "deploy" / "update.py"))
    with pytest.raises(update.UpdateError, match="production_worktree_not_clean"):
        update.preflight()
    assert not any(args[0] == "fetch" for args in calls)


def test_backup_failure_stops_before_build_or_migration(monkeypatch, tmp_path):
    old = "1" * 40
    new = "2" * 40
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(update, "LOG_ROOT", tmp_path / "logs")
    monkeypatch.setattr(update, "preflight", lambda: old)
    monkeypatch.setattr(update, "container_image_ids", lambda: {service: "sha256:" + old
                                                                   for service in update.PERSISTENT})
    monkeypatch.setattr(update, "compose_images", lambda: {service: "moodflow/" + service + ":tag"
                                                             for service in update.BUILDABLE})
    monkeypatch.setattr(update, "status_clean", lambda: None)
    commands = []

    def fake_git(*args):
        commands.append(("git", *args))
        if args == ("rev-parse", "origin/main"):
            return new
        if args[:2] == ("diff", "--name-only"):
            return "ai-service/app/main.py"
        return ""

    class Result:
        def __init__(self, code):
            self.returncode = code

    monkeypatch.setattr(update, "git", fake_git)
    monkeypatch.setattr(update.subprocess, "run", lambda args, **kwargs: Result(
        1 if args[-2:] == (new, old) else 0))
    monkeypatch.setattr(update, "compose", lambda *args: commands.append(("compose", *args)) or "")
    monkeypatch.setattr(update, "safe_log_tail", lambda: None)
    with pytest.raises(SystemExit, match="1"):
        update.update(dry_run=False, approve_migrations=False)
    assert not any(call[:2] == ("compose", "build") or call[:2] == ("compose", "up") for call in commands)
    log = json.loads(next((tmp_path / "logs").glob("*.json")).read_text())
    assert log["phase"] == "backup" and log["status"] == "failed"
    assert log["previous_image_ids"]["postgres"] == "sha256:" + old


class Response:
    def __init__(self, payload, content_type="application/json"):
        self.status = 200
        self.headers = {"Content-Type": content_type}
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.payload).encode()

    def __iter__(self):
        return iter(self.payload)


def test_public_smoke_marks_ai_request_as_acceptance(monkeypatch):
    requests = []
    monkeypatch.setattr(update_smoke, "urlopen", lambda request, **kwargs: requests.append(request))
    update_smoke.request("/api/ai/chat", {"message": "fixture"})
    update_smoke.request("/healthz")
    assert requests[0].get_header("X-moodflow-telemetry-class") == "acceptance"
    assert requests[1].get_header("X-moodflow-telemetry-class") is None


def test_public_smoke_checks_sse_order_and_worldline_read(monkeypatch):
    paths = []

    def fake_request(path, payload=None, timeout=30):
        paths.append(path)
        if path == "/api/ai/chat":
            return Response([b"event: update\n", b"data: {}\n", b"event: final\n",
                             b"event: done\n"], "text/event-stream; charset=utf-8")
        if path == "/api/auth/status":
            return Response({"code": 200})
        if path == "/api/ai/worldline/sessions":
            return Response({"session_id": "s1", "revision": 1, "graph": {"nodes": [{}]}})
        if path == "/api/ai/worldline/sessions/s1":
            return Response({"session_id": "s1", "revision": 1})
        return Response({})

    ticks = iter([0.0, 0.2, 0.5, 0.6])
    monkeypatch.setattr(update_smoke, "request", fake_request)
    monkeypatch.setattr(update_smoke.time, "monotonic", lambda: next(ticks))
    result = update_smoke.run()
    assert result["sse"]["status"] == "passed"
    assert result["worldline"] == {"status": "passed", "attempts": 1, "node_count": 1}
    assert paths[-1] == "/api/ai/worldline/sessions/s1"
    assert update_smoke.WORLDLINE_SMOKE_QUERY == "Radiohead"


def test_public_smoke_rejects_buffered_events(monkeypatch):
    def fake_request(path, payload=None, timeout=30):
        if path == "/api/auth/status":
            return Response({"code": 200})
        if path == "/api/ai/chat":
            return Response([b"event: update\n", b"event: final\n", b"event: done\n"],
                            "text/event-stream")
        return Response({})

    ticks = iter([0.0, 0.01, 0.02, 0.03])
    monkeypatch.setattr(update_smoke, "request", fake_request)
    monkeypatch.setattr(update_smoke.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError, match="sse_stream_may_be_buffered"):
        update_smoke.run()

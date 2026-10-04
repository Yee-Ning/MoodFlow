"""No-push release preserves the updater's review/backup/rollback gates."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deploy import update


def test_bundle_checksum_and_ref_verified_without_origin_write(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    bundle = staging / "release.bundle"
    bundle.write_bytes(b"fixture")
    monkeypatch.setattr(update, "LOG_ROOT", tmp_path)
    # POSIX permissions are checked in production; preserve this assertion on Windows.
    stat = type(bundle).stat
    monkeypatch.setattr(type(bundle), "stat", lambda path, *a, **k: SimpleNamespace(
        **{name: getattr(stat(path, *a, **k), name) for name in ("st_size", "st_mtime", "st_ino", "st_dev")},
        st_mode=stat(path, *a, **k).st_mode & ~0o077))
    target = "a" * 40
    calls = []
    def command(*args, **kwargs):
        calls.append(args)
        if args[1:3] == ("bundle", "list-heads"):
            return target + " refs/heads/codex/deploy"
        if args[1:3] == ("rev-parse", "FETCH_HEAD"):
            return target
        return ""
    monkeypatch.setattr(update, "command", command)
    checksum = hashlib.sha256(b"fixture").hexdigest()
    assert update.bundle_target(bundle, checksum, target) == target
    assert ("git", "bundle", "verify", str(bundle)) in calls
    assert ("git", "fetch", "--no-tags", "--recurse-submodules=no", str(bundle), "refs/heads/codex/deploy") in calls
    assert not any("origin" in args or "push" in args or "reset" in args for args in calls)
    calls.clear()
    with pytest.raises(update.UpdateError, match="checksum_mismatch"):
        update.bundle_target(bundle, "0" * 64, target)
    assert calls == []


@pytest.mark.parametrize("target,checksum", [("short", "a" * 64), ("a" * 40, "short")])
def test_bundle_rejects_ambiguous_inputs(tmp_path, target, checksum):
    with pytest.raises(update.UpdateError, match="full_target_and_sha256"):
        update.bundle_target(tmp_path / "missing", checksum, target)


def compose_pair():
    old = "name: production\n    AI_AGENT_GRAPH_MODE: foundation\n    EXISTING: true"
    new = old.replace("    AI_AGENT_GRAPH_MODE: foundation\n", "    AI_AGENT_GRAPH_MODE: foundation\n    HANDOFF_CANARY_ENABLED: ${HANDOFF_CANARY_ENABLED:-false}\n")
    return old, new


@pytest.mark.parametrize("bad", ["scope", "approval", "config", "infrastructure"])
def test_scoped_release_rejects_unreviewed_changes(monkeypatch, bad):
    old, new = compose_pair()
    monkeypatch.setattr(update, "command", lambda *args: old if args[-1].startswith("old:") else new + ("\nsecrets: changed" if bad == "config" else ""))
    services = ["netease", "java", "ai-service"] if bad != "scope" else ["ai-service"]
    paths = [".dockerignore"] if bad == "infrastructure" else ["docker-compose.production.yml"]
    with pytest.raises(update.UpdateError):
        update.reviewed_services("old", "new", paths, services, bad != "approval")


def test_scoped_review_allows_only_closed_switch_and_requested_services(monkeypatch):
    old, new = compose_pair()
    monkeypatch.setattr(update, "command", lambda *args: old if args[-1].startswith("old:") else new)
    assert update.reviewed_services("old", "new", ["soul-music-room/src/App.vue"], ["netease", "java", "ai-service"], True) == ["netease", "java", "ai-service"]


def test_p1_skip_requires_exact_approved_sources_and_schema(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(update, "command", lambda *args: (root / args[-1].split(":", 1)[1]).read_text().strip())
    evidence = {"versions": [Path(path).name for path in update.P1_SQL], "history_keys": 2,
                "unique_indexes": 2, "immutable_trigger": True, "execution_columns": 6}
    monkeypatch.setattr(update, "compose", lambda *args: json.dumps(evidence))
    assert update.verified_applied_p1(list(update.P1_SQL), "new") == evidence
    evidence["immutable_trigger"] = False
    with pytest.raises(update.UpdateError, match="schema_not_verified"):
        update.verified_applied_p1(list(update.P1_SQL), "new")
    with pytest.raises(update.UpdateError, match="only_reviewed_003_004"):
        update.verified_applied_p1(["ai-service/migrations/postgresql/005_new.sql"], "new")
    monkeypatch.setattr(update, "command", lambda *args: "changed SQL")
    with pytest.raises(update.UpdateError, match="source_changed"):
        update.verified_applied_p1(list(update.P1_SQL), "new")


def test_scoped_update_backup_build_start_and_no_migrate_or_frontend(monkeypatch, tmp_path):
    old, new = "1" * 40, "2" * 40
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(update, "LOG_ROOT", tmp_path / "logs")
    monkeypatch.setattr(update, "preflight", lambda **kwargs: old)
    monkeypatch.setattr(update, "bundle_target", lambda *args: new)
    monkeypatch.setattr(update, "reviewed_services", lambda *args: ["netease", "java", "ai-service"])
    monkeypatch.setattr(update, "verified_applied_p1", lambda *args: {"verified": True})
    monkeypatch.setattr(update, "container_image_ids", lambda: dict.fromkeys(update.PERSISTENT, "sha256:" + old))
    monkeypatch.setattr(update, "compose_images", lambda: {s: f"moodflow/{s}:existing" for s in update.BUILDABLE})
    monkeypatch.setattr(update, "status_clean", lambda: None)
    monkeypatch.setattr(update, "wait_state", lambda *args: None)
    monkeypatch.setattr(update.subprocess, "run", lambda args, **kw: SimpleNamespace(returncode=1 if args[-2:] == (new, old) else 0))
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/backup.sh").touch()
    manifest = tmp_path / "backup.json"
    manifest.write_text(json.dumps({"status": "complete", "restore_verified": True,
                                   "postgres": {"path": "pg"}, "qdrant": {"path": "qd"}}))
    original_relative = type(manifest).is_relative_to
    monkeypatch.setattr(type(manifest), "is_relative_to", lambda path, other: True if other == Path("/secure/moodflow/backups/manifests") else original_relative(path, other))
    calls = []
    def git(*args):
        calls.append(("git", *args))
        if args[:2] == ("diff", "--name-only"):
            return "\n".join([*update.P1_SQL, "ai-service/app/main.py", "soul-music-room/src/App.vue", "docker-compose.production.yml"])
        return ""
    def compose(*args):
        calls.append(("compose", *args))
        return ""
    def command(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("bash", "deploy/backup.sh"):
            return str(manifest)
        if args[:3] == ("python3", "-m", "deploy.update_smoke"):
            return '{}'
        return "sha256:" + new
    monkeypatch.setattr(update, "git", git)
    monkeypatch.setattr(update, "compose", compose)
    monkeypatch.setattr(update, "command", command)
    update.update(False, False, True, bundle=tmp_path / "test.bundle", services_only=["netease", "java", "ai-service"], p1_already_applied=True)
    assert calls.index(("bash", "deploy/backup.sh", "--predeploy")) < calls.index(("compose", "build", "netease"))
    assert ("git", "submodule", "update", "--init", "--recursive", "--no-fetch") in calls
    assert [("compose", "up", "-d", "--no-build", "--no-deps", service) for service in ("netease", "java", "ai-service")] == [c for c in calls if c[:2] == ("compose", "up")]
    assert not any(c[:2] == ("git", "fetch") or "migrate" in c or "frontend" in c or "qdrant" in c for c in calls)
    record = json.loads(next((tmp_path / "logs").glob("*.json")).read_text())
    assert record["status"] == "passed" and record["migration"] == "not_needed"
    assert record["source"] == "verified_bundle"
    assert record["already_applied_p1"] == {"verified": True}
    assert record["deferred_frontend_paths"] == ["soul-music-room/src/App.vue"]


def test_staged_updater_rejects_unverified_executor_before_other_checks(tmp_path, monkeypatch):
    script = tmp_path / "staging/update.py"
    script.parent.mkdir()
    script.write_text("unreviewed source\n")
    monkeypatch.setattr(update, "LOG_ROOT", tmp_path)
    monkeypatch.setattr(update, "__file__", str(script))
    monkeypatch.setattr(update, "command", lambda *args: pytest.fail("unverified executor must stop immediately"))
    with pytest.raises(update.UpdateError, match="not_exact_reviewed_blob"):
        update.preflight()


def test_rollback_restores_only_deployed_services_and_original_commit(tmp_path, monkeypatch):
    old, new = "1" * 40, "2" * 40
    run_id = "20261004T120000Z-22222222"
    record = {"previous_commit": old, "new_commit": new, "build_services": ["netease", "java", "ai-service"],
              "previous_image_ids": dict.fromkeys(update.PERSISTENT, "sha256:" + old),
              "active_image_tags": {s: f"moodflow/{s}:existing" for s in update.BUILDABLE}}
    (tmp_path / (run_id + ".json")).write_text(json.dumps(record))
    monkeypatch.setattr(update, "LOG_ROOT", tmp_path)
    monkeypatch.setattr(update, "preflight", lambda **kwargs: new)
    calls = []
    def git(*args):
        calls.append(("git", *args))
        return new if args == ("rev-parse", "HEAD") else ""
    monkeypatch.setattr(update, "git", git)
    monkeypatch.setattr(update, "command", lambda *args: calls.append(args) or "")
    monkeypatch.setattr(update, "compose", lambda *args: calls.append(("compose", *args)) or "")
    monkeypatch.setattr(update, "wait_state", lambda *args: None)
    update.rollback(run_id, True, executor_target=new)
    assert ("git", "reset", "--hard", old) in calls
    assert [c[-1] for c in calls if c[:2] == ("compose", "up")] == ["netease", "java", "ai-service"]
    assert not any("migrate" in c or "qdrant" in c or "frontend" in c for c in calls)
    assert json.loads((tmp_path / (run_id + ".json")).read_text())["rollback"]["application"] == "restored"

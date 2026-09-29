"""Conservative, logged, single-host production Compose updater.

Only committed code is deployed. Database and Qdrant rollback is deliberately
outside this command; a rollback restores application code and image IDs only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # Unit tests also run on Windows; production is Ubuntu.
    fcntl = None

ROOT = Path("/opt/moodflow")
LOG_ROOT = Path("/secure/moodflow/deployments")
COMPOSE = ("docker", "compose", "--env-file", ".env.production", "-f", "docker-compose.production.yml")
PERSISTENT = ("postgres", "qdrant", "netease", "java", "ai-service", "frontend")
BUILDABLE = ("qdrant", "netease", "java", "ai-service", "frontend")
REMOTE_URLS = {"git@github.com:Yee-Ning/MoodFlow.git", "https://github.com/Yee-Ning/MoodFlow.git"}
MIN_FREE_BYTES = 8 * 1024**3
SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


class UpdateError(RuntimeError):
    pass


def command(*args: str, input_data: bytes | None = None, capture: bool = True) -> str:
    result = subprocess.run(args, cwd=ROOT, input=input_data, capture_output=capture, check=False)
    if result.returncode:
        # Commands are fixed by this module; never echo command output that might
        # contain credentials, request bodies, or application/user data.
        raise UpdateError(f"command_failed:{args[0]}:exit_{result.returncode}")
    return result.stdout.decode("utf-8", "replace").strip() if capture else ""


def git(*args: str) -> str:
    return command("git", *args)


def compose(*args: str) -> str:
    return command(*COMPOSE, *args)


def status_clean() -> None:
    if git("status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none"):
        raise UpdateError("production_worktree_not_clean")
    for line in git("submodule", "status", "--recursive").splitlines():
        if line and line[0] in "-+U":
            raise UpdateError("submodule_not_at_pinned_commit")


def read_env_paths() -> dict[str, str]:
    env_file = ROOT / ".env.production"
    if not env_file.is_file() or env_file.stat().st_mode & 0o077:
        raise UpdateError("env_file_missing_or_permissions_not_600")
    names = {"POSTGRES_PASSWORD_FILE", "LLM_API_KEY_FILE", "QDRANT_API_KEY_FILE"}
    paths: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in names:
            paths[name.strip()] = value.strip().strip("'\"")
    for name in names:
        path = Path(paths.get(name, ""))
        if not path.is_absolute() or not path.is_file() or path.stat().st_size == 0:
            raise UpdateError(f"secret_file_missing_or_empty:{name}")
        if path.stat().st_mode & 0o077:
            raise UpdateError(f"secret_file_permissions_too_open:{name}")
    return paths


def preflight() -> str:
    if Path.cwd().resolve() != ROOT or Path(__file__).resolve().parents[1] != ROOT:
        raise UpdateError("not_moodflow_production_root")
    if git("rev-parse", "--show-toplevel") != str(ROOT) or git("branch", "--show-current") != "main":
        raise UpdateError("production_branch_or_directory_mismatch")
    if git("remote", "get-url", "origin") not in REMOTE_URLS:
        raise UpdateError("unexpected_origin_remote")
    if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") != "origin/main":
        raise UpdateError("main_upstream_mismatch")
    status_clean()
    read_env_paths()
    command("docker", "info", "--format", "{{.ServerVersion}}")
    command("docker", "compose", "version", "--short")
    compose("config", "--quiet")
    command("nginx", "-t")
    if command("systemctl", "is-active", "nginx") != "active" or command(
        "systemctl", "is-active", "snap.certbot.renew.timer"
    ) != "active":
        raise UpdateError("https_or_certbot_timer_not_active")
    free = shutil.disk_usage(ROOT).free
    if free < MIN_FREE_BYTES:
        raise UpdateError("insufficient_disk_free_under_8_gib")
    previous = git("rev-parse", "HEAD")
    if not SHA.fullmatch(previous):
        raise UpdateError("invalid_previous_commit")
    return previous


def changed_services(paths: list[str]) -> list[str]:
    selected: set[str] = set()
    for path in paths:
        if path in {"docker-compose.production.yml", ".env.example", "deploy/nginx-host.conf"}:
            raise UpdateError(f"manual_review_required_for_deployment_contract:{path}")
        if path == ".dockerignore":
            selected.update(BUILDABLE)
        elif path == "netease-music-api" or path.startswith("netease-music-api/") or path == "deploy/netease.Dockerfile":
            selected.add("netease")
        elif path.startswith("music-backend/") or path in {"deploy/java.Dockerfile", "deploy/HttpHealthCheck.java"}:
            selected.add("java")
        elif path.startswith("soul-music-room/") or path in {"deploy/frontend.Dockerfile", "deploy/nginx.conf"}:
            selected.add("frontend")
        elif path in {"deploy/qdrant.Dockerfile", "deploy/qdrant-start.sh", "deploy/qdrant-health.sh"}:
            selected.add("qdrant")
        elif path in {"deploy/ai.Dockerfile", "deploy/requirements.production.txt",
                      "deploy/runtime.py", "deploy/artifacts.py", "deploy/model_cache.py",
                      "deploy/frozen-artifacts.json", "deploy/model-cache-manifest.json"}:
            # The AI image copies deploy/*.py, but host operations helpers do
            # not affect its runtime and do not warrant a production rebuild.
            selected.add("ai-service")
        elif path == "ai-service/requirements.txt" or path.startswith((
            "ai-service/app/", "ai-service/migrations/", "ai-service/data/intent/",
        )) or path == "ai-service/scripts/migrate_agent_postgres.py":
            selected.add("ai-service")
    return [service for service in BUILDABLE if service in selected]


def migration_changes(paths: list[str]) -> list[str]:
    return [path for path in paths if path.startswith(("ai-service/app/agent/postgres_migrations/",
                                                     "ai-service/migrations/"))
            or path == "ai-service/scripts/migrate_agent_postgres.py"]


def compose_images() -> dict[str, str]:
    services = json.loads(compose("config", "--format", "json"))["services"]
    return {service: services[service]["image"] for service in BUILDABLE}


def container_image_ids() -> dict[str, str]:
    result: dict[str, str] = {}
    for service in PERSISTENT:
        container = compose("ps", "-q", service)
        if not container:
            raise UpdateError(f"missing_container:{service}")
        image = command("docker", "inspect", "--format", "{{.Image}}", container)
        if not image.startswith("sha256:"):
            raise UpdateError(f"invalid_image_id:{service}")
        result[service] = image
    return result


def write_log(log_path: Path, record: dict[str, object]) -> None:
    tmp = log_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(log_path)


def wait_state(service: str, expected: str, timeout: int = 600) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container = compose("ps", "-q", "-a", service)
        if container:
            state = json.loads(command("docker", "inspect", "--format", "{{json .State}}", container))
            if expected == "healthy" and state.get("Health", {}).get("Status") == "healthy":
                return
            if expected == "exited0" and state.get("Status") == "exited" and state.get("ExitCode") == 0:
                return
            if state.get("Status") == "exited" and state.get("ExitCode") != 0:
                raise UpdateError(f"service_exited_nonzero:{service}")
            if state.get("Health", {}).get("Status") == "unhealthy":
                raise UpdateError(f"service_unhealthy:{service}")
        time.sleep(5)
    raise UpdateError(f"service_health_timeout:{service}")


def safe_log_tail() -> None:
    # Compose's own logs can contain user content. Give an exact inspection
    # command instead of copying application logs into a root deployment log.
    print("诊断日志：cd /opt/moodflow && docker compose --env-file .env.production "
          "-f docker-compose.production.yml logs --tail=80 migrate postgres qdrant netease java ai-service frontend")


def update(dry_run: bool, approve_migrations: bool) -> None:
    phase = "preflight"
    record: dict[str, object] = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
                                 "status": "running", "phase": phase, "migration": "not_started",
                                 "health": "not_started", "smoke": "not_started"}
    log_path: Path | None = None
    try:
        previous = preflight()
        record["previous_commit"] = previous
        phase = "fetch"
        git("fetch", "--prune", "origin", "main")
        target = git("rev-parse", "origin/main")
        if not SHA.fullmatch(target):
            raise UpdateError("invalid_remote_commit")
        record["new_commit"] = target
        if previous == target or subprocess.run(("git", "merge-base", "--is-ancestor", target, previous), cwd=ROOT).returncode == 0:
            print(f"已是最新可用提交：{previous[:12]}")
            return
        if subprocess.run(("git", "merge-base", "--is-ancestor", previous, target), cwd=ROOT).returncode:
            raise UpdateError("main_diverged_from_origin_main")
        paths = git("diff", "--name-only", previous, target).splitlines()
        services = changed_services(paths)
        changed_migrations = migration_changes(paths)
        if changed_migrations and not approve_migrations:
            raise UpdateError("migration_review_required:rerun_with_--approve-migrations_after_review")
        record["build_services"] = services
        record["migration_paths"] = changed_migrations
        if dry_run:
            print(json.dumps({"dry_run": True, "previous_commit": previous, "target_commit": target,
                              "build_services": services, "migration_paths": changed_migrations,
                              "backup_required": True}, ensure_ascii=False))
            return
        LOG_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(LOG_ROOT, 0o700)
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + target[:8]
        log_path = LOG_ROOT / f"{run_id}.json"
        record["run_id"] = run_id
        old_ids = container_image_ids()
        record["previous_image_ids"] = old_ids
        record["active_image_tags"] = compose_images()
        record["phase"] = phase
        write_log(log_path, record)
        phase = "git_update"
        git("merge", "--ff-only", "origin/main")
        git("submodule", "sync", "--recursive")
        git("submodule", "update", "--init", "--recursive")
        status_clean()
        compose("config", "--quiet")
        phase = "backup"
        if not (ROOT / "deploy/backup.sh").is_file():
            raise UpdateError("mandatory_backup_script_missing")
        backup_manifest = command("bash", "deploy/backup.sh", "--predeploy")
        manifest_path = Path(backup_manifest.splitlines()[-1])
        if not manifest_path.is_file() or not manifest_path.resolve().is_relative_to(
            Path("/secure/moodflow/backups/manifests")
        ):
            raise UpdateError("backup_manifest_missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "complete" or not manifest.get("restore_verified"):
            raise UpdateError("backup_not_verified")
        record["backup_manifest"] = str(manifest_path)
        record["backup_files"] = {key: manifest[key]["path"] for key in ("postgres", "qdrant")}
        record["phase"] = phase
        write_log(log_path, record)
        phase = "build"
        for service in services:
            rollback_tag = f"moodflow/{service}:rollback-{run_id.lower()}"
            command("docker", "image", "tag", old_ids[service], rollback_tag)
            compose("build", service)
        record["new_image_ids"] = {service: command("docker", "image", "inspect", "--format", "{{.Id}}",
                                                     compose_images()[service]) for service in services}
        if shutil.disk_usage(ROOT).free < MIN_FREE_BYTES:
            raise UpdateError("insufficient_disk_after_build_under_8_gib")
        record["phase"] = phase
        write_log(log_path, record)
        phase = "migration"
        if "ai-service" in services or changed_migrations:
            record["migration"] = "running"
            write_log(log_path, record)
            compose("up", "-d", "--no-build", "--no-deps", "--force-recreate", "migrate")
            wait_state("migrate", "exited0")
            record["migration"] = "exited_0"
        else:
            record["migration"] = "not_needed"
        record["phase"] = phase
        write_log(log_path, record)
        phase = "start"
        compose("up", "-d", "--no-build")
        phase = "health"
        record["health"] = {}
        for service in PERSISTENT:
            wait_state(service, "healthy")
            record["health"][service] = "healthy"
            write_log(log_path, record)
        wait_state("migrate", "exited0")
        record["health"]["migrate"] = "exited_0"
        record["phase"] = phase
        write_log(log_path, record)
        phase = "smoke"
        record["smoke"] = json.loads(command("python3", "-m", "deploy.update_smoke"))
        record["phase"] = phase
        record["status"] = "passed"
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_log(log_path, record)
        print(f"部署成功；记录：{log_path}")
    except (UpdateError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        record["phase"] = phase
        record["status"] = "failed"
        record["reason"] = str(exc)
        record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        if log_path is not None:
            write_log(log_path, record)
        print(f"部署停止：阶段={phase}，原因={exc}", file=sys.stderr)
        if log_path is not None:
            print(f"部署记录：{log_path}", file=sys.stderr)
            print(f"人工回滚应用代码/镜像：bash deploy/update.sh rollback {record['run_id']} --acknowledge-schema-risk",
                  file=sys.stderr)
            print("数据库/Qdrant 不会自动回滚；请先评估 migration 与备份。", file=sys.stderr)
        safe_log_tail()
        raise SystemExit(1) from None


def rollback(run_id: str, acknowledge_schema_risk: bool) -> None:
    if not RUN_ID.fullmatch(run_id) or not acknowledge_schema_risk:
        raise UpdateError("rollback_requires_valid_run_id_and_--acknowledge-schema-risk")
    preflight()
    log_path = LOG_ROOT / f"{run_id}.json"
    if not log_path.is_file():
        raise UpdateError("rollback_log_missing")
    record = json.loads(log_path.read_text(encoding="utf-8"))
    previous = record.get("previous_commit", "")
    target = record.get("new_commit", "")
    if not SHA.fullmatch(previous) or not SHA.fullmatch(target) or git("rev-parse", "HEAD") != target:
        raise UpdateError("rollback_head_or_commit_mismatch")
    old_ids = record.get("previous_image_ids", {})
    tags = record.get("active_image_tags", {})
    services = record.get("build_services", [])
    if any(service not in old_ids or service not in tags for service in services):
        raise UpdateError("rollback_image_record_incomplete")
    for service in services:
        command("docker", "image", "inspect", old_ids[service], "--format", "{{.Id}}")
    # This is intentionally manual-only. It cannot undo SQL or Qdrant changes.
    git("reset", "--hard", previous)
    git("submodule", "sync", "--recursive")
    git("submodule", "update", "--init", "--recursive")
    for service in services:
        command("docker", "image", "tag", old_ids[service], tags[service])
    for service in ("qdrant", "netease", "java", "ai-service", "frontend"):
        if service in services:
            compose("up", "-d", "--no-build", "--no-deps", service)
    for service in PERSISTENT:
        wait_state(service, "healthy")
    record["rollback"] = {"at_utc": datetime.now(timezone.utc).isoformat(), "application": "restored",
                          "database": "untouched", "qdrant": "untouched"}
    write_log(log_path, record)
    print("应用代码与旧镜像已恢复；数据库和 Qdrant 未回退。请人工核对 schema 兼容性。")


def main() -> None:
    parser = argparse.ArgumentParser(description="MoodFlow production updater")
    parser.add_argument("action", nargs="?", choices=("update", "rollback"), default="update")
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--approve-migrations", action="store_true")
    parser.add_argument("--acknowledge-schema-risk", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if ROOT != Path.cwd().resolve():
        parser.error("run only from /opt/moodflow")
    if fcntl is None:
        parser.error("production updater requires Linux flock")
    lock = Path("/run/lock/moodflow-update.lock")
    with lock.open("w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("another update is running")
        if args.action == "rollback":
            try:
                rollback(args.run_id or "", args.acknowledge_schema_risk)
            except (UpdateError, OSError, ValueError, KeyError) as exc:
                parser.error(str(exc))
        else:
            update(args.dry_run, args.approve_migrations)


if __name__ == "__main__":
    main()

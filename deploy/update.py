"""Conservative, logged, single-host production Compose updater.

Only committed code is deployed. Database and Qdrant rollback is deliberately
outside this command; a rollback restores application code and image IDs only.
"""
from __future__ import annotations

import argparse
import hashlib
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
    names = {"POSTGRES_PASSWORD_FILE", "LLM_API_KEY_FILE", "QDRANT_API_KEY_FILE",
             "TYPESAFE_API_KEY_FILE"}
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


def preflight(executor_target: str | None = None) -> str:
    source_path = Path(__file__).resolve()
    if source_path.parents[1] != ROOT:
        # A no-push bundle may carry a newer updater. Only execute the exact
        # reviewed Git blob, from the root-owned staging directory.
        staging = LOG_ROOT / "staging"
        if (not executor_target or not SHA.fullmatch(executor_target)
                or not source_path.is_relative_to(staging.resolve())
                or source_path.stat().st_mode & 0o077
                or getattr(source_path.stat(), "st_uid", 0) != 0
                or hashlib.sha256(source_path.read_bytes()).hexdigest()
                != hashlib.sha256((command("git", "show", f"{executor_target}:deploy/update.py") + "\n").encode()).hexdigest()):
            raise UpdateError("staged_updater_not_exact_reviewed_blob")
    if Path.cwd().resolve() != ROOT:
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


def bundle_target(bundle: Path, checksum: str, target: str) -> str:
    """Import reviewed objects only; never change origin, its refs or history."""
    if not SHA.fullmatch(target) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise UpdateError("bundle_requires_full_target_and_sha256")
    staging = (LOG_ROOT / "staging").resolve()
    if (bundle.is_symlink() or not bundle.is_file() or not bundle.resolve().is_relative_to(staging)
            or bundle.stat().st_mode & 0o077 or getattr(bundle.stat(), "st_uid", 0) != 0):
        raise UpdateError("bundle_not_private_staged_file")
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != checksum:
        raise UpdateError("bundle_checksum_mismatch")
    command("git", "bundle", "verify", str(bundle))
    heads = command("git", "bundle", "list-heads", str(bundle)).splitlines()
    refs = [line.split()[1] for line in heads if line.split()[0] == target]
    if len(refs) != 1:
        raise UpdateError("bundle_target_not_unique")
    command("git", "fetch", "--no-tags", "--recurse-submodules=no", str(bundle), refs[0])
    if git("rev-parse", "FETCH_HEAD") != target:
        raise UpdateError("bundle_fetched_target_mismatch")
    return target


P1_SQL = {
    "ai-service/migrations/postgresql/003_context_loop_history.sql": "db21752bcb86affae039634342e5b6df8ec113053aa5ecb213f07c854acb67b3",
    "ai-service/migrations/postgresql/004_continuity_execution.sql": "5622723c341f6782085039ad2d0872d35ed93303a6f1114d7878e96eb0a388d2",
}


def verified_applied_p1(paths: list[str], target: str) -> dict:
    if set(paths) != set(P1_SQL):
        raise UpdateError("p1_skip_requires_only_reviewed_003_004")
    for path, expected in P1_SQL.items():
        content = command("git", "show", f"{target}:{path}") + "\n"
        if hashlib.sha256(content.encode()).hexdigest() != expected:
            raise UpdateError("p1_migration_source_changed")
    sql = """SELECT json_build_object(
    'versions', (SELECT json_agg(version ORDER BY version) FROM agent_schema_migration),
    'history_keys', (SELECT count(*) FROM information_schema.columns WHERE table_schema='public'
      AND table_name='agent_history_event' AND column_name IN ('event_key','event_seq')),
    'unique_indexes', (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
      WHERE i.indrelid=to_regclass('public.agent_history_event') AND i.indisunique AND i.indisvalid
      AND c.relname IN ('agent_history_event_key','agent_history_event_seq')),
    'immutable_trigger', EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.agent_history_event')
      AND tgname='agent_history_event_immutable' AND tgenabled IN ('O','A')),
    'execution_columns', (SELECT count(*) FROM information_schema.columns WHERE table_schema='public'
      AND table_name='agent_execution' AND column_name IN ('execution_key','auth_scope','owner','fence','revision','expires_at'))
    )::text"""
    shell = 'exec psql -X -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"'
    evidence = json.loads(compose("exec", "-T", "postgres", "sh", "-c", shell, "sh", sql))
    if (not {Path(path).name for path in paths}.issubset(evidence.get("versions", []))
            or evidence.get("history_keys") != 2 or evidence.get("unique_indexes") != 2
            or evidence.get("immutable_trigger") is not True or evidence.get("execution_columns") != 6):
        raise UpdateError("p1_production_schema_not_verified")
    return evidence


def reviewed_services(previous: str, target: str, paths: list[str], services: list[str],
                      approve_contract: bool) -> list[str]:
    if services == ["ai-service"]:
        # Explicit review for this observability release; existing deployment,
        # backup, provenance and health gates still run without exemptions.
        if not approve_contract:
            raise UpdateError("ai_observability_release_requires_explicit_review")
        if command("git", "show", f"{previous}:docker-compose.production.yml") != command(
            "git", "show", f"{target}:docker-compose.production.yml"
        ):
            raise UpdateError("ai_observability_release_compose_must_be_unchanged")
        allowed = {"ai-service/app/agent/handoff/observability.py", "ai-service/app/agent/handoff/routing.py",
                   "ai-service/app/agent/handoff/guard.py", "ai-service/app/agent/handoff/trace.py",
                   "ai-service/app/agent/foundation/main_graph.py", "ai-service/app/api/chat.py",
                   "ai-service/app/agent/foundation/long_horizon/runner.py",
                   "ai-service/app/agent/foundation/long_horizon/coordinator.py",
                   "deploy/runtime.py", "deploy/handoff_observer.py", "deploy/moodflow-handoff-observer.service",
                   "deploy/update.py"}
        if any(path not in allowed and not path.startswith(("docs/reports/", "ai-service/tests/", "deploy/tests/"))
               for path in paths):
            raise UpdateError("ai_observability_release_contains_unreviewed_path")
        if command("git", "rev-parse", f"{previous}:netease-music-api") != command(
            "git", "rev-parse", f"{target}:netease-music-api"
        ):
            raise UpdateError("ai_observability_release_submodule_must_be_unchanged")
        return services
    if services != ["netease", "java", "ai-service"] or not approve_contract:
        raise UpdateError("scoped_release_requires_reviewed_three_services_and_contract")
    # This release permits exactly the previously reviewed closed switch.
    old = command("git", "show", f"{previous}:docker-compose.production.yml")
    new = command("git", "show", f"{target}:docker-compose.production.yml")
    line = "    HANDOFF_CANARY_ENABLED: ${HANDOFF_CANARY_ENABLED:-false}\n"
    if new != old.replace("    AI_AGENT_GRAPH_MODE: foundation\n", "    AI_AGENT_GRAPH_MODE: foundation\n" + line):
        raise UpdateError("scoped_release_compose_delta_not_canary_only")
    forbidden = {".dockerignore", ".env.example", "deploy/nginx-host.conf", "deploy/qdrant.Dockerfile",
                 "deploy/qdrant-start.sh", "deploy/qdrant-health.sh", "deploy/frozen-artifacts.json",
                 "deploy/model-cache-manifest.json"}
    if forbidden.intersection(paths):
        raise UpdateError("scoped_release_contains_unreviewed_infrastructure")
    return services


def changed_services(paths: list[str], *, approve_contract: bool = False) -> list[str]:
    selected: set[str] = set()
    for path in paths:
        if path == "deploy/nginx-host.conf" or (
            path in {"docker-compose.production.yml", ".env.example"} and not approve_contract
        ):
            raise UpdateError(f"manual_review_required_for_deployment_contract:{path}")
        if path == "docker-compose.production.yml":
            # A reviewed Compose change may affect any service's build or runtime.
            selected.update(BUILDABLE)
        elif path == ".dockerignore":
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
        )) or path in {"ai-service/scripts/migrate_agent_postgres.py",
                       "ai-service/evals/config/router_capability_contract.json",
                       "ai-service/evals/config/jev_multi_frozen_strategy_v3.json"}:
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


def update(dry_run: bool, approve_migrations: bool, approve_contract: bool = False, *,
           bundle: Path | None = None, bundle_sha256: str = "", target_commit: str = "",
           services_only: list[str] | None = None, p1_already_applied: bool = False) -> None:
    phase = "preflight"
    record: dict[str, object] = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
                                 "status": "running", "phase": phase, "migration": "not_started",
                                 "health": "not_started", "smoke": "not_started"}
    log_path: Path | None = None
    try:
        target = bundle_target(bundle, bundle_sha256, target_commit) if bundle is not None else None
        previous = preflight(executor_target=target) if target else preflight()
        record["previous_commit"] = previous
        phase = "fetch"
        if target is None:
            git("fetch", "--prune", "origin", "main")
            target = git("rev-parse", "origin/main")
        record["source"] = "verified_bundle" if bundle is not None else "origin/main"
        if bundle is not None:
            record["bundle_sha256"] = bundle_sha256
        if not SHA.fullmatch(target):
            raise UpdateError("invalid_remote_commit")
        record["new_commit"] = target
        if previous == target or subprocess.run(("git", "merge-base", "--is-ancestor", target, previous), cwd=ROOT).returncode == 0:
            print(f"已是最新可用提交：{previous[:12]}")
            return
        if subprocess.run(("git", "merge-base", "--is-ancestor", previous, target), cwd=ROOT).returncode:
            raise UpdateError("main_diverged_from_origin_main")
        paths = git("diff", "--name-only", previous, target).splitlines()
        services = changed_services(paths, approve_contract=approve_contract)
        if services_only is not None:
            services = reviewed_services(previous, target, paths, services_only, approve_contract)
            record["deferred_frontend_paths"] = [path for path in paths if path.startswith("soul-music-room/")]
        changed_migrations = migration_changes(paths)
        if p1_already_applied:
            if services_only is None:
                raise UpdateError("p1_skip_requires_scoped_release")
            record["already_applied_p1"] = verified_applied_p1(changed_migrations, target)
            changed_migrations = []
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
        git("merge", "--ff-only", target)
        git("submodule", "sync", "--recursive")
        if bundle is not None:
            git("submodule", "update", "--init", "--recursive", "--no-fetch")
        else:
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
        record["release_tags"] = {}
        for service in services:
            tag = f"moodflow/{service}:release-{target[:12]}"
            command("docker", "image", "tag", record["new_image_ids"][service], tag)
            record["release_tags"][service] = tag
        if shutil.disk_usage(ROOT).free < MIN_FREE_BYTES:
            raise UpdateError("insufficient_disk_after_build_under_8_gib")
        record["phase"] = phase
        write_log(log_path, record)
        phase = "migration"
        if changed_migrations or ("ai-service" in services and services_only is None):
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
        if services_only is not None:
            for service in services:
                compose("up", "-d", "--no-build", "--no-deps", service)
        else:
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


def rollback(run_id: str, acknowledge_schema_risk: bool, *, executor_target: str | None = None) -> None:
    if not RUN_ID.fullmatch(run_id) or not acknowledge_schema_risk:
        raise UpdateError("rollback_requires_valid_run_id_and_--acknowledge-schema-risk")
    preflight(executor_target=executor_target) if executor_target else preflight()
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
    parser.add_argument("--approve-contract", action="store_true")
    parser.add_argument("--acknowledge-schema-risk", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--bundle-sha256", default="")
    parser.add_argument("--target-commit", default="")
    parser.add_argument("--services", nargs="+", choices=BUILDABLE)
    parser.add_argument("--p1-already-applied", action="store_true")
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
                executor_target = bundle_target(args.bundle, args.bundle_sha256, args.target_commit) if args.bundle else None
                rollback(args.run_id or "", args.acknowledge_schema_risk, executor_target=executor_target)
            except (UpdateError, OSError, ValueError, KeyError) as exc:
                parser.error(str(exc))
        else:
            update(args.dry_run, args.approve_migrations, args.approve_contract,
                   bundle=args.bundle, bundle_sha256=args.bundle_sha256, target_commit=args.target_commit,
                   services_only=args.services, p1_already_applied=args.p1_already_applied)


if __name__ == "__main__":
    main()

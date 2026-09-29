"""Root-only PostgreSQL/Qdrant backups with isolated restore verification."""
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
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo

try:
    import fcntl
except ImportError:  # Offline unit tests run on Windows; production is Ubuntu.
    fcntl = None

from deploy import update

ROOT = Path("/opt/moodflow")
BACKUPS = Path("/secure/moodflow/backups")
POSTGRES_DIR = BACKUPS / "postgres"
QDRANT_DIR = BACKUPS / "qdrant"
MANIFEST_DIR = BACKUPS / "manifests"
MIN_FREE_BYTES = 8 * 1024**3
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.snapshot$")
STAMP = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
CORE_TABLES = ("agent_session", "agent_task", "agent_long_term_memory", "checkpoints",
               "checkpoint_blobs", "checkpoint_writes")


class BackupError(RuntimeError):
    pass


def run(*args: str, input_data: bytes | None = None, stdin_file=None, output_file=None) -> str:
    result = subprocess.run(args, cwd=ROOT, input=input_data if stdin_file is None else None,
                            stdin=stdin_file, stdout=output_file or subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        # Never expose stderr; it may contain a URL, key, or user data.
        raise BackupError(f"command_failed:{args[0]}:exit_{result.returncode}")
    return "" if output_file else result.stdout.decode("utf-8", "replace").strip()


def compose(*args: str, input_data: bytes | None = None, stdin_file=None, output_file=None) -> str:
    return run(*update.COMPOSE, *args, input_data=input_data, stdin_file=stdin_file, output_file=output_file)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_file(path: Path, parent: Path) -> Path:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(parent.resolve()):
        raise BackupError("unsafe_or_missing_backup_path")
    if path.stat().st_size == 0:
        raise BackupError("empty_backup_file")
    return path


def create_dirs() -> None:
    for path in (BACKUPS, POSTGRES_DIR, QDRANT_DIR, MANIFEST_DIR):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)


def database_metadata() -> dict:
    sql = ("SELECT json_build_object('server_version', current_setting('server_version'), "
           "'database', current_database(), 'database_size_bytes', pg_database_size(current_database()), "
           "'pgvector_version', (SELECT extversion FROM pg_extension WHERE extname='vector'), "
           "'schema_migrations', (SELECT coalesce(json_agg(version ORDER BY version), '[]'::json) "
           "FROM agent_schema_migration))::text")
    shell = ('export PGPASSWORD="$(cat /run/secrets/postgres_password)"; '
             'exec psql -X -A -t -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"')
    return json.loads(compose("exec", "-T", "postgres", "sh", "-c", shell, "sh", sql))


def check_space(database_bytes: int) -> int:
    volume = run("docker", "volume", "inspect", "moodflow-production_qdrant_data",
                 "--format", "{{.Mountpoint}}")
    path = Path(volume).resolve()
    if not path.is_dir() or not path.is_relative_to(Path("/var/lib/docker/volumes")):
        raise BackupError("unexpected_qdrant_volume_path")
    qdrant_bytes = int(run("du", "-sb", str(path)).split()[0])
    # Qdrant creates a snapshot in its own volume before it is copied out.
    # Leave 8 GiB free even if both uncompressed sources double in size.
    required = MIN_FREE_BYTES + 2 * database_bytes + 3 * qdrant_bytes
    free = shutil.disk_usage(BACKUPS).free
    if free < required:
        raise BackupError("insufficient_disk_for_backup")
    return free


def postgres_dump(path: Path) -> None:
    shell = ('export PGPASSWORD="$(cat /run/secrets/postgres_password)"; '
             'exec pg_dump -Fc -Z 6 --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"')
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        compose("exec", "-T", "postgres", "sh", "-c", shell, output_file=stream)
    safe_file(path, POSTGRES_DIR)
    with path.open("rb") as stream:
        compose("exec", "-T", "postgres", "pg_restore", "--list", stdin_file=stream,
                output_file=subprocess.DEVNULL)


def qdrant_snapshot(path: Path, collection: str) -> dict:
    helper = (ROOT / "deploy/backup_qdrant_api.py").read_bytes()
    raw = compose("exec", "-T", "ai-service", "python", "-", collection, input_data=helper)
    metadata = json.loads(raw)
    snapshot = metadata["snapshot"]
    name = snapshot["name"]
    if not SAFE_NAME.fullmatch(name) or metadata["collection"] != collection:
        raise BackupError("invalid_qdrant_snapshot_name")
    container = compose("ps", "-q", "qdrant")
    if not container:
        raise BackupError("qdrant_container_missing")
    source = f"{container}:/qdrant/snapshots/{collection}/{name}"
    run("docker", "cp", source, str(path))
    os.chmod(path, 0o600)
    safe_file(path, QDRANT_DIR)
    if path.stat().st_size != snapshot["size"]:
        raise BackupError("qdrant_snapshot_size_mismatch")
    if snapshot.get("checksum") and sha256(path) != snapshot["checksum"]:
        raise BackupError("qdrant_snapshot_checksum_mismatch")
    return metadata


def wait_pg(container: str) -> None:
    for _ in range(40):
        probe = subprocess.run(("docker", "exec", container, "pg_isready", "-U", "postgres",
                                "-d", "verification"), capture_output=True, cwd=ROOT, check=False)
        if probe.returncode == 0:
            return
        time.sleep(2)
    raise BackupError("isolated_postgres_not_ready")


def verify_postgres(path: Path, image: str, stamp: str) -> dict:
    volume = "moodflow-verify-pg-" + stamp.lower()
    container = volume
    created_volume = False
    created_container = False
    try:
        run("docker", "volume", "create", "--label", "moodflow.verify=" + stamp, volume)
        created_volume = True
        run("docker", "run", "-d", "--name", container, "--network", "none",
            "--mount", f"type=volume,source={volume},target=/var/lib/postgresql/data",
            "--env", "POSTGRES_HOST_AUTH_METHOD=trust", "--env", "POSTGRES_DB=verification", image)
        created_container = True
        wait_pg(container)
        with path.open("rb") as stream:
            run("docker", "exec", "-i", container, "pg_restore", "--exit-on-error", "--no-owner",
                "--no-privileges", "-U", "postgres", "-d", "verification", stdin_file=stream,
                output_file=subprocess.DEVNULL)
        sql = ("SELECT json_build_object('pgvector_version',"
               " (SELECT extversion FROM pg_extension WHERE extname='vector'),"
               " 'agent_session', (SELECT count(*) FROM agent_session),"
               " 'agent_task', (SELECT count(*) FROM agent_task),"
               " 'agent_long_term_memory', (SELECT count(*) FROM agent_long_term_memory),"
               " 'checkpoints', (SELECT count(*) FROM checkpoints),"
               " 'checkpoint_blobs', (SELECT count(*) FROM checkpoint_blobs),"
               " 'checkpoint_writes', (SELECT count(*) FROM checkpoint_writes))::text")
        rows = json.loads(run("docker", "exec", container, "psql", "-X", "-A", "-t",
                             "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "verification", "-c", sql))
        if not rows.get("pgvector_version") or any(rows.get(table) is None for table in CORE_TABLES):
            raise BackupError("isolated_postgres_schema_incomplete")
        return rows
    finally:
        if created_container:
            subprocess.run(("docker", "rm", "-f", container), capture_output=True, cwd=ROOT, check=False)
        if created_volume:
            subprocess.run(("docker", "volume", "rm", volume), capture_output=True, cwd=ROOT, check=False)


def qdrant_schema(info: dict) -> dict:
    result = info["result"]
    params = result["config"]["params"]
    return {"status": result["status"], "points_count": result["points_count"],
            "dense": {name: {"size": value["size"], "distance": value["distance"]}
                      for name, value in params["vectors"].items()},
            "sparse": {name: {"modifier": value.get("modifier")}
                       for name, value in params["sparse_vectors"].items()}}


def verify_qdrant(path: Path, image: str, stamp: str, collection: str, expected: dict) -> dict:
    network = "moodflow-verify-" + stamp.lower()
    volume = "moodflow-verify-qd-" + stamp.lower()
    container = volume
    made_network = made_volume = made_container = False
    try:
        run("docker", "network", "create", "--internal", "--label", "moodflow.verify=" + stamp, network)
        made_network = True
        run("docker", "volume", "create", "--label", "moodflow.verify=" + stamp, volume)
        made_volume = True
        run("docker", "run", "-d", "--name", container, "--network", network, "--no-healthcheck",
            "--mount", f"type=volume,source={volume},target=/qdrant/storage",
            "--mount", f"type=bind,source={path},target=/backup/{path.name},readonly",
            "--entrypoint", "/qdrant/entrypoint.sh", image,
            "--snapshot", f"/backup/{path.name}:{collection}")
        made_container = True
        ip = run("docker", "inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                 container)
        if not ip or not re.fullmatch(r"[0-9.]+", ip):
            raise BackupError("isolated_qdrant_ip_missing")
        url = f"http://{ip}:6333/collections/{collection}"
        info = None
        for _ in range(60):
            try:
                with urlopen(url, timeout=2) as response:
                    info = json.load(response)
                if qdrant_schema(info)["status"] == "green":
                    break
            except (OSError, ValueError, KeyError, URLError):
                pass
            time.sleep(2)
        if info is None:
            raise BackupError("isolated_qdrant_not_ready")
        actual = qdrant_schema(info)
        if actual != expected:
            raise BackupError("isolated_qdrant_schema_or_points_mismatch")
        return actual
    finally:
        if made_container:
            subprocess.run(("docker", "rm", "-f", container), capture_output=True, cwd=ROOT, check=False)
        if made_volume:
            subprocess.run(("docker", "volume", "rm", volume), capture_output=True, cwd=ROOT, check=False)
        if made_network:
            subprocess.run(("docker", "network", "rm", network), capture_output=True, cwd=ROOT, check=False)


def file_entry(path: Path) -> dict:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def save_manifest(path: Path, manifest: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def verified_entry(entry: dict, parent: Path) -> Path:
    path = safe_file(Path(entry["path"]), parent)
    if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
        raise BackupError("backup_file_integrity_failed")
    return path


def retention(now: datetime) -> list[Path]:
    records = []
    for path in MANIFEST_DIR.glob("*.json"):
        if path.is_symlink() or not STAMP.fullmatch(path.stem):
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("status") != "complete" or not manifest.get("restore_verified"):
                continue
            recorded = datetime.fromisoformat(manifest["timestamp_utc"])
            if recorded.tzinfo is None:
                continue
            records.append((recorded, path, manifest))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    records.sort(reverse=True)
    keep: set[Path] = set()
    weeks: set[tuple[int, int]] = set()
    for recorded, path, _ in records:
        age = now - recorded
        if age < timedelta(days=7):
            keep.add(path)
        elif age < timedelta(days=35):
            week = recorded.isocalendar()[:2]
            if week not in weeks and len(weeks) < 4:
                keep.add(path)
                weeks.add(week)
    if records:
        keep.add(records[0][1])
    removed = []
    for _, path, manifest in records:
        if path in keep:
            continue
        try:
            pg = verified_entry(manifest["postgres"], POSTGRES_DIR)
            qd = verified_entry(manifest["qdrant"], QDRANT_DIR)
            # Never delete a path outside the dedicated backup directories.
            if pg.name != path.stem + ".dump" or qd.name != path.stem + ".snapshot":
                continue
            pg.unlink()
            qd.unlink()
            path.unlink()
            removed.append(path)
        except (BackupError, OSError, KeyError, TypeError):
            continue
    return removed


def backup(predeploy: bool) -> Path:
    if fcntl is None or Path.cwd().resolve() != ROOT or os.geteuid() != 0:
        raise BackupError("backup_requires_root_in_production_root")
    update.read_env_paths()
    compose("config", "--quiet")
    create_dirs()
    lock_path = BACKUPS / ".backup.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = datetime.now(timezone.utc)
        commit = run("git", "rev-parse", "HEAD")
        if not update.SHA.fullmatch(commit):
            raise BackupError("invalid_git_commit")
        stamp = now.strftime("%Y%m%dT%H%M%SZ") + "-" + commit[:8]
        if not STAMP.fullmatch(stamp):
            raise BackupError("invalid_backup_stamp")
        pg = POSTGRES_DIR / (stamp + ".dump")
        qd = QDRANT_DIR / (stamp + ".snapshot")
        manifest_path = MANIFEST_DIR / (stamp + ".json")
        if any(path.exists() for path in (pg, qd, manifest_path)):
            raise BackupError("backup_stamp_already_exists")
        meta = database_metadata()
        if not meta.get("pgvector_version"):
            raise BackupError("production_pgvector_missing")
        free_before = check_space(meta["database_size_bytes"])
        compose_config = json.loads(compose("config", "--format", "json"))["services"]
        pg_image = compose_config["postgres"]["image"]
        qd_image = compose_config["qdrant"]["image"]
        collection = json.loads((ROOT / "deploy/frozen-artifacts.json").read_text(encoding="utf-8"))["collection_name"]
        manifest = {"timestamp_utc": now.isoformat(),
                    "timestamp_asia_shanghai": now.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(),
                    "git_commit": commit, "kind": "predeploy" if predeploy else "scheduled_or_manual",
                    "collection": collection, "postgres_metadata": meta,
                    "disk_free_before_bytes": free_before, "status": "running", "restore_verified": False}
        try:
            postgres_dump(pg)
            qmeta = qdrant_snapshot(qd, collection)
            manifest["postgres"] = file_entry(pg)
            manifest["qdrant"] = file_entry(qd)
            manifest["qdrant_schema"] = qmeta["schema"]
            manifest["qdrant_snapshot_origin"] = qmeta["snapshot"]["name"]
            manifest["disk_free_after_backup_bytes"] = shutil.disk_usage(BACKUPS).free
            save_manifest(manifest_path, manifest)
            verification_id = uuid.uuid4().hex[:12]
            pg_rows = verify_postgres(pg, pg_image, verification_id)
            qd_schema = verify_qdrant(qd, qd_image, verification_id, collection, qmeta["schema"])
            verified_entry(manifest["postgres"], POSTGRES_DIR)
            verified_entry(manifest["qdrant"], QDRANT_DIR)
            manifest["postgres_restore"] = pg_rows
            manifest["qdrant_restore"] = qd_schema
            manifest["restore_verified"] = True
            manifest["status"] = "complete"
            manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
            save_manifest(manifest_path, manifest)
            retention(datetime.now(timezone.utc))
            return manifest_path
        except (BackupError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            manifest["status"] = "failed"
            manifest["failure"] = str(exc)
            save_manifest(manifest_path, manifest)
            raise


def verify_existing(path: Path) -> dict:
    if not path.is_file() or not path.resolve().is_relative_to(MANIFEST_DIR.resolve()):
        raise BackupError("manifest_outside_backup_directory")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise BackupError("manifest_not_complete")
    pg = verified_entry(manifest["postgres"], POSTGRES_DIR)
    qd = verified_entry(manifest["qdrant"], QDRANT_DIR)
    config = json.loads(compose("config", "--format", "json"))["services"]
    if not STAMP.fullmatch(path.stem):
        raise BackupError("invalid_backup_stamp")
    verification_id = uuid.uuid4().hex[:12]
    pg_rows = verify_postgres(pg, config["postgres"]["image"], verification_id)
    qd_schema = verify_qdrant(qd, config["qdrant"]["image"], verification_id,
                              manifest["collection"], manifest["qdrant_schema"])
    if pg_rows != manifest["postgres_restore"] or qd_schema != manifest["qdrant_restore"]:
        raise BackupError("isolated_restore_differs_from_manifest")
    return {"postgres": "passed", "qdrant": "passed"}


def main() -> None:
    parser = argparse.ArgumentParser(description="MoodFlow production backup")
    parser.add_argument("--predeploy", action="store_true")
    parser.add_argument("--verify", metavar="MANIFEST")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.verify:
            if args.verify == "latest":
                complete = [path for path in MANIFEST_DIR.glob("*.json")
                            if json.loads(path.read_text(encoding="utf-8")).get("status") == "complete"]
                if not complete:
                    raise BackupError("no_complete_backup")
                path = max(complete, key=lambda p: p.name)
            else:
                path = Path(args.verify)
            print(json.dumps(verify_existing(path), sort_keys=True))
        else:
            # The manifest path must be the final stdout line for update.sh.
            print(backup(args.predeploy))
    except (BackupError, OSError, ValueError, KeyError, json.JSONDecodeError, BlockingIOError) as exc:
        print(f"backup_failed:{exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

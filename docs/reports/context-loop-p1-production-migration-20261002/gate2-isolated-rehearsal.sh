python3 - <<'MF_ISOLATED_REHEARSAL'
"""Run 003/004 only on a newly-created, labelled, network-isolated container."""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PRODUCTION = "moodflow-production-postgres-1"
IMAGE = "pgvector/pgvector:pg16-trixie@sha256:c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b"
BACKUPS = Path("/secure/moodflow/backups")
MANIFEST = BACKUPS / "manifests/20261001T234103Z-3a11237c.json"
REPORTS = Path("/secure/moodflow/migration-rehearsals")
EXPECTED_HISTORY_ROWS = 328
EXPECTED_DATABASE_BYTES = 53894167
LABEL = "moodflow.context-loop-rehearsal"
INNER = "/tmp/moodflow-context-loop"
MIGRATIONS = {
    "003_context_loop_history.sql": {
        "base64": "LS0gTmV3IGV2ZW50cyBoYXZlIGEgZGV0ZXJtaW5pc3RpYyByZXBsYXkga2V5IGFuZCBzdGFibGUgZGF0YWJhc2Ugb3JkZXIuCi0tIE5vIFVQREFURSBvZiBpbW11dGFibGUgZXZlbnQgYm9kaWVzLCBub3IgYnJvYWRlciBjaGVja3BvaW50IGRlc2VyaWFsaXphdGlvbi4KQUxURVIgVEFCTEUgYWdlbnRfaGlzdG9yeV9ldmVudCBBREQgQ09MVU1OIElGIE5PVCBFWElTVFMgZXZlbnRfa2V5IFZBUkNIQVIoMTYwKTsKQUxURVIgVEFCTEUgYWdlbnRfaGlzdG9yeV9ldmVudCBBREQgQ09MVU1OIElGIE5PVCBFWElTVFMgZXZlbnRfc2VxIEJJR1NFUklBTDsKQ1JFQVRFIFVOSVFVRSBJTkRFWCBJRiBOT1QgRVhJU1RTIGFnZW50X2hpc3RvcnlfZXZlbnRfa2V5CiAgICBPTiBhZ2VudF9oaXN0b3J5X2V2ZW50KGV2ZW50X2tleSk7CkNSRUFURSBVTklRVUUgSU5ERVggSUYgTk9UIEVYSVNUUyBhZ2VudF9oaXN0b3J5X2V2ZW50X3NlcQogICAgT04gYWdlbnRfaGlzdG9yeV9ldmVudChldmVudF9zZXEpOwpDUkVBVEUgSU5ERVggSUYgTk9UIEVYSVNUUyBhZ2VudF9oaXN0b3J5X3Rhc2tfc2VxCiAgICBPTiBhZ2VudF9oaXN0b3J5X2V2ZW50KHRhc2tfaWQsIGV2ZW50X3NlcSk7Cg==",
        "sha256": "db21752bcb86affae039634342e5b6df8ec113053aa5ecb213f07c854acb67b3"
    },
    "004_continuity_execution.sql": {
        "base64": "LS0gTWFudWFsIHByb2R1Y3Rpb24gbWlncmF0aW9uIG9ubHkuIFN0YXJ0dXAgbXVzdCBuZXZlciBleGVjdXRlIHRoaXMgZmlsZS4KQ1JFQVRFIFRBQkxFIElGIE5PVCBFWElTVFMgYWdlbnRfZXhlY3V0aW9uICgKICAgIGV4ZWN1dGlvbl9rZXkgVEVYVCBQUklNQVJZIEtFWSwKICAgIGF1dGhfc2NvcGUgVEVYVCBOT1QgTlVMTCwKICAgIG93bmVyIFRFWFQsCiAgICBmZW5jZSBCSUdJTlQgTk9UIE5VTEwgREVGQVVMVCAwLAogICAgcmV2aXNpb24gQklHSU5UIE5PVCBOVUxMIERFRkFVTFQgMCwKICAgIGV4cGlyZXNfYXQgRE9VQkxFIFBSRUNJU0lPTiBOT1QgTlVMTCBERUZBVUxUIDAKKTsK",
        "sha256": "5622723c341f6782085039ad2d0872d35ed93303a6f1114d7878e96eb0a388d2"
    }
}

CORE = ("agent_session", "agent_task", "agent_long_term_memory",
        "checkpoints", "checkpoint_blobs", "checkpoint_writes")
OLD_LEDGER = ["001_agent_continuity.sql", "002_long_term_memory.sql"]
NEW_LEDGER = OLD_LEDGER + list(MIGRATIONS)


class Stop(RuntimeError):
    pass


def run(args, data=None, stream=None, timeout=90, check=True):
    result = subprocess.run(args, input=data, stdin=stream, capture_output=True,
                            timeout=timeout, check=False)
    if check and result.returncode:
        raise Stop("command_failed_exit_" + str(result.returncode))
    return result


def text(args, **kwargs):
    return run(args, **kwargs).stdout.decode("utf-8").strip()


def checked_backup(manifest_path=MANIFEST, root=BACKUPS):
    if (manifest_path.is_symlink() or not manifest_path.is_file()
            or not manifest_path.resolve().is_relative_to((root/"manifests").resolve())):
        raise Stop("unsafe_manifest")
    item = json.loads(manifest_path.read_text(encoding="utf-8"))
    if item.get("status") != "complete" or item.get("restore_verified") is not True:
        raise Stop("backup_not_verified")
    if item["postgres_metadata"]["database"] != "moodflow_agent":
        raise Stop("backup_database_mismatch")
    if item["postgres_metadata"]["database_size_bytes"] != EXPECTED_DATABASE_BYTES:
        raise Stop("backup_source_size_changed")
    dump = Path(item["postgres"]["path"])
    if (dump.is_symlink() or not dump.is_file()
            or not dump.resolve().is_relative_to((root/"postgres").resolve())
            or dump.name != manifest_path.stem + ".dump"):
        raise Stop("unsafe_dump")
    raw_hash = hashlib.sha256()
    with dump.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise Stop("invalid_archive")
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            raw_hash.update(chunk)
    if (dump.stat().st_size <= 0 or dump.stat().st_size != item["postgres"]["bytes"]
            or raw_hash.hexdigest() != item["postgres"]["sha256"]):
        raise Stop("backup_integrity_failed")
    return item, dump


def decoded_migrations():
    files = {}
    for name, item in MIGRATIONS.items():
        raw = base64.b64decode(item["base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise Stop("migration_hash_mismatch")
        files[name] = raw
    return files


def check_scale(restored, manifest):
    if (restored["database"] != "moodflow_agent" or restored["history_rows"] != EXPECTED_HISTORY_ROWS
            or restored["ledger"] != OLD_LEDGER or restored["execution_exists"] or restored["event_seq_exists"]):
        raise Stop("restored_scale_or_schema_mismatch")
    if restored["vector"] != manifest["postgres_restore"]["pgvector_version"]:
        raise Stop("restored_vector_mismatch")
    if any(restored[t] != manifest["postgres_restore"][t] for t in CORE):
        raise Stop("restored_core_counts_mismatch")


def inspect_owner(kind, name, token):
    if not re.fullmatch(r"mf-context-rehearsal-[0-9a-f]{12}", name):
        raise Stop("unsafe_resource_name")
    fmt = "{{json .Config.Labels}}" if kind == "container" else "{{json .Labels}}"
    labels = json.loads(text(["docker", kind, "inspect", "--format", fmt, name]))
    if labels.get(LABEL) != token:
        raise Stop("resource_ownership_mismatch")


def cleanup_resources(name, token, made_container, made_volume):
    if made_container:
        inspect_owner("container", name, token)
        text(["docker", "rm", "-f", name])
    if made_volume:
        inspect_owner("volume", name, token)
        text(["docker", "volume", "rm", name])


class Lab:
    def __init__(self, name, token):
        self.name, self.token = name, token

    def command(self, *args, data=None, stream=None, check=True, timeout=90):
        inspect_owner("container", self.name, self.token)
        return run(["docker", "exec", "-i", self.name, *args],
                   data=data, stream=stream, check=check, timeout=timeout)

    def write(self, name, raw):
        if name not in ("service.conf", "empty.pgpass", *MIGRATIONS.keys()):
            raise Stop("unsafe_lab_file")
        self.command("sh", "-c", "umask 077; cat > " + INNER + "/" + name + "; chmod 400 " + INNER + "/" + name, data=raw)

    def sql(self, source, check=True):
        inspect_owner("container", self.name, self.token)
        return run(["docker", "exec", "-i", "-e", "PGSERVICEFILE=" + INNER + "/service.conf",
                    "-e", "PGPASSFILE=" + INNER + "/empty.pgpass", "-e", "PGPASSWORD=",
                    "-e", "PGOPTIONS=", self.name, "psql",
                    "service=moodflow-approved-migration", "-X", "-w", "-q", "-A", "-t",
                    "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=sqlstate",
                    "-v", "SHOW_CONTEXT=never", "-P", "pager=off"],
                   data=source.encode(), check=check, timeout=150)

    def metadata(self):
        pairs = ", ".join("'" + t + "', (SELECT count(*) FROM " + t + ")" for t in CORE)
        sql = """BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object(
'database', current_database(), 'database_bytes', pg_database_size(current_database()),
'history_rows', (SELECT count(*) FROM agent_history_event),
'history_bytes', pg_total_relation_size('agent_history_event'),
'ledger', (SELECT json_agg(version ORDER BY version) FROM agent_schema_migration),
'execution_exists', to_regclass('public.agent_execution') IS NOT NULL,
'event_seq_exists', EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='agent_history_event' AND column_name='event_seq'),
'vector', (SELECT extversion FROM pg_extension WHERE extname='vector'), """ + pairs + """);
ROLLBACK;
"""
        return json.loads(self.sql(sql).stdout)

    def disk(self):
        data_bytes = self.command("du", "-sb", "/var/lib/postgresql/data").stdout.decode().split()[0]
        wal_bytes = self.command("du", "-sb", "/var/lib/postgresql/data/pg_wal").stdout.decode().split()[0]
        return {"data_directory_apparent_bytes": int(data_bytes), "wal_directory_apparent_bytes": int(wal_bytes)}


def guarded(statement):
    return statement + "\n" + r"""\if :ERROR
ROLLBACK;
\echo REHEARSAL_TRANSACTION_ROLLED_BACK
\q
\endif
"""


def migration_sql():
    sql = r"""\set ON_ERROR_STOP off
\timing on
"""
    sql += guarded("SELECT clock_timestamp() AS tx_started \\gset")
    sql += guarded("BEGIN;")
    sql += guarded("SET LOCAL lock_timeout='5s';")
    sql += guarded("SET LOCAL statement_timeout='60s';")
    sql += guarded("SELECT pg_advisory_xact_lock(hashtext('moodflow_agent_schema_migrations'));")
    sql += guarded("SELECT clock_timestamp() AS lock_window_started, pg_current_wal_insert_lsn() AS wal_before, pg_database_size(current_database()) AS database_before \\gset")
    for name in MIGRATIONS:
        sql += guarded("\\i " + INNER + "/" + name)
    sql += guarded("INSERT INTO agent_schema_migration(version) VALUES ('003_context_loop_history.sql'), ('004_continuity_execution.sql') ON CONFLICT(version) DO NOTHING;")
    sql += guarded("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() AND relation='agent_history_event'::regclass AND mode='AccessExclusiveLock' AND granted) AS history_locked \\gset")
    sql += r"""\echo HISTORY_ACCESS_EXCLUSIVE_HELD=:history_locked
\if :history_locked
\else
ROLLBACK;
\echo REHEARSAL_TRANSACTION_ROLLED_BACK
\q
\endif
"""
    sql += guarded("COMMIT;")
    sql += guarded("SELECT clock_timestamp() AS tx_completed, pg_current_wal_insert_lsn() AS wal_after \\gset")
    sql += r"""\set ON_ERROR_STOP on
\timing off
SELECT json_build_object(
'transaction_elapsed_ms', round(extract(epoch FROM (:'tx_completed'::timestamptz-:'tx_started'::timestamptz))*1000,3),
'lock_window_upper_bound_ms', round(extract(epoch FROM (:'tx_completed'::timestamptz-:'lock_window_started'::timestamptz))*1000,3),
'wal_bytes', pg_wal_lsn_diff(:'wal_after'::pg_lsn, :'wal_before'::pg_lsn),
'database_before_bytes', :'database_before'::bigint,
'database_after_bytes', pg_database_size(current_database()));
\echo REHEARSAL_TRANSACTION_COMMITTED
"""
    return sql


POSTCHECK_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT version FROM agent_schema_migration
WHERE version IN ('003_context_loop_history.sql','004_continuity_execution.sql');
SELECT to_regclass('public.agent_execution');
SELECT COUNT(*) AS missing_sequence FROM agent_history_event WHERE event_seq IS NULL;
SELECT event_key, COUNT(*) FROM agent_history_event
WHERE event_key IS NOT NULL GROUP BY event_key HAVING COUNT(*) > 1;
SELECT event_seq, COUNT(*) FROM agent_history_event
GROUP BY event_seq HAVING COUNT(*) > 1;
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgrelid='agent_history_event'::regclass AND NOT tgisinternal;
SELECT json_build_object(
'missing_sequence', (SELECT count(*) FROM agent_history_event WHERE event_seq IS NULL),
'duplicate_key_groups', (SELECT count(*) FROM (SELECT event_key FROM agent_history_event WHERE event_key IS NOT NULL GROUP BY event_key HAVING count(*)>1) x),
'duplicate_sequence_groups', (SELECT count(*) FROM (SELECT event_seq FROM agent_history_event GROUP BY event_seq HAVING count(*)>1) x),
'immutable_trigger_enabled', EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='agent_history_event'::regclass AND tgname='agent_history_event_immutable' AND tgenabled='O'),
'sequence_usage', has_sequence_privilege(current_user, pg_get_serial_sequence('agent_history_event','event_seq'), 'USAGE'),
'execution_select', has_table_privilege(current_user,'agent_execution','SELECT'),
'execution_insert', has_table_privilege(current_user,'agent_execution','INSERT'),
'execution_update', has_table_privilege(current_user,'agent_execution','UPDATE'));
ROLLBACK;
"""


def main():
    if os.geteuid() != 0:
        raise Stop("root_terminal_required")
    manifest, dump = checked_backup()
    files = decoded_migrations()
    if text(["docker", "inspect", "--format", "{{.Config.Image}}", PRODUCTION]) != IMAGE:
        raise Stop("production_image_changed")
    text(["docker", "image", "inspect", "--format", "{{.Id}}", IMAGE])
    if shutil.disk_usage("/var/lib/docker").free < 8*1024**3 + 4*EXPECTED_DATABASE_BYTES:
        raise Stop("insufficient_disk_for_rehearsal")
    mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    if int(mem["MemAvailable"].split()[0])*1024 < 2*1024**3:
        raise Stop("insufficient_available_memory")
    if REPORTS.is_symlink():
        raise Stop("unsafe_report_directory")
    REPORTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(REPORTS, 0o700)
    folder = Path(tempfile.mkdtemp(prefix="context-loop-20261002-", dir=REPORTS))
    token = uuid.uuid4().hex[:12]
    name = "mf-context-rehearsal-" + token
    made_volume = made_container = False
    report = {"status":"running", "started_at_utc":datetime.now(timezone.utc).isoformat(),
              "backup_manifest":MANIFEST.name, "container":name, "image":IMAGE,
              "production_modified":False, "production_migrations_executed":False,
              "lock_timeout":"5s", "statement_timeout":"60s",
              "resource_limits":{"cpus":0.5,"memory_bytes":1024**3},
              "replication_lag_seconds":None,
              "replication_note":"no_senders_or_slots_observed_in_production_preflight; isolated_container_has_no_replica",
              "migration_sha256":{n:v["sha256"] for n,v in MIGRATIONS.items()}}
    try:
        print("STAGE=creating_isolated_container", flush=True)
        for kind in ("container", "volume"):
            if run(["docker", kind, "inspect", name], check=False).returncode == 0:
                raise Stop("resource_name_collision")
        text(["docker", "volume", "create", "--label", LABEL+"="+token, name])
        made_volume = True
        inspect_owner("volume", name, token)
        text(["docker", "run", "-d", "--pull", "never", "--name", name, "--network", "none",
              "--label", LABEL+"="+token, "--cpus", "0.5", "--memory", "1g", "--pids-limit", "128",
              "--mount", "type=volume,source="+name+",target=/var/lib/postgresql/data",
              "--env", "POSTGRES_HOST_AUTH_METHOD=trust", "--env", "POSTGRES_USER=moodflow",
              "--env", "POSTGRES_DB=moodflow_agent", IMAGE])
        made_container = True
        lab = Lab(name, token)
        for _ in range(45):
            if lab.command("sh", "-c", '[ "$(cat /proc/1/comm)" = postgres ] && pg_isready -U moodflow -d moodflow_agent', check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise Stop("isolated_database_not_ready")
        print("STAGE=restoring_verified_backup", flush=True)
        started = time.monotonic()
        with dump.open("rb") as stream:
            lab.command("pg_restore", "--exit-on-error", "--no-owner", "--no-privileges",
                        "-U", "moodflow", "-d", "moodflow_agent", stream=stream, timeout=150)
        report["restore_wall_seconds"] = round(time.monotonic()-started, 6)
        lab.command("mkdir", "-m", "700", INNER)
        lab.write("service.conf", b"[moodflow-approved-migration]\nhost=/var/run/postgresql\nport=5432\ndbname=moodflow_agent\nuser=moodflow\nconnect_timeout=5\n")
        lab.write("empty.pgpass", b"")
        for file_name, raw in files.items():
            (folder/file_name).write_bytes(raw)
            lab.write(file_name, raw)
            actual = lab.command("sha256sum", INNER+"/"+file_name).stdout.decode().split()[0]
            if actual != MIGRATIONS[file_name]["sha256"]:
                raise Stop("lab_migration_file_integrity_failed")
        before = lab.metadata()
        check_scale(before, manifest)
        report["restored_before"] = before
        report["disk_before"] = lab.disk()
        report["host_free_before_bytes"] = shutil.disk_usage("/var/lib/docker").free
        print("RESTORED_BEFORE=" + json.dumps(before), flush=True)
        print("STAGE=running_003_004_in_isolated_transaction", flush=True)
        started = time.monotonic()
        result = lab.sql(migration_sql(), check=False)
        report["migration_wall_seconds"] = round(time.monotonic()-started, 6)
        output = result.stdout.decode("utf-8", "replace")
        (folder/"migration-output.txt").write_text(output, encoding="utf-8")
        if result.returncode or "REHEARSAL_TRANSACTION_COMMITTED" not in output or result.stderr:
            report["rollback_ledger_check"] = lab.metadata()
            raise Stop("rehearsal_transaction_failed_no_retry")
        metrics = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        if len(metrics) != 1:
            raise Stop("missing_rehearsal_metrics")
        report["metrics"] = metrics[0]
        report["restored_after"] = lab.metadata()
        if (report["restored_after"]["ledger"] != NEW_LEDGER or not report["restored_after"]["execution_exists"] or report["restored_after"]["history_rows"] != before["history_rows"]):
            raise Stop("postcheck_ledger_mismatch")
        checks = lab.sql(POSTCHECK_SQL).stdout.decode()
        (folder/"readonly-postchecks.txt").write_text(checks, encoding="utf-8")
        final = json.loads(next(line for line in reversed(checks.splitlines()) if line.startswith("{")))
        if (any(final[k] != 0 for k in ("missing_sequence","duplicate_key_groups","duplicate_sequence_groups"))
                or not all(final[k] for k in ("immutable_trigger_enabled","sequence_usage","execution_select","execution_insert","execution_update"))):
            raise Stop("postcheck_failed")
        report["readonly_postchecks"] = final
        report["disk_after"] = lab.disk()
        report["host_free_after_bytes"] = shutil.disk_usage("/var/lib/docker").free
        report["status"] = "passed"
    except (Stop, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        report["failure_reason"] = str(exc) if isinstance(exc, Stop) else type(exc).__name__
        print("STOP: rehearsal_failed_no_production_migration_no_retry", flush=True)
    finally:
        try:
            cleanup_resources(name, token, made_container, made_volume)
            report["cleanup"] = "passed"
        except (Stop, OSError, ValueError, subprocess.TimeoutExpired):
            report["cleanup"] = "failed"
            report["status"] = "failed"
            print("STOP: task_resource_cleanup_needs_review", flush=True)
        report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        (folder/"report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
        print("REPORT_PATH=" + str(folder/"report.json"), flush=True)
        print("REHEARSAL_RESULT=" + json.dumps(report, sort_keys=True), flush=True)
        if report["status"] == "passed":
            print("ISOLATED_REHEARSAL_FINISHED; production_migration_not_executed", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    os.umask(0o077)
    try:
        raise SystemExit(main())
    except (Stop, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        print("STOP: rehearsal_precondition_failed_no_production_migration")
        raise SystemExit(1)
MF_ISOLATED_REHEARSAL

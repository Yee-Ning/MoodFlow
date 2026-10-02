bash <<'MF_REHEARSAL_PREFLIGHT'
set -eu
df -B1 /secure/moodflow/backups /var/lib/docker
free -b
printf 'AVAILABLE_CPUS='
nproc
docker inspect --format 'POSTGRES_IMAGE={{.Config.Image}}' moodflow-production-postgres-1
docker stats --no-stream --format '{{.Name}} CPU={{.CPUPerc}} MEMORY={{.MemUsage}}' moodflow-production-postgres-1

python3 - <<'MF_SOURCE'
import hashlib, json
from pathlib import Path
manifest = Path("/secure/moodflow/backups/manifests/20261001T234103Z-3a11237c.json")
data = json.loads(manifest.read_text(encoding="utf-8"))
size = data["postgres_metadata"]["database_size_bytes"]
if type(size) is not int or size <= 0:
    raise SystemExit("STOP: invalid backup database size")
print("BACKUP_DATABASE_SIZE_BYTES=" + str(size))
for name in ("003_context_loop_history.sql", "004_continuity_execution.sql"):
    path = Path("/opt/moodflow/ai-service/migrations/postgresql") / name
    print(name + "_SHA256=" + (hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"))
MF_SOURCE

docker exec -i moodflow-production-postgres-1 sh -s <<'MF_PG'
set -eu
if [ "${POSTGRES_DB:-}" != "moodflow_agent" ]; then
  printf 'STOP: configured database mismatch.\n' >&2
  exit 1
fi
umask 077
task_dir=$(mktemp -d /tmp/moodflow-rehearsal-check.XXXXXX)
trap 'rm -f -- "$task_dir/service.conf" "$task_dir/empty.pgpass"; rmdir -- "$task_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
: > "$task_dir/empty.pgpass"
unset PGPASSWORD PGOPTIONS
printf '[moodflow-approved-migration]\nhost=/var/run/postgresql\nport=5432\ndbname=moodflow_agent\nuser=moodflow\nconnect_timeout=5\noptions=-c default_transaction_read_only=on\n' > "$task_dir/service.conf"
PGSERVICEFILE="$task_dir/service.conf" PGPASSFILE="$task_dir/empty.pgpass" psql "service=moodflow-approved-migration" -X -w -v ON_ERROR_STOP=1 -P pager=off <<'MF_SQL'
\timing on
BEGIN READ ONLY;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='10s';
SELECT current_database(), current_user, clock_timestamp(),
       current_setting('transaction_read_only') AS read_only,
       pg_is_in_recovery() AS is_standby,
       pg_database_size(current_database()) AS database_bytes;
SELECT count(*) AS history_rows,
       pg_total_relation_size('public.agent_history_event') AS history_total_bytes
FROM public.agent_history_event;
SELECT count(*) AS replication_senders FROM pg_stat_replication;
SELECT state, sync_state, write_lag, flush_lag, replay_lag
FROM pg_stat_replication;
SELECT count(*) AS replication_slots,
       count(*) FILTER (WHERE active) AS active_slots
FROM pg_replication_slots;
SELECT version FROM public.agent_schema_migration ORDER BY version;
ROLLBACK;
MF_SQL
MF_PG
printf 'REHEARSAL_PREFLIGHT_READONLY_FINISHED; no_restore_or_migration_run\n'
MF_REHEARSAL_PREFLIGHT

# Paste after reconnecting to the normal SSH Shell. No migration or retry.
docker ps --filter name=moodflow-production-postgres-1 --format '{{.Names}} {{.Status}}'
docker exec -i moodflow-production-postgres-1 sh -s <<'MF_STATE_CHECK'
set -eu
umask 077
task_dir=$(mktemp -d /tmp/mf-state-check-XXXXXX)
trap 'rm -f "$task_dir/service.conf" "$task_dir/empty.pgpass"; rmdir "$task_dir"' EXIT
: > "$task_dir/empty.pgpass"
printf '[moodflow-approved-migration]\nhost=/var/run/postgresql\ndbname=moodflow_agent\nuser=moodflow\nconnect_timeout=5\noptions=-c default_transaction_read_only=on\n' > "$task_dir/service.conf"
unset PGPASSWORD PGOPTIONS
PGSERVICEFILE="$task_dir/service.conf" PGPASSFILE="$task_dir/empty.pgpass" \
psql 'service=moodflow-approved-migration' -X -w -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -P pager=off <<'MF_STATE_SQL'
BEGIN READ ONLY;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='10s';
SELECT current_database(),current_user,current_setting('transaction_read_only') AS read_only,clock_timestamp();
SELECT version,applied_at FROM agent_schema_migration WHERE version IN ('001_agent_continuity.sql','002_long_term_memory.sql','003_context_loop_history.sql','004_continuity_execution.sql') ORDER BY version;
SELECT to_regclass('public.agent_execution') AS execution_table;
SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='agent_history_event' AND column_name IN ('event_key','event_seq') ORDER BY column_name;
SELECT count(*) AS other_active_psql_sessions FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND application_name='psql' AND state IS DISTINCT FROM 'idle';
SELECT count(*) AS other_advisory_locks FROM pg_locks WHERE locktype='advisory' AND pid<>pg_backend_pid() AND database=(SELECT oid FROM pg_database WHERE datname=current_database());
ROLLBACK;
MF_STATE_SQL
MF_STATE_CHECK

python3 - <<'MF_REPORT_CHECK'
import json,re
from pathlib import Path
def show(label,path,keys):
    try:
        if path.is_symlink(): raise ValueError()
        item=json.loads(path.read_text(encoding='utf-8'))
        safe={k:item[k] for k in keys if k in item}
        for k,v in list(safe.items()):
            if not isinstance(v,(bool,type(None))) and not (isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_:.+\-]+',v)):
                safe[k]='redacted'
        print(label+'='+json.dumps(safe))
    except (OSError,ValueError,TypeError): print(label+'=missing_or_unreadable')
lab=Path('/secure/moodflow/migration-rehearsals/context-loop-20261002-yopxiydv/report.json')
show('REHEARSAL_STATUS',lab,('status','cleanup','completed_at_utc'))
root=Path('/secure/moodflow/migration-executions')
folders=sorted((p for p in root.glob('context-loop-20261002-*') if p.is_dir() and not p.is_symlink()),key=lambda p:p.stat().st_mtime)
print('EXECUTION_REPORT_DIRECTORIES='+str(len(folders)))
for folder in folders[-3:]:
    print('REPORT_FOLDER='+folder.name)
    show('EXECUTION_STATUS',folder/'report.json',('status','reason','production_transaction_attempted','production_commit_confirmed','temporary_profile_cleanup','completed_at_utc'))
print('READONLY_INTERRUPTION_CHECK_FINISHED; no_migration_or_retry')
MF_REPORT_CHECK

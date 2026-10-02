"""One approved attempt: quiet checks, 003/004 transaction, read-only verification."""
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import rehearsal_runner as r

REHEARSAL = Path('/secure/moodflow/migration-rehearsals/context-loop-20261002-yopxiydv/report.json')
REPORTS = Path('/secure/moodflow/migration-executions')
AI = 'moodflow-production-ai-service-1'

# Only counts are returned. No messages, run identifiers, queries, or blob contents.
# Historical writes are excluded unless they belong to the latest checkpoint.
GATE_QUERY = """WITH latest AS (
 SELECT DISTINCT ON(thread_id,checkpoint_ns) * FROM checkpoints
 ORDER BY thread_id,checkpoint_ns,checkpoint_id DESC
), progress AS (
 SELECT DISTINCT ON(task_id,session_id,body#>>'{payload,run,run_id}')
 body#>'{payload,run}' AS run FROM agent_history_event
 WHERE event_type='run_progress'
 ORDER BY task_id,session_id,body#>>'{payload,run,run_id}',event_timestamp DESC,event_id DESC
), controls AS (
 SELECT c.checkpoint, v.key, v.value, b.type, b.blob,
 CASE WHEN v.key='__start__' THEN '__start__'
 ELSE regexp_replace(v.key, '^.*:', '') END AS node
 FROM latest c CROSS JOIN LATERAL jsonb_each_text(c.checkpoint->'channel_versions') v
 LEFT JOIN checkpoint_blobs b ON b.thread_id=c.thread_id
 AND b.checkpoint_ns=c.checkpoint_ns AND b.channel=v.key AND b.version=v.value
 WHERE v.key LIKE 'branch:%' OR v.key LIKE 'start:%' OR v.key LIKE 'join:%'
 OR v.key IN ('__start__','__pregel_tasks')
)
SELECT json_build_object(
 'database',current_database(),'role',current_user,'checked_at',clock_timestamp(),
 'readonly',current_setting('transaction_read_only'), 'standby',pg_is_in_recovery(),
 'ledger',(SELECT json_agg(version ORDER BY version) FROM agent_schema_migration),
 'history_rows',(SELECT count(*) FROM agent_history_event),
 'database_bytes',pg_database_size(current_database()),
 'execution_exists',to_regclass('public.agent_execution') IS NOT NULL,
 'event_seq_exists',EXISTS(SELECT 1 FROM information_schema.columns
 WHERE table_schema='public' AND table_name='agent_history_event' AND column_name IN ('event_seq','event_key')),
 'immutable_enabled',EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='agent_history_event'::regclass
 AND tgname='agent_history_event_immutable' AND tgenabled='O'),
 'active_sessions',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()
 AND pid<>pg_backend_pid() AND backend_type='client backend' AND
 (state IN ('active','idle in transaction','idle in transaction (aborted)') OR state IS NULL)),
 'advisory_locks',(SELECT count(*) FROM pg_locks WHERE locktype='advisory'
 AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid()),
 'active_runs',(SELECT count(*) FROM progress WHERE run->>'status' IN ('running','finalizing')),
 'pending_runs',(SELECT count(*) FROM progress WHERE
 CASE WHEN jsonb_typeof(run->'pending_task_ids')='array'
 THEN jsonb_array_length(run->'pending_task_ids')>0 ELSE true END),
 'unknown_runs',(SELECT count(*) FROM progress WHERE coalesce(run->>'run_id','')=''
 OR coalesce(run->>'status','') NOT IN ('running','finalizing','completed','needs_input','failed')),
 'latest_pending_writes',(SELECT count(*) FROM checkpoint_writes w JOIN latest c
 ON c.thread_id=w.thread_id AND c.checkpoint_ns=w.checkpoint_ns AND c.checkpoint_id=w.checkpoint_id
 WHERE w.channel<>'__no_writes__'),
 'legacy_pending_sends',(SELECT count(*) FROM checkpoint_writes w JOIN latest c
 ON c.thread_id=w.thread_id AND c.checkpoint_ns=w.checkpoint_ns AND c.parent_checkpoint_id=w.checkpoint_id
 WHERE w.channel='__pregel_tasks' AND coalesce((c.checkpoint->>'v')::int,0)<4),
 'pending_controls',(SELECT count(*) FROM controls WHERE type<>'empty' AND
 CASE WHEN key='__pregel_tasks' THEN encode(blob,'hex') NOT IN ('90','c0','5b5d','6e756c6c')
 ELSE coalesce(checkpoint#>>ARRAY['versions_seen',node,key],'')<>value END),
 'unknown_checkpoints',(SELECT count(*) FROM latest WHERE
 coalesce(jsonb_typeof(checkpoint->'channel_versions'),'')<>'object'
 OR coalesce(jsonb_typeof(checkpoint->'versions_seen'),'')<>'object') +
 (SELECT count(*) FROM controls WHERE type IS NULL OR (type<>'empty' AND blob IS NULL)),
 'replication_senders',(SELECT count(*) FROM pg_stat_replication),
 'replication_slots',(SELECT count(*) FROM pg_replication_slots));
"""

NETWORK_PROBE = """import json
from pathlib import Path
listen=active=0
for name in ('tcp','tcp6'):
 for line in Path('/proc/net/'+name).read_text().splitlines()[1:]:
  fields=line.split()
  if int(fields[1].split(':')[1],16)==8001:
   listen+=fields[3]=='0A'
   active+=fields[3]=='01'
print(json.dumps({'ai_listeners':listen,'ai_inbound_established':active}))
"""


def checked_rehearsal(path=REHEARSAL):
    if path.is_symlink() or not path.is_file():
        raise r.Stop('rehearsal_report_missing_or_unsafe')
    item = json.loads(path.read_text(encoding='utf-8'))
    if (item.get('status') != 'passed' or item.get('cleanup') != 'passed'
            or item.get('backup_manifest') != r.MANIFEST.name
            or item.get('image') != r.IMAGE
            or item.get('migration_sha256') != {n:v['sha256'] for n,v in r.MIGRATIONS.items()}
            or item.get('lock_timeout') != '5s' or item.get('statement_timeout') != '60s'):
        raise r.Stop('rehearsal_evidence_mismatch')
    if (item['restored_before']['history_rows'] != r.EXPECTED_HISTORY_ROWS
            or item['restored_before']['ledger'] != r.OLD_LEDGER
            or item['restored_after']['ledger'] != r.NEW_LEDGER
            or not 0 < item['metrics']['lock_window_upper_bound_ms'] < 1000
            or not 0 < item['metrics']['transaction_elapsed_ms'] < 1000):
        raise r.Stop('rehearsal_scale_or_timeout_not_confirmed')
    check_postchecks(item['readonly_postchecks'])
    return item


def check_postchecks(item):
    if (any(item[k] != 0 for k in ('missing_sequence','duplicate_key_groups','duplicate_sequence_groups'))
            or not all(item[k] is True for k in ('immutable_trigger_enabled','sequence_usage',
                                               'execution_select','execution_insert','execution_update'))):
        raise r.Stop('readonly_postcheck_failed')


def check_gate(item):
    if (item['database'] != 'moodflow_agent' or item['role'] != 'moodflow'
            or item['readonly'] != 'on' or item['standby'] is not False
            or item['ledger'] != r.OLD_LEDGER or item['execution_exists']
            or item['event_seq_exists'] or item['immutable_enabled'] is not True):
        raise r.Stop('production_prerequisite_changed_no_migration')
    if (item['history_rows'] != r.EXPECTED_HISTORY_ROWS
            or not 0.9*r.EXPECTED_DATABASE_BYTES <= item['database_bytes'] <= 1.1*r.EXPECTED_DATABASE_BYTES):
        raise r.Stop('production_scale_changed_rehearsal_needed')
    for key in ('active_sessions','advisory_locks','active_runs','pending_runs','unknown_runs',
                'latest_pending_writes','legacy_pending_sends','pending_controls','unknown_checkpoints',
                'replication_senders','replication_slots','ai_inbound_established'):
        if item[key] != 0:
            raise r.Stop('gate_3_not_quiet_'+key+'_no_migration')
    if item['ai_listeners'] < 1:
        raise r.Stop('ai_service_activity_not_observable_no_migration')


class Production:
    def __init__(self, inner):
        self.inner = inner

    def sql(self, source, check=True):
        return r.run(['docker','exec','-i','-e','PGSERVICEFILE='+self.inner+'/service.conf',
                      '-e','PGPASSFILE='+self.inner+'/empty.pgpass','-e','PGPASSWORD=',
                      '-e','PGOPTIONS=',r.PRODUCTION,'timeout','--signal=TERM','--kill-after=5','240',
                      'psql','service=moodflow-approved-migration',
                      '-X','-w','-q','-A','-t','-v','ON_ERROR_STOP=1','-v','VERBOSITY=sqlstate',
                      '-v','SHOW_CONTEXT=never','-P','pager=off'],data=source.encode(),check=check,timeout=260)

    def gate(self):
        item = json.loads(self.sql("BEGIN READ ONLY; SET LOCAL statement_timeout='10s';\n"+GATE_QUERY+'\nROLLBACK;').stdout)
        item.update(json.loads(r.run(['docker','exec','-i',AI,'python','-I','-'],data=NETWORK_PROBE.encode()).stdout))
        return item

    def ledger(self):
        source = """BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object('ledger',(SELECT json_agg(version ORDER BY version) FROM agent_schema_migration),
'execution_exists',to_regclass('public.agent_execution') IS NOT NULL,
'event_seq_exists',EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public'
AND table_name='agent_history_event' AND column_name='event_seq'));
ROLLBACK;
"""
        return json.loads(self.sql(source).stdout)


def guarded(statement):
    return statement + '\n' + r'''\if :ERROR
ROLLBACK;
\echo PRODUCTION_TRANSACTION_ROLLED_BACK
\q
\endif
'''


def migration_sql(inner):
    sql = r'''\set ON_ERROR_STOP off
\timing on
'''
    # BEGIN overrides the profile's default read-only mode only for this transaction.
    for statement in ("SELECT clock_timestamp() AS tx_started \\gset", 'BEGIN READ WRITE;',
                      "SET LOCAL lock_timeout='5s';", "SET LOCAL statement_timeout='60s';",
                      "SELECT pg_advisory_xact_lock(hashtext('moodflow_agent_schema_migrations'));"):
        sql += guarded(statement)
    # Recheck database activity and pending state inside the same transaction.
    gate = GATE_QUERY.replace("'readonly',current_setting('transaction_read_only')", "'readonly','on'")
    sql += guarded(gate.replace('SELECT json_build_object(', 'SELECT json_build_object(',1).rstrip().rstrip(';')+' AS gate_json \\gset')
    sql += r'''\echo TRANSACTION_GATE=:gate_json
'''
    # Evaluate independently in SQL, without passing application contents to psql.
    sql += guarded("SELECT (:'gate_json'::jsonb->>'database'='moodflow_agent' AND "
                   ":'gate_json'::jsonb->>'role'='moodflow' AND "
                   "NOT (:'gate_json'::jsonb->>'standby')::boolean AND "
                   "(:'gate_json'::jsonb->>'history_rows')::bigint=328 AND "
                   "(:'gate_json'::jsonb->'ledger')='[\"001_agent_continuity.sql\",\"002_long_term_memory.sql\"]'::jsonb AND "
                   "NOT (:'gate_json'::jsonb->>'execution_exists')::boolean AND "
                   "NOT (:'gate_json'::jsonb->>'event_seq_exists')::boolean AND "
                   "(:'gate_json'::jsonb->>'immutable_enabled')::boolean AND "
                   "NOT EXISTS(SELECT 1 FROM jsonb_each_text(:'gate_json'::jsonb) "
                   "WHERE key IN ('active_sessions','advisory_locks','active_runs','pending_runs','unknown_runs',"
                   "'latest_pending_writes','legacy_pending_sends','pending_controls','unknown_checkpoints',"
                   "'replication_senders','replication_slots') AND value<>'0')) AS quiet \\gset")
    sql += r'''\if :quiet
\else
ROLLBACK;
\echo PRODUCTION_GATE_3_STOPPED_NO_MIGRATION
\q
\endif
'''
    sql += guarded("SELECT clock_timestamp() AS lock_window_started, pg_current_wal_insert_lsn() AS wal_before \\gset")
    for name in r.MIGRATIONS:
        sql += guarded('\\i '+inner+'/'+name)
    sql += guarded("INSERT INTO agent_schema_migration(version) VALUES ('003_context_loop_history.sql'), ('004_continuity_execution.sql') ON CONFLICT(version) DO NOTHING;")
    sql += guarded('COMMIT;')
    sql += guarded("SELECT clock_timestamp() AS tx_completed, pg_current_wal_insert_lsn() AS wal_after \\gset")
    sql += r'''\set ON_ERROR_STOP on
\timing off
SELECT json_build_object(
'transaction_elapsed_ms',round(extract(epoch FROM (:'tx_completed'::timestamptz-:'tx_started'::timestamptz))*1000,3),
'lock_window_upper_bound_ms',round(extract(epoch FROM (:'tx_completed'::timestamptz-:'lock_window_started'::timestamptz))*1000,3),
'wal_bytes',pg_wal_lsn_diff(:'wal_after'::pg_lsn,:'wal_before'::pg_lsn));
\echo PRODUCTION_TRANSACTION_COMMITTED
'''
    return sql


def postcheck_sql():
    source = r.POSTCHECK_SQL
    for marker,query in (
        ('3.1_LEDGER','SELECT version FROM agent_schema_migration'),
        ('3.2_EXECUTION_TABLE',"SELECT to_regclass('public.agent_execution')"),
        ('3.3_MISSING_SEQUENCE','SELECT COUNT(*) AS missing_sequence'),
        ('3.4_DUPLICATE_EVENT_KEY','SELECT event_key, COUNT(*)'),
        ('3.5_DUPLICATE_EVENT_SEQ','SELECT event_seq, COUNT(*)'),
        ('3.6_IMMUTABLE_TRIGGER','SELECT tgname, tgenabled'),
        ('ADDITIONAL_PERMISSION_AND_INTEGRITY_CHECKS','SELECT json_build_object(')):
        source = source.replace(query,'\\echo '+marker+'\n'+query,1)
    return source


def main():
    if os.geteuid() != 0:
        raise r.Stop('root_terminal_required')
    print('STAGE=checking_verified_backup',flush=True)
    manifest, _ = r.checked_backup()
    print('STAGE=checking_successful_rehearsal_report',flush=True)
    checked_rehearsal()
    files = r.decoded_migrations()
    print('STAGE=checking_production_image',flush=True)
    if r.text(['docker','inspect','--format','{{.Config.Image}}',r.PRODUCTION]) != r.IMAGE:
        raise r.Stop('production_image_changed')
    if REPORTS.is_symlink():
        raise r.Stop('unsafe_report_directory')
    REPORTS.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(REPORTS,0o700)
    folder = Path(tempfile.mkdtemp(prefix='context-loop-20261002-',dir=REPORTS))
    inner = r.text(['docker','exec',r.PRODUCTION,'mktemp','-d','/tmp/mf-approved-migration-XXXXXXXXXXXX'])
    if not re.fullmatch('/tmp/mf-approved-migration-[A-Za-z0-9]{12}',inner):
        raise r.Stop('unsafe_container_temporary_path')
    db = Production(inner)
    report = {'status':'running','started_at_utc':datetime.now(timezone.utc).isoformat(),
              'backup_manifest':r.MANIFEST.name,'rehearsal_report':str(REHEARSAL),
              'backup_completed_at_utc':manifest.get('completed_at_utc'),
              'backup_integrity_rechecked':True,'backup_restore_verified':True,
              'recovery_responsible':'唐艺宁','lock_timeout':'5s','statement_timeout':'60s',
              'migration_sha256':{n:v['sha256'] for n,v in r.MIGRATIONS.items()},
              'production_transaction_attempted':False,'production_commit_confirmed':False,
              'real_write_verification':'skipped_no_dedicated_test_account',
              'gate_3_observations':[],'replication_lag_seconds':None}
    try:
        profile = b'[moodflow-approved-migration]\nhost=/var/run/postgresql\nport=5432\ndbname=moodflow_agent\nuser=moodflow\nconnect_timeout=5\noptions=-c default_transaction_read_only=on\n'
        staged = {'service.conf':profile,'empty.pgpass':b'',**files}
        for name, raw in staged.items():
            r.run(['docker','exec','-i',r.PRODUCTION,'sh','-c','umask 077; cat > '+inner+'/'+name+'; chmod 400 '+inner+'/'+name],data=raw)
            if name in files and r.text(['docker','exec',r.PRODUCTION,'sha256sum',inner+'/'+name]).split()[0] != r.MIGRATIONS[name]['sha256']:
                raise r.Stop('staged_migration_hash_mismatch')
        print('STAGE=gate_3_readonly_quiet_check_10_seconds',flush=True)
        for sample in range(6):
            item = db.gate()
            report['gate_3_observations'].append(item)
            print('GATE3='+json.dumps(item),flush=True)
            check_gate(item)
            if sample < 5:
                time.sleep(2)
        print('STAGE=one_approved_production_transaction',flush=True)
        report['production_transaction_attempted'] = True
        started = time.monotonic()
        result = db.sql(migration_sql(inner),check=False)
        report['migration_wall_seconds'] = round(time.monotonic()-started,6)
        output = result.stdout.decode('utf-8','replace')
        (folder/'migration-output.txt').write_text(output,encoding='utf-8')
        print(output,flush=True)
        report['ledger_after_attempt'] = db.ledger()
        committed = 'PRODUCTION_TRANSACTION_COMMITTED' in output
        report['production_commit_confirmed'] = committed
        if result.returncode or result.stderr or not committed:
            report['rollback_marker_observed'] = 'PRODUCTION_TRANSACTION_ROLLED_BACK' in output
            report['sqlstate_only_errors'] = re.findall(r'ERROR:\s*([A-Z0-9]{5})\b',result.stderr.decode('utf-8','replace'))
            raise r.Stop('transaction_not_verified_inspect_ledger_no_retry')
        if report['ledger_after_attempt']['ledger'] != r.NEW_LEDGER:
            raise r.Stop('committed_ledger_mismatch_no_retry')
        report['metrics'] = json.loads(next(line for line in output.splitlines() if line.startswith('{')))
        print('STAGE=runbook_section_3_readonly_postchecks',flush=True)
        checks = db.sql(postcheck_sql()).stdout.decode()
        (folder/'readonly-postchecks.txt').write_text(checks,encoding='utf-8')
        print('RUNBOOK_SECTION_3_OUTPUT_BEGIN\n'+checks+'RUNBOOK_SECTION_3_OUTPUT_END',flush=True)
        final = json.loads(next(line for line in reversed(checks.splitlines()) if line.startswith('{')))
        check_postchecks(final)
        report['readonly_postchecks'] = final
        report['status'] = 'passed'
    except (r.Stop,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired,KeyboardInterrupt) as exc:
        report['status'] = 'stopped_or_failed'
        report['reason'] = str(exc) if isinstance(exc,r.Stop) else type(exc).__name__
        if report['production_transaction_attempted'] and 'ledger_after_attempt' not in report:
            try:
                report['ledger_after_attempt'] = db.ledger()
            except (r.Stop,OSError,ValueError,subprocess.TimeoutExpired):
                report['ledger_after_attempt'] = 'unconfirmed_do_not_retry'
        print('STOP: '+report['reason'],flush=True)
    finally:
        # Exact files created in this mktemp directory; never recursive deletion.
        try:
            cleaned = r.run(['docker','exec',r.PRODUCTION,'rm','-f',*[inner+'/'+n for n in ('service.conf','empty.pgpass',*files)]],check=False)
            removed = r.run(['docker','exec',r.PRODUCTION,'rmdir',inner],check=False)
            report['temporary_profile_cleanup'] = 'passed' if cleaned.returncode == removed.returncode == 0 else 'needs_review'
        except (r.Stop,OSError,subprocess.TimeoutExpired):
            report['temporary_profile_cleanup'] = 'needs_review'
        report['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
        (folder/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print('REPORT_PATH='+str(folder/'report.json'),flush=True)
        print('PRODUCTION_RESULT='+json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)
        if report['status'] == 'passed':
            print('PRODUCTION_MIGRATION_AND_READONLY_POSTCHECKS_FINISHED; real_write_test_skipped; rollout_not_approved',flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    os.umask(0o077)
    try:
        raise SystemExit(main())
    except (r.Stop,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as exc:
        reason=str(exc) if isinstance(exc,r.Stop) and re.fullmatch(r'[A-Za-z0-9_]+',str(exc)) else type(exc).__name__
        print('STOP: '+reason+'; check_report_and_ledger_no_retry')
        raise SystemExit(1)

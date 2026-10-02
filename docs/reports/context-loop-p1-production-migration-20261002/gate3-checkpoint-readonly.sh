# Diagnose the stopped gate. Read-only; no migration, deletion, or resume.
docker exec -i moodflow-production-postgres-1 sh -s <<'MF_CP_DIAGNOSTIC'
set -eu
umask 077
task_dir=$(mktemp -d /tmp/mf-cp-check-XXXXXX)
trap 'rm -f "$task_dir/service.conf" "$task_dir/empty.pgpass"; rmdir "$task_dir"' EXIT
: > "$task_dir/empty.pgpass"
printf '[moodflow-approved-migration]\nhost=/var/run/postgresql\ndbname=moodflow_agent\nuser=moodflow\nconnect_timeout=5\noptions=-c default_transaction_read_only=on\n' > "$task_dir/service.conf"
unset PGPASSWORD PGOPTIONS
PGSERVICEFILE="$task_dir/service.conf" PGPASSFILE="$task_dir/empty.pgpass" \
psql 'service=moodflow-approved-migration' -X -w -q -A -t -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -P pager=off <<'MF_CP_SQL'
BEGIN READ ONLY;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='10s';
WITH latest AS (
 SELECT DISTINCT ON(thread_id,checkpoint_ns) * FROM checkpoints
 ORDER BY thread_id,checkpoint_ns,checkpoint_id DESC
), controls AS (
 SELECT c.checkpoint,v.key,v.value,b.type,b.blob,
 coalesce((c.checkpoint->'channel_values') ? v.key,false) AS has_inline,
 jsonb_typeof(c.checkpoint->'channel_values'->v.key) AS inline_kind,
 CASE WHEN v.key='__start__' THEN '__start__' ELSE regexp_replace(v.key,'^.*:','') END AS node
 FROM latest c CROSS JOIN LATERAL jsonb_each_text(c.checkpoint->'channel_versions') v
 LEFT JOIN checkpoint_blobs b ON b.thread_id=c.thread_id AND b.checkpoint_ns=c.checkpoint_ns
 AND b.channel=v.key AND b.version=v.value
 WHERE v.key LIKE 'branch:%' OR v.key LIKE 'start:%' OR v.key LIKE 'join:%'
 OR v.key IN ('__start__','__pregel_tasks')
)
SELECT jsonb_build_object(
 'database',current_database(),'read_only',current_setting('transaction_read_only'),'checked_at',clock_timestamp(),
 'latest_checkpoints',(SELECT count(*) FROM latest),
 'malformed_checkpoint_shapes',(SELECT count(*) FROM latest WHERE
 coalesce(jsonb_typeof(checkpoint->'channel_versions'),'')<>'object'
 OR coalesce(jsonb_typeof(checkpoint->'versions_seen'),'')<>'object'),
 'missing_blob_without_inline',(SELECT count(*) FROM controls WHERE type IS NULL AND NOT has_inline),
 'missing_blob_with_inline',(SELECT count(*) FROM controls WHERE type IS NULL AND has_inline),
 'invalid_nonempty_blob_rows',(SELECT count(*) FROM controls WHERE type IS NOT NULL AND type<>'empty' AND blob IS NULL),
 'inline_unseen_controls',(SELECT count(*) FROM controls WHERE has_inline AND key<>'__pregel_tasks'
 AND coalesce(checkpoint#>>ARRAY['versions_seen',node,key],'')<>value),
 'storage_groups',(SELECT json_agg(g) FROM (
 SELECT split_part(key,':',1) AS family,type AS blob_type,has_inline,inline_kind,count(*) AS rows
 FROM controls GROUP BY 1,2,3,4 ORDER BY 1,2,3,4) g),
 'old_pending_candidates',(SELECT json_agg(json_build_object(
 'family',split_part(key,':',1),'blob_type',type,'blob_bytes',octet_length(blob),
 'has_inline',has_inline,'inline_kind',inline_kind,'blob_md5',md5(blob),
 'known_empty_builtin_set',blob=decode('c70f0093a86275696c74696e73a373657490','hex'),
 'seen_version_equal',coalesce(checkpoint#>>ARRAY['versions_seen',node,key],'')=value))
 FROM controls WHERE type<>'empty' AND
 CASE WHEN key='__pregel_tasks' THEN encode(blob,'hex') NOT IN ('90','c0','5b5d','6e756c6c')
 ELSE coalesce(checkpoint#>>ARRAY['versions_seen',node,key],'')<>value END));
ROLLBACK;
MF_CP_SQL
printf 'CHECKPOINT_READONLY_DIAGNOSTIC_FINISHED; no_migration_or_resume\n'
MF_CP_DIAGNOSTIC
docker exec moodflow-production-ai-service-1 python -I -c 'import json,hashlib; from importlib.metadata import version; from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer; t,b=JsonPlusSerializer().dumps_typed(set()); print("CHECKPOINT_LIBRARY="+json.dumps({"versions":{n:version(n) for n in ("langgraph","langgraph-checkpoint","langgraph-checkpoint-postgres")},"synthetic_empty_set_type":t,"synthetic_empty_set_md5":hashlib.md5(b).hexdigest()}))'

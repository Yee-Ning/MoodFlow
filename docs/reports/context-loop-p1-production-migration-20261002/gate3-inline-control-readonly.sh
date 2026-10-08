# Inspect the remaining inline control only. No migration, deletion or graph resume.
if docker exec -i moodflow-production-postgres-1 sh -s <<'MF_INLINE_DIAGNOSTIC'
set -eu
umask 077
task_dir=$(mktemp -d /tmp/mf-inline-check-XXXXXX)
trap 'rm -f "$task_dir/service.conf" "$task_dir/empty.pgpass"; rmdir "$task_dir"' EXIT
: > "$task_dir/empty.pgpass"
printf '[moodflow-approved-migration]\nhost=/var/run/postgresql\ndbname=moodflow_agent\nuser=moodflow\nconnect_timeout=5\noptions=-c default_transaction_read_only=on\n' > "$task_dir/service.conf"
unset PGPASSWORD PGOPTIONS
PGSERVICEFILE="$task_dir/service.conf" PGPASSFILE="$task_dir/empty.pgpass" \
psql 'service=moodflow-approved-migration' -X -w -q -A -t -v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate -P pager=off <<'MF_INLINE_SQL'
BEGIN READ ONLY;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='10s';
WITH latest AS (
 SELECT DISTINCT ON(thread_id,checkpoint_ns) * FROM checkpoints
 ORDER BY thread_id,checkpoint_ns,checkpoint_id DESC
), controls AS (
 SELECT c.checkpoint, (c.checkpoint_ns='' OR EXISTS(
 SELECT 1 FROM agent_session s WHERE s.conversation_id=c.thread_id
 AND s.session_id=c.checkpoint_ns)) AS namespace_is_root,
 md5(jsonb_build_array(c.thread_id,c.checkpoint_ns,c.checkpoint_id)::text) AS checkpoint_fingerprint,
 v.key, v.value, v.value#>>'{}' AS version_text,
 jsonb_typeof(v.value) AS version_kind, b.type, b.blob,
 coalesce((c.checkpoint->'channel_values') ? v.key,false) AS has_inline,
 c.checkpoint->'channel_values'->v.key AS inline_value,
 CASE WHEN v.key='__start__' THEN '__start__'
 ELSE regexp_replace(v.key, '^.*:', '') END AS node
 FROM latest c CROSS JOIN LATERAL jsonb_each(CASE
 WHEN jsonb_typeof(c.checkpoint->'channel_versions')='object'
 THEN c.checkpoint->'channel_versions' ELSE '{}'::jsonb END) v
 LEFT JOIN checkpoint_blobs b ON b.thread_id=c.thread_id
 AND b.checkpoint_ns=c.checkpoint_ns AND b.channel=v.key AND b.version=v.value#>>'{}'
 WHERE v.key LIKE 'branch:%' OR v.key LIKE 'start:%' OR v.key LIKE 'join:%'
 OR v.key IN ('__start__','__pregel_tasks')
), storage AS (
 SELECT *, checkpoint->'versions_seen'->node AS seen_node,
 checkpoint#>ARRAY['versions_seen',node,key] AS seen_version,
 CASE WHEN type IS NOT NULL AND type<>'empty' AND blob IS NOT NULL THEN
   CASE WHEN key ~ '^join:[^:]+:[^:]+$' AND type='msgpack'
        AND blob=decode('c70f0093a86275696c74696e73a373657490','hex') THEN false
        WHEN key='__pregel_tasks' THEN encode(blob,'hex') NOT IN ('90','c0','5b5d','6e756c6c')
        ELSE true END
 ELSE has_inline AND CASE WHEN key='__pregel_tasks'
   THEN inline_value NOT IN ('null'::jsonb,'[]'::jsonb) ELSE true END END AS available
 FROM controls
), classified AS (
 SELECT *, CASE WHEN NOT available THEN false
 WHEN key='__pregel_tasks' THEN true
 WHEN seen_node IS NULL THEN true
 WHEN jsonb_typeof(seen_node)<>'object' THEN NULL
 WHEN version_kind='string' AND coalesce(jsonb_typeof(seen_version),'string')='string'
 THEN version_text COLLATE "C" > coalesce(seen_version#>>'{}','') COLLATE "C"
 WHEN version_kind='number' AND coalesce(jsonb_typeof(seen_version),'number')='number'
 THEN version_text::numeric > coalesce((seen_version#>>'{}')::numeric,0)
 ELSE NULL END AS pending,
 (type IS NOT NULL AND type<>'empty' AND blob IS NULL)
 OR (available AND (version_kind NOT IN ('string','number')
   OR (seen_node IS NOT NULL AND jsonb_typeof(seen_node)<>'object')
   OR (seen_version IS NOT NULL AND jsonb_typeof(seen_version)<>version_kind)
   OR (has_inline AND (type IS NULL OR type='empty') AND
       jsonb_typeof(inline_value) NOT IN ('null','string','number','boolean'))
   OR (key LIKE 'join:%' AND (type IS NULL OR type='empty')))) AS unknown
 FROM storage
)
SELECT jsonb_build_object(
 'database',current_database(),'read_only',current_setting('transaction_read_only'),'checked_at',clock_timestamp(),
 'pending_controls',(SELECT count(*) FROM classified WHERE pending),
 'unknown_checkpoints',(SELECT count(*) FROM latest WHERE
  coalesce(jsonb_typeof(checkpoint->'channel_versions'),'')<>'object'
  OR coalesce(jsonb_typeof(checkpoint->'versions_seen'),'')<>'object')+
  (SELECT count(*) FROM classified WHERE unknown OR pending IS NULL),
 'inline_controls',(SELECT jsonb_agg(jsonb_build_object(
  'checkpoint_fingerprint',checkpoint_fingerprint,'namespace_is_root',namespace_is_root,
  'node_static_name',CASE WHEN node ~ '^[A-Za-z_][A-Za-z_0-9-]{0,63}$' THEN node ELSE '[redacted_nonstatic_name]' END,
  'inline_kind',jsonb_typeof(inline_value),'version_kind',version_kind,
  'seen_node_present',seen_node IS NOT NULL,'seen_version_kind',jsonb_typeof(seen_version),
  'versions_equal',value=seen_version,'pending',pending,'unknown',unknown))
  FROM classified WHERE has_inline AND available));
ROLLBACK;
MF_INLINE_SQL
printf 'INLINE_CONTROL_READONLY_FINISHED; no_migration_or_resume\n'
MF_INLINE_DIAGNOSTIC
then :
else printf 'STOP: inline_readonly_check_failed; no_migration_or_retry\n'
fi

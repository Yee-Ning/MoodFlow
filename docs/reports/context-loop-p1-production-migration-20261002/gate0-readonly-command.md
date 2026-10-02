# 门控 0：临时 service profile 与只读检查命令

用户已在本聊天明确回复“允许”，授权创建无密码临时连接配置。该授权只调整原先“使用已有 profile”的连接方式限制；原门控顺序、备份硬门槛、003 / 004 范围及部署限制继续有效。

以下命令在 ECS 的 Bash 终端运行。第一个命令仅输出六项非敏感应用配置；第二个在已确认的生产 PostgreSQL 容器内使用本地 socket、现有 `POSTGRES_USER` 和获批库名创建临时 service 文件，同时创建空的临时 pgpass 文件，避免读取或提示密码。临时目录权限为 0700、文件为 0600；正常及错误退出时删除。没有改变认证规则或生产环境配置。

数据库会话默认及显式事务均为只读，执行手册 §1 的五项查询和补充 catalog / 权限查询，启用逐项计时；异常退出后连接关闭并回滚。`GATE0_READONLY_QUERIES_FINISHED` 仅表示查询结束，**不代表门控 0 已通过**；仍须审阅输出。任何错误均停止，不放宽超时重试。

```bash
docker exec moodflow-production-ai-service-1 python -c 'import os; [print(k+"="+os.getenv(k,"<unset>")) for k in ("POSTGRES_DB","POSTGRES_USER","MEMORY_BACKEND","PERSISTENCE_BACKEND","CHECKPOINT_BACKEND","PERSISTENCE_POOL_MAX_SIZE")]'

docker exec -i moodflow-production-postgres-1 sh -s <<'MF_GATE0'
set -eu
: "${POSTGRES_USER:?STOP: POSTGRES_USER is not set}"
if [ "${POSTGRES_DB:-}" != "moodflow_agent" ]; then
  printf 'STOP: configured database is not the approved database.\n' >&2
  exit 1
fi
case "$POSTGRES_USER" in
  *[!A-Za-z0-9_.-]*)
    printf 'STOP: role name needs manual validation.\n' >&2
    exit 1
    ;;
esac
if [ ! -S /var/run/postgresql/.s.PGSQL.5432 ]; then
  printf 'STOP: expected local PostgreSQL socket is absent.\n' >&2
  exit 1
fi
umask 077
task_dir=$(mktemp -d /tmp/moodflow-gate0.XXXXXX)
trap 'rm -f -- "$task_dir/service.conf" "$task_dir/empty.pgpass"; rmdir -- "$task_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
task_profile="$task_dir/service.conf"
: > "$task_dir/empty.pgpass"
unset PGPASSWORD PGOPTIONS
printf '[moodflow-approved-migration]\nhost=/var/run/postgresql\nport=5432\ndbname=moodflow_agent\nuser=%s\nconnect_timeout=5\noptions=-c default_transaction_read_only=on\n' "$POSTGRES_USER" > "$task_profile"
PGSERVICEFILE="$task_profile" PGPASSFILE="$task_dir/empty.pgpass" psql "service=moodflow-approved-migration" -X -w -v ON_ERROR_STOP=1 -P pager=off <<'MF_SQL'
\timing on
BEGIN READ ONLY;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '10s';
SELECT current_database(), current_user, version();
SELECT 1 / (current_database() = 'moodflow_agent')::integer AS approved_database_check;
SELECT CURRENT_TIMESTAMP AS checked_at, current_setting('transaction_read_only') AS read_only;
SELECT version, applied_at FROM agent_schema_migration ORDER BY version;
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema='public'
  AND table_name IN ('agent_history_event', 'agent_execution')
ORDER BY table_name, ordinal_position;
SELECT indexname FROM pg_indexes
WHERE schemaname='public' AND tablename='agent_history_event';
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgrelid='agent_history_event'::regclass AND NOT tgisinternal;
SELECT item, to_regclass('public.' || item) AS relation
FROM (VALUES
  ('agent_task'), ('agent_session'), ('agent_history_event'),
  ('agent_artifact'), ('agent_handoff'), ('agent_long_term_memory'),
  ('checkpoints'), ('checkpoint_blobs'), ('checkpoint_writes'),
  ('checkpoint_migrations')
) AS required(item);
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname='public' AND tablename='agent_history_event';
SELECT tgname, tgenabled, pg_get_triggerdef(oid) AS definition
FROM pg_trigger
WHERE tgrelid='agent_history_event'::regclass AND NOT tgisinternal;
SELECT pg_get_functiondef('public.reject_agent_immutable_change()'::regprocedure);
SELECT extname, extversion FROM pg_extension WHERE extname='vector';
SELECT has_table_privilege(current_user, 'public.agent_history_event', 'SELECT') AS history_select,
       has_table_privilege(current_user, 'public.agent_history_event', 'INSERT') AS history_insert;
ROLLBACK;
MF_SQL
printf 'GATE0_READONLY_QUERIES_FINISHED; temporary profile will be removed.\n'
MF_GATE0
```

## 命令验证

验证对象为已有本地 `moodflow-rehearsal-20260928` 容器，仅执行只读查询，未执行 003 / 004，也不是门控 2 的生产规模演练。

- POSIX `sh -n`：通过。
- 本地正常路径：exit 0；观察到 `read_only=on`、ROLLBACK 及结束标记；临时目录与文件清理通过。
- 错误目标库：exit 1；在连接前停止。
- 无效用户名：exit 1；在连接前停止。
- 不存在角色导致的连接失败：exit 2；临时目录与文件清理通过。
- 应用元数据：仅输出约定六项非敏感配置。

生产执行结果仍待用户回传；备份、门控 2 / 3、迁移事务和迁移后复验均未执行。

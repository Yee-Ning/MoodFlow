# 门控 0：补齐应用配置、连接池默认值及截断表记录

第一轮生产只读 SQL 已由用户执行并回传截图。当前还缺少应用配置截图上方四项、未设置的连接池容量对应的部署代码默认值，以及 `agent_handoff` / `agent_long_term_memory` 两条被截掉的表记录。本命令仍属于门控 0，备份门控尚未进入。

命令只读取六项非敏感环境配置及部署代码中的 `PERSISTENCE_POOL_MAX_SIZE` 默认值，不导入应用或初始化存储。补充数据库查询继续使用已获准的无密码临时 service；当前 `moodflow` 角色来自上一轮生产 `current_user` 结果。退出时清理临时文件。连接池容量仅由配置和源码默认值计算，不声称已查看运行进程中的 pool 对象。

```bash
bash <<'MF_GATE0_REMAINING'
set -eu
docker exec -i moodflow-production-ai-service-1 python - <<'MF_APP'
import ast
import os
from pathlib import Path

names = ("POSTGRES_DB", "POSTGRES_USER", "MEMORY_BACKEND",
         "PERSISTENCE_BACKEND", "CHECKPOINT_BACKEND",
         "PERSISTENCE_POOL_MAX_SIZE")
for name in names:
    print(name + "=" + os.getenv(name, "<unset>"))

source = Path("/app/app/agent/foundation/long_horizon/runtime.py")
tree = ast.parse(source.read_text(encoding="utf-8"))
defaults = {
    str(node.args[1].value)
    for node in ast.walk(tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "getenv"
    and len(node.args) >= 2
    and isinstance(node.args[0], ast.Constant)
    and node.args[0].value == "PERSISTENCE_POOL_MAX_SIZE"
    and isinstance(node.args[1], ast.Constant)
}
if len(defaults) != 1:
    raise SystemExit("STOP: cannot confirm pool default from deployed code")
default = defaults.pop()
print("POOL_SOURCE_DEFAULT=" + default)
try:
    capacity = int(os.getenv("PERSISTENCE_POOL_MAX_SIZE", default))
except ValueError:
    raise SystemExit("STOP: pool capacity is not an integer")
print("POOL_CONFIG_MAX_SIZE=" + str(capacity))
if capacity < 2:
    raise SystemExit("STOP: pool capacity is below 2")
MF_APP
docker exec -i moodflow-production-postgres-1 sh -s <<'MF_PG'
set -eu
if [ "${POSTGRES_DB:-}" != "moodflow_agent" ]; then
  printf 'STOP: configured database mismatch.\n' >&2
  exit 1
fi
umask 077
task_dir=$(mktemp -d /tmp/moodflow-gate0.XXXXXX)
trap 'rm -f -- "$task_dir/service.conf" "$task_dir/empty.pgpass"; rmdir -- "$task_dir"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
: > "$task_dir/empty.pgpass"
unset PGPASSWORD PGOPTIONS
printf '[moodflow-approved-migration]\nhost=/var/run/postgresql\nport=5432\ndbname=moodflow_agent\nuser=moodflow\nconnect_timeout=5\noptions=-c default_transaction_read_only=on\n' > "$task_dir/service.conf"
PGSERVICEFILE="$task_dir/service.conf" PGPASSFILE="$task_dir/empty.pgpass" psql "service=moodflow-approved-migration" -X -w -v ON_ERROR_STOP=1 -P pager=off <<'MF_SQL'
BEGIN READ ONLY;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='10s';
SELECT to_regclass('public.agent_handoff') AS handoff_table,
       to_regclass('public.agent_long_term_memory') AS memory_table,
       to_regclass('public.agent_execution') AS execution_table;
SELECT pg_get_userbyid(relowner) AS history_owner,
       pg_get_userbyid(relowner)=current_user AS history_owned_by_current_role,
       has_schema_privilege(current_user, 'public', 'CREATE') AS can_create_in_public
FROM pg_class WHERE oid='public.agent_history_event'::regclass;
ROLLBACK;
MF_SQL
MF_PG
MF_GATE0_REMAINING
```

## 本地验证

- 应用配置正常路径：exit 0，默认及计算容量均为 10。
- 配置容量小于 2：exit 1，停止。
- 配置容量不是整数：exit 1，停止。
- 补充 SQL 及临时文件清理：通过。
- 验证只在本地现有演练容器执行，只做只读查询；不作为生产门控结果或门控 2 演练。

生产补充结果仍待用户回传；003 / 004、备份门控、迁移演练及低峰门控均未执行。

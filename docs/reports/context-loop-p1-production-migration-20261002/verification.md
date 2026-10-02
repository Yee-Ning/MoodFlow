# Context Loop P1：前置查询与迁移后复验结果

日期：2026-10-02，Asia/Shanghai。**最新状态：生产 §1 五项只读 SQL 已执行并由用户回传截图；门控 0 尚待补齐配置及截断记录。003 / 004 未执行，§3 复验仍全部未执行。**

依据：[生产迁移手册](../../operations/context-loop-p1-postgres-migration-20261002.md) §1 / §3。执行过程及停止原因见 [执行报告](execution.md)。

## 1. 初次环境检查与失败的连接尝试

Windows PATH 未找到 `psql`、`pg_dump` 或 `pg_restore`；常见 Windows PostgreSQL 安装目录也未找到 `psql.exe`。

| 本机候选 profile 路径 | 存在性 |
| --- | --- |
| `%APPDATA%/postgresql/.pg_service.conf` | false |
| `%APPDATA%/postgresql/pg_service.conf` | false |
| `%USERPROFILE%/.pg_service.conf` | false |

本机及被检查的本地 PostgreSQL 演练容器均未设置 `PGSERVICE`、`PGSERVICEFILE`、`PGSYSCONFDIR`。只检查存在性，没有读取 profile 或凭据文件内容。

现有本地演练容器提供 `/usr/bin/psql`，其 `pg_config --sysconfdir` 返回 `/etc/postgresql-common`。检查用户及系统候选 service 文件均未发现文件。这不说明远端运维环境的 profile 状态。

以下命令仅使用手册 service 名称，`-w` 禁止交互密码提示，SQL 明确为只读事务。它在 service 解析阶段失败，未建立连接，也未发送 SQL：

```powershell
docker exec moodflow-rehearsal-20260928-postgres-1 psql "service=moodflow-approved-migration" -X -w --set=ON_ERROR_STOP=1 -c "BEGIN READ ONLY; SELECT current_database(), current_user, version(); ROLLBACK;"
```

退出码：1。耗时：0.774 s（本地命令 wall time）。原始客户端错误：

```text
psql: error: definition of service "moodflow-approved-migration" not found
```

## 2. 手册 §1 只读前置 SQL

### 2.1 当前数据库、角色与版本

```sql
SELECT current_database(), current_user, version();
```

结果：已执行，来源截图 1；0.344 ms。

```text
current_database = moodflow_agent
current_user = moodflow
version = PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2), x86_64, 64-bit
```

库名符合批准目标。应用配置中的角色仍待补齐核对；SQL 的 current_user 不是独立的应用角色配置证据。

### 2.2 Migration ledger

```sql
SELECT version, applied_at FROM agent_schema_migration ORDER BY version;
```

结果：已执行，来源截图 1；0.577 ms，恰有两条记录。

```text
001_agent_continuity.sql | 2026-09-22 03:16:04.671694+00
002_long_term_memory.sql | 2026-09-22 12:03:26.325776+00
(2 rows)
```

001 / 002 已在 ledger 记录；没有 003 / 004 记录。002 与当前 Memory 配置的适用性仍待补齐配置确认。

### 2.3 历史与执行表列清单

```sql
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema='public'
  AND table_name IN ('agent_history_event', 'agent_execution')
ORDER BY table_name, ordinal_position;
```

结果：已执行，来源截图 2；15.164 ms，八条记录。

| table_name | column_name | data_type |
| --- | --- | --- |
| agent_history_event | event_id | character varying |
| agent_history_event | task_id | character varying |
| agent_history_event | session_id | character varying |
| agent_history_event | event_type | character varying |
| agent_history_event | event_timestamp | timestamp with time zone |
| agent_history_event | node | character varying |
| agent_history_event | source | character varying |
| agent_history_event | body | jsonb |

未显示 event_key / event_seq 或 agent_execution 的列记录，符合 003 / 004 尚未记录的迁移前状态；execution 表是否存在仍待 to_regclass 复核。

### 2.4 历史表索引

```sql
SELECT indexname FROM pg_indexes
WHERE schemaname='public' AND tablename='agent_history_event';
```

结果：已执行，来源截图 2；3.741 ms。

```text
agent_history_event_pkey
agent_history_task_timestamp
agent_history_task_type_timestamp
(3 rows)
```

### 2.5 不可变触发器

```sql
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgrelid='agent_history_event'::regclass AND NOT tgisinternal;
```

结果：已执行，来源截图 2；1.073 ms。

```text
agent_history_event_immutable | O
(1 row)
```

补充定义查询在截图 3 / 4 中确认 BEFORE DELETE OR UPDATE FOR EACH ROW，调用 reject_agent_immutable_change()。函数体无条件 RAISE EXCEPTION，消息为 `% is append-only`，ERRCODE 为 `55000`。`O` 在 origin / local 模式触发，见 [PostgreSQL 16 pg_trigger](https://www.postgresql.org/docs/16/catalog-pg-trigger.html)。未作真实 UPDATE / DELETE 验证。

补充前置条件：四张 checkpoint 表存在已在截图 3 确认；当前数据库角色的历史 SELECT / INSERT 权限为 true。002 的 Memory 配置、应用角色一致性、pool 配置容量、被截断的两张表及相关 owner 权限仍待补齐。执行器跨进程共享库 / conversation key 的实际业务行为未作本轮验收。

## 3. 手册 §3 迁移后只读复验 SQL

### 3.1 两条 migration 记录

```sql
SELECT version FROM agent_schema_migration
WHERE version IN ('003_context_loop_history.sql','004_continuity_execution.sql');
```

结果：未执行；无输出。不能确认两条记录存在。

### 3.2 Execution 表

```sql
SELECT to_regclass('public.agent_execution');
```

结果：未执行；无输出。表存在性未知。

### 3.3 缺失 sequence

```sql
SELECT COUNT(*) AS missing_sequence FROM agent_history_event WHERE event_seq IS NULL;
```

结果：未执行；无输出。不能报告为 0。

### 3.4 重复 replay key

```sql
SELECT event_key, COUNT(*) FROM agent_history_event
WHERE event_key IS NOT NULL GROUP BY event_key HAVING COUNT(*) > 1;
```

结果：未执行；无输出。不能报告为无重复。

### 3.5 重复 sequence

```sql
SELECT event_seq, COUNT(*) FROM agent_history_event
GROUP BY event_seq HAVING COUNT(*) > 1;
```

结果：未执行；无输出。不能报告为无重复。

### 3.6 不可变触发器保留

```sql
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgrelid='agent_history_event'::regclass AND NOT tgisinternal;
```

结果：未执行；无输出。不能确认保护保留。

## 4. 业务复验与跳过项

| 复验项 | 本轮状态与原因 |
| --- | --- |
| JSON / SSE | 未执行；门控 0 阻断 |
| 同 run 空输入恢复 | 未执行；门控 0 阻断 |
| 跨 namespace 迁移与 lineage | 未执行；门控 0 阻断 |
| 真实写操作 | **按请求跳过**；暂无专用测试账号及可变更测试歌单 |
| 播放器 ACK | 未执行；门控 0 阻断，亦无实际播放器验收证据 |
| LLM 账单及期限 | 未执行；门控 0 阻断 |
| 同 conversation 冲突 / 不同 conversation 容量限制 | 未执行；门控 0 阻断 |
| 双存储缺回执追加 reconciled | 未执行；门控 0 阻断 |

没有执行真实写操作或任何生产 DDL / DML，也没有记录迁移后的通过结论。

## 5. 创建临时配置前的 ECS 回传（历史记录）

用户已能打开 ECS 远程终端；以下为用户回传结果，执行耗时未知，本代理没有直接远端验证。

```text
root
moodflow-production-frontend-1  Up 11 hours (healthy)
moodflow-production-java-1      Up 11 hours (healthy)
moodflow-production-ai-service-1        Up 11 hours (healthy)
moodflow-production-netease-1   Up 11 hours (healthy)
moodflow-production-qdrant-1    Up 11 hours (healthy)
moodflow-production-postgres-1  Up 11 hours (healthy)
psql: error: definition of service "moodflow-approved-migration" not found
```

随后只查客户端及 profile 文件位置，回传结果：

```text
Host PostgreSQL client:
/usr/bin/psql
Host service files:
Container service files:
```

宿主机有 `psql`；已检查路径没有候选 service 文件输出。当时客户端不能解析手册 service。这些历史输出不是数据库 SQL 结果，当时 §1 / §3 的 11 条 SQL 均未执行。当前进展见第 2 / 7 节；备份门控仍未进入。

## 6. 临时配置授权后的命令准备（历史记录）

请求方已允许创建无密码临时连接配置。[只读命令](gate0-readonly-command.md) 已完成本地正常、边界与连接失败路径验证；临时文件退出清理通过。这些本地 SQL 结果不记为生产查询输出，也不作为生产规模演练数据。

准备阶段的生产 §1 查询结果当时仍待回传，现已见第 2 / 7 节；§3 查询与真实写操作仍未执行。命令结束标记仅表示查询结束，不能直接认定门控 0 通过。

## 7. 第一轮生产只读结果与截图限制

第 2 节已更新为实际截图结果。用户的四张截图还提供以下证据：

| 补充项 | 结果 | 查询计时 |
| --- | --- | --- |
| 目标库校验 | approved_database_check=1 | 0.238 ms |
| 时间及事务 | 2026-10-02 10:32:02.162238+00；read_only=on | 0.096 ms |
| 必需表清单 | 显示 10 rows；四张 checkpoint 表及 task/session/history/artifact 可见；handoff / memory 两行被截断 | 0.663 ms |
| 索引定义 | 三条；长定义的右侧部分被截断，不报告逐字符完整输出 | 2.061 ms |
| 触发器定义 | BEFORE DELETE OR UPDATE、FOR EACH ROW、调用不可变函数 | 0.695 ms |
| 不可变函数定义 | 无条件抛出 append-only / 55000 | 0.458 ms |
| 扩展 | vector 0.8.6 | 0.620 ms |
| 历史权限 | history_select=t；history_insert=t | 0.660 ms |
| 事务结束 | ROLLBACK | 0.378 ms |
| shell 返回 | 出现 GATE0_READONLY_QUERIES_FINISHED 和 shell 提示符，未见错误 | 总耗时及退出码未显示 |

截图中的 CREATE 字样来自 SELECT 查询返回的对象定义，不是本轮执行了 DDL。脚本正常返回且未见清理错误；临时配置删除未在生产另行列目录确认。

截图上方四项应用配置未显示，pool 环境变量为 `<unset>`，不能推定部署源码默认值。补充检查见 [命令](gate0-supplement-command.md)。在补充结果审阅前不进入备份门控；§3 六项 SQL 仍全部未执行，无输出。

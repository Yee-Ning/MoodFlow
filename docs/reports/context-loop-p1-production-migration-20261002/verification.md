# Context Loop P1：前置查询与迁移后复验结果

日期：2026-10-02，Asia/Shanghai。**门控 0 未通过；未建立生产数据库连接，003 / 004 未执行。下列 SQL 全部未执行，均无数据库查询输出。**

依据：[生产迁移手册](../../operations/context-loop-p1-postgres-migration-20261002.md) §1 / §3。执行过程及停止原因见 [执行报告](execution.md)。

## 1. 环境检查与连接尝试

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

结果：未执行；无输出。目标库及应用角色未确认。

### 2.2 Migration ledger

```sql
SELECT version, applied_at FROM agent_schema_migration ORDER BY version;
```

结果：未执行；无输出。001 / 002 / 003 / 004 的实际应用状态均未确认。

### 2.3 历史与执行表列清单

```sql
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema='public'
  AND table_name IN ('agent_history_event', 'agent_execution')
ORDER BY table_name, ordinal_position;
```

结果：未执行；无输出。不能由本地 SQL 文件推断生产结构。

### 2.4 历史表索引

```sql
SELECT indexname FROM pg_indexes
WHERE schemaname='public' AND tablename='agent_history_event';
```

结果：未执行；无输出。

### 2.5 不可变触发器

```sql
SELECT tgname, tgenabled FROM pg_trigger
WHERE tgrelid='agent_history_event'::regclass AND NOT tgisinternal;
```

结果：未执行；无输出。未验证触发器存在、启用状态或 UPDATE / DELETE 保护。

补充前置条件：官方 LangGraph checkpoint 表、002 对既有 Memory 配置的适用性、应用权限、pool 容量及执行器共享业务库 / conversation key 条件也未确认。

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

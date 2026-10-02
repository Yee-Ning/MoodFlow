# Context Loop P1：PostgreSQL 生产迁移手册

日期：2026-10-02。状态：**待 West 批准并由运维手动执行，本轮未连接或迁移生产库**。

本手册只涉及 `003_context_loop_history.sql` 和 `004_continuity_execution.sql`。数据库变更、部署、默认图切换、灰度开闸是独立步骤；迁移成功不构成开闸批准。SQLite 自动换窗继续禁用。

## 1. 执行前确认

- 确认获批的数据库、应用角色、执行窗口、备份及恢复负责人。连接使用运维已有的 PostgreSQL service profile，不在终端命令、日志或聊天中填写密码或完整连接凭据。
- 确认 `001_agent_continuity.sql` 与官方 LangGraph checkpoint 表已存在。`002_long_term_memory.sql` 是否需要，由既有 Memory 配置决定，本手册不改变它。
- 在与生产规模接近的副本测量 `003` 的锁持有时间、WAL、磁盘与复制延迟。`BIGSERIAL` 新列和唯一索引可能扫描或改写长历史；本轮小型隔离数据库的执行时间不能作为生产 ETA。
- 保存 `agent_schema_migration`、现有列、索引及不可变触发器的清单。应用角色需要历史 INSERT / SELECT、序列 USAGE，以及 `agent_execution` SELECT / INSERT / UPDATE；历史表的 UPDATE / DELETE 保护必须保留。
- PostgreSQL continuity pool 至少 2 个连接；执行器最多占用一半连接作为 advisory lock，剩余连接用于 checkpoint、历史和 ACK。跨进程执行要求所有执行器使用同一个业务库及相同 conversation key。

可在获批连接中进行以下只读检查：

```sql
SELECT current_database(), current_user, version();
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
```

## 2. 获批后的手动迁移

从仓库根目录进入运维提供的 service profile；以下是待执行指令，本轮没有运行：

```powershell
psql "service=moodflow-approved-migration" -X --set=ON_ERROR_STOP=1
```

在该 psql 会话中执行：

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SELECT pg_advisory_xact_lock(hashtext('moodflow_agent_schema_migrations'));
\i ai-service/migrations/postgresql/003_context_loop_history.sql
\i ai-service/migrations/postgresql/004_continuity_execution.sql
INSERT INTO agent_schema_migration(version)
VALUES ('003_context_loop_history.sql'), ('004_continuity_execution.sql')
ON CONFLICT(version) DO NOTHING;
COMMIT;
```

超时取值须依据副本演练结果批准；不得在生产反复放宽超时试跑。任一步出错时先 `ROLLBACK`，核对实际事务状态与 migration ledger。该操作不调用全量 migration CLI，避免顺带执行其他未经批准的业务或 checkpoint 迁移。

应用启动和 Coordinator 初始化不执行这两份 SQL。`004` 缺失时执行器明确失败，不会静默创建生产表。现有 `scripts.migrate_agent_postgres` 仍是人工调用工具，若使用它须另行审查全部待执行迁移。

## 3. 迁移后复验

```sql
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
```

应有两条 migration 记录、execution 表、唯一游标，无重复 replay key。旧事件 `event_key=NULL` 是兼容状态；不得为追求覆盖率批量重写历史正文。

用获批测试账号及可变更测试歌单独立复验 JSON / SSE、同 run 空输入恢复、跨 namespace 迁移、真实写操作、播放器 ACK、LLM 账单及期限。并发相同 conversation 应返回冲突；不同 conversation 超出执行容量时应受限，不能挤占全部查询连接。业务与 checkpoint 双存储的缺回执应追加 reconciled，不能直接修改已提交历史。

## 4. 故障处理与回退

未 COMMIT 的失败由 PostgreSQL 回滚。已提交后优先回退应用版本，保留新增列、索引、表和事件：新增 schema 与旧读取兼容，无须删除账本。

执行中的会话由 advisory lock 与 fence 保护。连接丢失后旧执行器必须停止；不得手动减小 fence / revision、删除未知费用预留，或把未知写操作改为成功。不要仅因 `expires_at` 超时就假定旧执行器已退出。

发生 namespace 迁移中断时保留 proposed intent、源 checkpoint 和 Session lineage，再由恢复执行器补齐目标；不要用 prepare 重建原 run。若下游写入效果不明，只能只读核验或等待人工输入。

跨窗复制只允许可核验的父图边界。存在 cached pending writes、当前父 checkpoint 下的持久子图状态，或 lineage 扫描超限时，先在原 namespace 恢复；不要通过删子图 checkpoint 强行迁移。工具写回执跨 Session 按原 task/run 查询，已成功结果可重放，未知效果仍阻断。

只有确需数据库恢复时才使用经批准的备份恢复流程。不得关闭不可变触发器、删除新历史或盲目回退序列。完成复验后将证据交给 West；是否部署或开闸由 West 独立决定。

## 5. 本轮已做与未做

已在无挂载、仅 loopback 暴露的临时 PostgreSQL 容器中，为各专项新建隔离数据库执行迁移并做恢复测试；数据库由测试清理。生产库、生产凭据、配置文件和灰度开关均未变更。

暂无专用测试账号，真实歌单写入、实际播放器、模型供应商账单、生产规模迁移耗时和生产负载均仍待独立验收。工程验证结果见 [P1 验证报告](../reports/context-loop-p1-20261002/validation.md)。

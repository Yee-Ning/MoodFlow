# Phase 2.5 PostgreSQL Persistence Hardening Report

## 1. 当前问题与完成状态

Phase 2 已建立 `ContinuityStore` 与 checkpointer provider，但此前仍有以下缺口：

- 只有 SQLite adapter，生产运行时没有 PostgreSQL 实现；
- persistent checkpointer 只有注册接口，没有官方 PostgreSQL saver；
- FastAPI 没有数据库 pool 的启动和关闭生命周期；
- SQLite DDL 内联在 adapter 中，没有 PostgreSQL 版本化迁移；
- restart E2E 只验证 SQLite 业务记录，没有验证 LangGraph checkpoint；
- 根图传入的 `checkpoint_ns=session_id` 会被 LangGraph 归一化为空，实际没有形成 Session 隔离。

Phase 2.5 已补齐 PostgreSQL business persistence、官方 LangGraph PostgreSQL checkpoint、版本化 migration、FastAPI lifespan、Session checkpoint 映射和真实双进程恢复测试。Phase 1 Context Engineering 与 Phase 2 的 History/Handoff/Artifact 数据契约没有重构。

## 2. 迁移前后架构

### 迁移前

```text
Agent Runtime / Context Engine
        |
LongHorizonContextService
        |
ContinuityStore Protocol
        |
SQLiteContinuityStore

LangGraph -> InMemorySaver（默认）
```

### 迁移后

```text
Application / FastAPI lifespan
        |
Agent Runtime / Context Engine
        |
LongHorizonContextService
        |
ContinuityStore Protocol
        |
        +-- SQLiteContinuityStore          local / unit test
        +-- PostgresContinuityStore        persistent mode

LangGraph Checkpointer Boundary
        |
        +-- SessionScopedInMemorySaver     local / unit test
        +-- official PostgresSaver         persistent mode
              + async off-loop adapter
              + session_id namespace mapping
```

业务服务仍只依赖 `ContinuityStore`。PostgreSQL、Psycopg、连接池和 SQL 只存在于 adapter、migration 与 composition root。

## 3. PostgreSQL 接入方式

### 3.1 配置

```dotenv
DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/<database>
PERSISTENCE_BACKEND=postgres
CHECKPOINT_BACKEND=postgres
```

- `PERSISTENCE_BACKEND=sqlite` 保留本地业务持久化；
- `CHECKPOINT_BACKEND=memory` 保留 unit test/local debug；
- postgres 模式缺少 `DATABASE_URL`、业务 schema 或 checkpoint schema 时 fail closed；
- 代码、报告、测试输出和 commit 均不包含数据库凭据。

`.env.example` 已加入空 `DATABASE_URL` 和两个 backend 开关。

### 3.2 依赖

- `langgraph-checkpoint-postgres>=3.1,<4.0`
- `psycopg[binary]>=3.2,<4.0`
- `psycopg-pool>=3.2,<4.0`

实测环境为 `langgraph==1.2.11`、`langgraph-checkpoint==4.2.0`、`langgraph-checkpoint-postgres==3.1.2`。

### 3.3 业务表

版本化 migration `migrations/postgresql/001_agent_continuity.sql` 创建：

- `agent_task`
- `agent_session`
- `agent_history_event`
- `agent_handoff`
- `agent_artifact`
- `agent_schema_migration`

JSON model body 使用 `JSONB`；task、conversation、session、type 和 timestamp 保留独立列及索引。复合外键防止 task/session 错配；partial unique index 保证一个 task 只有一个 active session。History、Handoff 和 Artifact 通过 PostgreSQL trigger 拒绝 UPDATE/DELETE。

`PostgresContinuityStore.transaction()` 使用同一连接和事务。`LongHorizonContextService.rollover()` 在该事务内完成 checkpoint event、Handoff、handoff event、旧 Session close 和新 Session create；异常会整体回滚。SQLite adapter 保持原本的本地实现，不承担生产事务保证。

### 3.4 迁移命令

```powershell
python -m scripts.migrate_agent_postgres
```

该命令先在 PostgreSQL advisory lock 下执行未应用的业务 migration，再调用 LangGraph 官方 `PostgresSaver.setup()`。没有复制或手写 LangGraph checkpoint DDL。重复执行已验证为幂等。

目标数据库已实际创建并验证以下表：

```text
agent_artifact
agent_handoff
agent_history_event
agent_schema_migration
agent_session
agent_task
checkpoint_blobs
checkpoint_migrations
checkpoint_writes
checkpoints
```

## 4. Checkpoint 恢复与 ID 设计

身份语义保持不变：

| ID | 用途 |
|---|---|
| `thread_id/conversation_id` | LangGraph conversation continuity |
| `task_id` | 长程业务任务 |
| `session_id` | Active Context Window 生命周期 |
| `checkpoint_id` | saver 管理的 graph execution snapshot |

一个 thread 可有多个 task，一个 task 可跨多个 session。

运行时图配置携带 `thread_id=conversation_id` 与独立 `session_id`。`SessionScopedSaverMixin` 只在 checkpointer adapter 内将 `session_id` 映射为 PostgreSQL/Memory saver 的 storage namespace；对图和业务层仍保持四类 ID 分离。该映射修复了 LangGraph 根图清空外部 `checkpoint_ns` 导致的 Session checkpoint 串线问题。

PostgreSQL saver 使用原有 JsonPlus serializer allowlist。由于 Psycopg async connection 在 Windows Proactor event loop 下不可用，本实现复用官方同步 `PostgresSaver` 和 `ConnectionPool`，将 saver I/O 通过 `asyncio.to_thread` 移出事件循环，并实现 LangGraph 所需的 async checkpointer 方法。Linux/Windows 均使用同一持久化语义。

FastAPI lifespan：

1. 启动时创建并验证 checkpoint pool；
2. postgres 业务模式下创建并验证 `PostgresContinuityStore`；
3. 请求到来后 lazy compile graph，避免模块导入期固定为 memory；
4. shutdown 时关闭 business pool 与 checkpoint pool。

## 5. Restart Recovery E2E

`tests/test_postgres_persistence.py` 每次创建独立临时 PostgreSQL database，运行全部 migration，测试结束后终止该测试数据库连接并删除数据库。

双进程场景：

1. **进程 A** 创建 conversation/task/session；
2. 运行确定性 LangGraph：`Router → Retrieval → END`，由 PostgreSQL saver 写入多个 checkpoint；
3. 写入 route History、retrieval Artifact/History、Handoff，并 rollover 到新 Session；
4. 关闭 business/checkpoint pools，进程退出；
5. **进程 B** 只使用同一个 PostgreSQL database、`thread_id` 和从数据库读取的 `session_id`；
6. 读取旧 Session 最新 checkpoint，验证原始 Router/Retrieval state；
7. 在相同 thread/session 上继续 graph，验证新 state 接续旧 state；
8. 从 latest Handoff cold start 新 Session，读取 History 与 Artifact projection；
9. revalidate live state，并重新调用 Phase 1 `build_active_context()`，验证 token budget。

两个子进程不共享 Python Store、Service、Graph、Session 或 checkpoint 对象。测试同时确认 `checkpoint_id` 与 `session_id` 不相等。

## 6. 测试结果

- PostgreSQL persistence E2E：`3 passed`。
- Long-Horizon/Session-scoped checkpoint：`17 passed`。
- 完整测试：`851 passed, 2 skipped, 206 subtests passed`。
- FastAPI postgres lifespan：通过。
- PostgreSQL migration 二次执行：business 与 LangGraph migrations 均为 current。
- 修改范围 Ruff：通过。
- `python -m compileall -q app scripts tests/fixtures/postgres_restart_worker.py`：通过。
- 全仓 Ruff 仍有 5 个任务外既存问题，位于 `foundation/routing.py`、`pdf/adapters.py`、`source_ingestion/auto_service.py` 和 `source_ingestion/service.py`；本阶段未新增 Ruff 问题。

## 7. 未来 Memory Layer 复用

未来 Working/Short-Term/Long-Term Memory 可以复用同一分层：

```text
Memory Service
    -> Memory Repository Protocol
        -> SQLite/local adapter
        -> PostgreSQL adapter
    -> memory_source
        -> Phase 1 ContextCollector / Policy / Budget / Trace
```

Memory 不应直接读取 checkpoint 表，也不应把完整 checkpoint 当作长期记忆。Checkpoint 保存 thread-scoped graph execution state；History/Handoff/Artifact 保存业务连续性；未来 Memory 保存经过治理的跨任务事实、偏好和摘要。Memory projection 继续通过 Phase 1 已有 `memory_source` 进入 Context Engine。

## 8. 剩余限制

- 本阶段创建 PostgreSQL schema，没有自动搬运已有 SQLite 数据；如本地 SQLite 中存在需保留的真实任务，应增加一次性、可校验的数据 backfill 工具。
- Checkpoint retention/pruning 尚未自动化，需要按生产保留策略增加定时任务。
- `PostgresContinuityStore` 是同步 adapter；FastAPI 当前调用链与原 SQLite 一样同步执行短事务。高并发压测后可在不改业务接口的前提下增加线程 offload 或 async adapter。
- 数据库 migration 应作为部署前步骤执行；运行时只验证 schema，不在多 worker startup 中抢跑 migration。

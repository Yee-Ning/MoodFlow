# Phase 2.5 PostgreSQL Persistence Audit

## 1. 审查范围与环境

本审查基于当前 MoodFlow 代码、Phase 2 报告、已安装 Python 包和本机运行环境完成。审查阶段未修改 Context Engineering、Long-Horizon Continuity 或运行时代码。

- PostgreSQL：本机 `localhost:5432` TCP 可达，Docker 中 PostgreSQL 16 容器正在运行。
- 当前 Python 依赖：`langgraph==1.2.11`、`langgraph-checkpoint==4.2.0`。
- 当前缺少：`langgraph-checkpoint-postgres`、`psycopg`、`psycopg-pool`。
- 数据库凭据没有写入本报告、代码或 Git；后续只从 `DATABASE_URL` 读取。

## 2. 当前架构

### 2.1 LangGraph Checkpoint Provider

`app/agent/checkpointing.py` 已有：

- `CheckpointerFactory` Protocol；
- `create_memory_checkpointer()`，包含项目自定义 Pydantic 类型的安全 serializer allowlist；
- `configure_persistent_checkpointer(factory)` 注册点；
- `create_runtime_checkpointer()`，在 memory/persistent 两种模式间切换，persistent 未注册时 fail closed。

Foundation `create_main_graph()` 已通过 factory 注入 checkpointer。Legacy current-song graph 也调用 `create_runtime_checkpointer()`，但它在 `app.agent.graph` 导入时立即编译为全局 `CURRENT_SONG_GRAPH`。

### 2.2 InMemorySaver 使用位置

生产代码只有 `app/agent/checkpointing.py` 直接导入 `InMemorySaver`。单元测试通过 `create_memory_checkpointer()` 显式使用它。当前默认配置仍是 memory，因此未配置环境变量时运行服务仍使用进程内 checkpoint。

风险是 legacy graph 在模块导入阶段创建：如果 PostgreSQL provider 依赖 FastAPI startup 才注册，legacy graph 已经提前选择了 backend。现有 FastAPI `app/main.py` 也没有 lifespan/startup/shutdown 资源管理。

### 2.3 Long-Horizon Persistence

`ContinuityStore` Protocol 已覆盖：

- Task create/get/query/close；
- Session create/get/query/close；
- History append/get/query/reference validation；
- Artifact put/get；
- Handoff put/get。

`LongHorizonContextService` 只依赖该 Protocol，不依赖 SQLite。这一层无需重构。SQLite adapter 位于 `long_horizon/store.py`；composition root 位于 `long_horizon/runtime.py`。

SQLite 当前内联创建 `tasks/sessions/history_events/artifacts/handoffs`，并用 trigger 保证 History、Artifact、Handoff 不可修改或删除。每个 Store 方法独立提交事务；完整 rollover 尚不是单个数据库事务。

### 2.4 ID 语义

现有代码已经区分：

- `thread_id/conversation_id`：LangGraph conversation continuity；
- `task_id`：长程业务任务；
- `session_id`：Active Context Window；
- `checkpoint_id`：由 LangGraph saver 管理的 execution snapshot。

运行时使用 `thread_id=conversation_id` 与 `checkpoint_ns=session_id`。一个 conversation 可有多个 task，一个 task 可有多个 session。该设计应原样保留。

## 3. 需要替换或补充的位置

| 位置 | 当前状态 | Phase 2.5 目标 |
|---|---|---|
| `requirements.txt` | 无 PostgreSQL/checkpointer 包 | 增加版本兼容的官方 checkpointer、Psycopg 3 与 pool |
| `checkpointing.py` | provider 抽象，无具体实现 | 从 `CHECKPOINT_BACKEND` 和 `DATABASE_URL` 构造 PostgreSQL saver，复用安全 serializer |
| `long_horizon/store.py` | Protocol 与 SQLite adapter 同文件 | 保持 Protocol/SQLite 行为，新增独立 PostgreSQL adapter |
| `long_horizon/runtime.py` | sqlite 或手工注册 persistent factory | 通过配置直接选择 sqlite/postgres，业务层不感知 |
| `app/main.py` | 无资源生命周期 | 增加数据库 pool/checkpointer 的 startup validation 与 shutdown close |
| 数据库 schema | SQLite 内联 DDL | 增加有版本号、可追踪的 PostgreSQL 业务迁移 |
| LangGraph schema | 未创建 | 使用官方 `PostgresSaver.setup()` migration，不复制官方 DDL |
| `.env.example` | 无 persistence 配置 | 增加空 `DATABASE_URL`、backend 开关，不写凭据 |
| 测试 | SQLite 双进程恢复，无 PG checkpoint 恢复 | 增加 PostgreSQL adapter、官方 checkpoint、双进程 restart recovery E2E |

## 4. 主要风险

1. **导入期 backend 固化**：legacy `CURRENT_SONG_GRAPH` 在 FastAPI startup 前编译，不能依赖稍后注册的 provider。
2. **连接生命周期**：全局 saver/pool 若没有 shutdown close，会泄漏连接并影响测试隔离。
3. **同步与异步调用**：Foundation 使用 `astream/ainvoke`。选择 saver 时必须验证其 async 调用兼容性，不能仅验证同步 `put/get`。
4. **Serializer 一致性**：PostgreSQL saver 必须使用当前 JsonPlus allowlist，否则既可能无法反序列化项目模型，也可能降低反序列化安全边界。
5. **Schema 所有权**：LangGraph checkpoint 表必须由官方 `.setup()` 管理；业务表使用独立 migration namespace，避免与官方表冲突。
6. **Rollover 原子性**：当前 service 通过多个 Store 调用完成 rollover。仅逐方法换成 PostgreSQL 仍可能在进程崩溃时留下部分状态。
7. **并发 active session**：多个 worker 同时 rollover 需要 task 级锁或唯一约束，否则可能产生多个 active session。
8. **Append-only 保证**：PostgreSQL 需要等价 trigger/permission，不能只依赖应用约定。
9. **测试污染**：真实 PostgreSQL E2E 必须使用唯一 ID 并清理本次数据，不能 truncate 共享数据库或删除其他任务数据。
10. **秘密泄漏**：`DATABASE_URL` 不得进入代码、迁移、日志、测试输出、报告或 commit。

## 5. 迁移方案

### 5.1 依赖和配置

增加与当前 `langgraph-checkpoint` 兼容的 `langgraph-checkpoint-postgres`，并使用 Psycopg 3。配置统一为：

- `DATABASE_URL`：唯一连接来源；
- `CHECKPOINT_BACKEND=memory|postgres`；
- `PERSISTENCE_BACKEND=sqlite|postgres`。

memory/sqlite 保留给 unit test 与 local debug；postgres 模式缺少 `DATABASE_URL` 时 fail closed。

### 5.2 业务 Persistence Adapter

新增 `PostgresContinuityStore` 实现现有 `ContinuityStore`，目标表：

- `agent_task`
- `agent_session`
- `agent_history_event`
- `agent_handoff`
- `agent_artifact`

模型 body 使用 `JSONB`，检索字段保留独立 typed columns 和索引。外键保持 task/session/lineage 一致性。History/Handoff/Artifact 用 PostgreSQL trigger 拒绝 UPDATE/DELETE。

迁移文件采用有序版本号并记录到独立 migration table；runner 获取 PostgreSQL advisory lock 后逐版执行。SQLite 不迁移或删除，继续作为本地 adapter。

### 5.3 LangGraph Checkpointer

使用官方 `PostgresSaver` migration：首次部署或显式 migration 命令调用 `.setup()`，运行时不复制官方 schema。连接必须满足官方要求的 autocommit 与 dictionary row factory。saver 使用与 InMemorySaver 相同的 serializer allowlist。

官方资料：

- [LangGraph PostgreSQL checkpointer README](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/README.md)
- [LangGraph persistence guide](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/persistence.mdx)

### 5.4 运行时生命周期

具体 PostgreSQL factory 应能在模块导入期根据环境直接选择 backend，避免依赖 startup 后注册。pool 使用进程级 singleton；FastAPI lifespan 负责启动验证与 shutdown close。测试可继续显式注入 memory saver 或独立 saver。

### 5.5 Restart Recovery E2E

测试使用两个独立 Python 子进程与同一 PostgreSQL 数据库：

1. 进程 A 运行 Router → Retrieval 模拟图并生成官方 checkpoint；同时写入 History、Artifact、Handoff，rollover 到新 Session；
2. 进程 A 完全退出；
3. 进程 B 用相同 `thread_id + checkpoint_ns(session_id)` 读取并继续 graph；
4. 进程 B 验证 checkpoint state、History、Handoff、Artifact、新 Session cold start 和 `build_active_context()`；
5. 测试按唯一 task/thread 前缀定向清理自身业务行和 checkpoint 行。

## 6. 验收映射

- 生产 checkpoint：postgres backend，memory 仅 local/test。
- LangGraph 恢复：官方 PostgreSQL saver + 官方 migration。
- 业务恢复：`PostgresContinuityStore` 保存五类 `agent_*` 表。
- 进程恢复：独立子进程 E2E，不共享 Python 对象。
- Context Engineering：恢复后重新调用既有 `build_active_context()`，不改 Phase 1 设计。
- 回归：保留并运行 Phase 1、Phase 2 与全量测试。

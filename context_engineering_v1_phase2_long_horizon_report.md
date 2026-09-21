# Context Engineering V1 · Phase 2 Long-Horizon Context Continuity

## 1. 本次实现范围

Phase 2 继续复用 Phase 1 的单次调用 Context Engine，只增加跨窗口连续性。Phase 1 的 Collector、Policy、预算、压缩、Assembler 和各节点 Prompt 没有重写。

- `long_horizon/models.py`：History、Artifact、Handoff、Task、Session 与 rollover 契约。
- `long_horizon/store.py`：`ContinuityStore` 持久化边界与 SQLite 本地/测试 adapter。
- `long_horizon/service.py`：只依赖 `ContinuityStore`，实现事件追加、Artifact、Handoff、lineage、rollover、cold start、revalidation、drill-down 和 Phase 1 回注。
- `long_horizon/runtime.py`：composition root。`sqlite` 模式用于本地和测试；`persistent` 模式必须注册共享 Store provider，否则启动时明确失败。
- `checkpointing.py`：LangGraph checkpointer provider 边界。`InMemorySaver` 仅用于单元测试和本地 fallback；`persistent` 模式没有 provider 时明确失败。
- Foundation streaming：按 conversation 找到当前业务 task/session，并将 conversation 与 active context window 分别映射到 LangGraph checkpoint 配置。

## 2. 四类身份及关系

四类 ID 已分离：

| ID | 含义 | 生命周期与关系 |
|---|---|---|
| `thread_id` / `conversation_id` | LangGraph 和用户对话连续性 | 一个 conversation 可以包含多个 task |
| `task_id` | 一个长程业务任务 | 一个 task 属于一个 conversation，可跨多个 session |
| `session_id` | 一个 Active Context Window 生命周期 | rollover 后生成新 session，并保留 previous/parent lineage |
| `checkpoint_id` | LangGraph 单次 execution snapshot | 由 checkpointer 生成和管理，不用作 task/session 身份 |

运行时使用 `configurable.thread_id = conversation_id`，使用 `configurable.checkpoint_ns = session_id` 隔离不同 Active Context Window。代码不生成或复用 `checkpoint_id`；恢复指定 LangGraph snapshot 时由 persistent checkpointer 处理该字段。

`TaskRecord` 持久化 `task_id/conversation_id/created_at/closed_at`，`SessionRecord` 同时持有 `task_id` 和 `conversation_id`。`start_task(conversation_id)` 可在同一 conversation 下创建多个 task；rollover 让同一 task 产生多个 session。`open_task()` 的兼容入口在没有 conversation 时生成独立 conversation ID，不再默认令二者相等。

## 3. Store / Repository 边界

`LongHorizonContextService` 的构造参数类型是 `ContinuityStore` Protocol，不导入 SQLite。该 Protocol 明确定义：

- Task：创建、读取、按 conversation 查询、关闭；
- Session：创建、读取、关闭、按 task 查询 active/lineage；
- History：append、精确读取、按 task/filter 查询、引用校验；
- Artifact：写入与按 ref 读取；
- Handoff：写入与按 ID 读取。

SQLite 只存在于 adapter 和本地 composition root。现有 Phase 2 SQLite 文件会自动增加 `tasks` 与 `sessions.conversation_id`；旧记录迁移到 `legacy-conversation:<task_id>`，避免继续制造相等 ID。History、Artifact、Handoff 仍由 SQLite trigger 保证不可更新和不可删除。

PostgreSQL 替换只需实现同一 `ContinuityStore`，并在进程启动时调用 `configure_persistent_continuity_store(factory)`，设置 `AI_CONTINUITY_STORE_MODE=persistent`。业务服务和 Phase 1 无需修改。生产 adapter 应将 rollover 的 checkpoint event、handoff、session close 与新 session 创建放入同一事务，并用 task 级行锁或 advisory lock 防止并发 rollover。

## 4. Rollover 与 Handoff

`RolloverPolicy` 支持 Active Context token threshold、显式阶段 checkpoint 和可配置 Artifact 触发。流程为：验证 draft 与 refs → 预算化 Handoff → 追加 checkpoint event → 持久化 Handoff → 追加 handoff event → 关闭旧 Session → 创建带 lineage 的新 Session。

Handoff 保留 goal、constraints、constraint refs、decisions、current stage、pending items、next actions、Artifact refs、evidence refs、revalidation fields 与 last-known state。预算不足时先删除低价值 summary、key messages、completed steps、entity projection 和 preferences；关键字段不能静默丢弃。连续 rollover 从上一份结构化 Handoff 合并原始 refs，避免 summary-of-summary 成为事实来源。

## 5. Cold Start、Reality Revalidation 与 Drill-down

新 Session cold start 只装载 latest inherited Handoff、被引用关键事件、Artifact projection 和当前 live state。播放器等易变字段以历史值和当前值并列呈现，并追加 `reality_revalidation` event，不覆盖旧 History。

精确读取接口包括 `get_history_event`、`query_task_history`、`get_artifact` 和 `drill_down`。下钻结果作为 `history_drilldown`/`artifact_projection` 回到 Phase 1，继续受到 selection、dedup、source budget、总预算、compression 和 ContextTrace 管理。

## 6. LangGraph Persistent Checkpointer 边界

`create_main_graph()` 和 legacy current-song graph 的运行时默认值均改为 `create_runtime_checkpointer()`。两种模式：

- `AI_LANGGRAPH_CHECKPOINTER_MODE=memory`：创建 `InMemorySaver`，仅供 unit test/local fallback；
- `AI_LANGGRAPH_CHECKPOINTER_MODE=persistent`：必须先调用 `configure_persistent_checkpointer(factory)`；未配置时 fail closed，不会静默退回内存。

迁移到 PostgreSQL persistent checkpointer 的实施方案：

1. 引入与当前 LangGraph 版本匹配的 PostgreSQL checkpointer 包，并由部署环境提供独立 DSN/连接池；
2. 在应用启动阶段运行官方 checkpointer schema setup/migration；
3. 用 `PostgresSaver`（异步入口则使用对应 async saver）的构造 factory 调用 `configure_persistent_checkpointer`；
4. 生产环境设置 `AI_LANGGRAPH_CHECKPOINTER_MODE=persistent`，本地与 unit test 保持 `memory`；
5. 使用 `conversation_id + checkpoint_ns(session_id)` 查询快照，`checkpoint_id` 始终保留为 saver 管理的 execution snapshot；
6. 先做 shadow/canary 恢复验证，再切换流量；验证 crash recovery、并发写入、连接池耗尽和 schema migration rollback。

本阶段没有新增 PostgreSQL 依赖或数据库凭据，也没有将某个厂商 adapter 写入业务层。

## 7. 进程重启恢复 E2E

`test_history_and_lineage_survive_store_reopen` 使用两个独立 Python 子进程：

1. 进程 A 创建 conversation/task/session，写入 History 与 Artifact，生成 Handoff 并 rollover 到新 Session，然后退出；
2. 进程 B 仅凭同一数据库重新构造 Store/Service，查询 active task/session；
3. 进程 B 验证旧 History、Artifact projection、Handoff、两段 Session lineage 均存在；
4. 新 Session 通过 latest inherited Handoff cold start，并将歌曲 ID 从历史值 1 revalidate 为当前值 2。

进程 B 不接收进程 A 的 Python service、store、session 或 event 对象，只读取持久化记录。该测试证明业务任务恢复不依赖进程内对象。

另有身份测试验证：同一 conversation 可同时保存两个不同 task；其中一个 task rollover 后拥有两个 session，另一 task不受影响。运行时集成测试验证 LangGraph `thread_id` 保持 conversation ID，`checkpoint_ns` 使用新 Session ID。

## 8. 验证结果

- `python -m pytest -q tests/test_long_horizon_context.py`：16 passed。
- `python -m pytest -q tests`：847 passed、2 skipped、206 subtests passed。
- 修改范围 Ruff：通过。
- `python -m compileall -q app`：通过。
- 全仓 `ruff check app tests`：仍有 5 个任务外既存问题，位于 `foundation/routing.py`、`pdf/adapters.py`、`source_ingestion/auto_service.py` 和 `source_ingestion/service.py`；本次修改未新增 Ruff 问题。

## 9. 已知限制

- SQLite adapter 适用于 Phase 2 V1 本地/测试；共享生产部署仍需实现 PostgreSQL `ContinuityStore` 及事务级 rollover 锁。
- PostgreSQL LangGraph saver provider 接口和 fail-closed 配置已存在，实际依赖、DSN 与启动 wiring 需随生产基础设施接入。
- Token 仍使用项目现有 Unicode 启发式计数器，需要按生产模型 tokenizer 校准。
- Runtime 已自动记录 user/assistant、route 和结构化 task result；现有 SubGraph 未公开的全部内部 raw Tool/RAG event 尚未逐条自动持久化。
- History 尚无语义向量索引；V1 使用精确 ref、metadata/filter 与 keyword 查询。

## 10. Three-Layer Memory 后续接口

Phase 1 已预留 `memory_source`。未来 Memory 模块只能作为新 source 输入既有 Collector，并继续受 Policy、Budget 和 Trace 管理。History 表示发生过什么，Handoff 表示任务做到哪里，Artifact 表示任务产生什么；本阶段未实现 Working/Short-Term/Long-Term Memory consolidation。

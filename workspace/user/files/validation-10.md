# Journal 独立恢复 checkpoint：方案 A 验收报告

日期：2026-10-09（Asia/Shanghai）。分支：`codex/journal-checkpoint-recovery`。

采用用户在第二轮明确批准的 Gate 0 基线：`1286f5f669d6700a26a790470848915a80b1275d`，原分支 `codex/context-agent-pack-impl-c`。本任务从该 commit 创建新分支，没有合并其他分支。目标为降级重建 `RecoveryCheckpoint`，不是从公开摘要重建完整 `HandoffRun`。

共享工作区并发记录：验证期间另一会话在当前分支创建了 `59ecce53a6a341e864adc9d3e80cb3a4afcff238`（`docs: inventory current PostgreSQL data`），其父提交为上述基线，仅新增 `docs/reports/postgres-data-inventory-20261009.md`。未读取该盘点内容，未回退或改写该用户提交；相关源码基线不变。本任务提交建立在该文档提交之后，只暂存、提交本任务文件。

## 基线核对表

以下各行的基线 commit 均为 `1286f5f669d6700a26a790470848915a80b1275d`；P3 一行另注明只读参考分支。

| 项目 | 文件路径与关键类 / 函数 | 基线事实 |
| --- | --- | --- |
| Journal | `ai-service/app/agent/handoff/continuity.py`：`AgentTransferJournal`、`observe_transfer`、`confirm_checkpoint` | Command 只产生 proposed/rejected；保存成功并读回后确认 committed/reconciled。结果观察为 target_started/target_finished。proposed 不计入 accepted。 |
| 事件与不可变性 | `ai-service/app/agent/foundation/long_horizon/models.py`：`HistoryEventType`、`HistoryEvent`；`store.py` / `postgres_store.py`：`append_event_once`、`query_task_history`；`migrations/postgresql/001_agent_continuity.sql`、`003_context_loop_history.sql` | 按 `event_seq` 分页排序，时间戳与 event_id 为次级排序字段；seq 是表级游标，不能用相邻 seq 的差判断某个 run 丢事件。模型 frozen，Store 同键同内容返回原事件；数据库触发器禁止更新、删除历史。 |
| Writer 与旧路径 | `ai-service/app/agent/foundation/long_horizon/history_writer.py`：`HistoryWriter.append_public_event`、`PUBLIC_SCHEMAS`、`validate_public_document`；`service.py`：`LongHorizonContextService.record_event` | 新 Writer 校验身份、DTO 白名单、私有文本、引用与默认 16,384 字节上限。旧 record_event 直接写 Store，仍与白名单路径并存，本任务不迁移。 |
| Checkpointer | `ai-service/app/agent/checkpointing.py`：`AsyncCompatiblePostgresSaver`、`SessionScopedSaverMixin`；`ai-service/app/agent/handoff/continuity.py`：`JournalCheckpointer`；`graph.py`：`create_handoff_graph` | 使用官方 PostgresSaver。thread_id=conversation_id；handoff conversation 为 `handoff:v1:{thread_id}`。SessionScopedSaver 将 session 映射到 namespace，有子 namespace 时为 `session_id\|checkpoint_ns`。JournalCheckpointer 实际位于 continuity.py。 |
| 执行 state 与 V2 投影 | `ai-service/app/agent/handoff/graph.py`：`HandoffGraphState(MainState)`；`state.py`：`HandoffRun`；`foundation/long_horizon/models.py`：`AgentProgress`、`HandoffDraftV2`、`HandoffProjectionV2` | run 在 `channel_values.handoff_run.run_id`。完整 HandoffRun 要求 original_input/plan/hops/usage 等私有执行数据。V2 AgentProgress 有 last_agent、last_run_id、run_status、recent_run_chain、transfer_phase、accepted_hop_count 及引用。公开 Journal 无法独立还原完整执行 state。 |
| 恢复入口 | `ai-service/app/agent/foundation/long_horizon/recovery.py`：`RecoveryPlanner.decide → RecoveryPlan`；`recovery_executor.py`：`RecoveryExecutor.execute → (plan, report, live)`；`revalidation.py`：`RealityRevalidator` | 既有入口依赖已验证的完整 checkpoint。返回 action 为 resume_target/resume_source/refresh_facts/needs_input/stop；本次增加 RecoveryCheckpoint 分支，原分支保持原行为。 |
| Receipt 与 SHA-256 | `ai-service/app/agent/foundation/long_horizon/journal_contracts.py`：`JournalReader.transfer`；`models.py`：`TransferJournalEvent`、`TransferJournalReference`；`handoff/integrity.py`：`payload_digest` | 新 receipt V2 通过 summary_ref 指向 canonical proposal；SHA-256 实际字段是 proposal 的 summary.payload_digest。旧 V1 receipt 内嵌 summary。摘要算法对 `{contract: handoff-payload:v1, payload: ...}` 的规范 UTF-8 JSON 求 SHA-256；原始完整 payload 未存入公开 Journal。 |
| P3 | 只读参考 `codex/redis-p3-events` / `10e90595ebdef5425e8cb3d5edf01a6ec7e13bfe`：`ai-service/app/redis_events/transfer.py`，`TransferStore.append_event_once` | key 为 `transfer:idempotency:{transfer_id}:{event_id}`，SET NX PX，PG 为最终裁决源。该实现未合入采用的基线，本任务不依赖或合并它。 |

基线事件类型共 24 个：user_message、assistant_message、route_decision、structured_output、tool_call、tool_result、retrieval、rag_evidence、artifact_created、checkpoint、handoff、agent_transfer、session_handoff、run_progress、reality_revalidation、context_trace、error、user_decision、action_ack、client_action、model_usage、history_archive、namespace_migration、session_projection。

Transfer 事件 phase 为 proposed/rejected/committed/reconciled/target_started/target_finished；unknown 仅是旧状态标记，不是可写的 Journal phase。

## 实现与方案 A 取舍

- 新增 `foundation/long_horizon/recovery_checkpoint.py`：frozen、extra=forbid 的 RecoveryCheckpoint/RecoveryHop/RecoveryMissing/RecoveryBudgetRef/RecoveryProvenance。最多 5 个 committed 跳转、64 个用量引用、64 个缺失类别。缺失字段取 null，并同时保留 missing；transfer_phase 不允许 unknown。
- 新增 `foundation/long_horizon/journal_rebuild.py`：`JournalCheckpointRebuilder.rebuild` 返回 JournalRebuildReport，包含 checkpoint、实际 saver config、events_consumed、events_skipped、missing_markers。
- 选择 session 时，由服务器 session 解出长期 task scope，选择该前缀最后一个有 run 身份的事件；选择 run 时，构造器必须绑定 continuity task_id，并在该 task 的既有 sessions 中分页读取，不做全库搜索。
- 冻结上界后分两次按 seq 读取：第一遍确定 run/session 和前缀时间，第二遍折叠 `(after_seq, through_seq]`。page_size 为 1–100；引用可读取起点以前的不可变锚点，但不能引用上界以后的事件。不存在全量 Journal 数组。
- handoff_chain/accepted_hop_count 只折叠有可比较摘要锚点的 committed，按 transfer_id 去重。proposed/rejected 仅更新审计 phase；target_started/target_finished/reconciled 不增加 accepted。后续观察缺 committed、hop_index 断裂、run progress 无法证明完整链时标记 missing，recent_run_chain 不补猜缺失边。
- MODEL_USAGE 只保留事件引用；超过 64 个保留最近引用并标记截断。budget_ref 指向既有 MODEL_USAGE 查询作用域（continuity_task_id + 原 run_id），不保存余额，不调用 totals，不写 usage，不建立第二套账本。
- 通过现有 saver 的 put 写入 `channel_values.recovery_checkpoint`。公共 namespace 为 `journal-recovery`，实际 SessionScoped 存储 namespace 为 `session_id\|journal-recovery`。不写 handoff_run，因此 JournalCheckpointer 不生成新的 committed receipt，正常执行 namespace 不被覆盖。
- checkpoint id、channel version 由冻结 head 与规范状态 SHA-256 确定，ts 来自前缀事件而不是重建时钟。channel version 包含状态摘要，避免 Postgres 跨 checkpoint 的 channel blob 共用版本导致历史状态串写。同一前缀重复写入同一 ID/version，不形成重复 lineage 或 receipt。
- 当前 HistoryEvent 的关闭枚举无法读取未来未知类型。仅在重建读取遇到该问题时，使用 SQLite/PostgreSQL 的有界只读 envelope 适配，保留 opaque 缺失标记；没有放宽 HistoryEvent 类型或公开 Writer 白名单，也没有新增 payload 存储。

### 摘要校验的精确边界

每个可读 transfer 都检查声明的摘要格式，以及阶段 summary 与 canonical proposal 的摘要和身份字段一致性；可检测的不一致写一次 integrity_conflict 并中止，saver.put 不被调用。Legacy 两侧均缺摘要、缺 proposal 或引用超出前缀时记录 missing，不合成摘要，也不把未经锚定的 committed 计入恢复链。

公开 Journal 没有原始 transfer_payload，无法重新计算其 SHA-256，也无法检测“只有唯一 proposal 摘要本身被替换、没有可比较证据”的情况。**因此本方案将 rebuilt_from.digest_verified 收窄为 Literal[False]**，并记录 original_payload_unavailable_for_sha256_recomputation。锚点一致性绝不标成原始载荷验证成功。

### 服务端 Reality Revalidation

RecoveryPlanner.decide 增加对 RecoveryCheckpoint 的接受路径，将未补齐 missing 字段映射为 required_reads/refresh_facts。RealityRevalidator 复用既有 scope、来源、freshness 和用户权限事实检查；补充执行事实仅接受 server_execution 的新鲜服务器观察。

RecoveryExecutor 沿既有 read_facts 回调向服务端读取，保留提供给回调的 identity 上下文；没有可用回调、读取失败或无进展时降级为 needs_input，仍返回未补齐的 required_reads。现有 coordinator.refresh_facts 只能刷新既有事实，不能凭空返回遗失的执行数据。完整执行 checkpoint、载荷证明和断链证明不能被一个观察声明补齐；该路径始终不返回 resume_agent/resume_checkpoint_ref，也不执行工具、重算预算或重放已完成任务。Journal 明确记录 completed/failed 时继续产出 stop。

显式调用示例（同步重建；不加入默认图或定时器）：

```python
builder = JournalCheckpointRebuilder(writer, existing_checkpointer, task_id=continuity_task_id)
result = builder.rebuild(run_id, page_size=100)
# 也可只传 session_id；下一次固定同一前缀：
repeat = builder.rebuild(run_id, through_seq=result.checkpoint.rebuilt_from.journal_head_event_seq)
plan, report, live = await existing_recovery_executor.execute(projection, live, result.checkpoint)
```

## 验证

验证使用 `ai-service/.venv/Scripts/python.exe` 和新增的 `scripts/validate_journal_recovery.py`。入口只注入 fixture 设置，禁止主进程及测试子进程读取项目私有 .env；允许测试自建临时 dotenv。父进程未启用 UTF-8 时，入口在导入应用前用 `-X utf8` 重启自身，保证 Windows 的父子进程 stdout 编解码一致。PostgreSQL 使用已缓存 pgvector/pg16 镜像的一次性容器，仅绑定 `127.0.0.1:60968`，不挂载生产目录；既有 fixture 为每个测试创建并销毁独立数据库。

命令的工作目录均为 `ai-service`。

| 命令 | 结果 |
| --- | --- |
| `.venv/Scripts/python.exe scripts/validate_journal_recovery.py -q tests/test_journal_checkpoint_recovery.py` | 首轮 29 passed；补充边界后 34 passed，0 skipped、0 failed。 |
| 同入口，`-q tests/test_context_loop_recovery.py tests/test_context_loop_projection.py tests/test_context_loop_history.py tests/test_context_loop_payload_integrity.py tests/test_recovery_selfhealing.py tests/test_recovery_combo_query_compat.py` | 115 passed、0 skipped、0 failed；1 个既有 LangGraph durability 警告。 |
| 同入口，`--postgres-port 60968 -q tests/test_journal_checkpoint_recovery_postgres.py` | 1 passed、0 skipped、0 failed。实际 PostgresSaver 验证存储丢失、重复前缀、不同前缀保留旧 blob、独立 namespace、无重复 receipt。 |
| 同入口，`--postgres-port 60968 -q tests/test_journal_checkpoint_recovery.py tests/test_journal_checkpoint_recovery_postgres.py tests/test_long_horizon_context.py::LongHorizonContextTests::test_history_and_lineage_survive_store_reopen` | 最终代码 38 passed、0 skipped、0 failed：37 个新增用例 + 1 个原有中文重启持久化用例，32.64 秒，退出码 0。 |
| 同入口，`--postgres-port 60968 -q tests --junitxml=.dev/journal-checkpoint-recovery/pytest-full-utf8.xml` | **2302 passed、12 skipped、0 failed、0 errors；288 subtests passed**，189 warnings，1905.59 秒，退出码 0。JUnit 共 2602 项（含 subtests），failures=0、errors=0。 |
| `.venv/Scripts/python.exe -m ruff check`，仅本任务 5 个应用文件、2 个测试文件及验证入口 | All checks passed，退出码 0。 |
| `.venv/Scripts/python.exe -m ruff check . --output-format json` | 45 个既有问题（E402=40、F401=4、F841=1），Ruff 退出码 1。全部问题所在的 18 个文件与指定基线逐文件 git diff 相同；新增问题为 0，未顺手修改。 |
| `.venv/Scripts/python.exe -m compileall -q app tests/test_journal_checkpoint_recovery.py tests/test_journal_checkpoint_recovery_postgres.py scripts/validate_journal_recovery.py` | 通过，退出码 0。 |

中间失败如实记录：首次 PostgreSQL 扩展批次为 35 passed、1 failed。失败是新增测试使用含 checkpoint_id 的 config 列举所有历史，实际 PG 接口按该 ID 过滤，只返回一行；已改为显式去掉 checkpoint_id 后断言确有两条历史，保留旧新 checkpoint 精确值及字节一致性断言。修正后该 PostgreSQL 测试通过。早先的全量批次在补充版本边界测试后被停止，不作为验收数字。

编码修正前的完整全量批次为 **2300 passed、12 skipped、1 failed，288 subtests passed**，2111.76 秒。唯一失败：`tests/test_long_horizon_context.py::LongHorizonContextTests::test_history_and_lineage_survive_store_reopen`（第 348 行中文持久化值比较）。原因是本任务新增验证入口只将新子进程设为 UTF-8，父进程仍按 Windows 默认编码读取 text=True stdout；已修正本任务验证入口，补充中文子进程输出回归测试。该旧测试、service/store 和产品逻辑没有因此修改，也没有删、skip 或弱化断言。修正后 38 项定向批次通过，重新运行全量；最终结果以表中 UTF-8 全量批次为准。

12 个既有环境跳过项的原因：真实 TypeSafe API（JEV_LIVE_TEST，1 项）、向开发数据库写 smoke 记录（RUN_LIVE_POSTGRES_SMOKE，4 项）、真实 Qdrant（QDRANT_INTEGRATION_URL，2 项）、未配置专用 PG 重启容器（2 项）、Worldline 真实 PG 重启（RUN_WORLDLINE_POSTGRES_RESTART，3 项）。不使用生产凭据或开关补跑这些验收；新增重建用例及实际 PG saver 用例没有跳过。

新增测试覆盖：交织多跳精确 DTO、各 phase 纪律、摘要/身份冲突与错误审计幂等、整个 checkpoint 存储丢失、空 Journal、缺 proposal、真实未来类型 envelope、分页起止边界及并发尾部追加、用量引用窗口、请求校验与跨 task 拒绝、服务器来源/作用域/新鲜度、服务端不可用降级、跨 scope 无副作用、completed 保持终态、reconciled 不冒充 committed、不同前缀 channel blob 隔离。

## 已知局限与未修改范围

1. 本方案恢复的是公开证据与补齐计划，无法独立恢复完整执行。原始输入、完整计划、原始 hop tool_call_id、执行消息、完整 envelope、预算使用快照和执行 checkpoint 缺失时不会猜测。
2. checkpoint saver 与 History store 的双提交边界保持既有局限；没有新增跨存储事务，也没有改变原提交顺序。重建冻结 seq 上界，读取可见性仍遵循现有 Store；不修复 PostgreSQL 本身的损坏或不可用。
3. 旧 record_event 与白名单路径仍并存；此次未迁移、删除或改变 Journal 契约。
4. P3 Redis 转交幂等键未合入该基线。本方案使用确定的 checkpoint ID/channel version、Store/Writer 已有唯一键保证幂等，不依赖 Redis。
5. 部分 replay window、引用缺失、未知事件或有界引用截断都可能降低完整性；missing 和原账本查询指针保留该事实。不存在定时重建或 Journal 备份/恢复功能。
6. 未改生产 .env、默认图、灰度开关、Guard 阈值或依赖；不部署、不 push。仅验证容器与临时测试数据库用于测试；fixture 数据库随测试销毁，一次性 `--rm` 容器已在最终测试结束后停止并清理。
7. 用户已有的报告改动、删除和数据文件保留，不纳入提交。仓库静态检查的 45 项既有问题保留，不能声称全仓 Ruff 全绿。

验收结论：方案 A 的有界 Journal 重放、证据 checkpoint 写入、幂等与冲突门、服务端重校验 / 无法补齐时降级均完成；新增 37 项用例和完整 pytest 通过。方案 A 不恢复完整执行状态，不声称原始载荷 SHA-256 已验证。全仓 Ruff 仍有 45 项存量问题；12 项依赖外部环境的验收未执行，原因如上。

验证证据保存在 `ai-service/.dev/journal-checkpoint-recovery/`（Git 忽略的本地验证产物）：`pytest-full-utf8.log` / `pytest-full-utf8.xml`、`pytest-targeted-utf8.log` / `pytest-targeted-utf8.xml`、`pytest-related.log`、`ruff-full.json`。中间失败日志也保留，不覆盖最终证据。

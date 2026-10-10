# Redis Phase B-P1：Session 热数据缓存验收报告

日期：2026-10-07。分支：`codex/redis-session-cache`。
基线：P0 验收 HEAD `5744ce6f9bbae9af8e1f1b1bfba545367e78804b`；P0 实现 commit `065297ee2e613f346d15886e187a3be666c1340c`。

## Gate 0 核对

先只读核对 P0 报告、验收 JSON 和代码，再创建本轮分支。P0 验收记录为 2355 passed、12 skipped、288 subtests passed、185 warnings，退出码 0。基线含 Phase 1/2/3 和 P0；本轮没有从 main 重做，也未改 P0。

| 核对项 | 基线实际形态及本轮接入边界 |
| --- | --- |
| Redis 依赖/配置 | `requirements.redis.txt` 复用默认依赖并加 `redis==5.2.1`；P0 仅在 `BUDGET_LEDGER_BACKEND=redis` 时读取 `REDIS_URL`，缺席/不可用走原预算逻辑。本轮复用该可选依赖和 URL，设置独立缓存开关 |
| Session 元数据 | `models.py::SessionRecord` 包含 task/conversation/session、previous/parent/inherited_handoff、created_at/closed_at。`PostgresContinuityStore.create_session/close_session/get_session/require_session/active_session/sessions_for_task` 读写 `agent_session`。SQLite 实现保留 |
| 可变工作状态 | 存于 LangGraph checkpoint，并不在 SessionRecord 内。`SessionScopedSaverMixin.get_tuple/put/put_writes` 映射 session namespace；`AsyncCompatiblePostgresSaver` 提供异步调用。普通热读缓存其最新 tuple，保持原 PG 写入和 serializer；执行/恢复读取及指定 checkpoint_id 的见证直接读 PG |
| checkpoint 提交 | `JournalCheckpointer.put/aput` 先保存 checkpoint，再 `AgentTransferJournal.confirm_checkpoint` 写 committed/reconciled 和 RUN_PROGRESS。没有反转、合并或绕过该顺序 |
| 持久投影 | `PostgresContinuityStore.put_handoff` 经 `archive_projection` 写 SESSION_PROJECTION，`agent_handoff.body` 保存 SessionProjectionPointer。`get_handoff -> resolve_projection` 校验 Journal 类型/作用域/身份并物化 v1/v2；旧表完整投影由 `read_handoff_projection` 只读恢复 |
| 当前进度投影 | `ProgressProjector.project_progress(session, event_cursor=0, projection_budget=None, *, draft)` 查询持久回执、验证 committed/reconciled 链与结果，再用原 `build_handoff` 完整 envelope 预算。结果还依赖 draft/cursor/budget/policy/counter，不能只凭 session_id 复用任意一次结果 |
| ContextPack 输入 | `LongHorizonContextService.build_active_context -> cold_start -> require_session/get_handoff`；`ContinuityCoordinator.begin_call` 从 checkpointer 和会话投影构造 `continuity_context`；旧 streaming 的 `_continuity_start` 使用 cold_start。Collector 读取已传入的 handoff/key_events/artifacts/revalidation；Assembler 本身不查 PG |
| 生命周期 | 现有会话通过 close/rollover 关闭，没有统一的 Session 超时配置。本轮仅加入可选缓存 TTL，不新增业务会话过期规则；部署者可把 TTL 设置为自己的会话超时值 |

核对后按上述真实接口实施：Session 元数据、最新 checkpoint 工作状态、归档/进度物化视图分别缓存。没有把 SessionRecord 假装成 graph state，也没有把 Journal 当成独立重建 checkpoint 的事实源。

## Key 及配置

| Key | 类型/内容 | 默认 TTL |
| --- | --- | --- |
| `session:{session_id}` | hash；`record:v1` 为 SessionRecord JSON；`checkpoint:<作用域摘要>` 为最新 checkpoint tuple。摘要区分 execution thread 与子 namespace；工作状态按原 allowlist serializer 编码，保存 messages/metadata/parent/pending_writes 的实际类型 | 3600 秒 |
| `sessionview:{session_id}` | hash；`archive:<handoff_id>` 为完整物化归档；`progress:<输入摘要>` 为 HandoffDraftV2 视图，输入摘要包含 draft、cursor、projection_budget、完整 policy、counter 版本、缓存 schema | 60 秒 |

`{session_id}` 在此是占位符，不另加 Redis hash tag。真实 session_id 自带 `session:` 前缀，因此示例完整 key 为 `session:session:<UUID>`。每次回填设置 key TTL，并用 Redis 服务端 TIME 写该数据字段独立的 `__expires:<字段>` 到期时间；HMGET 原子读取文档及其到期时间，过期即回源。因此回填新变体刷新 hash TTL 时，也不能续活旧字段；不同应用主机的时钟不参与到期判断。每个 hash 最多缓存 32 个数据字段，淘汰时一起移除其到期字段。`__generation` 是失效墓碑，`__thread_id` 只供 checkpoint 管理操作定位，这些元数据均不作为业务状态。

| 可选环境项 | 默认/行为 |
| --- | --- |
| `SESSION_CACHE_BACKEND` | `none`；显式 `redis` 才启用，未知值报配置错误 |
| `REDIS_URL` | 与 P0 共用；不开缓存时不连接 Redis。缺少 URL 或可选依赖时 warn 并使用原 PG 实现 |
| `SESSION_CACHE_TTL_SECONDS` | 3600；必须为正整数，按部署会话超时配置 |
| `SESSION_VIEW_TTL_SECONDS` | 60；必须为正整数且不大于 Session TTL |
| `SESSION_CACHE_TIMEOUT_SECONDS` | 0.25；连接/命令超时，必须为有限正数 |

仅 PostgreSQL 后端启用本缓存；SQLite、memory checkpointer、显式注入的测试/provider factory 保持原路径。没有改 requirements、生产环境文件、预算配置或部署配置。

## cache-aside 与失效

`RedisSessionCache.read` 先读 Redis，命中解码得到新的视图对象；miss 只执行一次原 PG loader，再回填。PG loader 报错继续报错，不吞掉、不重试其副作用。未知 Session/不存在的 checkpoint 不做负缓存。缓存类型/作用域损坏时失效并读 PG；不会把连接串、异常细节或会话正文写入日志。

`CachedPostgresContinuityStore` 继承原 PG adapter，保留原连接池、SQL、事务和 `isinstance(PostgresContinuityStore)` 身份，兼容 P0 的 PostgresBudgetLedger。create/close/put_handoff/Journal append 全部先走原 PG 方法；外层事务成功提交后失效，事务内读取直接走 PG。rollback 不发布事务内记录、不清掉原缓存；rollover 的旧会话关闭、新会话创建、投影归档仍在原事务内。

Journal 新事件失效本 Session 的 view，包含 proposal/rejected/decision 等会改变公开观察的事件；accepted hop 仍完全由原 projector 的 committed/reconciled 验证决定。缓存 miss 用原投影函数完整物化；cache hit 不重算 token_count、不改 schema 或已确认链。自定义可变 token counter 没有稳定缓存身份时直接走原读取。

回填 WATCH 对应 key，再读 PG，最后 MULTI/HSET/EXPIRE。失效用事务 DEL/HSET 随机 generation/EXPIRE，空 key 也留下墓碑，因此失效前开始的旧读取无法在提交后把旧数据回填。Redis WATCH 的修改检测与事务条件提交见[官方事务说明](https://redis.io/docs/latest/develop/using-commands/transactions/)。本轮用真实 Redis 的阻塞 loader/线程竞争验证该行为。

`CachedPostgresSaver` 的 put/put_writes 先执行原 PG saver，再失效 Session hash；latest tuple 包含 pending writes，更新后不会继续用旧 pending 状态。delete_thread 等管理操作按 thread 定位失效；无法确认管理失效时本进程保守禁用热读。指定 checkpoint_id、list/历史祖先仍直接读 PG。`authoritative_session_evidence` 让 ContinuityCheckpointer 的同步/异步执行读取、coordinator 的恢复/rollover draft 在原函数运行期间跳过缓存，确保 Redis 失效丢失也不能影响执行事实。该 ContextVar 自动随 to_thread 传播，退出恢复原状态；没有新增 Guard 或恢复算法。JournalCheckpointer 保存及确认顺序不变。

ContextPack 的接入只发生在数据读取边界：cold_start/coordinator 的归档读取使用 `read_session_projection`；ProgressProjector 外层装饰器复用有稳定输入身份的视图；runtime 显式开启时选择缓存 PG adapter/saver。没有缓存整个 cold_start/build_active_context，因为其中有 live revalidation 和历史审计写入。每次 live state 验证和 CONTEXT_TRACE/REALITY_REVALIDATION 写入保留；Collector/Selector/Assembler、L1 选择、L2/L3、路由、Guard、转交和预算实现无修改。

## 故障、重建及边界

Redis 连接/读写/回填/失效故障记 warn，当前读改走 PG，PG 写成功不会因可选缓存失败而回滚。失效失败的 key 在本进程进入 dirty 集合，下次热读前重试失效；确认失效前只返回 PG。Redis 数据丢失后，SessionRecord 从 agent_session、工作状态从 checkpoint 表、归档从 SESSION_PROJECTION 或旧表、进度从已提交 Journal 自动回填；无需把缓存反向当成事实源。

这是 cache-aside，不是 PG/Redis 分布式事务。若 PG 提交成功后写进程终止或其 Redis 失效失败，其他进程的普通热读可能在 TTL 内持有旧缓存；本轮没有新增持久 outbox/跨进程故障广播。短 view TTL 和本进程 dirty 重试限制该窗口。执行 checkpoint、恢复/rollover 投影及恢复祖先读取保留 PG，避免陈旧热读授权执行或改变确认链。普通读的该限制需在后续强一致缓存方案中处理，不能声称故障下跨进程零陈旧。

## 验收

本轮新增用例使用真实 loopback Redis 和独立 PG 数据库，覆盖：命中零 PG 连接获取、miss 回填、create/close/Journal/checkpoint/pending-write 失效、回滚不污染缓存、旧在途回填竞争、v1/v2 指针/旧表精确物化、proposal 不计 accepted hop、投影输入隔离、ContextPack 输入等价、Redis 丢失重建、故障 warn/直读 PG/恢复后重试失效、坏文档/作用域拒绝、TTL/变体上限、runtime 开关和 P0 adapter 身份兼容。

另补故障回归：模拟 PG 提交后 producer 未执行 Redis 失效，新 reader 的普通缓存读仍能看见旧值，但经过实际 ContinuityCheckpointer 的同步/异步执行读取拿到 PG 新状态；恢复投影读取也跳过刻意保留的旧视图。

最终相关套件：147 passed、19 warnings，88.40 秒，退出码 0；包括新增缓存、P0 预算、Phase 3 契约、coordinator、投影、恢复和 checkpoint 验收。先前相关套件 83 passed，初次新缓存套件 26 passed。一次相关命令误写不存在的 `tests/test_checkpointing.py`，无用例执行；改为实际的 `test_handoff_checkpoint_acceptance.py` 后运行通过，没有修改或跳过原测试。

首次全量运行通过 2387 passed、12 skipped、185 warnings、288 subtests passed（318.53 秒），但运行末期的复核补充了执行证据直接读 PG 的边界与两个故障用例；该次源码哈希不再固定，因此不作为最终交付验收。147 项回归通过后重新运行完整固定版本。

第二次全量通过 2389 passed、12 skipped、185 warnings、288 subtests passed（312.98 秒）；末期 TTL 复核发现 hash 的新变体回填会延长旧字段保留，增加字段独立到期和回归用例。新缓存套件 35 passed（19.40 秒）；HMGET 读取文档/到期时间的小调整随最终全量再次验证。最终验收采用这些改动后的固定源码版本，前两次结果仅作过程记录。

| 验收项 | 用例 |
| --- | --- |
| 二次命中零 PG 查询 | `test_record_miss_refill_then_hit_with_zero_pg_queries`、`test_working_state_cache_hit_matches_pg_and_writes_evict`、`test_progress_hits_zero_pg_and_new_proposal_cannot_fake_committed_hop`、`test_archive_cache_preserves_exact_materialization_and_old_table` |
| miss/丢失后回源重建 | `test_real_hash_miss_then_hit_never_calls_cold_loader`、`test_redis_loss_rebuilds_records_checkpoints_and_projection_from_pg` |
| 写入/Journal 提交失效 | `test_session_close_invalidates_after_commit_and_rollback_retains_cache`、`test_journal_rollback_keeps_view_then_commit_evicts_it`、`test_pending_writes_and_delete_thread_evict_working_state` |
| Redis 故障/恢复 | `test_redis_outage_falls_back_and_failed_invalidation_is_retried`、`test_redis_outage_covers_working_state_and_committed_view`；日志同时断言不输出连接/异常正文 |
| 投影与 L1 输入一致 | v1/v2 + pointer/legacy 四组参数精确比较；`test_context_pack_cached_and_uncached_projection_inputs_identical` 比较 messages/items/tokens/manifest，保留审计写入 |
| 并发及 TTL | `test_empty_key_invalidation_blocks_old_inflight_refill`；`test_refilling_another_field_cannot_extend_old_field_freshness`；配置、坏文档和 32 变体边界用例 |
| 执行事实不读旧缓存 | `test_execution_reads_latest_pg_when_writer_dies_before_cache_eviction`、`test_recovery_projection_bypasses_stale_hot_view` |
| Redis 缺席行为不变 | runtime 开关/依赖缺失/缺 URL 用例；全量保持 default none，启用缓存路径由新增真实 PG/Redis 用例覆盖 |

从 `ai-service` 目录可复现最终全量（URL 为一次性 loopback 测试凭证，须先准备隔离 PG 和 `redis-server`）：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -c "import os,sys,pytest;os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PERSISTENCE_BACKEND='sqlite',AI_CONTINUITY_STORE_MODE='sqlite',DATABASE_URL='postgresql://postgres:redis-session-test-only@127.0.0.1:15469/moodflow_redis_session_test',MEMORY_BACKEND='memory',CHECKPOINT_BACKEND='memory',JEV_ENABLED='false',DEEPEVAL_DISABLE_DOTENV='1',AI_RAG_RETRIEVAL_MODE='empty',BUDGET_LEDGER_BACKEND='history',SESSION_CACHE_BACKEND='none');sys.path.append('.venv-eval/Lib/site-packages');raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','-p','xdist.plugin','-n','4','--dist','loadfile','tests','evals/tests','-q','--disable-warnings','--tb=short']))"
```

新增缓存套件把 pytest 最后的目录参数替换为 `tests/test_redis_session_cache.py tests/test_redis_session_postgres.py`；147 项相关套件另包含 context_loop_coordinator/projection/recovery、context_contracts_phase3、handoff_checkpoint_acceptance、redis_budget/redis_budget_postgres。

静态验证：对本轮 10 个 Python 文件运行 `.venv/Scripts/python.exe -m ruff check <文件列表>`、`.venv/Scripts/python.exe -m compileall -q <文件列表>`，以及任务暂存区 `git diff --cached --check`，均通过。只新增测试文件，已有测试及其断言/条件跳过未修改。没有前端变更，因此没有前端构建或视觉验证；没有调用远程模型，也没有把原本需要 live 服务/凭证的条件测试算作已验证。

最终固定版本全量通过：**2390 passed、12 skipped、288 subtests passed，0 failures / 0 errors**，退出码 0；pytest 用时 **311.84 秒**，统计插件用时 312.01 秒。185 个 warning 与 P0 相同。相比 P0 新增 35 个通过用例；12 项 skip 的 nodeid 集合与 P0 完全一致，未新增条件跳过。实现/测试的 10 个源码文件运行前后 SHA-256 均相同，详见 `verification-summary.json`。

原 12 项条件跳过仍涉及 live Qdrant、live PG/远程 embedding/模型、独立 PG 服务重启等外部条件，未声称这些 live 场景已验证。Session 新增 PG/Redis 验收全部实际运行，无条件跳过。任务隔离 PG 容器及测试 Redis 进程已清理；用户原有服务没有停止或修改。

## 提交与范围复核

实现及测试 commit：`112d52aea46fd155a7639138b5f0f19028ebac10`（`Add optional Redis session and projection caches`）。验收报告与统计另作本任务文档提交。

实现提交仅包含本轮 10 个 Python 文件；报告提交仅包含本目录文件。P0 RedisBudgetLedger、Lua、BudgetLedger Protocol、RequestLedger、DurableAccounting、requirements.redis.txt 和已有测试均与基线一致；L1 算法、预算阈值及降级顺序未改。独立分支未 push，未部署，未修改生产配置。工作区原有报告修改/删除及其他未跟踪文件保留，未提交到本任务。

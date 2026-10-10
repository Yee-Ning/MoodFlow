# Redis Phase B-P0：RequestLedger 原子预算验收报告

日期：2026-10-07。分支：`codex/redis-budget-ledger`。
基线：West 确认的 Phase 3 HEAD `d1ff818c9318b532dfe5de81fb0c5cf2865a789e`，包含 Phase 1/2/3；本轮不等待 main 合并。

## Gate 0 核对与授权

实施前读取 `docs/reports/context-engineering-phase2-20261005/report.md` 及 `verification-summary.json`。历史验收记录为 2316 passed、12 skipped、288 subtests passed，退出码 0。当前 Phase 3 的 budget.py、DurableAccounting 和 context_policy 与 Phase 2 验收分支相同；runtime 另含 Phase 3 的 Pack 生命周期清理和 CallManifest 校验。

| 核对项 | 实际形态 |
| --- | --- |
| BudgetLedger Protocol | `add(task_id, agent, input_tokens, output_tokens, model, *, estimated=False)`；`get_task_total(task_id) -> TokenUsage`；`get_agent_total(task_id, agent) -> TokenUsage`；`get_model_breakdown(task_id) -> dict[str, TokenUsage]` |
| TokenUsage | `input_tokens`、`output_tokens`、`estimated_tokens`、`calls`；`total_tokens = input_tokens + output_tokens` |
| PostgreSQL 适配器 | `app/llm/budget.py::PostgresBudgetLedger(accounting)`；要求 accounting.writer.store 为 PostgresContinuityStore，复用连接池和不可变 JSONB 历史 |
| L2/L3 | context_policy 初始阈值 16000/32000；运行时取其与 RunLimits 的较小值 |
| 实际调用 | accounted_call 依次调用 RequestLedger.reserve、DurableAccounting.reserve、SDK、RequestLedger.settle、DurableAccounting.settle；Protocol 原本只提供记账/查询视图，不参与预留 |

首次核对发现本地 main `0fdf4f71dfa19a9a7fb57b04ed6b73719c02cd72` 不包含 Phase 2，且仅切换查询适配器无法接管原子预留，因此按硬门停止，未先写实现。West 随后明确确认：从 Phase 3 HEAD 创建任务分支；允许在 RequestLedger 接入 Redis 原子预留和原逻辑回退，并在 DurableAccounting 增加结束 flush。以下实现基于这两项授权。

## 实现与范围

新增 `app/llm/redis_budget.py::RedisBudgetLedger(accounting)`，完整实现原 Protocol，额外提供 reserve、settle、reconcile、rebuild、flush；没有改 Protocol 或 TokenUsage。输入/输出、模型、估算和 JEV 展示维度继续从原冷账本查询，以免单一热计数替代拆分事实。add 同样复用原不可变预留/结算事件，再同步热计数。

RequestLedger 的 remaining、任务检查、预留和结算接入可选热适配器；模型与业务调用方的签名、accounted_call 的调用顺序保持原样。DurableAccounting 让热预留与冷 MODEL_USAGE 共用 call_id。每个异步调用通过 ContextVar 保留自己的预留身份，同 owner、同预留金额的调用即使乱序完成，也不会交换结算记录。

改动仅涉及热适配器、Lua、运行时接入、持久化接入、PublicUsage 的可选快照字段、对账 worker、可选依赖和新增测试。没有改 L1 组装、预算阈值、降级顺序、路由、Guard、转交、生产环境或部署配置。

## Key 与生命周期

run_id 是同一次原始请求的服务器 run_id，覆盖转交和恢复。下列 `{run_id}` 是 Redis hash tag 的实际大括号；同一 run 的 key 共用 slot。

| Key | 内容 |
| --- | --- |
| `budget:l2:{run_id}:general_chat/curator/explorer` | 对应执行 owner 的已消耗 + 已预留 token |
| `budget:l3:{run_id}` | 全 run 的已消耗 + 已预留 token，每个调用只计一次 |
| `budget:receipts:{run_id}` | call_id → owner、当前计入金额、reserved/settled 状态；只存数字和公开标识，不存 prompt/模型正文 |
| `budget:floor:{run_id}` | 恢复时已经保存的保守累计下限，避免重新获得已花费额度 |

JEV 的 L2 仍计入调用它的 owner；Protocol 查询继续支持独立的 billing_agent=jev 维度。没有把 owner 合计和 JEV 展示维度重复计入 L3。

TTL 走可选配置，RequestLedger 使用 `max(配置 TTL, RunLimits.run_timeout_seconds)`，至少覆盖允许的执行生命周期。活动预留、结算、绑定与结束 flush 刷新 TTL。定时对账保留剩余 TTL，不延长结束任务的保留期；不存在的热数据由下一次绑定或显式 rebuild 恢复。

## Lua 原子预留与结算补差

脚本独立存放于 `app/llm/lua/reserve_budget.lua`。它先检查 L3，再检查 L2；两层都超限时保持 Phase 2 的任务优先信号。任一拒绝返回小写 `l3`/`l2`，两个计数器和 receipt 都不修改；成功在一次脚本执行中增加两个已用计数，并写 receipt。增加已用数等价于扣减可用额度。缺失计数返回 rebuild，适配器从冷事实重建后再试；相同 call_id 的重试不重复预留。

Lua 的服务器原子执行保证来自 [Redis 官方文档](https://redis.io/docs/latest/develop/programmability/eval-intro/)，本轮另外使用真实 Redis 的多线程用例验证，没有用 fakeredis 模拟原子性。

拒绝交由现有 fail_budget：L2 保持截断→摘要→停拉→诚实降级，L3 保持 task_degradation→refuse。budget.exceeded 和原 phase=budget 事件仍走既有 pg History 路径，不写入 Redis。

结算执行 `INCRBYFLOAT(actual - reserved)`，负 delta 退还多预留部分，正 delta 保留真实超额。两个独立原子命令放在 WATCH/MULTI 事务中，与 receipt 状态一起提交，没有增加结算 Lua。[INCRBYFLOAT 官方文档](https://redis.io/docs/latest/commands/incrbyfloat/)支持负增量。receipt 使提交成功但回复丢失后的重试不会再次退款；冲突的第二次结算拒绝执行。

供应商实际账单仍完整保存，不把账单截断到预算上限。共享热计数因实际用量超过预留而超额时，也阻止结果继续执行并走现有降级路径；仅有在途预留恰好填满共享计数不会据此截断已获准的结果。

## pg 结算、对账与重建

最外层 request_scope 结束时调用 DurableAccounting.flush_budget；嵌套 agent scope 不额外 flush。flush 经一致性对账读取终值，再通过原 HistoryWriter 白名单追加一条 MODEL_USAGE：phase=settled、tokens=L3、redis_snapshot 含三个 L2 值，call_id 为终值内容的摘要。同一终值重复 flush 幂等。

该快照没有配对的 reserved 事件，因此原逐调用账单查询和恢复不再加一次总数。pg 原本的每次 reserved/settled 事实全部保留；快照是终值与保守下限的结算记录。PublicUsage 校验快照 owner、非负值、phase 和总数一致性。

reconcile/rebuild 从 pg MODEL_USAGE 的首个 owner baseline 和每次调用的 reserved/settled 重放；已有结算替换预留，未知费用的冷预留继续计入。再合并尚未落入 pg 的热 receipt 以及保守累计下限。历史 flush 的下限取最大值而非相加，避免重复计费，也保留 Phase 2 的恢复累计不下降语义。

维护通过 WATCH 观察三个 L2、L3、receipt 和 floor，再以事务修正，不能覆盖同时发生的 Lua 预留或退款。正常的在途差额不会被误认为可退还额度；真实漂移记 warn 并修正。

`scripts/reconcile_redis_budget.py` 是独立周期 worker，默认间隔 60 秒；`python -m scripts.reconcile_redis_budget --once` 执行单次扫描。它只扫描尚有 L3 热 key 的 run，并通过参数化 SQL 在 pg 解析所属历史。完全丢失的 Redis 数据可用 `RedisBudgetLedger.rebuild(run_id)` 恢复，正常 RequestLedger 再绑定时也会自动重建。没有安装定时服务或启动生产 worker。

## 可选配置与故障决策

| 环境项 | 默认/行为 |
| --- | --- |
| `BUDGET_LEDGER_BACKEND` | 默认 history：仍按原 store 选择 PostgreSQL/SQLite；显式 redis 才启用热预算 |
| `REDIS_URL` | 仅在启用 Redis 时读取；没有硬编码连接地址 |
| `BUDGET_REDIS_TTL_SECONDS` | 3600，正整数，运行时至少覆盖 run timeout |
| `BUDGET_REDIS_TIMEOUT_SECONDS` | 0.25，正数，限制连接与命令等待 |
| `BUDGET_REDIS_RECONCILE_SECONDS` | 60，正数，独立 worker 间隔 |

可选依赖单独放在 requirements.redis.txt，使用 redis-py 5.2.1；原 requirements.txt 和生产安装配置未改。未选择 Redis 时不导入 Redis 客户端或发起 Redis 连接。

连接缺失、可选依赖缺失、连接/命令失败或维护争用无法确认时，记 warn 并执行 West 确认的原进程内预算检查，逐调用账单照常写 pg。当前 RequestLedger 在故障后继续原逻辑，下一次绑定/flush/worker 可对账恢复；没有绕过 ensure_available。连接串和异常详情不进入日志。

预留回复丢失时，热 receipt 与随后冷事件仍使用同一 call_id，恢复不会重复收费。结算回复丢失时，原子事务中的 settled receipt 防止重复退款。结束 flush 期间 Redis、pg 或 lease 不可用则保留已写的逐调用 pg 事实，记 warn 并等待重建；次级快照失败不覆盖原调用结果、不打断请求清理。实际调用的 pg 预留/结算写入错误仍正常向上传播，不能借快照容错绕过事实写入。

## 验收与验证

实现冻结后的专项回归：**89 passed，退出码 0**。覆盖新增 Redis/pg 测试与 Phase 2 预算、Handoff 预算、Phase 3 契约。静态检查、编译检查及 diff 检查通过。

| 验收项 | 证据 |
| --- | --- |
| 真 Redis 并发原子性 | 24 线程、240 次预留；L2=200、L3=500，恰好 50 次成功，各 L2 ≤200、L3=500 |
| L2/L3 拒绝与原降级 | 独立 ledger 的陈旧本地视图分别打满 L2/L3，断言层级、原阶段顺序、History 记录及拒绝不改计数 |
| 补差精确、重复退款防护 | 120 预留结算为 27；正补差、重复结算、提交成功后回复丢失均有断言 |
| 异步并发与真实超额 | 同金额调用乱序结算；共享实际超额保留真实账单并拒绝继续执行 |
| 结束 flush | 真 PostgreSQL 存一条准确快照；重复 flush 不新增账单，删除不可变事件被拒绝 |
| 数据丢失与保守恢复 | 全部/部分 key 丢失、pg reopen、未知费用预留、已有保守累计下限重放 |
| 漂移修正与定时执行 | 真 pg 对账修复 900→34；在途预留不误退款；worker 周期及不延长 TTL |
| 缺席/故障回退 | 未配置、无 URL、无依赖、断连、预留回复丢失、初始化/flush 不可用；pg 用量与原拒绝结果正确；pg 结束读/写快照失败不阻断清理，主账单可重放 |

最终完整套件：**2355 passed、12 skipped、288 subtests passed，0 failures / 0 errors，退出码 0**。pytest 用时 931.50 秒（15 分 31 秒），统计驱动含启动开销为 931.91 秒；185 个 warning 未作为失败。相对 Phase 3 的 2332 passed 新增 23 个通过用例。

verification-summary.json 已逐项比对 Phase 3 的 12 个跳过 nodeid，清单完全相同：live JEV、开发数据库 smoke、真实 Qdrant opt-in、指定旧容器重启及 Worldline opt-in。本次新增真实 Redis 与 pg 用例全部执行，没有新增跳过。288 个 unittest subtest 独立列出，不重复计入 2355；原始 passed report 为 2643。九个实现/测试/依赖文件的 SHA-256 与完整运行前的冻结版本一致。

本轮命令从 ai-service 执行。完整运行的临时统计驱动只记录数量、失败/跳过 nodeid 与耗时，不纳入提交。等价完整命令如下；数据库 URL 仅含本次一次性测试凭证：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -c "import os,sys,pytest;os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PERSISTENCE_BACKEND='sqlite',AI_CONTINUITY_STORE_MODE='sqlite',DATABASE_URL='postgresql://postgres:redis-budget-test-only@127.0.0.1:15468/moodflow_redis_budget_test',MEMORY_BACKEND='memory',CHECKPOINT_BACKEND='memory',JEV_ENABLED='false',DEEPEVAL_DISABLE_DOTENV='1',AI_RAG_RETRIEVAL_MODE='empty',BUDGET_LEDGER_BACKEND='history');sys.path.append('.venv-eval/Lib/site-packages');raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','-p','xdist.plugin','-n','4','--dist','loadfile','tests','evals/tests','-q','--disable-warnings','--tb=short']))"
```

使用环境已有的 pytest-xdist，按文件分配四个进程；没有安装新的并行测试依赖，也没有筛除用例。[loadfile 的官方说明](https://pytest-xdist.readthedocs.io/en/stable/distribution.html)保证同一文件内的测试由同一进程运行。串行中止和实现冻结前的试跑不作为完整验收结果。

相同隔离环境下的最新专项命令为 pytest 运行 `tests/test_redis_budget_postgres.py tests/test_redis_budget.py tests/test_context_budget_phase2.py tests/test_handoff_budget.py tests/test_context_contracts_phase3.py -q --disable-warnings --tb=short`，结果 89 passed，用时 83.47 秒。

以下检查退出码均为 0：

```powershell
.venv/Scripts/ruff.exe check app/llm/redis_budget.py app/llm/runtime.py app/agent/foundation/long_horizon/accounting.py app/agent/foundation/long_horizon/public_events.py scripts/reconcile_redis_budget.py tests/test_redis_budget.py tests/test_redis_budget_postgres.py
.venv/Scripts/python.exe -m compileall -q app/llm/redis_budget.py app/llm/runtime.py app/agent/foundation/long_horizon/accounting.py app/agent/foundation/long_horizon/public_events.py scripts/reconcile_redis_budget.py tests/test_redis_budget.py tests/test_redis_budget_postgres.py
```

对账 CLI 的 `--help` 检查通过。Protocol、策略、阈值、路由、Guard、转交与既有测试文件相对基线的 diff 均为空；新增测试未替换、删除、跳过或弱化原断言。

## 限制与交付

- 跨进程原子预留依赖 Redis 可用。故障回退按 West 的明确授权使用今天的进程内检查与 pg 账本，不声称故障时新增了 pg 跨进程原子预留。
- 热预算接在既有 DurableAccounting 执行作用域；没有冷持久化作用域的独立 RequestLedger 保持原逻辑。
- 新增可选 redis_snapshot 字段由本次 reader 支持；旧 Phase 2/3 的严格 PublicUsage reader 不认识该字段，回滚代码时须保留兼容读取能力。关闭热后端而保留本次 reader 可以继续读这些历史。
- 真实 Redis 验证使用本机一次性 Windows Redis 8.10.0 进程；Redis Cluster、Sentinel、跨机网络/故障转移和供应商 live 压测未验证。key 使用共同 hash tag，连接工厂仍为标准 Redis 客户端。
- pg 验证使用仅监听 127.0.0.1:15468 的一次性 pgvector/pgvector:pg16 容器；使用既有迁移和随机测试数据库，没有生产迁移。验证后核对创建时完整 ID `d58176bcb6a33443306f075f5ebbdee8a2bbc4090dd3ef03e1e7122f797b04a7`、容器名与任务标签，只移除该容器及其匿名测试卷；临时统计驱动也已删除。
- 无前端改动，不需要前端构建或视觉验证；没有 push、部署或修改生产配置。原有无关未提交改动不纳入本轮提交。

实现 commit：`065297ee2e613f346d15886e187a3be666c1340c`（`feat: add optional Redis atomic request budgets`），包含本任务的 9 个实现/测试/可选依赖文件。提交前核对 git status、完整相关 diff 和精确暂存清单，`git diff --cached --check` 通过。

本报告和 verification-summary.json 作为独立文档提交，只包含本任务报告与最终统计。没有 push、部署、改生产配置或改写既有历史；原有无关修改和删除保持未提交状态。

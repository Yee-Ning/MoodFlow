# Context Engineering Phase 2 验收报告

日期：2026-10-05。分支：`codex/context-engineering-phase2`。基线：Phase 1 commit `9508a7bd54f8def193354b48faf6e10168fcb9cb`。

## Gate 0 核对

动手前已阅读 `docs/reports/context-engineering-phase1-20261005/report.md` 并核对实现。Phase 1 报告记录完整套件 2287 passed、12 skipped、288 subtests passed。用户已确认已有账本的兼容扩展方案；本轮按提供的 `token-budget-design.md` 实施。

| 对接项 | Phase 1 的实际形态 | 本轮对接 |
| --- | --- | --- |
| ContextPack manifest | `tokens`、`budget`、`sources`；各来源含 `candidate_tokens/final_tokens/events/dedup_dropped` | 保留选择/组装字段，在实际 SDK 调用后生成合并的 `context_engineering.call_manifest` |
| MainState manifest | `lite/full/source_status`；full 按 route 分组 | 保留入口形态，不把输出账单写回选择策略 |
| Source registry | `app/agent/foundation/context_sources.py` 的 SourceRequest/SourceRegistry | 增加 `allow_new_sources`，停拉时返回 `budget_stopped`，不调用来源 |
| context_policy | `app/agent/foundation/context_policy.json` 的 lite/sources/profile_categories | 新增 budget 区块，原来源优先级和配额保持原值 |
| 账本 | 没有名为 BudgetLedger 的抽象；已有 `app/llm/runtime.py::RequestLedger` 和 `long_horizon/accounting.py::DurableAccounting` | 用户确认复用；扩展 MODEL_USAGE 事件和现有调用边界，未另建余额表 |
| token 数据 | RequestLedger 保留三 agent 的总量、reported/estimated、调用数、重试数；DurableAccounting 预留和结算总量 | 新增 input/output、model、billing_agent、estimated、max_output_tokens；旧事件仍可读取 |

Gate 0 的“已有 RequestLedger/DurableAccounting”差异已获用户确认，后续实施没有按空账本假设另起实现。

## 账本与存储

`app/llm/budget.py` 定义 BudgetLedger Protocol：`add(task_id, agent, input_tokens, output_tokens, model, estimated=...)`、`get_task_total`、`get_agent_total`、`get_model_breakdown`。返回 TokenUsage，含输入、输出、估算 token、调用数以及 total_tokens 属性。

PostgresBudgetLedger 复用 PostgresContinuityStore 的连接池、现有 `agent_history_event` JSONB 存储和 HistoryWriter 白名单。HistoryBudgetLedger 是同一接口的 SQLite/公开历史适配器，供既有本地持久化和测试使用。策略不依赖数据库 SQL；替换持久化适配器不需要改 L1 或预算策略。本轮未引入 Redis 或新依赖。

- `task_id` 是一次原始用户请求的 run_id，包含转交和恢复，不是 route 子任务 ID，也不是长期 conversation/task ID。
- 保留 existing MODEL_USAGE 的 baseline/reserved/settled/retry 纪律；新增 budget 阶段记录降级事件，token=0，不计费用。
- 调用先预留，再按 usage 结算；查询按 call_id 用结算替换预留。失败、取消或进程死亡后没有结算的预留继续按估算收费，不能成为免费调用。
- 旧的非零 baseline 缺少输入/输出和模型时，显式归为 unknown 模型、估算输入。只计每个 owner 最早的 baseline，避免会话迁移后的 baseline 再次加到账单。恢复时保留原有“已存累计数不下降”的保守语义，不重置额度。
- 现有 RequestLedger 仍按执行 owner 累计，JEV 归调用它的 owner，保持 agent/run 语义。新查询按 billing_agent 展示 `jev` 独立维度。L3 对每个调用计一次；不把 owner 合计与 JEV 展示维度再相加。
- Handoff 入口复用服务器 continuity_context 的 run_id/started_at；没有服务器上下文的旧图调用仍创建自己的 ID。这样入口记忆筛选、首跳分类、各 agent、Guard 和末尾记忆提取归同一请求。
- 新增 `query_run_usage`，按 continuity task + run 过滤，一次查询取得本请求的账单；`007_model_usage_run_index.sql` 为 PostgreSQL 查询增加部分索引，不修改不可变事件内容。

成本在查询时按 model 分组，用 Decimal 计算输入/输出的每百万 token 单价。单价和币种来自 budget 配置。未提供的单价返回 `complete=false/unpriced_models`，不会把未知费用当免费。本轮没有虚构生产费率，prices 初始为空；合成费率测试准确算出 `0.00374 test-unit`。真实金额需要填入经确认的价格；估算 token 对应的费用仍是估算。

## L2/L3 策略

初始默认 L2=16000、L3=32000；history_messages=4、summary_tokens=256；通用输出上限 1024、jev-latest 上限 256。所有值位于 context_policy 的 budget 区块，注明待 eval 调优。实际累计上限取配置与原 RunLimits 的较小值，不能借新配置放宽原运行约束。

完整拼包前执行累计预算预处理，再进入原 L1 Collect/Select/Dedup/Token Budget/Compress/Assemble。L1 的选择和组装算法未重写：

1. 保留最近普通历史，保留工具调用/结果协议。
2. 对普通历史做有界、确定性的摘录摘要，不增加压缩模型调用。
3. 清空注册来源、长期记忆和 drilldown 候选；记录 owner 停拉状态，恢复后继续生效。
4. 仍不够则停止调用，返回诚实的预算提示，保留已提交结果。

每个尝试写 `budget.exceeded`，包含 level/stage、agent/task token 快照和 trace_id；trace_id 与 handoff.trace 的 run_id 相同。预算阶段另存 MODEL_USAGE，恢复时不会丢失停拉状态。已实际发生的超额账单完整保留；不能通过截断历史退回已经花掉的 token。

L3 在拼包、调用预留和转交执行节点检查。整任务已满，或必需输入加预留输出已无法容纳时，执行 `task_degradation → refuse`。跨 agent 的调用和 JEV 合计只算一次；handoff.trace 的 model_call token 是同一账单的观测，不额外加一遍。max_handoffs=5 保持原值、独立生效。没有改路由分类逻辑、Guard 阈值、转交边或载荷契约。

Foundation 的生产执行作用域覆盖入口记忆筛选、图执行和末尾记忆提取；Handoff 的节点账本从同一不可变账单恢复。Foundation 超限输出现有 needs_input 响应，只返回本轮 route 的结果，避免累计 state 混入上轮结果或动作。Handoff 保留原 failed/stop_reason 机制并显示预算原因，不产生额外模型兜底调用。

已拒绝的请求不继续提取记忆。末尾记忆调用若实际超额，Final 响应同样显示预算原因，保留本轮已完成结果；预算提示使用独立、幂等的公开历史事件键，不覆盖原答案。同请求的完成态重放读取预算事件，仍返回相同拒绝提示，不重复提取或调用模型。

## 输出核算与预算门

实际 SDK 返回 input/output usage 时直接记实数，支持 LangChain usage_metadata、OpenAI usage、response_metadata.token_usage 和 TypeSafe usage。只给 total 时保留总数、估算拆分并标注；缺少 usage 时沿用现有 UnicodeHeuristicTokenCounter 估算输入和实际输出，标注 estimated/usage_basis。本轮未更换 L1 分词算法，估算不等同于供应商精确计费。

`context_engineering.call_manifest` 合并原来源 token/dedup/压缩事件，并增加 call_id、task_id/trace_id、agent/model、input_tokens、output_tokens、total_tokens、estimated、usage_basis、max_output_tokens、output_limit_exceeded 和调用状态。账单持久化数字与模型信息；不保存原 prompt、画像全文或私有模型思考。合成示例见 `manifest-example.json` 和 `budget-trace-example.json`。

ChatOpenAI 通过独立配置副本设 max_tokens、stream_usage=True、关闭隐藏 SDK 重试；共享缓存模型不被修改。Responses 使用 max_output_tokens；TypeSafe SDK 通过其 extra_body 传 max_tokens。调用方要求更小上限时按实际较小值预留并写 manifest。供应商忽略上限、返回更长输出时，仍记真实用量并拒绝将结果交给工具执行。

新增路径经过最终序列化预算门；JEV 连 state/questions 的 framing 一起检查。新增来源仍通过 Assembler，AST lint 同时保护来源模块不能构造 prompt/调用 SDK，以及三个实际 SDK dispatch 必须嵌在 accounted_call 边界内。

## 验收与验证

最终完整结果：**2316 passed、12 skipped、288 subtests passed，0 failures / 0 errors**，退出码 0。pytest 用时 795.90 秒（约 13 分 16 秒）；统计插件计入启动开销为 796.27 秒。185 个 warning 未作为失败。统计证据见 `verification-summary.json`。

| 验收项 | 测试证据 |
| --- | --- |
| L2 截断→摘要→停拉→诚实降级 | `test_l2_history_summary_stop_sources_and_refusal_are_ordered`；`test_observed_overshoot_keeps_bill_and_prevents_next_dispatch[l2]` |
| L3 跨 agent 超额与拒绝 | 同测试的 l3 分支；`test_l3_prepack_refusal_does_not_change_hop_limit`；生产 Foundation 拒绝测试 |
| usage/max_tokens manifest | `test_manifest_combines_source_counts_with_actual_call_usage`；供应商输出掐断/忽略上限测试；较小调用方上限测试 |
| JEV 实数/估算 | `test_jev_sdk_usage_or_estimate_enters_same_l3_and_model_dimension[True/False]`；JEV 调用前预算拒绝测试 |
| 同请求/跨 agent/恢复不重置 | 现有账单恢复、未知派发预留、跨请求隔离、停拉状态恢复、辅助记忆作用域、旧结果过滤及末尾超额拒绝/重放测试 |
| PostgreSQL 实际持久化 | `test_postgres_budget_reuses_immutable_receipts_and_survives_store_reopen`；原 PostgreSQL 进程死亡/恢复测试 |
| 预算旁路 lint | 新来源超长输入拒绝；SDK dispatch AST lint；Phase 1 Source 模块 AST lint；原 Responses schema/framing 预算测试 |
| L1 回归 | tests + evals/tests 完整套件；未删除、跳过或弱化原预算断言 |

分组回归：入口/L1/SDK 87 passed；最新预算专项 23 passed；PostgreSQL 恢复、载荷篡改、会话迁移、可见回答和 Handoff safeguard 55 passed、4 subtests passed；拒绝结果隔离、记忆超额/重放及 coordinator/流式回归 67 passed。此前 Phase 1 的简化 ledger mock 缺少新接口，已换用真实 RequestLedger，保留 token_limit 断言并新增降级顺序断言。

首轮完整测试为 2292 passed、21 failed、12 skipped、288 subtests passed。失败原因：18 个崩溃/恢复用例的故障注入 reserve 钩子不接收新增 usage 参数，未到达预期崩溃点；2 个模拟模型不接收 max_tokens；1 个用例假设极小预算先创建业务 run，实际新增完整拼包预检查已在入口拒绝。钩子和模型适配已同步，原退出码/恢复/篡改/历史过滤断言保留，并新增输出上限检查；早拒绝用例仍严格断言 token_limit、零模型调用、零结果和零转交动作。另有运行中的测试因模块修改产生旧/新模块混用，相关未完成结果已废弃，最终验证使用固定代码版本。

完整套件在 ai-service 目录执行：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -c "import os,sys,pytest; os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PERSISTENCE_BACKEND='sqlite',AI_CONTINUITY_STORE_MODE='sqlite',DATABASE_URL='postgresql://postgres:phase2-test-only@127.0.0.1:15466/moodflow_phase2_test',MEMORY_BACKEND='memory',CHECKPOINT_BACKEND='memory',JEV_ENABLED='false',DEEPEVAL_DISABLE_DOTENV='1',AI_RAG_RETRIEVAL_MODE='empty'); sys.path.append('.venv-eval/Lib/site-packages'); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests','evals/tests','-q','--disable-warnings','--tb=no']))"
```

最终运行另附仅记录数量、失败 nodeid 和耗时的 pytest 统计插件；不写敏感配置或失败上下文。以下检查均通过：

- `.venv/Scripts/ruff.exe check <本轮 Python 文件>`，包含实现、故障 worker 和全部修改测试。
- `.venv/Scripts/python.exe -m compileall -q app <本轮测试文件>`。
- `git diff --cached --check`；已核对暂存范围，只含本任务的 29 个实现/测试/迁移文件。

12 个条件跳过保持原配置：live JEV、开发数据库 smoke、Worldline opt-in、指定旧容器重启及真实 Qdrant 服务用例。本轮 PostgreSQL 新账本、进程死亡/恢复、载荷篡改和迁移测试实际执行，没有额外跳过断言。

## 限制与提交

- 没有运行远程 live JEV 或真实供应商长输出压测；实数/缺 usage 分支使用 SDK 形态的受控响应。TypeSafe extra_body 已提交上限，远端是否支持该字段需要 live 验证，本地超长结果拒绝门有测试覆盖。
- 费率未提供，初始配置不报告完整实际金额；支持按确认后的模型价格计算。默认预算和摘要配额仍需真实分布 eval 调优。
- 可见正文仍流式发送；Final 事件在本请求的记忆提取核算之后发出，确保辅助调用超额也能反映在最终响应中。真实记忆提取时延对 Final 的影响需要后续 eval。
- 原有 opt-in 外部服务用例的跳过情况以完整测试统计为准；没有为本任务额外跳过用例。
- PostgreSQL 迁移仅应用到一次性本地测试容器 `moodflow-context-phase2-test-20261005`，监听 127.0.0.1:15466；测试后已核对其创建时 ID 并移除该容器。未迁移生产数据库，未修改生产环境/部署配置，未部署、未 push。
- 无前端改动，未运行前端构建/视觉验证。原有无关未提交文件保留，不纳入提交。

实现 commit：`153fd06d7e6517a6b870c4404a58ed2a453f9938`（`feat: extend request accounting with task token budgets`）。实现、迁移和测试共 29 个文件；报告、最终统计和两个合成示例作为独立文档 commit，只包含本任务文件。没有改写已有提交。

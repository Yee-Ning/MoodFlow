# MoodFlow Handoff 阶段 4 验证记录

日期：2026-10-01，Asia/Shanghai。阶段 3 基线 `da5c3a1`，分支 `codex/parse-normalize-corpus-v1`。

## 1. 修改内容与范围

完成阶段 4 的受控开发验收。实现说明见 [运行保障文档](../../architecture/handoff-runtime-phase4.md)，结构化证据见 [evidence.json](evidence.json)。

- 新增请求账本、JEV 护栏、trace 三个模块，以及 57 项测试（预算 28、护栏 13、运行保障 16）。
- 修改 handoff 的业务模型绑定、图调度、transfer 工具、计量状态和 trace 结果码；在公共 Responses/JeVClient 请求及异步 ContextPack、结构化输出修复入口增加仅在 handoff scope 内生效的接线。
- L2/L3 累计预算、调用前预留、实际 usage 结算、缺 usage/失败保守计费及并发预留；路由、重试和回访不重置计量。
- JEV 只审查本地契约已允许的提案；低分一次重新决策，Shadow 保持观察，不可用一次本地检查兜底。既有权限、任务顺序和授权仍生效。
- 瞬时模型请求重试、结构化修复和 guard 重决策共享每 Agent 一次额度；写工具后只重试生成，不重跑业务子图。
- 45 秒节点/120 秒整轮窗口覆盖工具、模型和 guard；预算、取消、超时或重试上限确定性结束，普通失败使用无额外模型/工具的 GeneralChat 文本兜底。
- trace 只保留白名单元数据，模型 call ID 哈希；日志与 checkpoint 同一 run trace ID。新轮清空、恢复保留，JSON/SSE 公共协议保持兼容。

没有新增依赖，没有修改 `.env`、Java、前端 UI、默认 Foundation 模式或首跳 JEV 策略，没有 push 或部署。原用户的修改、删除和未跟踪资产保持原状；提交前后核对 status 和保护文件 SHA-256。

旧 ToolModel 测试替身补充显式受控 usage（100 token），使业务行为验收与预算验收分别可解释；未提供 usage 的真实保守行为另有专门测试。原整轮 deadline 用例更新受控 clock 的读取次数，继续验证没有模型/动作执行。

## 2. 命令及最终结果

AI 命令工作目录为 `ai-service`；前端命令工作目录为 `soul-music-room`。原始日志和 JUnit 存在被忽略的 `.dev/handoff-phase4-20261001/`，机器证据保存摘要、计数、退出码和哈希。

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests -q -ra --tb=short` | **1578 passed，14 skipped，1 warning，286 subtests passed**；退出 0 |
| `.venv/Scripts/python.exe -m pytest tests/test_handoff_safeguards.py tests/test_handoff_budget.py tests/test_handoff_guard.py tests/test_handoff_network.py tests/test_handoff_runtime.py tests/test_handoff_contracts.py tests/test_jev_decision_layer.py tests/test_llm_client.py tests/test_structured_output_service.py tests/test_context_engineering.py tests/test_foundation_streaming.py -q -ra --tb=short` | **285 passed，1 warning，8 subtests passed**；退出 0；属于全量子集 |
| 应用环境运行 `evals/tests` 除 Judge 外的 20 个测试文件 | **173 passed**；退出 0，36.39 秒 |
| `.venv-eval/Scripts/python.exe -m pytest evals/tests/test_judges.py -q -ra --tb=short` | **4 passed**；退出 0，0.28 秒；受控客户端 |
| `.venv/Scripts/python.exe -m ruff check app/llm/runtime.py app/llm/client.py app/agent/handoff app/agent/decision/jev/client.py app/agent/foundation/context_engineering.py app/agent/structured_output/service.py tests/test_handoff_budget.py tests/test_handoff_guard.py tests/test_handoff_safeguards.py tests/test_handoff_runtime.py` | All checks passed；退出 0 |
| `.venv/Scripts/python.exe -m ruff format --check app/llm/runtime.py app/agent/handoff app/agent/decision/jev/client.py tests/test_handoff_budget.py tests/test_handoff_guard.py tests/test_handoff_safeguards.py tests/test_handoff_runtime.py` | 20 files already formatted；退出 0 |
| `.venv/Scripts/python.exe -m compileall -q app tests evals scripts` | 退出 0 |
| `.venv/Scripts/python.exe -m pip check` | No broken requirements found；退出 0 |
| `.venv/Scripts/python.exe -m ruff check . --output-format json` | 45 项既有问题，退出 1；与阶段 0 的 path/line/code/message 完全一致，差异 0 |
| `npm run test:ai-stream` | AI chat streaming store checks passed；退出 0 |
| `npm run test:worldline` | flow/view passed；退出 0 |
| `npm run lint` | 退出 0 |
| `npm run build` | TypeScript + Vite 通过；退出 0，保留原大 chunk warning |
| `git diff --cached --check` | 本阶段文件通过 |

评测框架仍按既有环境分开运行，避免应用 venv 未安装 DeepEval 的收集失败：

```powershell
$phase4EvalFiles = @(
    Get-ChildItem -LiteralPath 'evals/tests' -Filter 'test_*.py' -File |
        Where-Object { $_.Name -ne 'test_judges.py' } |
        Sort-Object Name |
        ForEach-Object { 'evals/tests/' + $_.Name }
)
& .venv/Scripts/python.exe -m pytest @phase4EvalFiles -q -ra --tb=short
```

全量测试使用进程级离线配置：JEV/live Postgres opt-in 关闭、memory checkpoint、SQLite continuity、disabled memory backend、空数据库 URL、LLM 占位值。测试使用显式依赖替身，没有改写环境文件或安装依赖。

## 3. 真实 JEV 护栏 smoke

独立 smoke 使用 `ai-service/.venv` 中正常 `get_settings()` 加载的配置和 JevClient，未替换 API key。通过 SDK 对“暂停一下”的有效 GeneralChat→Curator envelope 做一次 Noul 审查；只调用 JEV，没有音乐账户或播放器操作，没有自动重试。

- 配置：`jev-latest`、既有 Shadow 为 true。
- 实际返回：`jev-1.13.0`，541.51 ms。
- provider usage：494 token，账本 `reported_tokens=494`、`estimated_tokens=0`、`model_calls=1`、`retries=0`。
- guard 分数：0.68，Shadow code 为 `guard_shadow`。分数低于 0.8，非 Shadow 会拒绝这一提案；本次只确认 API/结构化护栏/计量连通，不能解释为质量验收通过。
- 记录不含 key、完整请求或上游错误正文。机器证据保存该 smoke 结果及原始 JSON 哈希。

## 4. 关键场景及开发期纠正

新用例验证：预算在请求前拒绝、实际 overshoot 如实记录且阻止工具派发、缺 usage 保守计费、SDK/ChatOpenAI 隐藏重试关闭、并发预留及不同 run 隔离、跨 Agent 累计、路由账单、结构化修复与 guard 共用额度、400/401/403 不重试而 429/5xx 可恢复重试、0.8 阈值边界、两次低分无动作、guard 异常本地兜底、禁止边在 JEV 前拒绝、guard 超时、取消、云端工具后重试仅一次写入、checkpoint 重决策恢复、新轮清理、trace sink 故障/脱敏及 SSE 私有字段隔离。

首次相关集合 15 failed / 185 passed：新增 trace 缺少 clarification code、旧替身不返回 usage、旧 clock 读取次数不符。补齐终态/计量语义和受控替身，保留正常业务路径及无用量保守测试。随后集合 241 passed / 8 subtests passed。

进一步测试发现子图包装取消信号丢失原因，以及 SSE 测试误把类型事件当成编码字符串；修复 ledger 的取消原因传播和测试对公开类型事件的正确消费。最终核对又补充护栏重决策继承原节点截止时间，并验证重复拒绝 trace 不会重复记录。最终相关集合和全量集合均通过。开发期曾有一次测试文件名拼写错误导致零用例收集，纠正后执行完整相关集合。未删除、跳过或降低失败/权限检查。

## 5. 风险及未完成的环境验证

1. 全目录 Ruff 仍有 45 项既有问题；本次文件检查通过。原保护报告自身有 trailing whitespace，未改动，Git whitespace 检查只包含本次提交文件。
2. 14 项原 opt-in skip 仍为真实 JEV 1、Postgres smoke 4、Postgres E2E 4、WorldLine Postgres 重启 3、真实 Qdrant 2；另有既有 Qdrant 版本 warning。独立真实 JEV smoke 不等同于完整在线集。
3. Unicode 预留和无 usage 计费是估计，不是精确账单或绝对 tokenizer 上界；reported/estimated 分开记录。provider 报告实际超限仍保留，远程已发送请求不保证撤销。
4. 对当前本轮的模型/结构化修复/guard 有统一有限重试；不重试写工具和整个业务子图。真实云端幂等、外部进程中断后的工具重放及 PostgreSQL 重启恢复尚未验收，超时不表示副作用已回滚。
5. 仅一条真实 JEV guard smoke，正向例得分 0.68；保持 Shadow，需要独立护栏阈值/质量评测。未做全链路真实模型质量、成本/时延 benchmark、登录音乐接口、浏览器音频或响应式交互验收。
6. 本次没有 UI 或 Java 修改，未重跑 Java 构建；前端脚本/构建通过不代替浏览器播放验收。公共请求/成功/失败路径与 HTTP/SSE 兼容由后端全量测试覆盖。

阶段 4 开发验收通过后，可继续独立评测、在线验收和灰度准备；本次没有启用非 Shadow、切换默认图或部署。

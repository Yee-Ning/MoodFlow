# Jev Decision Layer v1 实施报告

## 1. Phase 0 结果

**PASSED**（2026-09-23）。使用本地 `.env` 中的 TypeSafe Key，经官方 `typesafe-sdk==0.7.1` 查询模型并调用真实 `system_one`。Key 未写入代码、日志、测试或报告；`.env` 被 `ai-service/.gitignore` 排除。配置沿用 `app.config.settings.Settings`，没有第二套配置系统。

## 2. 实际可用模型

当前账号的 `/v1/models` 返回 `jev-latest`、`jev-preview`。未返回固定版本 ID。原配置 `jev-1.13.0` 不在模型列表；用户已明确批准改用 `jev-latest`，并排除 `jev-preview`。

## 3. 最终模型

请求模型 `jev-latest`（mutable alias）。本地被 Git 忽略的 `.env` 已改为 `JEV_MODEL=jev-latest`；`.env.example` 保留空 Key，示例模型为 `jev-latest`。所有后续记录同时保留 requested_model、API response_model 和 evaluation_timestamp，以便识别 alias 漂移。

## 4. system_one smoke test

输入 `I was charged twice for my subscription.`；Choice 候选为 `billing`、`technical`、`other`。真实返回：choice=`billing`，confidence=`1.0`，probabilities=`{billing: 1.0, technical: 0.0, other: 0.0}`，usage=`303 input / 38 output tokens`，耗时约 `699.4 ms`。请求模型 `jev-latest`，API 实际返回 `jev-1.13.0`，验收时间 `2026-09-23T10:32:02.068721+00:00`。调用使用官方 System One SDK，不经过 Chat Completion 或 OpenRouter。

## 5. 修改文件

- 配置与依赖：`app/config/settings.py`、`.env.example`、`requirements.txt`，以及本地被忽略的 `.env`。
- 决策层：`app/agent/decision/jev/{client,router,memory_filter,context_selector,telemetry,evaluation}.py`。
- 接入：`app/agent/foundation/{main_graph,graph_factory,streaming,context_engineering}.py`，四个异步 SubGraph assistant/generation 调用点。
- 评测与测试：`run_jev_eval.py`、`tests/conftest.py`、`tests/test_jev_*.py`。

## 6. 架构

`AsyncTypeSafeClient → JevClient → Router / Memory Filter / Context Select`。`JevClient` 统一管理请求生命周期、模型、超时、零自动重试、Choice/Noul/Score typed parsing、usage、latency、异常归一化和敏感字段遮蔽。各决策点可以注入 fake client。LangGraph、PostgreSQL/pgvector、Qdrant 和主生成 LLM 的原职责保持原样。

## 7. Router 接入

MainGraph 仍先执行现有 Hybrid Router。Jev 使用现有 `RouteKey` 值构建 Choice，仅接收当前 query。Shadow 模式下只比较。Live 模式仅对现有完整、单任务且覆盖原请求的路由开放接管；Jev 输出须合法且 confidence 达配置阈值，否则沿用现有决定。多任务、澄清和部分路由始终沿用现有路径。

## 8. Memory Filter 接入

现有 query embedding、PostgreSQL/pgvector 检索和 Top-K 排序未改动。仅在检索后对候选记忆用稳定 memory_id 映射批量 Noul 判断，每批最多 20 条；输入限于 query、memory_id、类型、规范化内容、importance、更新时间，不发送 embedding 或存储内部字段。先验证 retrieval user_id 与当前 user_id 一致。

## 9. Context Select 接入

异步 Foundation LLM 节点在原 `ContextCollector` 与 `ContextSelector.build` 之间运行 Jev Noul 筛选；后者的去重、预算、压缩、组装逻辑未改动。仅考虑可裁剪的 history、memory、RAG evidence、handoff/artifact projection 和非必需 tool result。`required` 项和关键 runtime/structured state 绕过 Jev。每批最多 20 项，按稳定 ContextItem ID 映射。

## 10. Shadow Mode

本地保持 `JEV_SHADOW_MODE=true`。Jev 只做旁路判断，不改变生产路由、记忆或最终上下文；记录 agreement、scores、候选数与拟选择数。未自动切换 Live。

## 11. Feature Flags

总开关 `JEV_ENABLED`；独立开关 `JEV_ROUTER_ENABLED`、`JEV_MEMORY_FILTER_ENABLED`、`JEV_CONTEXT_SELECT_ENABLED`。示例配置默认关闭功能；当前本地 `.env` 的开关与 Shadow 均为 true。总开关关闭时走旧逻辑。

## 12. Fallback Policy

Router 在低置信度、非法输出、异常或复杂既有路由时沿用现有 Router。Memory Filter 遇到超时、API 错误、解析错误或部分 batch 失败时 fail-open，保留完整原 Top-K 结果。Context Select 遇到对应失败时保留原候选池，继续原 Select。跨用户检索结果会被丢弃，不进入上下文。

## 13. Observability

复用应用 logger，发出不含 Key 或完整 memory 文本的 `jev.decision` JSON 事件：decision_type、provider、requested_model、response_model、evaluation_timestamp、latency_ms、confidence/probabilities、candidate_count、selected_count、applied_count、fallback、agreement、trace_id、usage、稳定候选 ID/分数。发送给 TypeSafe 的结构化 state 会移除 secret/token/password/API key 字段，并遮蔽常见内联凭证。

## 14. Tests

新增确定性 fake client 单元测试，覆盖 route ID/conditional edge、Shadow、置信度与错误回退、memory ID/批处理/失败保留/用户隔离、context required/批处理/失败保留，以及评测与配置。日常 pytest 通过 fixture 关闭 Jev，避免本地 `.env` 导致 CI 意外真实调用。轻量 Jev 开关门控只读取该开关，不把 `.env` 中的 PostgreSQL 等无关配置注入测试进程；真正启用时仍由 `Settings` 统一验证。

最终验证：`python -m pytest tests -q --tb=short` 首次整体运行 `873 passed, 11 skipped, 206 subtests passed`。修复后按测试文件分四个独立进程顺序运行，合计 `879 passed, 11 skipped, 206 subtests passed`；另有定向回归 `31 passed, 3 subtests passed`。最后补充的缺 Key SKIP 测试单独验证：live opt-in `2 passed`，默认模式 `1 passed, 1 skipped`。`python -m ruff check` 对本任务全部修改的 Python 文件通过；`python -m compileall -q app run_jev_eval.py` 通过；任务相关 `git diff --check` 通过。`run_jev_eval.py` 已用示例 JSONL 实际运行。

## 15. Live smoke test

`tests/test_jev_live.py` 需显式 `JEV_LIVE_TEST=1` 且有 Key；缺少条件时 SKIP。该测试调用统一 `JevClient` 的真实 Choice，并验证选择、置信度、概率、requested_model、response_model、timestamp 和 latency。

## 16. 已知问题

- `jev-latest` 为可变别名，API 当前映射 `jev-1.13.0`，未来可能变化。任何对比评测必须按 response_model 与时间分组。
- 目前没有覆盖三个任务的人工 gold 标签，因此 `run_jev_eval.py` 仅提供对比入口，无法据此断言效果提升。缺少 gold 的指标报告为 `null`。
- 同步的 LongHorizon `build_active_context` 没有直接调用 Jev；生产异步 Foundation LLM 节点已接入。现有同步方法仅见于测试及 PostgreSQL 恢复验证。
- 第二次单进程全量测试在 Windows 原生层出现 `0x8007000e` / `0xc000070a` 异常，未取得完整结果；随后分四个独立进程运行全部测试文件并通过。期间分片测试曾暴露 `.env` 整体加载的真实回归，该问题已修复，失败的分片和定向用例均重跑通过。
- 仓库范围 Ruff 目前仍报告 5 项原有未使用导入/变量，分布在旧 routing 与 RAG 模块；未为了本任务改动那些无关位置。任务修改文件的 Ruff 检查通过。
- 未运行带真实 PostgreSQL/Qdrant/生成 LLM 的端到端在线场景；已有相关单元、E2E 回归和 Jev live Choice probe 已运行。

## 17. Shadow → Live 验收条件

在保留同一批既有 Gold/E2E 案例的前提下，按 requested_model、response_model、evaluation_timestamp 分组比较：Router route accuracy 与 fallback/latency；Memory key-memory recall、无关记忆减少、fail-open；Context required preservation 必须为 100%，并测有用上下文召回、token 变化、下游兼容与延迟。人工审查误路由和误删案例后，再单独决定是否关闭 Shadow；本次交付不关闭。

评测入口：`python run_jev_eval.py <annotated-decisions.jsonl>`。输入可使用既有评测案例扩充决策结果和可选 gold 字段，不另造数据集；输出缺失标签对应的指标为 `null`。

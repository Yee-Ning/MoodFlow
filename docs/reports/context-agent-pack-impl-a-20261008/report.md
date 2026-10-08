# Agent Pack 实现 A 验收报告

日期：2026-10-08。分支：`codex/context-agent-pack-impl-a`。

基线：Phase 3 `d1ff818c9318b532dfe5de81fb0c5cf2865a789e`。设计依据：`codex/context-agent-pack-design` 的 `8b88dc2ee8ea992ea75fd5d721620116472ee914`，`docs/architecture/context-agent-pack-design-20261008.md` §1、§8。

本轮完成共用适配器、静态 Prompt 锚点和可选严格 Manifest 扩展。没有接入具体 agent 业务，没有实现 Envelope Source、RAG Source、配方选择策略或 B/C/D/E 的端到端迁移。

## 1. Gate 0 核对

先完成只读核对并报告实际形态；West 的“继续完成”放行本轮 A。未合入设计分支代码、RAG 或 Redis 分支。

| 核对项 | 基线实际形态与本轮处理 |
| --- | --- |
| 分支与 HEAD | `codex/context-engineering-phase3` 的 HEAD 等于指定完整 SHA；从该提交创建本轮分支 |
| 设计可读 | 用 `git show codex/context-agent-pack-design:docs/architecture/context-agent-pack-design-20261008.md` 读取；§1 明确调用快照／Source／pipeline／账本职责，静态 prompt 不走 Collect；§8 明确锚点、摘要和可选审计字段 |
| ContextCollector | `ai-service/app/agent/foundation/context_engineering.py`，`ContextCollector(counter=None).collect(*, query, system, messages=None, ..., registered_items=None, triage_items=None)` 返回 `list[ContextItem]`；空 system 不产生材料 |
| ContextSelector | 同文件，`ContextSelector(counter=None).build(items, policy, trace_context=None, *, intent=None)` 返回 `ContextPack`；已执行类型／意图过滤、Dedup、来源配额、总预算、压缩和 Assemble |
| ContextAssembler | 同文件，`ContextAssembler().assemble(items, *, intent=None)` 返回原生 LangChain messages；由 Selector 使用，适配器不重写它 |
| 原 PackManifest | `context_contracts.py`，严格、冻结；`tokens/budget/sources` 全为必填；SourceManifest 的 `candidate_tokens/final_tokens/events/dedup_dropped` 全保留 |
| 原 CallManifest | 严格、冻结；必填 `call_id/trace_id/task_id/agent/model/input_tokens/output_tokens/total_tokens/estimated/usage_basis/max_output_tokens/output_limit_exceeded/status/budget_events`；Pack 三字段可选但必须成组；原一致性校验保留 |
| Prompt 位置 | GeneralChat／工具 Curator／当前歌曲 Curator 位于各 `assistant.py`；Explorer 两个主 prompt 位于 `worldline/prompts.py`；查询分析已有 `QUERY_ANALYSIS_PROMPT_V1`；转交、重试和只读补充规则原为 `handoff/agents.py` 内联字面量 |
| L1 实际位置 | **不在 context_policy.json**，位于 Python `POLICIES`。沿用原值：GeneralChat／CurrentSong 输入上限 5888，ToolCalling 9984，WorldLine 9532；JSON 现有 lite／Source／L2/L3 配置保持原样 |

内联 prompt 原样提取到 `handoff/prompts.py`，原调用点只改常量引用。SHA-256 UTF-8 金值来自指定基线的 AST 字面量，测试覆盖全部 11 个锚点，包括前导换行；没有修改文本。

## 2. 共用适配器

`AgentPackInvocation` 绑定 `owner_agent/run_id/task/phase/recipe_id/recipe_version/prompt_refs`，以及服务端已校验的 `AgentPackInputs` 和可选观测引用。输入 DTO 不接收 `system/query/source_registry/profile_store` 覆盖项。业务载荷仍由原生产方校验；此 DTO 不是客户端授权接口，不负责验证／接受转交。

`AgentPackBuilder.build(...)` 的处理顺序：

1. 复制调用快照，解析代码内静态版本引用，核对 owner／phase 和现有 policy。已有账本时核对 run／owner；Envelope 观测引用核对 run。
2. 带完整静态 system 复用 Phase 2 `prepare_sources` 累计预算检查及原降级预处理，不预留、不结算、不初始化新的账本。
3. 对原 Collector 传空 system，只收集其他输入；静态文本单独构造成一个 required、不可压缩的 system item。
4. 将全部材料交给原 Selector，沿用其 Dedup、配额、Budget 和 Assembler。读取已有实时余额时使用入口相同的 policy 上限约束；没有自定义 selector 或 assembler。
5. 从最终 pack.items 生成审计 DTO，保留原 aggregate manifest，并更新既有 `active_manifest` 观测通道。支持 `include_agent_context=False` 生成旧格式。

静态 prompt 不经过 SourceRegistry，不触发新的文件／配置动态读取、检索或模型调用。它在 L1 中计一次，不能在预算检查之后追加。工具调用和工具回执保持原生配对协议。

本轮适配器尚未由 agent 业务入口调用；原入口继续使用既有 `build_context_async`。实际模型派发的 reserve／settle 仍由原 `accounted_call/RequestLedger` 管理，审计余额不能用于恢复账本。

## 3. Prompt 锚点

`prompt_bindings.py` 的不可变映射绑定现有代码常量，版本均为 `v1`。引用未知版本、重复引用、owner／phase 不匹配、缺少主 prompt 或同时选择两个互斥工具 variant，均在 Collect 前拒绝。

| 适用阶段 | 锚点 |
| --- | --- |
| GeneralChat answer／read_observation | `general-chat-base/v1`，可附 `general-chat-handoff-read/v1` |
| Curator tool_step／tool_observation | `curator-tools-base/v1`，可附 `search-only/v1` 或 `song-playback/v1` |
| Curator song_analysis | `curator-current-song/v1` |
| Explorer query_analysis | `query-analysis/v1`，直接引用现有 V1 常量 |
| Explorer narrative／branches | `explorer-narrative/v1`、`explorer-branches/v1` |
| 三 agent relay | `handoff-transfer/v1`，可附 `transfer-retry/v1` |

每项审计只含 ID、版本、variant 和 `sha256:<64 位小写十六进制>`，不存 prompt 原文或动态 feedback。Curator 原有换行拼接和 handoff 片段自带前导换行保持一致。Guard 的 `handoff-guard-v3` 保持独立、不改动。

Explorer 生成器当前的探索模式改写、重试附加规则和动态 feedback 仍留在原调用路径。真实调用阶段如何绑定完整 variant／渲染反馈，是 D 的业务接入范围；A 没有宣称主 prompt digest 已代表那些尚未迁移的完整请求。

## 4. Strict Manifest 扩展

新增可选 `agent_context`，所有 DTO 继续 `extra="forbid"`、冻结；没有扩宽旧 DTO 的额外字段规则，也没有删除原必填字段或校验。

| 字段 | 实现 |
| --- | --- |
| schema_version | 扩展自身为 1 |
| recipe_id／recipe_version／owner_agent／route_key／phase | 记录调用身份；owner 独立于 CallManifest 的 billing agent，兼容 JEV 单独记账维度 |
| prompts | 静态 ID／版本／variant／UTF-8 内容摘要，不含原文 |
| envelope | transfer_id、run_id、tool_call_id、schema_version、完整 payload_digest、可选 summary_ref／commit_ref、selected_fields；仅观测，不消费载荷 |
| session | session_id、可空 projection_version／source_event_cursor／state_revision、可空 checkpoint_ref；未知值保持 null |
| source_selection | 最终 item ID、source、type、原 references、可用画像版本、最终 token；无候选全文。丢弃与压缩事件仍在原 sources.events |
| budget_snapshot | 已有账本的 owner／run 余额、预留量与 owner 降级阶段；允许余额为负以如实表达已发生超额 |

旧文档没有扩展或扩展为 null 都能读取，序列化省略空扩展。`legacy_projection()` 显式移除这一个已知扩展，为仅识别 v1 聚合字段的消费者提供原格式。Pack 和实际 CallManifest 都支持新扩展，SDK 用量、budget_events、usage_ref 等原字段保留。

summary_ref／commit_ref 复用长期 task 作用域的 `EventPointer`。checkpoint 使用与既有 `CheckpointRef` 完全同形的严格观测 DTO，并校验 session 一致；这是为避免 `long_horizon.__init__ → service → context_engineering` 的初始化循环，没有改原 CheckpointRef。兼容测试核对字段集合和原契约 JSON 往返一致性。

## 5. 验收记录

| 验证 | 结果 |
| --- | --- |
| 新测试 `tests/test_agent_pack_impl_a.py` | 47 passed；覆盖静态读取纪律、11 个原文摘要、组合 variant、L1 精确边界、单次 system 计数、原 pipeline 等价、原生工具协议、来源配额与去重审计、旧／新 manifest、嵌套 extra 拒绝、作用域错误、原 L2 降级以及真实记账边界的扩展保留 |
| 原 Phase 1/2/3 相关测试首轮 | 原相关 64 项通过；新增测试有 1 项因夹具遗漏 intent 失败，修正夹具后新增 47 项全部通过。没有改现有测试或 Select 规则 |
| 全量 `tests` + `evals/tests` | 2378 passed、13 skipped、0 failed；185 warnings，288 subtests passed，1736.83 秒。首次使用普通 postgres:16，额外一项 skip 为该镜像缺少 pgvector |
| pgvector 完整模块补验 | 使用本机已有 pgvector/pgvector:pg16，在相同隔离端口补跑 `tests/test_postgres_persistence.py`：7 passed、0 skipped、0 failed，20.18 秒；包含此前额外跳过的向量持久化测试 |
| 两轮合并验收覆盖 | JUnit 按 classname＋name 核对：额外 skip 已有通过记录，剩余正好 12 项原环境 skip；完整测试集合中 2379 个普通用例及 288 个子测试均有通过记录，无失败。不是把两轮重复的 6 个普通用例重复计数，也不声称首次全量只有 12 项 skip |
| Ruff | 本轮 6 个 Python 文件全部通过 |
| Python 编译／原必填字段核对 | `compileall` 通过；AST 对照基线，PackManifest 原 3 个、CallManifest 原 14 个必填字段集合完全不变 |
| 本轮 diff 空白检查 | 7 个暂存任务文件 `git diff --cached --check` 通过；只检查任务提交，没有修改 unrelated gold review 的原有尾随空格 |

相关测试／检查命令（工作目录 `ai-service`；pytest 通过下方受控环境驱动调用）：

```text
pytest.main(['-p','pytest_asyncio.plugin','tests/test_agent_pack_impl_a.py','-q','--tb=short'])
.venv/Scripts/ruff.exe check app/agent/foundation/agent_pack.py app/agent/foundation/prompt_bindings.py app/agent/foundation/context_contracts.py app/agent/handoff/prompts.py app/agent/handoff/agents.py tests/test_agent_pack_impl_a.py
.venv/Scripts/python.exe -m compileall -q app/agent/foundation/agent_pack.py app/agent/foundation/prompt_bindings.py app/agent/foundation/context_contracts.py app/agent/handoff/prompts.py app/agent/handoff/agents.py tests/test_agent_pack_impl_a.py
git diff --cached --check
```

全量驱动设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、SQLite continuity、内存 memory/checkpoint、`JEV_ENABLED=false`、`DEEPEVAL_DISABLE_DOTENV=1`、`AI_RAG_RETRIEVAL_MODE=empty`，并把已有 `.venv-eval/Lib/site-packages` 加到 `sys.path`。`DATABASE_URL` 指向本轮独立回环 PG 测试容器的 15478 端口，使用合成测试凭据，不接触应用数据库。

完整的全量命令如下，合成测试凭据仅用于本轮隔离容器：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -c "import os,sys,pytest; os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PERSISTENCE_BACKEND='sqlite',AI_CONTINUITY_STORE_MODE='sqlite',DATABASE_URL='postgresql://postgres:agent_pack_a_test_only@127.0.0.1:15478/agent_pack_a_test',MEMORY_BACKEND='memory',CHECKPOINT_BACKEND='memory',JEV_ENABLED='false',DEEPEVAL_DISABLE_DOTENV='1',AI_RAG_RETRIEVAL_MODE='empty'); sys.path.append('.venv-eval/Lib/site-packages'); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests','evals/tests','-q','--disable-warnings','--tb=short','--junitxml=.pytest_cache/agent-pack-a-full.xml']))"
```

补验使用上方相同 Python 环境和 `DATABASE_URL`，只把 pytest 参数改为：

```python
pytest.main(['-p', 'pytest_asyncio.plugin', 'tests/test_postgres_persistence.py', '-q',
             '--disable-warnings', '--tb=short', '--junitxml=.pytest_cache/agent-pack-a-pgvector.xml'])
```

为补齐缺失环境，仅替换本轮临时 PG 容器为本机已有 `pgvector/pgvector:pg16`，没有改源码、测试或断言；因此没有重复执行已通过的全量用例。补跑完整持久化模块还覆盖了其 `include_memory=True` 的共享 fixture。JUnit 对照确认已补齐 `test_pgvector_memory_persists_and_isolates_users`，不是删除 skip 标记或缩小测试集合。

剩余 12 项环境 skip 为：

- TypeSafe/JEV 真实线上调用：1 项，未开启 `JEV_LIVE_TEST`。
- 真实开发库 PG smoke（含真实 Qwen embedding）：4 项，未开启 `RUN_LIVE_POSTGRES_SMOKE`。
- 真实 Qdrant 写入与 hybrid 检索：2 项，未提供 `QDRANT_INTEGRATION_URL`。
- 专用 PG 容器真实重启恢复：2 项，未配置重启容器。
- WorldLine 真实 PG 重启验收：3 项，未开启 `RUN_WORLDLINE_POSTGRES_RESTART`。

这些真实外部 API／服务重启行为未在本轮验证，符合原 12 项环境 skip 边界；原断言未改。验收的两个临时 PG 容器均按创建时的完整 ID 核对名称后清理，仅移除自身匿名卷，没有处理其他容器或用户文件。

## 6. 范围与提交

仅提交本轮的适配器、Prompt 绑定／内联文本提取、Manifest DTO、新增测试和此报告。未改 ContextCollector／ContextSelector／ContextAssembler 实现、SourceRegistry、policy JSON、L1/L2/L3 阈值、预算 Protocol、路由、Guard、转交协议或生产配置。没有 cherry-pick RAG；没有修改 Redis 特性分支；没有新增依赖、push 或部署。

原有 gold review 改动、四个根目录报告删除及其他未跟踪资料均不纳入提交；gold review 文件摘要保持与开始时一致。

独立提交号在最终交付答复给出，可用 `git log -1 --format=%H -- docs/reports/context-agent-pack-impl-a-20261008/report.md` 查询本报告所属提交，避免向报告自身写入不可稳定生成的自引用提交摘要。

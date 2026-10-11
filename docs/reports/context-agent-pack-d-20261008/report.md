# Agent Pack D：Explorer 三阶段接入

## 基线与范围

分支：`codex/context-agent-pack-impl-d`。B 基线为 `3e09b919570d9491a34e9a729fe2aa12235d6e80`；正常合入已验收 RAG `041392a788660483f591ec6be495bb409dab7975`，获准的合并兼容修复为 `f734049168e57309a8264f49962d6e91e9b17bdd`。本轮从该已验收合并结果继续实现 D，合并过程见同目录 `merge-compatibility.md`。

所有修改位于独立 worktree。没有修改 A/B 的调用签名、DTO 必填字段、`extra=forbid`、SourceManifest 序列化契约或原测试断言。没有修改 GeneralChat/Curator、路由图边、预算阈值、Guard、转交、生产配置或其他特性分支；未 push、部署或 rebase。

## 三阶段接入

| 阶段 | 实际调用点 | recipe / phase | 静态 prompt |
| --- | --- | --- | --- |
| query_analysis | WorldLine `understand_query` → QueryUnderstandingService → ExistingLLMQueryAnalysisRuntime | `explorer/v1` / `query_analysis` | `query-analysis/v1` |
| narrative | `StructuredWorldLineGenerator.generate_narrative` → ProviderWorldLineRuntime | `explorer/v1` / `narrative` | `explorer-narrative/v1` |
| branches | `generate_branches` / `generate_exploration_branches` → ProviderWorldLineRuntime | `explorer/v1` / `branches` | `explorer-branches/v1`，或探索模式的两个 binding |

WorldLine 原有“查询理解 → narrative → branches”的节点、边和生成重试路径保持不变。查询分析的规则命中仍不调用模型；模型回退才创建该阶段 Pack。query_analysis 在注册和输入候选两处排除正式 RAG 与旧 rag_pack，既不检索也不带入入口 RAG 候选。

`worldline/stage_pack.py` 是 D 的阶段适配器。有可信当前 Hop 时调用 B 的 `build_hop_pack`；独立调用走 A 的 `AgentPackBuilder.build`。当前 Hop 在 Collect 前验证 lease、run、owner 和 route，任务文本仍来自可信快照；不同的当前分析问题作为 required 数据保留。当前问题与 canonical task 相同时不重复添加 required 副本，最终模型请求保持旧 `{query: ...}` 形状。

## 配额与预算

各阶段继续采用 WorldLine 的现有预算：总量 12288、预留输出 2500、安全余量 256，原始 L1 输入上限 9532；最终 SDK/schema/JSON framing 另经现有硬门检查，实际可用空间更小。L2/L3 阈值不变。

| 来源 | query_analysis | narrative / branches | 说明 |
| --- | --- | --- | --- |
| received_envelope | 1280 token / 最多 24 候选 | 同左 | 仅当前可信 Hop，沿 B 的 Explorer recipe |
| session_view | 600 token / 最多 8 候选 | 同左 | 沿 B；不伪造 Session 审计 |
| user_profile | 240 token / 最多 16 候选 | 同左 | 仅已有经过校验的入口候选 |
| history_memory | 600 token / 最多 24 候选 | 同左 | 仅已有入口候选；不新增下钻或历史查询实现 |
| conversation / tool_result | 800 / 1800 token | 同左 | 沿既有 WorldLine policy，实际输入由原路径提供 |
| 正式 rag + transitional rag_evidence | 0；不 Collect | **合计 5000 token** | 保持各自来源和审计身份；正式 RAG 最多 24 重排输入候选 |

required 图边／目录事实引用继续保留，并占用共享证据池；required 本身超过来源软额时不删除事实约束，可选证据全部裁掉，最终仍受 L1/SDK 硬预算。相同优先级下，正式来源在 Dedup、来源配额和全局裁剪中均优先于可选过渡来源。原 Selector 默认排序、来源额度和裁剪规则不变；D 通过私有策略钩子共享额度及选择次序，未修改 A/B 接口。

正式 Source 的 embedding/rerank 消费原有 L2/L3。Collect 后重新读取实际 remaining（含未结算预留），压缩该次生成请求的最终预算；不第二次 Collect，也不从原始输入重新放回已丢弃材料。来源元数据较大时，D 的重新选择下界是 required-only 的组装成本；最终 framing 超额仍拒绝。

## RAG、出处和审计

正式 RAG 分支已就绪，复用现有 `RagSource`。旧 rag_pack 经原 Collector 包装，来源仍为 `rag_evidence`，metadata 标记 `transitional=true`，引用含 `transitional:legacy-rag-pack/v1`，不冒充正式 Source。正式 Source 的 empty、shadow、unavailable 三种情况都与旧路径做了 SDK payload / instructions 精确对照。

每阶段、每次生成重试都重新 Collect 正式 RAG；没有查询／实体／过滤／索引／参数身份元组，不能证明入口候选或上一阶段 Pack 可复用，因此这些 RAG 候选不作为新 Collect 的来源。上一阶段正式引用的可选副本被过滤；原图 required 事实快照保持原有处理方式。

D 的 call-local Registry 保留 SourceItems 审计 sidecar；每次最终选择按原候选及存活项计算 candidate/final tokens、丢弃／去重事件、chunk、collection 和 model_usage。重新选择保留 AgentContextManifest 的 prompt、Envelope/Session、预算观测及 trace 的 task/session/request 身份，同时更新实际 selected_fields/source_selection。线程化 Collect 传递当前 ContextVars，最终 manifest 回写当前调用；并发隔离用例验证没有串话。

正式 Child 使用确定性 `knowledge:rag:<digest>` 引用，与 WorldLine 的既有知识引用命名空间对齐。来源仍携带真实 rag item、doc、source、chunk 身份；不展开 Parent。narrative 的 grounding 校验使用最终实际送入模型的来源，选中正式来源时正确声明知识可用，最终公开 sources/citations 同步这些引用。未知引用仍触发原结构化输出重试。公开 sources 上限 30 保持不变，超额仅裁可选项；required 超额明确拒绝。

## Prompt 与 feedback

原有 11 个静态 prompt 原文摘要回归保持不变。探索模式原有两次替换形成 `explorer-branches-exploration/v1`，附加资格规则为 `explorer-exploration-rules/v1`；固定重试规则为 `explorer-retry/v1`。manifest 保存版本、variant、SHA-256 摘要。

动态 feedback 继续按原次序插入：阶段静态规则 → feedback → 固定重试规则。feedback 是 required 数据，manifest 只记其类型、实际 token 和 `structured-output-feedback:sha256:<digest>` 归因，不记全文，不赋予静态版本。实际生成器探索重试用例校验了规则摘要、feedback 缺席原文以及不可选目标的原校验。

已有 StructuredOutputService 会包装非校验异常。D 在查询回退及 narrative/branches 的包装边界沿显式 cause 链识别 SourceIntegrityError、RagBudgetRefusal、RagAccountingFailure，保留原异常类型；这些拒绝不触发规则／目录 fallback，也不进入输出修复重试。普通模型／输出校验错误仍走原处理路径，没有修改 StructuredOutputService 或 Guard。

## 验证

使用外部验证驱动设置离线测试环境及本轮独立 PostgreSQL 容器，没有读取／修改生产 .env。驱动明确载入 pytest_asyncio，记录用例结果及运行前后 app/tests Python/Lua 源码哈希。环境详情和计数以同目录验收数据为准。

联合回归命令：

```text
python -X utf8 <validation-driver> <joint-result.json> -p xdist.plugin -n 2 --dist loadfile \
  tests/test_agent_pack_impl_a.py tests/test_agent_pack_impl_b.py tests/test_agent_pack_impl_d.py \
  tests/test_context_rag_source.py tests/test_context_rag_postgres.py tests/test_context_source_manifest_compat.py \
  tests/test_context_contracts_phase3.py tests/test_context_loop_budget.py tests/test_context_engineering.py \
  tests/test_worldline_generation.py tests/test_worldline_graph.py \
  tests/test_query_understanding_service.py tests/test_query_understanding_graph.py \
  -q --disable-warnings --tb=short
```

结果：**232 passed、0 failed、0 skipped、1 warning**，pytest 143.33 秒；源码哈希未变。D 新增 35 个用例，覆盖三阶段真实调用、无 RAG、三种过渡对照、共享额度、正式来源优先、静态与动态规则、真实 Hop、最终预算、失败透传、裁剪审计与 trace 身份、30 来源边界、公开引用闭环、查询重复计数及并发隔离。

调试记录：第一轮新增用例的配置／来源注册夹具错误已更正；完整 408 项联合回归中，407 项通过，唯一失败为新增探索重试夹具误用了可接受的空分支。改为不存在节点的分支后，223 项联合回归通过。最终复核又发现 Source 异常会被生成器包装并触发目录 fallback，先暂停本轮全量，再新增 9 项拒绝用例：修复前 9 项全部失败，修复后与原联合用例一起通过。随后补验裁剪 trace 身份，冻结代码重新完整验证。没有修改既有失败断言、增加 skip 或弱化 Guard。

全量命令：

```text
python -X utf8 <validation-driver> <full-result.json> -q --disable-warnings --tb=short
```

全量结果：**2479 passed、288 subtests passed、12 skipped、185 warnings、0 failed、0 errors**，pytest 1101.19 秒。12 项 skip 与既有基线逐项一致；运行前后 app/tests 的 Python/Lua 源码哈希一致。完整计数、命令、各文件通过数与 skip node IDs 记录于 `validation-results.json`。本轮测试依赖与既有离线知识／意图／eval 资产沿用验收环境，未将这些资产纳入 D 提交。

静态检查：本次 8 个源码文件及新增测试 `ruff check`、`compileall -q`、`git diff --check` 通过；提交前另检查暂存 diff 和任务文件清单。

## 验证限制

正式 RAG 用例执行实际 Source/Registry/Collector/Selector 和用量包装，使用合成检索索引及离线模型替身；生成器测试在 SDK 边界提供受控响应。没有声称已验证线上模型效果或真实服务账单。12 项既有环境 skip 涉及在线 JEV、显式启用的真实 Qwen/Qdrant 与进程重启验证条件，本轮逐项比对一致，不新增跳过项。不涉及前端 UI 或部署。

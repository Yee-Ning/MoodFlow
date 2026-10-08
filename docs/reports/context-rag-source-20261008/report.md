# ContextPack RAG Source 接入验收

日期：2026-10-08。分支：`codex/context-rag-source`。

## Gate 0 与确认记录

最初指定的 Phase 3 实现 commit 是 `56b1090ab62f373855a54d908235a11903aeb75b`，实际分支 HEAD 为 `d1ff818c9318b532dfe5de81fb0c5cf2865a789e`。先停下报告差异，West 明确选择 `d1ff818c`。两者只差 Phase 3 报告、契约样例与验证摘要三个文件，没有实现差异；从获准 HEAD 建立本分支。

只读盘点完成后再次停下；West 确认情形 A，并裁决允许 Embedding、直接启用 rerank、逐 chunk 审计为兼容增补、测试不依赖线上索引、保留 WorldLine rag_pack。

| 盘点项 | 获准基线实际形态 | 结论 |
| --- | --- | --- |
| SourceRequest / Registry | request 字段为 query、continuity、profile_store、history_store、allow_new_sources；没有 intent/SearchPlan。collect(request, *, limit) 同步返回 list；Registry.collect 返回候选与状态二元组 | 不改变接口签名，入口原先通过 asyncio.to_thread 调用 Collect |
| 已注册 sources | 默认仅 user_profile、history_memory | rag 尚未正式注册 |
| context_policy.json | user_profile：240 token / 16 项 / 优先级 10；history_memory：600 token / 24 项 / 优先级 20；无 rag 项 | 仅新增 rag 配额，原来源配置不改 |
| 既有 L1 RAG | WorldLine allowed_types 包含 rag_evidence，来源预算 5000、优先级 85；ContextCollector 支持 rag_pack | 保留原侧通道，复用现有类型和预算量级 |
| 检索器 | app/rag/retrieval/service.py 的 HybridRetrievalService；QwenQueryEncoder + Qdrant dense/BM25 + RRF；独立 QwenCandidateReranker 已存在 | 检索器存在，执行情形 A，不新造检索器 |
| 模式读取 | worldline/retrieval.py 的 RetrievalRuntimeSettings 读取 AI_RAG_RETRIEVAL_MODE（默认 empty）与 AI_RAG_INDEX_MANIFEST；旧 rerank_mode 默认 off | 复用 empty/shadow/hybrid，Source 的 rerank 始终启用，旧 WorldLine 设置不改 |
| Collect 调用 | EntryContextEngine.complete 只调用 Registry；WorldLine executor 另检索后向 ContextCollector 传 rag_pack | 原 Collect 未直接检索，正式 Source 接入缺失 |
| knowledge/v2 | 开发机存在，但不在 Git 树；运行绑定须校验发布 manifest 与 pinned 模型、向量格式 | 不读取或复制线上语料；本轮测试只用自备候选/受控索引后端 |
| manifest | 原字段 tokens/budget 与 sources 的 candidate_tokens/final_tokens/events/dedup_dropped | 保留原字段含义，新增可选审计字段 |

## 接入与选择策略

新 item ID 为 `evidence:rag:{parent_id}`，保留 metadata.evidence_id 的原 parent_id 去重键。旧 rag_pack 与新 Source 同时出现时，trace 的 ID 不混用；是否 included 按 source + item_id 判断，丢弃的 RAG chunk 不会被旧证据误标为已保留。混合来源用例验证原侧通道继续组装、重复新候选被去重且审计 token=0。

新增 RagSource，默认注册顺序为 user_profile、history_memory、rag。既有 SourceRequest 和 ContextSource Protocol 不改；候选列表的兼容 sidecar 只携带观测，不供下一次选择使用。入口将候选和 sidecar 分别送给既有组装路径，lite 仍不收新来源。

SearchPlan 是代码构造的单条 literal hybrid 查询：没有 LLM 查询改写、HyDE 或生成调用。模型不参与 Select。每个命中 Child 携带 doc_id、source_id、source_name、可选 URL、chunk_id、融合及重排分数；缺少出处的候选在 rerank 前剔除。Parent 不隐式展开。内容和 citation 保持数据区段转义。

配额：`rag` 类型 rag_evidence，priority=85、tokens=5000、candidate_limit=24。5000/85 取自既有 WorldLine L1，24 取自既有 history_memory 候选量级，并低于现有 reranker 的 40 项硬上限。先限制候选，再重排；现有 reranker 输出最多 12 项。代码 Select 按当前路由白名单及 intents 过滤，随后原 Dedup、来源配额、组装序列化预算门处理。超限整条进入 dropped_items，未增加新的压缩策略。

empty 不检索/不加载模型；shadow 执行检索和重排并记成本，但不向 Pack 提供证据；hybrid 提供证据。RAG Source 的 rerank 明确开启，即使旧 WorldLine 设置为 off 也不会关闭；推理失败沿用既有 RRF 降级。旧 rag_pack 注册、执行器、路由与 Guard 完整保留。

## 记账和审计

非生成推理复用 RequestLedger 的 reserve/settle 与 DurableAccounting 的 MODEL_USAGE，BudgetLedger Protocol、L2/L3 阈值及降级顺序不变。模型维度：

- `rag-embedding:Qwen/Qwen3-Embedding-0.6B`
- `rag-rerank:Qwen/Qwen3-Reranker-0.6B`

当前 Agent 的 L2 与任务 L3 均累计，避免检索成本绕过任务预算；未新建 Agent 枚举。输入计数使用现有 Qwen 实际格式化、截断的 tokenizer 序列，不含 padding，output_tokens=0。模型权重加载不计为 forward；替身 encoder/scorer 明确使用估算口径。无生成输出 cap，PublicUsage 的 max_output_tokens 留空，未修改既有约束。

forward 前持久化预留，成功后写结算。失败保留 reserved 事实，当前 ledger 计入保守估算；恢复时同一 call 只取一次最终/预留账单。预算拒绝穿透检索器及 Source 可选错误捕获，触发现有 L2/L3 降级。PG 预留/结算失败中止请求，不静默丢账。共享本地模型受进程内锁保护，检索服务/asyncio 锁按调用建立，客户端在调用结束关闭。

manifest 的 sources.rag 增补 chunks、model_usage、collection。逐 chunk 记录文档、来源、标题、chunk、融合/重排/最终分数、候选和最终 token、included 及原因；model_usage 提供独立维度、模型 revision、call_id、计数口径、估算标记、状态与 MODEL_USAGE 指针。成功指向 settled，未知失败指向 reserved。原聚合值仍由 L1 计算，sidecar 无权覆盖。旧 manifest 仍可校验；manifest 不再计第二笔账。

## 验证

重点回归（RAG Source / PG、Phase 1、Phase 2、Phase 3 契约、LongHorizon）：107 passed，1 个既有 warning，44.09s。新增索引不可用用例也通过；原测试无删除、跳过或断言改写。

执行命令（项目根目录；DATABASE_URL 指向本轮隔离测试 PG）：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:AI_RAG_RETRIEVAL_MODE='empty'
$env:JEV_ENABLED='false'
$env:PERSISTENCE_BACKEND='sqlite'
$env:AI_CONTINUITY_STORE_MODE='sqlite'
$env:MEMORY_BACKEND='memory'
$env:CHECKPOINT_BACKEND='memory'
./ai-service/.venv/Scripts/python.exe -m pytest -p pytest_asyncio.plugin ai-service/tests/test_context_rag_source.py ai-service/tests/test_context_rag_postgres.py ai-service/tests/test_context_entry_phase1.py ai-service/tests/test_context_contracts_phase3.py ai-service/tests/test_context_budget_phase2.py ai-service/tests/test_long_horizon_context.py -q
```

首次全量：2356 passed、1 failed、12 skipped、185 warnings、288 subtests，374.11s。唯一失败为旧 `test_history_and_lineage_survive_store_reopen` 的中文 stdout 解码：子进程 PYTHONIOENCODING=utf-8，而父进程 subprocess(text=True) 按 Windows 本地编码读取，得到乱码。补齐父子进程 PYTHONUTF8=1 后原断言通过；没有修改原测试或持久化代码。首轮诊断保存在 verification-first-run.json。

编码修正后全量：2358 passed、0 failed、12 skipped、185 warnings、288 subtests，451.45s（verification-before-compat.json）。此后兼容复查发现新 Source 与旧 rag_pack 的 item ID 别名会误标 chunk included；修正命名空间并加入混合来源用例，未改旧去重算法。混合来源用例初版遗漏 route intent，被既有 Select 正常排除；补齐测试输入后 RAG / PG / ContextEngineering 38 项全部通过（10.85s）。

最终全量：**2359 passed、0 failed、12 skipped、185 warnings、288 subtests passed，366.06s**。与获准 Phase 3 基线的 12 项 skip 集合完全一致，增加 27 项通过用例，无新增 skip。完整运行范围为 `tests evals/tests`，显式启用 pytest_asyncio.plugin / xdist.plugin，4 workers、--dist loadfile；额外 eval 依赖复用现有 .venv-eval。隔离 PG 使用 pgvector/pgvector:pg16、127.0.0.1:15474，未操作业务数据库或其他线程容器。最终运行源码 SHA256 与提交前工作区逐一核对一致。

清理完成：本轮 moodflow-context-rag-pg-test-20261008 容器及匿名卷已删除，五个临时 driver/SQLite 样例文件已删除；验收记录保留在报告目录。不操作其他线程容器。

完整运行等效命令（ai-service 目录，复用上述环境并追加 DEEPEVAL_DISABLE_DOTENV=1 与现有 eval 依赖路径）：

```powershell
./.venv/Scripts/python.exe -m pytest -p pytest_asyncio.plugin -p xdist.plugin -n 4 --dist loadfile tests evals/tests -q
```

本轮临时 driver 只设置测试环境、运行同一命令并收集 logreport/最终摘要，不修改测试、断言或实现；最终摘要记录各源文件 SHA256。driver 和自备 SQLite 样例库不入 Git。

Ruff 检查所有本轮 Python 文件通过；compileall 通过；任务文件 git diff --check 通过。整个工作区 diff --check 另发现用户既有 gold_review_v0_blind_pack.md 空白行尾空格，未修改、未提交该文件。

覆盖路径：正式注册与原配置不变；hybrid 检索和强制 rerank；24 项前置候选限制；无出处剔除；代码路由 Select、重复内容 Dedup、来源配额及全局 L1 丢弃；逐 chunk / 模型维度 manifest；empty/shadow；缺 manifest / 索引不兼容；推理失败 RRF 回退、未知账单恢复；L2/L3 拒绝及原降级次序；PG 写失败中止；精确 tokenizer 格式/截断计数；入口线程作用域到异步 Pack 的观测接入；真实 PG 结算与未知预留重开恢复；旧 manifest 兼容。

manifest-example.json 是受控模型/自备 hybrid 后端的实际产物：3 个候选中保留 1 个，1 个去重、1 个配额丢弃，含两种推理的独立模型维度及四条 reserve/settle 用量事实；替身用量明确 estimated=true。

## 范围与限制

无线上索引、模型下载、真实模型质量或性能验证；测试使用 empty/shadow 与自备后端/候选、受控 encoder/scorer。tokenizer 包装用受控 Qwen 实例验证格式和准确计数边界。真实 PG 用量持久化与重开恢复另有集成测试。当前 literal 查询不做指代消解或 LLM 查询扩展；源文本按既有整条丢弃规则，不声称新增逐 chunk 截断策略。

旧 WorldLine rag_pack 路径为兼容保留，hybrid 启用后可能与入口发生重复检索，未在本轮删改该执行链。新 Source 成本已记账；本轮不声称修改旧侧通道的既有用量机制。未改变生产配置、路由、Guard、转交、预算 Protocol 或五个 Redis 特性分支；未 push、未部署。用户已有修改/删除与未跟踪数据均保留，不纳入提交。

## 提交

实现 commit：`2f6d5f9a90a1a96fb191b8d726fae2ef10f63c0b`（feat: integrate accounted RAG source into context packs），仅 10 个任务文件。验收报告另建独立提交，HEAD 可由 git log 查询。未 push、未部署。

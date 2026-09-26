# MoodFlow RAG Eval Harness V0

## Semantic Relations Evaluation v1

Era + Scene + Influence 使用同一评测入口：`.\.venv\Scripts\python.exe run_semantic_relations_eval.py`。
数据 schema、独立来源快照、freeze、指标分母和人工审核流程见
[`semantic_relations_v1/README.md`](semantic_relations_v1/README.md)。
正式人工 Gold、待审核真实诊断和 synthetic 回归分别计分；正式 Gold 覆盖不足时退出码为 2，
不将待审核样本或 synthetic 通过率报告为真实 baseline。结果位于 `reports/semantic_relations_v1/`。

2026-09-26 人工审核新增独立版本 [`semantic_relations_v1_review_r1/README.md`](semantic_relations_v1_review_r1/README.md)：
8 条 APPROVE 晋升，4 条 REJECT 排除，4 条新 weak negative 保持 pending；
复审包见 [`human_review_pack.md`](semantic_relations_v1_review_r1/human_review_pack.md)。该版本尚未运行 Evaluation，旧 freeze 与报告未改动。

第二轮 4 条 weak negative 均获人工 APPROVE，登记于 [`semantic_relations_v1_review_r2/README.md`](semantic_relations_v1_review_r2/README.md)。
正式 Gold 现为 12 条（Era/Scene/Influence 各 4），本批 pending 为 0；原 4 条 REJECT 仍排除。r2 尚未运行 Evaluation。

真实 negation、conflict 与 Influence 正反方向候选新增于独立版本
[`semantic_relations_v1_review_r3/README.md`](semantic_relations_v1_review_r3/README.md)：
10 条全部 pending，人工审核包见 [`human_review_pack.md`](semantic_relations_v1_review_r3/human_review_pack.md)。
既有 12 条 approved Gold、旧 freeze 和 failure 报告未变；只验证数据完整性与既有功能回归，暂不运行最终 Evaluation。

r3 的 10 条候选已全部获得明确人工 APPROVE，登记于独立
[`semantic_relations_v1_review_r4/README.md`](semantic_relations_v1_review_r4/README.md)。
正式 Gold 为 22 条（Era 6、Scene 6、Influence 10），pending 为 0；原 4 条 REJECT 仍排除。
新增 receipts 绑定原冻结标签和 replay 行，审核登记见
[`human_review_registration.md`](semantic_relations_v1_review_r4/human_review_registration.md)。r4 尚未运行 Evaluation。

## WorldLine Evaluation V0

WorldLine 的专项确定性评测使用独立数据集和报告，不调整 RAG 参数，也不使用
LLM Judge 代替 relation、evidence、identity、graph 或 checkpoint 校验。

快速运行 Component Replay 与 Session Replay：

```powershell
.\.venv\Scripts\python.exe run_worldline_eval.py
```

同时运行真实 PostgreSQL 跨进程恢复：

```powershell
$env:RUN_WORLDLINE_EVAL_POSTGRES='1'
.\.venv\Scripts\python.exe run_worldline_eval.py --include-postgres
```

数据集位于 `evals/datasets/worldline_eval_v0.jsonl`。当前 V0 使用明确标注的合成契约
fixtures，验证执行边界和失败原子性，不表示已经完成真实音乐事实 Gold 的独立人工审核。
运行会生成逐案例结果、指标、失败审计和 Markdown 报告到 `evals/reports/`。

## WorldLine Gold Dataset V1 Infrastructure

WorldLine runner 支持彼此隔离的 suite：

```powershell
.\.venv\Scripts\python.exe run_worldline_eval.py --suite synthetic
.\.venv\Scripts\python.exe run_worldline_eval.py --suite real-gold
.\.venv\Scripts\python.exe run_worldline_eval.py --suite all
```

`synthetic` 继续读取 V0 contract fixture。`real-gold` 读取
`evals/datasets/worldline_gold_v1.jsonl` 与
`evals/fixtures/worldline_gold_v1_sources.jsonl`，在任何 case 执行前完成 schema、exact span、
source hash、ledger 引用、重复 relation 和 identity 校验。`all` 顺序执行两个 suite，
分别输出 `synthetic_baseline` 与 `real_gold_baseline`，不会混合统计。

Gold V1 的 adapter 只在 `evals/` 中构造隔离的 replay 输入，不会写入生产 Knowledge Base、
Entity Registry、WorldLine Graph 或数据库。本阶段没有创建 Gold JSONL；在真实审核数据冻结前，
`--suite real-gold` 会以 `worldline_gold_dataset_missing` 明确失败。

本目录承载离线评测资产，不参与在线请求。V0 的目标是先冻结
`Query Understanding -> Top40 -> Rerank Top12 -> Context Top8 -> Generation`
的可回放合同，再用同一份人工标签分别评估检索、上下文和最终回答。

## 为什么 DeepEval 使用独立环境

`deepeval==4.2.3` 要求 `click<8.4.0`，MoodFlow 当前 Hugging Face 运行栈要求
`click>=8.4.2`。因此 DeepEval 不加入主 `requirements.txt`，Judge 进程只读取导出的
JSONL，不导入 `app`，也不会改变 Qwen Reranker 或线上服务依赖。

```powershell
python -m venv .venv-eval
.\.venv-eval\Scripts\python.exe -m pip install -r evals\requirements.txt
.\.venv-eval\Scripts\python.exe -m pip check
```

主工程继续使用 `.venv`。不要把两个环境混用。

## 数据与 Trace 合同

### 独立 Gold V0 复核（与 Silver 分开）

目前 50 条批量通过的数据是由旧最终上下文 Top3 自动生成证据的 **Silver**，不是独立 Gold。固定 30 条分层盲审候选见
`datasets/rag_eval_gold_v0.pending.jsonl`，无旧证据/答案标签；盲审清单见
`reports/gold_review_v0_blind_pack.md`，质量审计和原在线回归状态见
`reports/gold_review_v0_preparation_20260916.md`。原始候选仍全部为 `pending`；
审批后的独立文件见下文。

审核包现已增加每题 5 条**独立来源证据候选**：只读取冻结的
`data/knowledge/v2/knowledge_release_manifest.json` 和通过其 SHA256 校验的 Parent 原文，
展示 Parent/Source ID、Source Document 链接、足够上下文的原文节选以及可精确核对的候选 span。
候选结构化副本在 `reports/gold_review_v0_source_candidates.jsonl`。生成器不接收旧 Silver、
在线 Trace、排名、分数或答案；Source 候选并不自动成为 relevant Parent。复现命令：

```powershell
.\.venv\Scripts\python.exe run_rag_gold_evidence_pack.py `
  --pending-gold evals/datasets/rag_eval_gold_v0.pending.jsonl `
  --release-manifest data/knowledge/v2/knowledge_release_manifest.json `
  --review-pack evals/reports/gold_review_v0_blind_pack.md `
  --candidates-jsonl evals/reports/gold_review_v0_source_candidates.jsonl
```

审核人应先独立判断预期结果，再展开待审骨架中的候选值；逐条对 relevant Parent、
supporting span、required facts 和 reference answer 选择 `APPROVE/CORRECT/REJECT`
并写明最终值。`required facts` 和 `reference answer` 未由机器自动拼接，需依据已核实的证据填写。
此步骤仅生成审核材料，不修改 pending Gold，也不 finalize。

项目负责人现已确认审批原稿；其原样副本位于
`reports/gold_review_v0_owner_approved.md`（SHA256：`de4e389259dd2b572e6072603ae22e18ee09252b540b11ee1d6bb7ed9ad5bfc1`）。30 条审批标签保存在
`datasets/rag_eval_gold_v0.approved.jsonl`，标签注明 `owner_approved_ai_assisted_review`：
负责人认可了 AI 辅助复核稿，但它不是逐条独立人工盲标数据。冻结 Parent 校验、候选决策、
精确片段和输出合同由 `run_rag_gold_finalize.py` 检查；待审骨架仍保留原样。运行时传入审批稿：

```powershell
.\.venv\Scripts\python.exe run_rag_gold_finalize.py `
  --review evals\reports\gold_review_v0_owner_approved.md `
  --pending-gold evals\datasets\rag_eval_gold_v0.pending.jsonl `
  --candidates-jsonl evals\reports\gold_review_v0_source_candidates.jsonl `
  --release-manifest data\knowledge\v2\knowledge_release_manifest.json `
  --output evals\datasets\rag_eval_gold_v0.approved.jsonl
```

审批稿中 `rag-v0-024` 的修正引文尾部与原文不完全一致，finalize 只取其已审批且
逐字存在于 Parent 中的首句；`rag-v0-037` 无本题候选精确片段，复用同一 Parent
在 `rag-v0-001` 已审批的精确片段。摘要会报告这两项以及未逐 Parent 附片段的数量，
不得将它们误写为逐条独立人工标注。`needs_input` 的追问准则和示例答复存于备注，
不填入合同禁止的答案证据字段。

`run_rag_gold_review_prep.py` 可复现抽样和原文/血缘审计，但不会生成或批准 Gold 标签，也不会调用 Judge。若后续需要“独立人工盲标 Gold”，审核人仍须从冻结语料逐项独立核对结果类别、相关 Parent、精确支持片段、必要事实与参考答案；本次项目负责人认可 AI 辅助复核稿不等同于该流程。`validate-dataset` 当前的 `baseline_ready` 是面向原 50 条 V0 基线的检查；对 30 条 Gold 子集会显示 `false`，不能把它误解成 JSONL 格式错误。

2026-09-16 的同名歧义结果策略只调整 `rag-v0-046/047`：用户确认继续返回 `needs_input`，与 Query Understanding Golden Set 一致。策略版本位于 `datasets/rag_eval_gold_v1.outcome_policy.approved.jsonl`；V0 保留不变，原答案证据仍可在 V0 查到。复现使用 `run_rag_gold_outcome_policy.py`，固定 Trace 回放和影响解释见 `reports/gold_v1_outcome_policy_replay_20260916.md`。这个版本没有批准独立的 Gold V1 source-span 候选；JSONL 合同的 `schema_version=v0` 与稳定 case ID 不因数据集版本而改名。

后续冻结来源复核产物为 `datasets/rag_eval_gold_v2.ai_assisted_source_review.jsonl`。18 条候选的逐条接受/排除理由在 `reports/gold_v2_source_decisions.jsonl`，固定 Trace 对比和局限见 `reports/gold_v2_source_review_replay_20260916.md`。使用 `run_rag_gold_source_adjudication.py` 从 V0、V1、候选、判定和冻结 release 复现。V2 仍为用户委托的 AI 辅助审查结果；逐字来源校验不等于独立人工盲标或模型改进。

- `datasets/rag_eval_v0.pending.jsonl`：从项目现有 Query Understanding Golden Set 中按
  `jay_chou_corpus_v1` 范围筛选的 50 条待审样本；45 条预期可回答、5 条预期追问。
  独立的蔡依林、林俊杰、王菲、张学友、G.E.M.、S.H.E 等查询不进入主 Baseline；只有
  明确涉及周杰伦合作或对比的跨艺人问题才保留。
- `annotation_status=pending` 表示不能进入正式指标或 LLM Judge。
- 可回答样本改为 `approved` 前，必须人工填写 `relevant_parent_ids`、
  `supporting_spans`、`required_facts` 和 `reference_answer`。
- `notes` 是可选审核备注，不要求填写；仅记录歧义、排除原因或特殊判定。
- `RAGEvaluationTrace` 同时保存 Top40、Top12、最终上下文、引用、版本和耗时。
  合同会阻止跨阶段来源不一致、重复排名和引用未知 Parent。

```powershell
# 校验 50 条 V0 数据集；未达到 30 条 approved 时 baseline_ready=false
.\.venv\Scripts\python.exe run_rag_eval.py validate-dataset `
  --dataset evals\datasets\rag_eval_v0.pending.jsonl

# 如 Query Understanding Golden Set 更新，可重新生成待标注骨架
.\.venv\Scripts\python.exe run_rag_eval.py generate-skeleton `
  --source tests\fixtures\query_understanding\golden_v1.jsonl `
  --output evals\datasets\rag_eval_v0.pending.jsonl `
  --limit 50

# 校验一次在线链路导出的 Trace
.\.venv\Scripts\python.exe run_rag_eval.py validate-traces `
  --traces evals\artifacts\baseline-v0\traces.jsonl

# 计算不调用 Judge 的硬指标
.\.venv\Scripts\python.exe run_rag_eval.py evaluate `
  --dataset evals\datasets\rag_eval_v0.approved.jsonl `
  --traces evals\artifacts\baseline-v0\traces.jsonl `
  --output evals\artifacts\baseline-v0\deterministic-report.json
```

### 采集真实 Top40 / Top12 / Top8

先启动已经持久化正式索引的 Qdrant，再用独立采集入口执行与线上一致的 Query
Understanding、Hybrid Retrieval、Qwen Reranker 和 WorldLine Context Adapter：

```powershell
docker compose -f docker-compose.qdrant.yml up -d

.\.venv\Scripts\python.exe run_rag_trace_collection.py `
  --dataset evals\datasets\rag_eval_v0.pending.jsonl `
  --index-manifest data\indexing\v1\knowledge-v2-8608ffc2fa72e441\index_build_manifest.json `
  --output evals\artifacts\baseline-v0\traces.jsonl `
  --run-id rag-baseline-v0-20260915 `
  --no-llm `
  --rerank
```

`--no-llm` 只关闭 Query Understanding 的 LLM fallback，不会关闭 Dense Embedding 或
Qwen Reranker。采集器会在批次开始前加载并预热本地 Reranker，模型下载和冷启动不计入
单条 30 秒推理超时。采集按样本隔离失败：单条异常会写入 Trace 并令进程返回 `2`，不会
丢失其余结果；索引或配置级错误返回 `1`。产物包含 Top40、Top12 和最终 Top8 的 Parent
血缘、版本、状态与耗时。

然后生成可读审核包，人工只需围绕真实候选选择 relevant Parent、核对 supporting span，
并填写 required facts 与 reference answer：

```powershell
.\.venv\Scripts\python.exe run_rag_eval.py export-review-pack `
  --dataset evals\datasets\rag_eval_v0.pending.jsonl `
  --traces evals\artifacts\baseline-v0\traces.jsonl `
  --output evals\artifacts\baseline-v0\annotation-review.md
```

审核包中的勾选框只是阅读辅助，不是正式标签。确认后的字段仍需写入新的
`rag_eval_v0.approved.jsonl`；不要覆盖 pending 原始骨架。答案生成尚未接入时，可以先完成
Top40/Top12/Top8 的确定性评测；只有 `actual_answer` 和引用齐备后才能导出 Judge 用例。

若项目负责人明确选择整批通过、暂不逐条审核，可从已落盘的真实 Trace 生成一份单独的
Silver 数据集：

```powershell
.\.venv\Scripts\python.exe run_rag_eval.py bootstrap-silver `
  --dataset evals\datasets\rag_eval_v0.pending.jsonl `
  --traces evals\artifacts\baseline-v0\traces.jsonl `
  --output evals\datasets\rag_eval_v0.approved.silver.jsonl `
  --max-evidence 3
```

该命令会给每条记录增加 `silver_bootstrap_v0` 和 `user_bulk_approved` 标签：成功检索的
样本从最终 Top8 的前三个 Parent 抽取可追溯证据；空检索样本标为
`insufficient_evidence`；追问样本不生成伪证据。Silver 标签由当前链路结果反向生成，
因此只能用于合同、血缘和回归冒烟，不能当作独立人工 Gold 证明检索质量。

## Context Adapter V1 单变量调优

调优固定使用同一份已批准数据集和同一份真实 Top40/Top12 上游结果，只扫描 Context
Adapter 参数。`context_adapter_v1_baseline.yaml` 冻结调优前默认值，避免 V1 配置落地后
重跑时污染 baseline。扫描顺序为 `context_top_k → neighbor_window → source_cap →
token_budget`；每一阶段只改变当前参数，并把该阶段最佳配置传给下一阶段，不做全组合。

当 Qdrant 可用时可省略 `--upstream-traces` 和 `--index-projection`，重新执行一次固定上游；
日常复现实验优先使用回放模式，避免 Retrieval/Rerank 漂移和重复模型开销：

```powershell
.\.venv\Scripts\python.exe run_context_adapter_tuning.py `
  --dataset evals\datasets\rag_eval_v0.approved.silver.jsonl `
  --index-manifest data\indexing\v1\knowledge-v2-8608ffc2fa72e441\index_build_manifest.json `
  --baseline-config evals\config\context_adapter_v1_baseline.yaml `
  --upstream-traces evals\artifacts\baseline-v0\traces.context-v1.jsonl `
  --index-projection data\indexing\v1\knowledge-v2-8608ffc2fa72e441\index_projection.jsonl `
  --report evals\artifacts\context-adapter-v1\context-tuning-report.json `
  --markdown-report evals\artifacts\context-adapter-v1\context-tuning-report.md `
  --best-traces evals\artifacts\context-adapter-v1\best-traces.jsonl `
  --run-id context-adapter-v1-tuning `
  --no-llm
```

扫描只计算 Precision、Supporting Span、Subquery/Entity Coverage、Duplicate Rate、Token 和
Context 延迟等确定性指标，不读取 Judge Key。选出最佳配置后只生成一次答案；将产物通过
`--generated-traces` 传回调优命令即可在同一报告补齐 Generation P50/P95/P99。最终再用
`run_rag_eval.py evaluate` 生成包含 Retrieval、Rerank、Context Adapter、Generation 四阶段
延迟与引用合法性的 V1 验收报告。

## DeepEval Judge

先用主工程已经配置的回答模型消费最终 Top8，并生成带 Parent 引用的 Trace。生成器不会读取
数据集中的 `reference_answer`，每条事实必须使用 `[parent_id]` 内联引用；未知引用、引用数组
与正文不一致会自动重试一次，单条失败不会丢掉整批结果：

```powershell
.\.venv\Scripts\python.exe run_rag_answer_generation.py `
  --dataset evals\datasets\rag_eval_v0.approved.silver.jsonl `
  --traces evals\artifacts\baseline-v0\traces.jsonl `
  --output evals\artifacts\baseline-v0\traces.generated.jsonl `
  --max-concurrent 4
```

先由主工程把审批后的标签和真实 Trace 转成依赖无关的交换文件：

```powershell
.\.venv\Scripts\python.exe run_rag_eval.py export-deepeval `
  --dataset evals\datasets\rag_eval_v0.approved.silver.jsonl `
  --traces evals\artifacts\baseline-v0\traces.generated.jsonl `
  --output evals\artifacts\baseline-v0\deepeval-cases.silver.jsonl
```

再在隔离环境运行 Judge。V0 默认配置保存在 `evals/config/judge_v0.json`，使用项目现有
DeepSeek 接口：Provider `deepseek`、模型 `deepseek-v4-flash`、Base URL
`https://api.deepseek.com`、密钥变量名 `LLM_API_KEY`、temperature `0`。Runner 通过
自定义 `DeepSeekJudge(DeepEvalBaseLLM)` 访问 OpenAI-compatible Chat Completions，
不会实例化 OpenAI Judge。

默认配置允许从项目根目录 `.env` 中只读取配置指定的 `LLM_API_KEY`；环境中已存在同名
变量时优先使用环境值。密钥值不会写入配置、数据集、Trace 或报告。DeepEval 自身的隐式
dotenv 读取仍由 `DEEPEVAL_DISABLE_DOTENV=1` 关闭。

```powershell
.\.venv-eval\Scripts\python.exe evals\run_deepeval.py `
  --config evals\config\judge_v0.json `
  --budget-config evals\config\semantic_smoke_v0.json `
  --dataset evals\artifacts\baseline-v0\deepeval-cases.silver.jsonl `
  --output evals\artifacts\baseline-v0\deepeval-report.json
```

以上命令默认只打印预算计划，**不会读取 API Key，也不会调用 Judge**。确认
`planned_judge_metric_runs` 和 `estimated_source_tokens` 后，只有显式增加 `--execute`
才允许联网。Smoke V0 固定 8 条代表案例，只运行 Faithfulness 与 Answer Relevancy，
最多 16 个“案例 × 指标”槽位；配置超过上限会在读取密钥之前失败。

复用历史报告时传入 `--resume-from`。匹配当前 Judge 配置的成功指标会直接命中缓存，
402、429、Timeout 等错误项才会重试。输出报告自身也可在后续运行中作为缓存；答案、
引用证据、Judge 模型、阈值或评测配置变化后缓存指纹会自动失效。

```powershell
.\.venv-eval\Scripts\python.exe evals\run_deepeval.py `
  --config evals\config\judge_v0.json `
  --budget-config evals\config\semantic_smoke_v0.json `
  --dataset evals\artifacts\baseline-v0\deepeval-cases.silver.jsonl `
  --resume-from evals\artifacts\baseline-v0\deepeval-report.previous.json `
  --output evals\artifacts\baseline-v0\deepeval-smoke-report.json `
  --execute
```

Provider、模型、Base URL、API Key 环境变量名、temperature 和 timeout 均可在 JSON
配置，也可分别由 `--provider`、`--model`、`--base-url`、`--api-key-env`、
`--temperature`、`--timeout-seconds` 覆盖。对应环境覆盖为
`DEEPEVAL_JUDGE_PROVIDER`、`DEEPEVAL_JUDGE_MODEL`、`DEEPEVAL_JUDGE_BASE_URL`、
`DEEPEVAL_JUDGE_API_KEY_ENV`、`DEEPEVAL_JUDGE_TEMPERATURE` 和
`DEEPEVAL_JUDGE_TIMEOUT_SECONDS`。未来切换 OpenAI 或其他兼容接口时使用 Provider
`openai` / `openai_compatible` 并提供对应 URL 与密钥变量即可，不需要修改指标代码。

Judge 报告会保存实际 Provider、模型、Base URL、密钥变量名、temperature、阈值和并发
数，但绝不保存密钥值；不要混用不同 Judge 配置的绝对分数。

经济模式 V0 只有 Faithfulness 和 Answer Relevancy 调用 Judge。Faithfulness 只发送答案
实际引用的 Parent Context；Answer Relevancy 只使用 Query 与 Answer；两项均默认关闭理由
生成以减少额外调用。Contextual Precision / Recall 使用人工标签中的 Parent ID 和
Supporting Span 做确定性评估，不再调用 LLM。Recall@K、MRR、nDCG、最终 Context
Coverage、Citation Presence / Validity、Token Budget Compliance 和状态门禁同样由主环境
`run_rag_eval.py evaluate` 确定性计算，不会导入 DeepEval、读取 Judge Key 或发起网络请求。
阈值 0.5 只是首轮观测值，在人工审批 Baseline 稳定前不用它阻断发布。

## 分层执行策略

1. PR：运行普通单测、Ruff 和 `validate-dataset`，只阻断合同、引用合法性、排名血缘和
   token 预算等确定性错误；不联网、不调用 Judge。
2. 发布候选：针对同一索引 Release 生成 Trace，比较 Retrieval/Rerank/Context 的
   Recall、MRR、nDCG、fallback、延迟和引用指标。
3. 重要改动：运行固定 8 条 Semantic Smoke；先看预算计划，再显式执行。
4. Nightly/发布前：只有在 Smoke 和确定性门禁通过后才扩充样本；仍必须设置案例数、
   指标槽位和估算输入上限，并复用缓存，禁止无预算全量重跑。

RAGAS 暂不加入 V0。等至少 30 条样本完成双人复核并得到稳定 Baseline 后，再将相同交换
文件接入 RAGAS 做二次交叉验证；它不替换项目自己的确定性门禁，也不与 DeepEval 同时成为
唯一质量裁决者。

## Gold V2 检索/精排投影对照

`run_retrieval_eval.py` 的 `--rag-gold` 只读取已审批且 `answerable`、带相关 Parent 的
案例；`needs_input` 不计入召回分母。用 `--no-llm` 关闭 Query Understanding 的 LLM
fallback，用 `--rerank` 才加载本地精排模型。示例（V3）：

```powershell
.\.venv\Scripts\python.exe run_retrieval_eval.py `
  --dataset evals\datasets\rag_eval_gold_v2.ai_assisted_source_review.jsonl `
  --rag-gold --no-llm --rerank `
  --index-manifest data\indexing\v1\knowledge-v2-8608ffc2fa72e441\v3\index_build_manifest.json `
  --output evals\reports\gold_v2_retrieval_v3_rerank.json
```

同命令将 manifest 换成 release 根目录下的 V2 版本即可得到同 Gold 对照；本次结果及
限制见 `reports/gold_v2_retrieval_projection_v3_20260916.md`。这些命令不调用 DeepEval
或付费 Judge。V3 完整构建应使用本地模型缓存并单独的版本目录，不覆盖旧 V2 产物。

## Jev Shadow Evaluation v1

在 `ai-service` 目录使用现有 `run_jev_eval.py`：

```powershell
.\.venv\Scripts\python.exe run_jev_eval.py prepare
.\.venv\Scripts\python.exe run_jev_eval.py router
.\.venv\Scripts\python.exe run_jev_eval.py prepare-memory
.\.venv\Scripts\python.exe run_jev_eval.py memory
.\.venv\Scripts\python.exe run_jev_eval.py context
.\.venv\Scripts\python.exe run_jev_eval.py report
```

Router/Context 数据来自现有人工 intent 保留集、Foundation E2E 和 Gold V4 冻结轨迹；
Memory 从现有 PostgreSQL/pgvector 经 `MemoryRetriever` 读取，原文诊断集及逐项结果保存在
Git 忽略的 `evals/local/`。Router/Memory/Context 命令会真实调用 TypeSafe SDK，要求
`JEV_SHADOW_MODE=true`、`JEV_MODEL=jev-latest` 和可用 API Key。输出按
`requested_model`、`response_model`、`evaluation_timestamp` 记录；只比较同批输入和候选。
报告见 `reports/jev_shadow_evaluation_v1.md`，完整 failure audit 见
`reports/jev_shadow_failure_audit_v1.jsonl`。旧的 `run_jev_eval.py <jsonl>` 汇总入口保留。

## Jev Shadow Evaluation v2

v2 继续复用 `run_jev_eval.py`，把 Router 单意图准确率与多意图 Noul 诊断分开，
以 100 条合成 Memory 和真实 pgvector Top-8 采样构造固定候选集，并按任务效用重标 30 个
ContextPool 场景。全量复评顺序：

```powershell
.\.venv\Scripts\python.exe run_jev_eval.py prepare-router-v2
.\.venv\Scripts\python.exe run_jev_eval.py router-v2
.\.venv\Scripts\python.exe run_jev_eval.py prepare-memory-v2
.\.venv\Scripts\python.exe run_jev_eval.py memory-v2
.\.venv\Scripts\python.exe run_jev_eval.py prepare-context-v2
.\.venv\Scripts\python.exe run_jev_eval.py context-v2
.\.venv\Scripts\python.exe run_jev_eval.py report-v2
```

`prepare-memory-v2` 需要本地 `moodflow-postgres` 容器与项目 embedding 模型；它仅临时
写入专用 synthetic user/ID 记录，采样后清理。默认 100 条查询与 30 条指定
`memory_type=episodic` 的探针在报告中分别统计。Router/Memory/Context 的真实 Jev
回放需要官方 TypeSafe SDK、`JEV_MODEL=jev-latest`、`JEV_SHADOW_MODE=true` 及可用 API Key。
数据集在 `datasets/*_eval_v2.jsonl`，逐 case 结果与完整 failure audit 在 `reports/`，
报告见 `reports/jev_shadow_evaluation_v2.md`。`jev-latest` 为可变别名，结果逐项记录
requested/response model 和 UTC timestamp；不要跨版本合并结果。

## Jev Targeted Optimization v3

继续使用 `run_jev_eval.py`。先冻结 dev/test，然后按以下顺序完成真实 Shadow 回放：

```powershell
.\.venv\Scripts\python.exe run_jev_eval.py prepare-v3
.\.venv\Scripts\python.exe run_jev_eval.py router-v3
.\.venv\Scripts\python.exe run_jev_eval.py router-calibrate-v3
.\.venv\Scripts\python.exe run_jev_eval.py memory-diagnostic-v3
.\.venv\Scripts\python.exe run_jev_eval.py memory-retrieval-dev-v3
.\.venv\Scripts\python.exe run_jev_eval.py memory-calibrate-v3
.\.venv\Scripts\python.exe run_jev_eval.py memory-filter-dev-v3
.\.venv\Scripts\python.exe run_jev_eval.py memory-filter-calibrate-v3
.\.venv\Scripts\python.exe run_jev_eval.py context-audit-v3
.\.venv\Scripts\python.exe run_jev_eval.py context-calibrate-v3
.\.venv\Scripts\python.exe run_jev_eval.py memory-retrieval-test-v3
.\.venv\Scripts\python.exe run_jev_eval.py memory-filter-test-v3
.\.venv\Scripts\python.exe run_jev_eval.py memory-filter-decide-v3
.\.venv\Scripts\python.exe run_jev_eval.py memory-boundary-audit-v3
.\.venv\Scripts\python.exe run_jev_eval.py context-test-v3
.\.venv\Scripts\python.exe run_jev_eval.py report-v3
```

Router 用一次请求中的四个 Noul 得到多标签分数；阈值仅在 dev 校准。Memory exact/HNSW
诊断和新旧 Retrieval 用 v2 合成记录临时插入本地 pgvector，结束后清理专用 user/ID；
query/episode 向量仅缓存在 Git 忽略的 `evals/local/`。Context 只复核 v2 的九个误删并重放
已记录的 Jev score，不重复调用 API。最终报告见
`reports/jev_targeted_optimization_v3.md`，包含冻结 test 的质量、P50/P95/P99、模型血缘和
Live 建议。整个流程要求 `JEV_SHADOW_MODE=true`，不会将 Router/Context 切 Live。

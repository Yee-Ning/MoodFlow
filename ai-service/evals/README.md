# MoodFlow RAG Eval Harness V0

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

再在隔离环境显式选择 Judge 模型。Runner 会设置
`DEEPEVAL_DISABLE_DOTENV=1`，避免 DeepEval 自动读取 MoodFlow 的运行时 `.env`；模型凭证
必须通过 Judge 进程环境注入，不写入数据集或 Trace。

```powershell
$env:DEEPEVAL_JUDGE_MODEL="gpt-4.1-mini"
.\.venv-eval\Scripts\python.exe evals\run_deepeval.py `
  --dataset evals\artifacts\baseline-v0\deepeval-cases.silver.jsonl `
  --threshold 0.5 `
  --max-concurrent 4 `
  --output evals\artifacts\baseline-v0\deepeval-report.json
```

V0 Judge 指标为 Faithfulness、Answer Relevancy、Contextual Precision 和 Contextual
Recall。阈值 0.5 只是首轮观测值；在第一份人工审批 Baseline 产生前，不用它阻断发布。

## 分层执行策略

1. PR：运行普通单测、Ruff 和 `validate-dataset`，只阻断合同、引用合法性、排名血缘和
   token 预算等确定性错误；不联网、不调用 Judge。
2. 发布候选：针对同一索引 Release 生成 Trace，比较 Retrieval/Rerank/Context 的
   Recall、MRR、nDCG、fallback、延迟和引用指标。
3. Nightly/人工触发：固定数据集版本、Trace、Judge 模型与阈值运行 DeepEval，并保存
   报告供回归比较。

RAGAS 暂不加入 V0。等至少 30 条样本完成双人复核并得到稳定 Baseline 后，再将相同交换
文件接入 RAGAS 做二次交叉验证；它不替换项目自己的确定性门禁，也不与 DeepEval 同时成为
唯一质量裁决者。

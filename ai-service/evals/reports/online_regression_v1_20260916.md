# Qdrant 在线检索回归（2026-09-16）

## 范围与结果

固定输入为 50 条 `rag_eval_v0.approved.silver.jsonl`，索引 Release 为 `knowledge-v2-8608ffc2fa72e441`。Docker Desktop/Qdrant 已恢复，现有集合 `music_knowledge_v2_8608ffc2fa72e441_v1_v2_v2` 为 green、1270 points；未重建或清空集合。本轮执行真实 Query Understanding → Qdrant Dense/BM25 → Qwen Rerank → WorldLine Context Adapter，**未调用答案生成 LLM 或 DeepEval Judge**。

第一次运行的 45 条 ready 查询全部 `retrieval=unavailable`，警告为 `retrieval_index_validation_failed`。原因是本机 Python HTTP 客户端访问 `localhost:6333` 经代理得到 502；PowerShell 直连 Qdrant 正常。只给采集进程设置 `NO_PROXY=localhost,127.0.0.1` 后重跑成功，未修改系统代理设置。这个发现促使采集 CLI 将 `unavailable` 和 `failed` 同等计入非成功状态并返回退出码 2，避免“全部检索不可用却汇总为 success”的误报。

重跑 Trace：`evals/artifacts/online-regression-v1/traces.no-proxy.jsonl`（本地忽略产物）。50/50 条 Trace 落盘；44 条 ready 查询检索/精排/Context 成功，1 条 `rag-v0-045` 检索为空且上下文为空，5 条需要追问未检索；阶段异常、unavailable 均为 0。1 条检索为空与 Silver 预期的 insufficient evidence 相符。此处的成功是三阶段在线回归成功，**不是包括 Generation 的端到端回答验收**。

## 确定性 Silver 诊断

同一批在线 Trace 的不调用 LLM 的报告：`evals/artifacts/online-regression-v1/deterministic.silver.json`（本地忽略产物）。44 条 Silver 可回答样本：

| 指标 | 结果 |
| --- | ---: |
| Recall@40 / Rerank Recall@12 | 1.000 / 1.000 |
| Retrieval MRR@10 / nDCG@10 | 0.590 / 0.568 |
| Rerank MRR@10 / nDCG@10 | 1.000 / 1.000 |
| Final Context Precision / Recall | 0.705 / 0.924 |
| Supporting Span Coverage | 0.659 |
| 平均最终 Context Token | 2095.3 |
| Retrieval P50 / P95 / P99 | 344 / 414 / 2781 ms |
| Rerank P50 / P95 / P99 | 1600 / 2883 / 5738 ms |
| Context Adapter P50 / P95 / P99 | 19 / 34 / 38 ms |

这些是**机器生成的 Silver 标签**上的链路回归诊断。44/44 条相关 Parent 标签直接来自旧最终上下文 Top3，因此尤其不能把 Rerank MRR/nDCG=1.0 解读为独立精排质量；也不能据此继续调参。无答案生成，本轮引用出现率和 Generation 延迟不适用（不是 0 质量）。上一轮已有 44 条真实生成调用及延迟，见 `context_adapter_v1_evaluation_20260916.md`，但不属于本次在线 Trace。

## 复现与剩余门禁

在 `ai-service` 下运行：

```powershell
$env:NO_PROXY = 'localhost,127.0.0.1'
.\.venv\Scripts\python.exe run_rag_trace_collection.py `
  --dataset evals/datasets/rag_eval_v0.approved.silver.jsonl `
  --index-manifest data/indexing/v1/knowledge-v2-8608ffc2fa72e441/index_build_manifest.json `
  --output evals/artifacts/online-regression-v1/traces.no-proxy.jsonl `
  --run-id rag-online-regression-v1-no-proxy-20260916 --no-llm --rerank
.\.venv\Scripts\python.exe run_rag_eval.py evaluate `
  --dataset evals/datasets/rag_eval_v0.approved.silver.jsonl `
  --traces evals/artifacts/online-regression-v1/traces.no-proxy.jsonl `
  --output evals/artifacts/online-regression-v1/deterministic.silver.json
```

唯一尚未完成的质量门禁是 30 条独立 Gold 人工复核；复核数据和方法见 `gold_review_v0_preparation_20260916.md`。目前 30/30 条仍为 pending。拿到 Gold 后，先用同一在线 Trace 子集做确定性评测。若需验证答案语义质量，才从困难样本中选最多 8 条做带预算的 Judge 冒烟；当前不执行 44 条全量 Judge。

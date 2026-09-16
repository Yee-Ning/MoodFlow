# Gold V0 复核准备与在线回归状态（2026-09-16）

## 结论

固定 50 条 Silver 样本中已按查询类型/预期结果抽取 30 条盲审候选（26 可回答、3 追问、1 证据不足）。**30 条目前全部 `pending`，Gold 已通过数为 0；不得据此发布 Gold 指标或宣称最终验收。**

抽样使用了 Silver 的预期结果和首要查询类型来做分层，因而分层本身也需复核；盲审材料不展示旧 Parent 排名、答案或证据标签。`expected_outcome` 因 V0 数据合同必填，在 JSONL 中保留为待核对的 Silver 种子值，不应被视为已审核结论。

## Silver 质量审计

审计输入：`rag_eval_v0.approved.silver.jsonl`、`baseline-v0/traces.jsonl`、冻结索引投影 `knowledge-v2-8608ffc2fa72e441/index_projection.jsonl`。可机器复现的明细见 `gold_review_v0_preparation_audit.json`。

| 检查 | 结果 | 可说明 / 不可说明 |
| --- | ---: | --- |
| Silver 样本 | 50，其中可回答 44 | 数据量与结果分布 |
| 相关 Parent 等于旧最终上下文 Top3 | 44/44 | 标签来自旧链路，有明显评测泄漏；不能证明独立召回质量 |
| 支持片段在匹配 Parent 的索引原文中且来源 ID 一致 | 132/132 | 证明引用对象存在；不能证明片段与问题相关 |
| `required_facts` 直接等于所选片段列表 | 36/44 | 事实标签明显由原片段复制，不是独立的必要事实判断 |
| `reference_answer` 直接等于片段拼接 | 36/44 | 答案标签明显是机械拼接，不是独立参考回答 |

例如 `rag-v0-001` 问“介绍一下周杰伦的音乐生涯”，旧 Top3 中一条支持片段讨论的是父母离异及《爸，我回来了》的误解；该片段是否属于回答问题所需证据，必须由独立审核员判定。上表仅是来源/血缘检查，不是语义相关性评判。

## 盲审产物与复现

- `../datasets/rag_eval_gold_v0.pending.jsonl`：30 条待复核数据，旧证据/答案标签已清空。
- `gold_review_v0_blind_pack.md`：仅给问题和核对项目，不展示旧排序或答案。
- `gold_review_v0_preparation_audit.json`：可机器读取的 Silver 质量审计。

在 `ai-service` 目录可重复运行：

```powershell
.\.venv\Scripts\python.exe run_rag_gold_review_prep.py `
  --silver evals/datasets/rag_eval_v0.approved.silver.jsonl `
  --pending evals/datasets/rag_eval_gold_v0.pending.jsonl `
  --review-pack evals/reports/gold_review_v0_blind_pack.md `
  --baseline-traces evals/artifacts/baseline-v0/traces.jsonl `
  --index-projection data/indexing/v1/knowledge-v2-8608ffc2fa72e441/index_projection.jsonl `
  --audit-output evals/reports/gold_review_v0_preparation_audit.json
```

人工审核应从冻结原始语料/索引中独立找证据，核对查询是否属于该语料范围，逐条填写 `relevant_parent_ids`、`supporting_spans`、`required_facts`、`reference_answer`、`expected_source_ids`，确认结果类型后才将 `annotation_status` 改为 `approved`。`notes` 可选。不要用现有 Silver 的 Top3 直接回填。建议至少由一名熟悉语料的审核员逐条签署；有争议的样本记录裁决依据。完成前继续使用 Silver 仅作工程冒烟。

## 在线回归与后续验收

本轮尝试非破坏性启动 Docker Desktop，但 Docker Engine 未出现，Qdrant `127.0.0.1:6333` 不通。Docker backend 日志显示启动 Ingest 服务时无法将 `sailor-ingest.sock` 重命名为 `.stale`，随后 backend 崩溃。因此**没有运行真实在线 Top40→Top12→Context→Generation 回归**。没有重置 Docker、删除 socket/持久化卷或重建索引。环境恢复后先确认现有 Qdrant volume 和索引 release，再对同一固定样本采集在线 Trace；不得把索引投影回放说成在线回归。

Gold 经独立审核后，先用同一索引版本/同一固定 Trace 子集运行不调用 LLM 的 Recall@K、MRR、nDCG、最终上下文精度、支持片段覆盖、引用合法性和 P50/P95/P99。仅当答案语义质量确需复核时，另选最多 8 条困难样本做 DeepSeek Judge 冒烟并设置预算；不运行 44 条全量语义 Judge。

当前剩余两项验收前置条件：**30 条独立 Gold 审核**、**Docker/Qdrant 恢复后的真实在线回归**。在此之前参数调优结论仅可作为 Silver 基线，不再扩大扫描。

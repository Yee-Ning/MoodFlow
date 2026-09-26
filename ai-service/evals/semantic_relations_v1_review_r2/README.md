# Semantic Relations v1 — human review revision r2

本轮仅登记第二轮人工审核：`human-review-WT`，`2026-09-26`。4 条新 weak-evidence negative 均获 APPROVE，保持 negative 标签，晋升正式 Gold。**未运行最终 Evaluation，未生成新指标，也未修复关系算法或部署。**

| 正式 Gold | Era | Scene | Influence | 合计 |
|---|---:|---:|---:|---:|
| 全部 approved real | 4 | 4 | 4 | 12 |
| 其中 weak negative | 1 | 1 | 2 | 4 |

正式 Gold 共 4 条 positive、8 条 negative（4 isolation + 4 weak）。本批 pending 已清零；28 条 synthetic 仍单独作契约回归，不能计入真实 baseline。

## 审核、血缘和数据文件

- `review_decisions.json`：本轮用户逐条提供的 4 条 APPROVE 及原理由，日期保留日精度。
- `receipts/`：新增 4 份批准 receipt，绑定 r1 原 label payload（排除 review）、Gold 行、input 行、父 manifest 的 SHA256；原 12 份 receipt 原样保留。
- `approved_gold.jsonl`：当前正式 Gold，12 条。各条 claim、expected relation、query、exact span、实体和 rationale 均与 r1 一致，仅本轮 4 条的 review metadata 改变。
- `gold.jsonl`：统一 harness 的 28 synthetic + 12 approved real；不含旧版被拒的 4 条 weak 样本。
- `inputs.jsonl`、`sources.jsonl`、`schema.json`：与 r1 字节完全相同，无运行时索引或生产数据写入。
- `excluded_cases.jsonl`：原 4 条 REJECT 的排除台账，字节未改动；这些记录仍不得进入正式 Gold。
- `pending_weak_evidence.jsonl`：空 JSONL 视图，表示本批没有待审样本；不是删除或覆盖父版本的 pending 数据。
- `review_history/`：r1 决策、汇总、待复审包的原样归档。其 pending 表述是历史状态，当前状态以本目录 `review_summary.json` 为准。
- `manifest.json`：固定所有核心数据、审核记录、派生视图、归档和 receipt 哈希，绑定 r1 freeze。

父版本 r1、原始 v1 与原评测报告均保留。此前 1 条 Scene evidence mismatch、2 条 Influence direction error 对应的 Gold 和 replay 输入保持不变；本轮没有重新评分或声称失败已解决。默认评测入口未切换到 r2。

本次批准的 negative 只表示给定 evidence 不支持明确所询的 predicate/方向，不否认现实中其他关系存在。例如 Flying 的发行时间仍可支持年代归属，但本 query 询问的是时代代表性。候选注释继续用于冻结文档下的安全性评测，不代表真实来源已经证实关系，也不能用于声称线上 retrieval/claim extraction 质量。

## 验证与后续状态

```powershell
cd D:\code\ProjectsAll\MoodFlow\ai-service
$env:OPENBLAS_NUM_THREADS='1'
.\.venv\Scripts\python.exe -m pytest evals/tests/test_semantic_relations_review.py evals/tests/test_semantic_relations_review_r2.py -q
```

此命令只检查审核、receipt、freeze、原标签/输入/失败血缘；不执行统一 Evaluation。新的指标 baseline 尚未计算，原有暂不运行最终 Evaluation 的指令继续有效。

这 4 条的人工复审已完成。后续评测可以使用 r2 的 approved real cohort，但真实 negation、conflicting evidence、反向 Influence Gold 的审核覆盖仍未补齐；checkpoint 检查及最终回归也仍需按原任务范围执行。尚不能据此宣称完整 baseline 或 Final Worldline Regression 已通过。

# Semantic Relations Evaluation v1 — r4 人工审核登记

Reviewer：`human-review-WT`；reviewed_at：`2026-09-26`。用户对 r3 的 10 条候选全部给出明确 APPROVE，已在独立 `evals/semantic_relations_v1_review_r4/` 登记。没有修改 r3 或任何旧冻结版本，没有运行最终 Evaluation。

## 登记结果与覆盖

| relation_type | 本批 APPROVE | 正式人工 Gold | 正例 | 负例 | weak negative | negation negative | conflict | direction_sensitive |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Era | 2 | 6 | 2 | 4 | 1 | 1 | 1 | 0 |
| Scene | 2 | 6 | 2 | 4 | 1 | 1 | 1 | 0 |
| Influence | 6 | 10 | 5 | 5 | 2 | 1 | 1 | 6 |
| Overall | 10 | 22 | 9 | 13 | 4 | 3 | 3 | 6 |

各类别可重叠，不应把类别列相加作为独立样本数。direction_sensitive 的 6 条中有 2 条新 reverse negative，另有 2 条配套正向控制与 2 条既有正向样本；配对共用 evidence，与旧控制样本有相关性。整个 suite 为 50 cases / 50 inputs / 48 sources：22 条 approved real、28 条 synthetic；pending 为 0。原 4 条 REJECT 继续排除，不恢复到正式 Gold。

## Review receipt 与冻结保护

本批 10 份 receipt 使用既有 `HumanReviewBatch`、`SemanticGoldCase`、`label_hash` 与 `load_suite` 契约。每份绑定用户提供的 reviewer、日期、完整 reason、原 label SHA256（排除 review）、r3 parent manifest SHA256、r3 case 原始行 SHA256 和 replay input 原始行 SHA256。没有推断审批时间或修改审核理由。

当前 suite 的 10 条 review 元数据从 pending 更新为 approved，label/query/entities/expected relations/categories/evidence/offsets/rationale 均保持原值。既有 40 行 Gold 保留原字节；inputs、sources、schema、excluded cases、source audit 和 research exclusions 与 r3 逐字一致。全部旧 16 份 receipt 保留，加上本批共 26 份；r3 pending 审核包、checklist、视图、摘要和保护清单逐字存入 `review_history/r3_*`，r1/r2 历史同样保留。

`approved_gold.jsonl` 包含正式 22 条；`pending_cases.jsonl` 为空。`review_decisions.json` 保存用户结论，`new_review_receipts.jsonl` 提供本批机器可读 receipts，`human_review_registration.md` 提供可读登记。`review_summary.json` 输出 overall 与 relation_type 的审核覆盖统计，不是关系质量分数。source_audit 中 pending 是原采集时状态，作为历史记录保留；当前 case 审核状态由 Gold review 与 receipt 决定，也不自动批准该来源上的其它 query。

`protected_artifacts.json` 锁定此前 119 个数据/审核/诊断文件，旧 failure 报告未改。旧 v1 的 7 份历史报告在 Windows checkout 为 CRLF、Git blob 为 LF，保留两种已核对的字节 hash；仅允许这 7 份的既有换行差异，Gold/input/source 和其它冻结文件仍要求精确字节一致。r4 manifest 绑定父版本、核心数据及全部审核材料。

## 冲突与方向语义继续保留

Giant Steps 的 APPROVE 确认两个互斥年代候选的冲突表示：两条均关联双方 evidence，要求 `temporal_conflict=true`、空年份和 `temporal_precision=unknown`，不把两个年代当作两次确定首发。Blur 与 Cluster 保留双方归因与 `attribution_conflict=true`；审核通过并不裁定争议事实，也不证明算法已能处理这些关系。

三条 negation 的原作/首发/早期场景范围、Paik → Migung 的规范方向及 passive claim 均未扩大或改写。两个 reverse negative 仍只拒绝给定 evidence 不支持的反方向，不断言现实中不存在反向影响；正反 query 的 replay documents 完全相同，未翻转真实 claim 来帮助测试通过。

## 验证

命令在 `ai-service` 下运行，pytest 设置 `OPENBLAS_NUM_THREADS=1`。

```powershell
.\.venv\Scripts\python.exe -m pytest evals/tests/test_semantic_relations_review.py evals/tests/test_semantic_relations_review_r2.py evals/tests/test_semantic_relations_review_r3.py evals/tests/test_semantic_relations_review_r4.py -q
# 49 passed in 9.20s
.\.venv\Scripts\python.exe -m pytest tests/test_worldline_era_expansion.py tests/test_worldline_scene_expansion.py tests/test_worldline_influence_expansion.py tests/test_worldline_session.py -q
# 74 passed in 10.67s
.\.venv\Scripts\python.exe -m ruff check evals/tests/test_semantic_relations_review_r4.py
# All checks passed
.\.venv\Scripts\python.exe -m compileall -q evals/tests/test_semantic_relations_review_r4.py
# exit 0
```

共 123 项测试通过，新增 11 项登记/完整性检查覆盖：22 条 approved cohort 与空 pending、仅 review 变化、10 个 receipt 的完整 lineage、旧历史与排除记录保护、真实 negation/conflict/方向语义保留、119 个旧文件保护、完整 manifest、禁止加载评分模块，以及刷新 hash 后仍拒绝改写 label、伪造 reviewer、缺失 receipt。

没有执行 `test_semantic_relations_evaluation.py` 或最终评测入口；用户此前暂停 Evaluation 的约束仍有效。没有算法、生产索引、默认 runner 或前端改动；无关完整项目集成与前端构建不在本次数据登记的验证范围内。环境未阻止上述验证。相关 session 功能回归通过不能替代完整 Evaluation 中的 checkpoint/persistence 语义连续性指标。

## 当前结论

1. **本轮没有新的指标 baseline。** 只更新人工审核覆盖；正式数据审核完成，关系质量仍须由统一 harness 实测。
2. **没有审核登记或测试 blocker；pending 为 0。** 22 条真实样本数量有限且方向配对有相关性，不能据此宣称总体质量或统计置信度。
3. **可以进入统一 baseline Evaluation 与 failure diagnosis 的准备阶段。** 最终评测继续暂缓。后续获指令运行时应显式使用 r4 数据路径和独立输出目录，避免默认原始 pending 版本被误当成当前 Gold。
4. **进入真实失败修复及 Final Worldline Regression 前，仍须完成：** Era + Scene + Influence 统一实测、overall 与 relation_type 指标/分母/trace、unsupported/negation/conflict/反向方向检查和 checkpoint/persistence 连续性评测。保留旧失败、新增真实失败后再按影响排序；本轮不以批准结论或源码推测冒充已测 failure categories。

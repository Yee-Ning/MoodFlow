# Semantic Relations v1 — r6 人工审核登记

Reviewer: `human-review-WT`；reviewed_at: `2026-09-26`。用户明确 APPROVE 的 r5 三条 Influence query-family confusable negative 在本独立后继版本晋升为正式人工 Gold。正式真实 Gold 共 25 条（Era 6、Scene 6、Influence 13），正例 9、负例 16；pending 为 0。原 4 条 REJECT 仍排除，28 条 synthetic 仍保留独立 cohort。

- [human_review_registration.md](human_review_registration.md) 逐项记录原始理由、query、实体对、candidate predicate 与 hashes；[new_review_receipts.jsonl](new_review_receipts.jsonl) 提供三份 receipt 的机器可读汇总。
- `review_decisions.json` 复用既有 HumanReviewBatch schema，保留明确 reviewer、日期、理由和 `explicit_user_review` provenance。没有自动批准其它候选或其它 query。
- `receipts/` 共 29 份：原 26 份逐字节保留，新 3 份绑定 r5 parent manifest、parent case line、label payload 和 replay input line 的 SHA256。
- `gold.jsonl` 总计 53 条，只有 r5 三条的 review 元数据改变；其余 50 行的原字节保留。`approved_gold.jsonl` 原 22 条为完整前缀，追加这三条。所有 query、实体、predicate、标签、expected relation、category、conflict flag、rationale、source 与 span 保持原值。
- `inputs.jsonl`（53 条）、`sources.jsonl`（52 个）、schema、source audit 与排除视图均与 r5 逐字节一致。`source_audit.json` 的 pending 是采集当时的核验状态，当前 case 审核以 Gold review 和 receipt 为准；批准某 query 的标签不等于批准来源中的所有关系。
- `review_history/r5_*` 保留 r5 pending 审核包、checklist、joined review items、coverage projection、manifest、scope lock 和摘要等；之前的 r1–r4 历史全部保留。r4、r5 本体及所有旧报告未改。

三条 negative 仅表示冻结 evidence 不支持所询 Influence 方向与 predicate，不表示现实世界不存在该影响。Scene 关联、竞争语境和同发行年代不会被改写成 source 已证实的 Influence。候选 input 中沿用的 Influence 假设仍是拒绝能力探针，不是生产抽取事实；source audit 解释、review reason 与 Gold label 不进入 runtime input。

[coverage_inventory.json](coverage_inventory.json) 仅静态读取未改动 evaluator 的类别要求并清点 approved Gold：Influence confusable 为 3，Era / Scene / Influence 的必需 categories 均已覆盖，coverage blocker 已补齐。没有运行新 baseline，因此没有新 `baseline_ready` / `can_enter_targeted_repairs` machine 结果；最新正式结果仍是冻结 r4 的 false / false。人工批准不会清除 r4 的质量失败。

`manifest.json` 绑定父 r5 manifest、核心数据 hashes 和 r4 baseline artifact manifest；其 review_files 覆盖 receipt、审核结论、覆盖清点与全部历史材料。`protected_artifacts.json` 保护之前 259 个数据/报告文件，`scope_lock.json` 保护 28 个运行时、validator/projector 所在模块与评测规则文件。原 7 个历史 v1 报告的 CRLF / Git LF 差异继续明确记录；本轮未改变旧文件。

本轮 `evaluation_run=false`。没有运行新 baseline、targeted repairs、Final Regression、Online E2E 或部署，没有修改算法、validator、projector、评分、gate、生产数据或运行时索引。真实来源 excerpt / offsets 保留；完整 HTTP 页面未归档的限制与 r5 相同。验证命令和结果见 [review_registration_report.md](../reports/semantic_relations_v1_review_r6/review_registration_report.md) 与 [review_registration_validation.json](../reports/semantic_relations_v1_review_r6/review_registration_validation.json)。

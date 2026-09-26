# Semantic Relations v1 review r4 — 人工审核登记

Reviewer: human-review-WT；reviewed_at: 2026-09-26。r3 的 10 条 pending 均获得用户明确 APPROVE，本版本仅登记 review 元数据与新的 review receipt。正式人工 Gold 为 22 条：Era 6、Scene 6、Influence 10；正例 9、负例 13。pending_cases.jsonl 为空；原 4 条 REJECT 继续排除，28 条 synthetic 仍为独立回归 cohort。

- approved_gold.jsonl：22 条正式人工审核 Gold。
- review_decisions.json / review_decisions.schema.json：完整人工结论，复用既有 HumanReviewBatch schema。
- receipts/：26 份历史/新 receipt；其中新增 10 份绑定 parent manifest、parent case line、replay input line 和 label SHA256。
- new_review_receipts.jsonl / human_review_registration.md：本批机器可读及可读登记摘要。
- review_summary.json：只统计数据/审核覆盖，overall 与 relation_type breakdown；不是关系质量分数。
- review_history/r3_*：原 pending 审核包、checklist、视图和摘要等逐字存档，原 r1/r2 历史继续保留。
- source_audit.json：原 r3 的采集核验日志，pending 表示采集当时状态；当前 case 审核状态以 Gold review 和 receipts 为准，不修改来源历史或自动批准该来源的其它 query。
- protected_artifacts.json / manifest.json：保护旧版本与旧报告，绑定父 freeze 与所有审核文件。

Gold 标签（排除 review）及所有 input/source/schema 均保持 r3 原值。既有 40 行 Gold 原字节保留；r3 最后 10 行只更新 review，source/target 与 supporting evidence 不变。没有新增或删除 query，没有改变 conflict flag、方向或负例 scope，也没有为消除 failure 改动任何冻结标签。

本次 APPROVE 的三个 conflict 保留原语义：Giant Steps 的两个互斥年代候选都要求双方 evidence、temporal_conflict=true、年份=null 和 unknown 精度；Blur / Cluster 保留双方归因与 attribution_conflict=true。审核通过不等于争议事实定案，也不等于当前算法能正确处理。Influence 反向负例只表示所给证据不支持反向关系；配对及旧控制样本有相关性，不能当作独立随机样本。

本轮 evaluation_run=false。没有运行 final Evaluation、生成新性能 baseline、修改算法、默认 runner 或生产数据；旧 failure 报告完整保留。审核门禁已完成，下一阶段可按用户指令对这个明确的 r4 数据路径做统一 baseline 和 failure diagnosis；当前暂停评测的约束仍有效。

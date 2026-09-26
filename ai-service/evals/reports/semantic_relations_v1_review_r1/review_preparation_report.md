# Semantic Relations v1 — 审核登记与弱证据复审准备

审核人：`human-review-WT`；审核日期：`2026-09-26`。本轮是 review preparation，**没有运行最终 Evaluation，也没有计算新的指标 baseline**。

8 条 APPROVE 已晋升正式 Gold；4 条 REJECT 已记录 reject receipt 并排除；重新采集的 4 条 weak negative 全部保持 pending。新目录 `evals/semantic_relations_v1_review_r1/` 与原冻结版本分离，兼容现有统一 harness/schema。原运行时、生产索引、默认评测版本与部署没有改动。

| cohort | Era | Scene | Influence | 合计 |
|---|---:|---:|---:|---:|
| approved real Gold | 3 | 3 | 2 | 8 |
| pending real weak proposals | 1 | 1 | 2 | 4 |

case ID 前缀不是 relation_type 分组依据。4 条被拒的旧 weak 案例只在排除台账及父版本中保留，不进入新版 Gold 或 replay 输入。28 条 synthetic 继续作为单独回归 cohort。

12 份 receipt 保存全部原审核理由与日期，绑定原 label payload、Gold 行、input 行及父 manifest 的 SHA256。批准只更改 review metadata；原 query、expected relation、exact span、source metadata 和保留输入的行字节未改动。原版数据及 `reports/semantic_relations_v1/` 全部文件未修改，其字节哈希记录于 `review_preparation_validation.json`。

原诊断中的 Scene evidence mismatch 和两条 Influence direction error 所对应的正例标签及输入均保留。没有修正算法、用 Gold 消除失败、重算旧结果或把旧 pending 诊断分数冒充正式 baseline。

新 weak 提案来自 4 个已打开核对的官方页面：Flying 发行记录不足以证明时代代表性；Dylan 单场演出地点不足以证明指定 Scene 成员归属；Bowie/Eno 共同作曲署名不足以证明 Bowie → Eno 影响；Dylan 客串 Baez 演出不足以证明 Baez → Dylan 创作启发。详见新数据目录的 `human_review_pack.md`，包含完整 URL、publisher、locator、原文 span/offset、候选 predicate、端点和逐条审核模板。

这些 proposed negative 是证据相对判断，需要人工复审页面上下文。新输入中的不充分关系候选是评测安全性探针，不是已由真实来源证实的 claim；未写入生产数据。正式 Gold 仅为 `approved_gold.jsonl` 的 8 条，pending 提案没有审核人或审批 receipt。

验证通过：审核/血缘/篡改防护测试 15 项；既有 Era、Scene、Influence、session 测试 74 项；Ruff、compileall 与原冻结目录/报告 Git diff 检查通过。命令和结果见 `review_preparation_validation.json`。未运行新的 relation Evaluation、Final Worldline Regression、线上检索或跨进程生产持久化验收。

下一步是人工复审新 4 条候选。新的审核结论应创建下一数据版本；在本轮冻结数据上不改标签。真实 negation、conflicting evidence、反方向案例的人工审核覆盖，以及已暴露的 Scene span 绑定和 Influence 方向识别失败，仍须在 Final Worldline Regression 前处理。本轮没有开展这些修复。

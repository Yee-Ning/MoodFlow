# Semantic Relations Evaluation v1

Run: 2026-09-26T06:41:29.208734+00:00

## 审计与边界

复用 V2.4A/B/C schema、SearchPlan、MusicKnowledgeDocument、extractor/validator/projector、WorldLineSessionResult 和 application checkpoint saver；运行时模块未修改。
旧 worldline_eval_runner / Gold v1/v1.1 主要覆盖 artist→genre、identity、merge，其 expected→fixture adapter 不适合三类关系质量评测。本 harness 的输入文件与标签独立。
生产路径是 Query Understanding → hybrid retrieval/rerank → ContextPack → build_graph (Era→Scene→Influence) → session。当前离线回放从冻结 ContextPack 文档边界开始；既有身份映射和 supported_claims 是人工/评测注释，不是生产检索或 claim extraction 的测量。

## 数据与可复现性

Gold/input/source/schema freeze hashes: `{"gold.jsonl": "69bdcf6471349fdc74c81426775fd220dcffe3f82278ae1aa99fccfc0da88c07", "inputs.jsonl": "43948da8e7259f48777bc92f80cbe96e30e47454be5cde51631ab1108dff7c6b", "schema.json": "019033ce34e5ad97bb6c2f1703dd97f4b300ba871bda46d8ad23b0dfa66fb193", "sources.jsonl": "85a7e20d60fb03ef78a43197bc0060e1dd6537bf401db6581c2de0d0ed7af31e"}`
Code revision: `c25e0c84f386b683637748db179081e10d966abb`; dirty at run: `True`.
正式 Gold 只接纳有匹配标签哈希的人工审批 receipt；待审核真实样本和 synthetic 回归分开计分。检索来源快照保存短原文、URL、publisher、采集时间、locator、SHA256；span offsets 为快照 Unicode 字符偏移。从原文提取的片段不是网页全文，定位需同时查看 URL/locator。评测不读取或写入生产索引。

## 指标 baseline

以下每个单元格为 numerator/denominator（百分比）；分母为 0 时为 N/A，不能当作 100%。

### approved_real_baseline

| Metric | overall | era | scene | influence |
|---|---|---|---|---|
| relation_precision | 1/1 (100.0%) | 1/1 (100.0%) | N/A (0/0) | N/A (0/0) |
| evidence_coverage | 1/12 (8.3%) | 1/3 (33.3%) | 0/3 (0.0%) | 0/6 (0.0%) |
| evidence_traceability | 1/1 (100.0%) | 1/1 (100.0%) | N/A (0/0) | N/A (0/0) |
| unsupported_hallucination_rate | 0/1 (0.0%) | 0/1 (0.0%) | N/A (0/0) | N/A (0/0) |
| influence_direction_accuracy | 0/5 (0.0%) | N/A (0/0) | N/A (0/0) | 0/5 (0.0%) |
| weak_evidence_rejection | 4/4 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 2/2 (100.0%) |
| negation_rejection | 3/3 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| conflict_handling | 0/3 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| query_isolation | 22/22 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 10/10 (100.0%) |
| checkpoint_continuity | 22/22 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 10/10 (100.0%) |
Cases: 22; exact case passes: 14.

### pending_real_diagnostic

| Metric | overall | era | scene | influence |
|---|---|---|---|---|
| relation_precision | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| evidence_coverage | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| evidence_traceability | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| unsupported_hallucination_rate | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| influence_direction_accuracy | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| weak_evidence_rejection | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| negation_rejection | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| conflict_handling | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| query_isolation | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| checkpoint_continuity | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
Cases: 0; exact case passes: 0.

### synthetic_regression

| Metric | overall | era | scene | influence |
|---|---|---|---|---|
| relation_precision | 10/12 (83.3%) | 3/4 (75.0%) | 3/4 (75.0%) | 4/4 (100.0%) |
| evidence_coverage | 13/19 (68.4%) | 4/6 (66.7%) | 4/6 (66.7%) | 5/7 (71.4%) |
| evidence_traceability | 15/15 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) |
| unsupported_hallucination_rate | 2/12 (16.7%) | 1/4 (25.0%) | 1/4 (25.0%) | 0/4 (0.0%) |
| influence_direction_accuracy | 4/6 (66.7%) | N/A (0/0) | N/A (0/0) | 4/6 (66.7%) |
| weak_evidence_rejection | 1/3 (33.3%) | 0/1 (0.0%) | 0/1 (0.0%) | 1/1 (100.0%) |
| negation_rejection | 3/3 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| conflict_handling | 3/3 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| query_isolation | 28/28 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 10/10 (100.0%) |
| checkpoint_continuity | 28/28 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 10/10 (100.0%) |
Cases: 28; exact case passes: 20.

指标定义见 README：precision 以输出关系计；coverage 以 Gold 正例所需 claim 计；traceability 以输出证据计；unsupported 为未被 Gold+证据支持的输出关系；direction 分母包含 Influence 正例的 abstention。weak/negation 仅计负例；conflict 要求所有断言、标记和不确定字段；isolation 检查全部三个模块及 graph-only 边。

## Failure cases 与下一轮优先级

完整链路在 results.jsonl；所有失败在 failures.jsonl。pending 的失败是诊断候选，未获人工确认前不能称为真实 Gold failure。synthetic 不计入真实失败优先级。

| Priority | Category | Real reviewed failures | Pending real diagnostic failures | Suggested fix point |
|---|---|---|---|---|
| 1 | conflict failure | 3 | 0 | 确认反证保留、conflict metadata 与汇总不确定字段 |
| 2 | direction error | 5 | 0 | 方向与实体别名/代词解析；保持保守拒绝 |
| 3 | evidence mismatch | 3 | 0 | exact span、文档与 claim 对齐及可接受表达 |

### 待审核真实漏检明细

| Case | Query | Failure category | Runtime rejection |
|---|---|---|---|

这三类表达诊断包括具名 Scene 的地名/代词指代、机构 Influence 列表、采访第一人称创作归因。direction_missing 表示没有识别出方向，当前没有生成反向关系；需人工复核后才能确认修复范围。

## 验证及未验证限制

验证命令/结果见 validation.json（交付时记录，普通 harness 运行不会自动执行完整测试）。当前 checkpoint 指标仅代表 application saver 序列化、重新构图读取及相同输入查询回放；并非完整 WorldLineSessionService 多轮执行或 PostgreSQL 进程重启。后者依赖已有 session/graph 测试和 PostgreSQL restart 测试，线上 Qdrant、真实 Music API/LLM 不在此次离线分母。
真实样本规模和来源多样性有限，且同源 case 相关，不具备生产统计代表性。真实 negation/conflict 等缺口按 coverage_inventory.json 明示，不能用 synthetic 填平。

## 进入下一轮判断

1. 各指标 baseline：见上表；正式人工审核 cohort 有 22 条。
2. Blocker：influence: approved real coverage missing confusable；reviewed real failures require diagnosis before Final Worldline Regression。
3. 只有通过人工审核且覆盖完成后，才可进入“针对真实失败点修复”；当前可进行审核与诊断复核，不能将 pending 结果作为已验收 baseline。
4. Final Worldline Regression 前必须解决：人工审核与真实场景覆盖缺口、已确认的 unsupported/direction/isolation/conflict 错误、真实 retrieval/ContextPack 的覆盖验证、生产 persistence/restart 连续性。不得以当前离线通过率替代这些验收。

# Semantic Relations Evaluation v1

Run: 2026-09-26T03:05:38.267386+00:00

## 审计与边界

复用 V2.4A/B/C schema、SearchPlan、MusicKnowledgeDocument、extractor/validator/projector、WorldLineSessionResult 和 application checkpoint saver；运行时模块未修改。
旧 worldline_eval_runner / Gold v1/v1.1 主要覆盖 artist→genre、identity、merge，其 expected→fixture adapter 不适合三类关系质量评测。本 harness 的输入文件与标签独立。
生产路径是 Query Understanding → hybrid retrieval/rerank → ContextPack → build_graph (Era→Scene→Influence) → session。当前离线回放从冻结 ContextPack 文档边界开始；既有身份映射和 supported_claims 是人工/评测注释，不是生产检索或 claim extraction 的测量。

## 数据与可复现性

Gold/input/source/schema freeze hashes: `{"gold.jsonl": "e0ecf98e1048d6dd81dacc217302e822bbf2b536e3ed458f3f057b13f918a1ba", "inputs.jsonl": "9a40e8384ed049bed500ce4428e7aab706e011660c83ebe2a11ebf4ebcc48477", "schema.json": "019033ce34e5ad97bb6c2f1703dd97f4b300ba871bda46d8ad23b0dfa66fb193", "sources.jsonl": "3edfae785cb01f355c90a0599d4e64cb0f227f310d44e0d98ef4abbed8ee5c6e"}`
Code revision: `f4b436f57f38daf01e265c56aedf75fd120beb36`; dirty at run: `True`.
正式 Gold 只接纳有匹配标签哈希的人工审批 receipt；待审核真实样本和 synthetic 回归分开计分。检索来源快照保存短原文、URL、publisher、采集时间、locator、SHA256；span offsets 为快照 Unicode 字符偏移。从原文提取的片段不是网页全文，定位需同时查看 URL/locator。评测不读取或写入生产索引。

## 指标 baseline

以下每个单元格为 numerator/denominator（百分比）；分母为 0 时为 N/A，不能当作 100%。

### approved_real_baseline

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

### pending_real_diagnostic

| Metric | overall | era | scene | influence |
|---|---|---|---|---|
| relation_precision | 1/1 (100.0%) | 1/1 (100.0%) | N/A (0/0) | N/A (0/0) |
| evidence_coverage | 1/4 (25.0%) | 1/1 (100.0%) | 0/1 (0.0%) | 0/2 (0.0%) |
| evidence_traceability | 1/1 (100.0%) | 1/1 (100.0%) | N/A (0/0) | N/A (0/0) |
| unsupported_hallucination_rate | 0/1 (0.0%) | 0/1 (0.0%) | N/A (0/0) | N/A (0/0) |
| influence_direction_accuracy | 0/2 (0.0%) | N/A (0/0) | N/A (0/0) | 0/2 (0.0%) |
| weak_evidence_rejection | 4/4 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 2/2 (100.0%) |
| negation_rejection | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| conflict_handling | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| query_isolation | 12/12 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) |
| checkpoint_continuity | 12/12 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) | 4/4 (100.0%) |
Cases: 12; exact case passes: 9.

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
| 1 | direction error | 0 | 2 | 方向与实体别名/代词解析；保持保守拒绝 |
| 2 | evidence mismatch | 0 | 1 | exact span、文档与 claim 对齐及可接受表达 |

### 待审核真实漏检明细

| Case | Query | Failure category | Runtime rejection |
|---|---|---|---|
| real-scene-1-positive | Neko Case 的音乐场景归属 | evidence mismatch | scene_not_in_evidence_span |
| real-influence-2-positive | The Pixies 对 Nirvana 的音乐影响 | direction error | direction_missing |
| real-influence-3-positive | Pixies 对 Kurt Cobain 的创作启发 | direction error | direction_missing |

这三类表达诊断包括具名 Scene 的地名/代词指代、机构 Influence 列表、采访第一人称创作归因。direction_missing 表示没有识别出方向，当前没有生成反向关系；需人工复核后才能确认修复范围。

## 验证及未验证限制

| Command | Result |
|---|---|
| `.venv python -m pytest tests -q` | 1024 passed / 14 skipped / 206 subtests passed |
| `.venv python -m pytest <all non-Judge evals tests> -q` | 61 passed (30 new semantic evaluator tests) |
| `.venv-eval python -m pytest evals/tests/test_judges.py -q` | 4 passed |
| `ruff check` new evaluator/fixtures/tests/entrypoint | passed |
| `compileall -q` same scope | passed |
| `run_semantic_relations_eval.py` | 40 cases; exit 2: real human-Gold coverage blocker |
| `pytest tests/test_worldline_postgres_restart.py -q -rs` | 3 skipped: opt-in not enabled |

最初的主环境合并 tests+evals 命令在 Judge 收集时缺少 DeepEval；按项目独立环境约定拆分后，全部主后端测试、非 Judge eval 测试与 4 项 Judge 测试通过，没有删除或弱化测试。完整命令和被测代码 SHA256 见 validation.json。

本轮没有前端代码变化，因此未重复前端构建或视觉验收。运行时模块和生产索引未改动。真实 source 的采集时间仅记录日期；manifest 记录了时间精度元数据更正，Gold 标签、input 与 source 原文的解析内容均未改变；冻结序列化已固定为 LF，并以 Git 属性保证检出一致性。

验证命令/结果见 validation.json（交付时记录，普通 harness 运行不会自动执行完整测试）。当前 checkpoint 指标仅代表 application saver 序列化、重新构图读取及相同输入查询回放；并非完整 WorldLineSessionService 多轮执行或 PostgreSQL 进程重启。后者依赖已有 session/graph 测试和 PostgreSQL restart 测试，线上 Qdrant、真实 Music API/LLM 不在此次离线分母。
真实样本规模和来源多样性有限，且同源 case 相关，不具备生产统计代表性。真实 negation/conflict 等缺口按 coverage_inventory.json 明示，不能用 synthetic 填平。

## 进入下一轮判断

1. 各指标 baseline：见上表；正式人工审核 cohort 有 0 条。
2. Blocker：era: approved real coverage missing conflict, confusable, isolation, negation, negative, positive, weak_evidence；scene: approved real coverage missing conflict, confusable, isolation, negation, negative, positive, weak_evidence；influence: approved real coverage missing conflict, confusable, direction_sensitive, isolation, negation, negative, positive, weak_evidence。
3. 只有通过人工审核且覆盖完成后，才可进入“针对真实失败点修复”；当前可进行审核与诊断复核，不能将 pending 结果作为已验收 baseline。
4. Final Worldline Regression 前必须解决：人工审核与真实场景覆盖缺口、已确认的 unsupported/direction/isolation/conflict 错误、真实 retrieval/ContextPack 的覆盖验证、生产 persistence/restart 连续性。不得以当前离线通过率替代这些验收。

# Semantic Relations v1 — r6 人工审核登记报告

根据 `human-review-WT` 于 `2026-09-26` 提供的三条明确 APPROVE，创建独立 r6，生成三份 receipt 并晋升对应的 r5 confusable negative。没有运行新 Evaluation 或调整算法。

[人工审核登记](../../semantic_relations_v1_review_r6/human_review_registration.md) 保留全部原始理由、所询方向和 predicate；[机器 receipt 汇总](../../semantic_relations_v1_review_r6/new_review_receipts.jsonl) 绑定 r5 原 label、parent case line、parent manifest 和 replay input line hashes。

| Relation type | 正式真实 Gold | 人工审核 confusable | 缺少必需 category |
| --- | ---: | ---: | --- |
| Era | 6 | 2 | 无 |
| Scene | 6 | 2 | 无 |
| Influence | 13 | 3 | 无 |
| Overall | 25 | 7 | 无 |

本版本总计 53 条 case / 53 条 input / 52 个 source，pending 为 0。原 28 条 synthetic、4 条 REJECT 排除记录与 26 份旧 receipt 保留；receipt 总数为 29。新增来源或 query 为 0。

[coverage_inventory.json](../../semantic_relations_v1_review_r6/coverage_inventory.json) 是静态 category 清点：读取现有 evaluator 的未改动要求，不导入或执行关系查询、评分器或 runner。Influence query-family 的人工审核真实 confusable coverage blocker 已补齐。没有新性能分数、failure trace 或 machine gate 结果；最新正式 machine gate 仍是冻结 r4 的 `baseline_ready=false` / `can_enter_targeted_repairs=false`。覆盖补齐不会消除 r4 已发现的质量失败，后续需在新报告目录运行统一 baseline 才能产生新门禁结果。

r6 只改变三条 Gold 的 review 元数据，其他 50 行保持原字节。所有标签 payload（排除 review）与 r5 完全相同；input、source、schema、source audit、排除视图、原 receipts 和历史材料逐字节保留。r5 pending 审核包及 joined checklist 等存入 r6 的 review_history，r4/r5 本体及旧报告保持冻结。Negative 仍限于当前 evidence，不断言现实世界绝无该影响；人工批准也不表示三条 Influence 关系成立。

259 个旧数据/报告文件与 28 个运行时/evaluator 文件逐项 hash 校验未变。新 manifest 绑定父 r5 及 r4 baseline artifact manifest，review_files 覆盖人工结论、receipts 与审核历史；Git 索引核对 manifest 对应 blobs 的 SHA256，防止换行转换破坏绑定。

在 `ai-service` 执行：

```powershell
$env:OPENBLAS_NUM_THREADS='1'
.\.venv\Scripts\python.exe -m pytest evals/tests/test_semantic_relations_review.py evals/tests/test_semantic_relations_review_r2.py evals/tests/test_semantic_relations_review_r3.py evals/tests/test_semantic_relations_review_r4.py evals/tests/test_semantic_relations_review_r5.py evals/tests/test_semantic_relations_review_r6.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_worldline_era_expansion.py tests/test_worldline_scene_expansion.py tests/test_worldline_influence_expansion.py tests/test_worldline_session.py -q
.\.venv\Scripts\python.exe -m ruff check evals/tests/test_semantic_relations_review_r6.py
.\.venv\Scripts\python.exe -m compileall -q evals/tests/test_semantic_relations_review_r6.py
```

审核/数据完整性测试 78 passed；现有 Era / Scene / Influence / session 功能回归 74 passed；Ruff、编译和本轮 Git diff 检查通过。覆盖 receipt、身份/日期/理由、原 label/input 绑定、父版本、历史冻结、静态 category 清点与 label/reason/parent/input/receipt/登记包篡改拒绝。测试检查登记无需 scorer，并确认没有新 baseline 输出。

机器验证记录见 [review_registration_validation.json](review_registration_validation.json)。来源完整 HTML 未归档、offset 以保留 excerpt 为基准、候选为冻结注释输入的限制保持不变。没有应用代码改动或构建范围，无环境原因导致的验证缺项；新 baseline、targeted repairs、Final Regression、Online E2E 与部署依照本轮范围未运行。

# Semantic Relations v1 — 第二轮人工审核登记

`human-review-WT` 于 `2026-09-26` 对 4 条真实 weak-evidence negative 全部 APPROVE。已生成 4 份新 receipt，并登记到独立数据版本 `semantic_relations_v1_review_r2`。**本轮未运行最终 Evaluation，没有计算新 baseline。**

| relation_type | 正式 Gold 总数 | 本次新增 weak negative |
|---|---:|---:|
| Era | 4 | 1 |
| Scene | 4 | 1 |
| Influence | 4 | 2 |
| overall | 12 | 4 |

当前正式 Gold 共 4 条 positive、8 条 negative，本批 pending 为 0。原始 4 条被拒 weak 样本仍在排除台账中，不进入正式 Gold 或 replay 输入；28 条 synthetic 仍为单独回归 cohort。

批准保留 negative 标签及 `expected_relations=[]`。新增 receipt 保存用户原审核理由、日期、case ID 和审核人，绑定 r1 原 label payload、Gold 行、input 行和 manifest 的 SHA256。原 8 条 approved 的行字节和原 12 份历史 receipt 完全保留。

所有 query、端点、predicate、expected relation、exact span 和 rationale 保持不变。新版本的 inputs、sources、schema、旧排除台账与 r1 字节完全相同；原 v1/r1 数据及全部既有评测报告未改动。原 3 个真实失败对应的标签和输入仍保留，没有重评、改变算法或修改 Gold 消除失败。评测默认入口和运行时未改动。

数据、schema、receipt 索引与机器可读汇总见 `evals/semantic_relations_v1_review_r2/`。`review_history/` 的 pending 审核包是历史记录，当前状态以 r2 的 `review_summary.json` 为准。

验证：审核、receipt、freeze、血缘检查 22 项，既有 Era/Scene/Influence/session 回归 74 项，共 96 项通过。Ruff、compileall 及历史冻结目录/运行时代码 diff 检查通过。实际命令、结果和历史文件哈希见 `review_registration_validation.json`。

本批 4 条的人工审核已全部完成，但尚未产生新的各项指标 baseline。真实 negation、conflicting evidence、反方向 Influence Gold 的人工审核覆盖仍需补齐。完整 Final Worldline Regression、线上检索、生产持久化验收未执行；上一轮暂不运行最终 Evaluation 的要求继续有效。本轮没有进入真实失败点修复或部署。

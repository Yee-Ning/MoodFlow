# Semantic Relations Evaluation v1

一次运行 Era + Scene + Influence 的离线评测。**本版的 12 条真实来源样本待人工审核；正式 Gold baseline 尚未成立。** 28 条 synthetic 只验证评测与运行时契约，不能代替真实 Gold。禁止根据 runtime 输出修改冻结标签。

```powershell
cd D:\code\ProjectsAll\MoodFlow\ai-service
$env:OPENBLAS_NUM_THREADS='1'
.\.venv\Scripts\python.exe run_semantic_relations_eval.py
.\.venv\Scripts\python.exe -m pytest evals/tests/test_semantic_relations_evaluation.py -q
```

退出码 2 表示审核、覆盖或正式 Gold 失败 blocker；仍会写出全部结果。schema/integrity 或执行异常属于 harness error，退出码 1，不能报告成功 baseline。输出目录不能位于数据目录内。

## 独立输入与 provenance

- `gold.jsonl`：query、relation_type（era/scene/influence）、source/target entity、positive/negative、expected predicate/fields/conflict flags、证据、exact span 字符偏移、审核状态。
- `inputs.jsonl`：独立冻结的 SearchPlan、预置 evaluation entity graph、retrieved documents/claim annotations；不含 Gold label/expected/review。使用 eval provider，未冒充已核实的生产 catalog ID。
- `sources.jsonl`：真实网页短原文摘录或显式 synthetic 文本；URL、publisher、locator、时间、内容 SHA256。真实内容不是模型改写，网页全文/说话人上下文必须由人工打开 URL 核对。
- `schema.json`：三个 strict Pydantic contracts 的 JSON Schema。
- `manifest.json`：上述四文件的字节哈希。普通运行只读这些文件；运行前后校验。source metadata 通过 span.source_id 关联 ledger，并在输出 evidence trace 中解析。

真实输入的 supported_claims 为评测用注释，negative/weak/confusable 包括故意不充分的候选；因此这里测量冻结文档下的 validator/projection 安全性和表达覆盖，**不测量线上 retrieval、claim extraction、entity resolution 或生产 relation precision**。不能将这里构造的 claim 写入生产索引。无证据拒绝、错误 span、反方向、冲突等额外契约场景属于 synthetic cohort。

## 人工审核

运行生成 `../reports/semantic_relations_v1/human_review_pack.md`。人工逐条核对来源上下文和 proposed label，记录 reviewer、UTC 时间、case ID、理由与最终标签。不根据 harness failure 修改标签。审核结论需新增数据版本，而非覆写本版 freeze。

approved 的 review 必须引用 suite 内 JSON receipt（record_path/record_sha256）。receipt 包含 `case_id`、`status: approved`、`reviewer`、`reviewed_at`、`label_sha256`。label_sha256 为去除 review 后 `json.dumps(case.model_dump(mode='json', exclude={'review'}), sort_keys=True, ensure_ascii=False)` 的 UTF-8 SHA256。Loader 校验 receipt 的 case、标签、reviewer 和时间。技术校验只能固定审核声明，不能代替真实人工阅读或验证审核人身份。

正例应明确 relation 与支持范围；negative 表示当前证据不足，不能解释为现实中关系不存在。真实 negation、conflicting evidence 和反方向样本仍需补采与审核；不能把改写的否定句标成真实来源。

## 指标与分母

每项按 `approved_real_baseline`、`pending_real_diagnostic`、`synthetic_regression` 独立计算，再输出 overall + era/scene/influence。overall 为 micro aggregation；同源 query 有相关性，小样本不提供虚假的置信区间。0 分母为 JSON null / N/A。

| 指标 | Numerator / denominator |
|---|---|
| Relation Precision | 完全匹配端点、predicate、Gold 所需证据、字段与冲突标记的输出关系 / 全部输出关系 |
| Evidence Coverage | 与对应输出关系匹配且可追溯的 Gold claim 数 / 正例预期的唯一 claim 数；部分证据按实际覆盖计 |
| Evidence Traceability | edge→source→document→parent/chunk→claim→source snapshot→exact span 完整有效的证据 / 输出证据数；不等同于语义支持 |
| Unsupported / Hallucination Rate | 不满足 Gold 与证据/字段约束的输出关系 / 输出关系数；零输出为 N/A |
| Influence Direction Accuracy | 有可追溯证据且端点方向/predicate 正确的 Influence 正例关系 / 预期 Influence 正例关系；拒绝正例记 0，避免 abstention 抬高分数 |
| Weak Evidence Rejection | 没有返回关系的 weak negative / weak negative cases |
| Negation Rejection | 没有返回关系的 negation negative / negation negative cases；含 support+contradiction 的正例归 conflict |
| Conflict Handling | 所有预期关系、断言 evidence、conflict flags、不确定字段都保留的 conflict case / conflict cases |
| Query Isolation | 没有输出非所询 family（包括 graph-only semantic edges）的 case / 全部 cases |
| Checkpoint Continuity | application saver 保存/恢复、重新编译 reader、重复冻结输入查询及额外状态 turn 语义一致的 case / 全部 cases |

checkpoint scope 是 `result_contract_and_query_replay`；没有声称验证完整 session 多轮处理、跨进程 PostgreSQL 恢复或线上服务。既有 session、graph、API、PostgreSQL restart 测试需另行运行并报告 skip 原因。

## 诊断与输出

`metrics.json` 保存 cohort breakdown、代码 revision/dirty 状态、freeze 与 blockers；`results.jsonl` 保存 query→retrieved evidence→candidate→filtering/scoring→final→expected→failure reasons；`failures.jsonl` 为全部失败的完整 trace；`coverage_inventory.json` 列明人工审核覆盖缺口；`semantic_relations_evaluation_v1_report.md` 给出结论与下一轮建议。

错误 taxonomy：retrieval failure / evidence mismatch / relation classification / direction error / weak evidence accepted / negation failure / conflict failure / unsupported relation / cross-relation contamination，另有 checkpoint continuity failure。一个 case 可有多个原因。direction error 可以表示支持方向未识别，不一定已经生成了反方向关系；原始 validator reasons 保留用于区分。

# Semantic Relations v1 — human review revision r1

本轮只登记人工审核、晋升/排除和生成复审包。**该版本未运行 Evaluation，未计算或更新 baseline 指标。** Era / Scene / Influence 运行时、原评测入口的默认版本、生产索引和部署均未改动。

审核人 `human-review-WT`；审核日期 `2026-09-26`（保留用户提供的日期精度，不补造时分秒）。`review_decisions.json` 保存用户逐条提供的原理由，`receipts/` 含全部 12 份 approved/rejected receipt。每份 receipt 绑定不含 review 的 label SHA256、原 Gold 行 SHA256、原 replay input 行 SHA256 和原 manifest SHA256。技术校验固定审核声明，不替代人工阅读来源。

| 状态 | Era | Scene | Influence | 合计 |
|---|---:|---:|---:|---:|
| 人工 APPROVE，正式 Gold | 3 | 3 | 2 | 8 |
| 新弱证据候选，pending | 1 | 1 | 2 | 4 |

分组采用样本实际所询 `relation_type`，不是 case ID 前缀。正式 Gold 的 8 条中有 4 条 positive 和 4 条 isolation negative；28 条 synthetic 仍只作契约回归。

## 数据与原冻结版本的关系

- `approved_gold.jsonl`：仅 8 条人工 APPROVE；每条除 review 外与原版本完全一致。
- `pending_weak_evidence.jsonl`：仅 4 条新 pending negative 提案；没有继承旧 reviewer 或 approval。
- `excluded_cases.jsonl`：4 条 REJECT 的原标签 payload 与本次 reject receipt，作为排除台账，不进入正式 Gold 或新版评测输入。
- `gold.jsonl`：统一 harness 兼容输入，包含 28 synthetic + 8 approved + 4 pending。文件名不意味着 40 条均为正式 Gold；正式计分只取 `approved_real_baseline` cohort。
- `inputs.jsonl`：复用旧版保留样本的原始行字节，并新增 4 条独立冻结输入；不含 Gold label/expected/review。
- `sources.jsonl`：复用原 35 条 source snapshot 行字节，新增 4 个真实官方页面短原文摘录。
- `schema.json`：复用原版 strict schema；未重构稳定接口。`review_decisions.schema.json` 仅描述本轮人工审核记录。
- `manifest.json`：固定四个 suite 文件以及 receipt、审核包和派生视图的字节哈希，并绑定父版本 freeze。
- `review_summary.json`：审核状态和血缘的 machine-readable 汇总，不是 evaluation results。

原 `../semantic_relations_v1/` 的全部文件及 `../reports/semantic_relations_v1/` 的报告、指标、失败 trace 保持原样。未修改旧 weak 标签、没有把旧通过率转成正式 baseline。已批准的旧 isolation negative 也保留历史 probe target/claim 字段，避免无声改写本次审核对象；它们用于 query isolation，不提供 Influence 正例方向准确度的分母。

此前诊断的 `real-scene-1-positive` evidence mismatch，以及两条 `real-influence-*-positive` direction error 对应的标签、来源和查询输入全部保留。这里不重新评分，也不宣称这些失败已消失。

## 4 条新复审候选

| 替代被拒样本 | 新 case ID | 真实证据 / 不足之处 |
|---|---|---|
| real-era-0-weak_evidence | real-era-r1-flying-representative-weak | Flying 发行时间支持年代归属，但不证明 `representative_of_era` 时代代表性；新 query 明确所询代表性。 |
| real-scene-1-weak_evidence | real-scene-r1-dylan-venue-weak | Bob Dylan 单场纽约演出地点不明确陈述 New York folk scene 成员归属。 |
| real-influence-2-weak_evidence | real-influence-r1-bowie-eno-cowrite-weak | Bowie/Eno 共同作曲署名不明确陈述 Bowie → Eno 的音乐影响。 |
| real-influence-3-weak_evidence | real-influence-r1-baez-dylan-guest-weak | Dylan 客串 Baez 演出不明确陈述 Baez → Dylan 的创作启发。 |

详细原文、URL、publisher、locator、exact span 字符偏移、候选 predicate/端点和 label hash 见 `human_review_pack.md`。短摘录保留来源原词与拼写；字段间换行用于文本分隔。需复审完整页面上下文，不能将这些 provisional negative 当作已审核 Gold。

negative 仅表示给定证据不能支持明确所询的关系，不断言现实中关系不存在。例如 Flying 的发行年代已明确，本 query 单独询问时代代表性。`supported_claims` 中的新关系候选是评测用不充分证据探针；固定候选置信度不代表真实支持度，不能写入运行时索引，也不能据此报告端到端 retrieval/claim extraction 质量。

## 只校验审核，不运行 Evaluation

```powershell
cd D:\code\ProjectsAll\MoodFlow\ai-service
$env:OPENBLAS_NUM_THREADS='1'
.\.venv\Scripts\python.exe -m pytest evals/tests/test_semantic_relations_review.py -q
.\.venv\Scripts\python.exe -c "from pathlib import Path; from evals.semantic_relations_review import validate_review_revision; print(validate_review_revision(Path('evals/semantic_relations_v1_review_r1'), Path('evals/semantic_relations_v1')))"
```

`prepare_review_revision` 和 `validate_review_revision` 只依赖数据 contracts，不导入 runner/metrics。新版本不得覆盖旧目录；批准原标签不修改其 payload，REJECT 排除，pending 不能自动晋升。复审的新结论仍需新数据版本及新 receipt。

本次自动化检查范围是审核状态、原标签和输入血缘、exact span、receipt/view 完整性与既有 Era / Scene / Influence / session 回归。完整 Final Worldline Regression、真实线上检索和跨进程生产持久化验收没有在本轮执行。真实 negation、conflicting evidence 和反方向 Gold 的人工审核覆盖仍需在 Final Worldline Regression 前补齐。

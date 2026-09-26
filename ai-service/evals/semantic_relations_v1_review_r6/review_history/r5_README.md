# Semantic Relations Evaluation v1 — r5 pending Influence confusable

本版本只准备人工审核数据。它独立继承 r4，追加 3 条真实来源的 Influence query-family confusable 候选，不运行评测，也不调整运行时关系算法或评测规则。

先阅读 [human_review_pack.md](human_review_pack.md)，逐条核对原始来源、实体对、predicate、span 和负例边界。全部新候选保持 `review.status=pending` / `method=pending_human`；`label=negative` 是待审核的 proposed label，不能作为正式人工 Gold 使用。

## 新候选与来源

| Case | 所询方向与 predicate | 当前证据支持的非 Influence 事实 | 原发布方 |
| --- | --- | --- | --- |
| `real-influence-r5-oasis-blur-britpop-confusable` | Oasis → Blur / `influenced` | Britpop 场景归类及奖项竞争 | [BRIT Awards / BPI](https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/) |
| `real-influence-r5-soundgarden-nirvana-grunge-confusable` | Soundgarden → Nirvana / `shaped_style` | Seattle / grunge / Sub Pop 共同场景 | [Museum of Pop Culture](https://www.mopop.org/experience/puget-sound-stories) |
| `real-influence-r5-ten-nevermind-era-confusable` | Ten → Nevermind / `inspired` | 两张原专辑在 1991 / 1990s 的发行资料 | [Pearl Jam](https://pearljam.com/music/album/ten)、[Nirvana / UME](https://www.nirvana.com/releases-archive/nevermind/) |

共 3 条新 case、4 个新来源：Scene→Influence 混淆 2 条，Era→Influence 混淆 1 条。实体在同一真实音乐语境中有关联，负例仅表示本次冻结证据不支持所询方向与 predicate，不表示现实世界不存在该影响。

## 数据与证据边界

- `gold.jsonl` / `inputs.jsonl` / `sources.jsonl` 保留 r4 原始内容为完整前缀，再追加新记录；总数分别为 53 / 53 / 52。
- `approved_gold.jsonl` 仍为 22 条（Era 6、Scene 6、Influence 10）；28 条 synthetic、原 4 条 REJECT 排除记录、26 份历史 receipt 全部保留。
- `pending_cases.jsonl` 是 3 条待审 Gold；`pending_review_items.jsonl` 与 `review_checklist.json` 联接 query、实体、候选 predicate、expected relations、source metadata、IDs、exact span、offsets 与 SHA256，方便逐条审核。
- `sources.jsonl` 存储真实原文/原始 JSON 字段。offset 是保留 excerpt 的 Unicode 字符位置 `[start,end)`；source-content 与 exact-span SHA256 均可离线复核。`source_audit.json` 另外记录原始 URL、最终 URL、publisher、locator、HTTP 状态、采集时间、response hash 与 fragment 映射。未知作者/网页发布日期明确置空，不把事件/发行年份冒充发布日期。
- Ten 官网的 HTML `script[type=application/json]#data` 提供原始 `album.name` 和 `album.released`。两个非连续字段以两 LF 分隔，未伪装成连续原文；Gold span 只取 `released` 字段。完整 HTML 未归档，response hash 仅作采集 provenance，当前 URL 完整上下文仍需人工核对。
- 中文 rationale、audit 的 `claim_scope` / `review_caution` 及问答说明不作来源证据。audit 中 `context_is_gold_evidence=false` 指这些人工审核辅助解释；候选支持证据以真实 `sources.jsonl` excerpt 与 Gold span 为准。
- `inputs.jsonl` 沿用既有 `supported_claims` 候选输入接口：保留非 Influence 事实，并单独列出所询 Influence 命题作为拒绝能力探针。探针不是 source 已证实的事实，也不是生产抽取结果。沿用的 0.96 候选输入值不代表证据强度或 Influence 成立概率。Gold label、expected relations、review、rationale 不传入 runtime input。这是冻结候选证据边界测试数据，不代表线上检索质量。

## 覆盖结论与冻结

[coverage_projection.json](coverage_projection.json) 仅以 AST 读取原 evaluator 的必需 category，并清点当前 approved 和 pending；没有导入或执行 scorer / runner。

当前 approved Influence 仍缺少 `confusable`，因此 `baseline_ready_now=false`、`can_enter_targeted_repairs_now=false`。3 条候选中至少 1 条经明确人工 APPROVE、在后续独立版本登记 receipt 并晋升后，足以补齐这一 coverage 条件；如全部批准，Influence approved 为 13 条、全部真实 Gold 为 25 条。该投影不清除 r4 的质量失败，也不是新 baseline 的 gate 结果。

`manifest.json` 绑定父 r4 manifest、父核心文件 SHA256、r4 baseline artifact manifest，以及本版本核心与审核材料 hashes。`protected_artifacts.json` 记录此前 190 个冻结数据/报告文件；`scope_lock.json` 记录 28 个运行时、validator/projector 所在模块与 evaluator 文件。此前 7 个历史 v1 报告存在工作树 CRLF 与 Git LF 的已知差异，记录同时保留原始 bytes hash 与 Git blob hash；本轮没有改动这些文件。

本版本不生成新 APPROVE / REJECT 决策或 receipt。请提供 `reviewer`、`reviewed_at`、每条 `case_id` / `decision` / `reason`；无回复保持 pending。需要修改实体、predicate、scope、span 或标签时，应 REJECT 当前候选，再生成独立版本，不能覆盖冻结数据。

数据构造 blocker：无。人工审核仍待完成。本轮不运行正式 baseline、targeted repairs、Final Regression、Online E2E 或部署。验证结果见 [pending_coverage_report.md](../reports/semantic_relations_v1_review_r5/pending_coverage_report.md) 与 [review_preparation_validation.json](../reports/semantic_relations_v1_review_r5/review_preparation_validation.json)。

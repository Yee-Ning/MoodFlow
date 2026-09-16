# Gold V0 固定回放评测与失败案例（2026-09-16）

## 范围与可复现性

- 数据集：`evals/datasets/rag_eval_gold_v0.approved.jsonl`，SHA256 `45d5db91cb2f9a87aee19b75ab648699cab1ed05d3e0a9dc5607cafc3f4b1eaf`；30 条项目负责人批准的 AI 辅助复核标签，其中 18 条 `answerable`、11 条 `insufficient_evidence`、1 条 `needs_input`。
- 固定语料/索引 Release：`knowledge-v2-8608ffc2fa72e441`。把已有的 50 条 Trace 按 Gold case_id 无损筛为 30 条；未重跑在线链路，未调用 DeepEval、Judge 或生成模型，因此没有新增 API token 成本。
- 调优前 Trace：`evals/artifacts/baseline-v0/traces.context-v1.generated.jsonl`，SHA256 `50b6c3e6620a6724342bf833d33849081d5a688310187e078161b22cab6fd61f`，run_id `rag-context-v1-20260915`。
- 调优后 Trace：`evals/artifacts/context-adapter-v1/best-traces.generated.jsonl`，SHA256 `67583b2329ce84d173ad5d9754d9379f044723757870d6bb93208f4a87157f7b`，run_id `context-adapter-v1-tuning-20260916`。两组上游 Top40/Top12 相同，可比较确定性的 Context Adapter 结果；历史答案的模型版本标签分别为 `deepseek-v4-flash` 与 `deepseek-flash`，不能把答案差异单独归因于 Context 参数。已生成答案来自历史调用，不代表本次重新生成。
- 机器可读结果的归档副本：`evals/reports/gold_v0_deterministic_context_baseline_20260916.json` 和 `gold_v0_deterministic_context_tuned_20260916.json`；原始运行输出仍在本地 `evals/artifacts/gold-v0/`。30 条筛选后的 Trace 也仅保留在本地 artifacts 目录，不作为源码提交。

## 确定性指标

| 指标 | 调优前 | 调优后 | 解释 |
| --- | ---: | ---: | --- |
| Top40 精确 Parent Recall | 53.80% | 53.80% | 仅 18 条可回答题；与 Gold 所列 Parent ID 比较 |
| Top12 精确 Parent Recall | 23.15% | 23.15% | Rerank 之后的平均相关 Parent 覆盖 |
| Top40 MRR@10 | 0.1794 | 0.1794 | 精确 Parent 匹配 |
| Top12 MRR@10 | 0.2469 | 0.2469 | Rerank 提升首个相关 Parent 的位置，但明显损失覆盖 |
| Final Context Precision | 7.18% | 12.50% | 提高 5.32 个百分点 |
| Final Context Recall | 20.37% | 18.98% | 降低 1.39 个百分点 |
| Supporting Span Coverage | 17.59% | 17.59% | 没有改善 |
| Outcome 状态一致率 | 66.67% | 66.67% | 比较 Gold outcome 与 Trace 状态，不是语义答案正确率 |
| 引用 ID 合法率 / Token 预算合规率 | 100% / 100% | 100% / 100% | 只证明结构合法，不证明引用支持答案 |

调优后 18 条可回答题的历史延迟样本：Retrieval P50/P95/P99 为 109/1344/2546 ms；Rerank 为 1615/2480/2739 ms；Context Adapter 为 14/20/21 ms；Generation 为 4742/7067/7889 ms。样本量仅 18，且为旧 Trace 的记录值，不可当作当前线上负载的 SLO 验收。调优前快照未记录 Context/Generation 可用延迟，不能做这两阶段的严格前后延迟比较。

## 逐阶段失败定位

18 条可回答题中，Top40 至少命中 1 个已标注 Parent 的有 14 条，Top12 还有命中的有 7 条，Final Context 还有命中的有 6 条：

| 首个断点 | Case ID | 数量 | 直接观察 |
| --- | --- | ---: | --- |
| Top40 未命中已标注 Parent | 003、006、039、042 | 4 | `006` 只召回 6 条，目标来源 `jc-v1-007` 未进入；`003` 的 Top40 第 4 条却是另一篇看似直接讨论影响力的原文，需复核 Gold 是否漏标替代证据 |
| Top40 命中但 Top12 全部丢失 | 001、002、004、026、037、040、043 | 7 | 如 `002` 的已标注 Parent 在 Top40 第 5、12 位，Rerank Top12 均未保留；`040` 的已标注 Parent 在第 12、13、17 位，亦全部丢失 |
| Top12 命中但 Final Context 丢失 | 044 | 1 | 相关 Parent 在候选第 7 位、Rerank Top12 内，最终未进入上下文 |
| Final Context 仍命中 | 007、023、024、034、036、038 | 6 | `034` 从调优前 2 个相关 Parent 变为调优后 1 个，构成此次 Context Recall 下滑的具体案例 |

此处的 Recall 是**对当前已标注 Parent 的精确匹配**，不能直接等同于真实证据召回。特别是 `003` 的 Top40 包含另一篇标题为 “Conclusion: Chou’s Impact” 的原文，但不在 Gold 的相关 Parent 列表；`042` 也召回其他英文资料。应先从冻结原文独立复核这些替代候选是否真正满足 required facts，再决定是补充 Gold 还是修改检索。不能用当前在线排名/分数反向自动扩充 Gold。

## Outcome 与答案风险

- Gold 判为 `insufficient_evidence` 的 11 条中，旧 Trace 有 8 条标为 `success`（005、008、009、025、031、032、033、035），1 条标为 `insufficient_evidence`（045），2 条标为 `needs_input`（046、047）。`048` 的 `needs_input` 与 Trace 一致。`005`、`032`、`033` 的答案文字实际上明确说明证据不足，因此状态不一致不必然等于内容越界；现有确定性状态指标不足以判断语义正确性。
- `025` 是高优先级错误：回答把陶喆/蔡依林的《今天妳要嫁給我》和郭富城等人的《Shake Your Body》列成“周杰伦与蔡依林的合作记录”。引用 ID 合法，但来源与所声称的合作主体不匹配；这说明 Citation Validity 不能替代事实支撑检查。
- `008` 回答了《东风破》的发行、配器和风格，却没有证明具体创作缘起；`035` 以流行程度等信息作“音乐风格差异”。这些应纳入人工支持性判定，暂不把表面引用当作 Faithfulness 通过。
- `046`（“七里香的创作背景”）和 `047`（“霍元甲的创作背景”）有明确歌名，但 Query Understanding 给出 `needs_input`，Retrieval 根本未运行；这是与 Gold `insufficient_evidence` 不同的路由/指代判断问题。

## 结论与下一步

1. 先做一次**定向标签补充审查**：只看 003、006、039、042 等零命中题及 Rerank 全丢题的替代原文，判断已标注 Parent 是否遗漏；同时补齐目前 15 个相关 Parent 缺少的题内精确 span。原 Gold 保留，任何修订单独版本化并保留审计记录。
2. 标签稳定后，再定位检索召回与 Rerank Top12 的损失。尤其先查 `006` 的检索候选过少、`002/040` 的 Rerank 丢证，而不是继续调 Context TopK：Context 调参无法挽回上游已丢的证据。
3. 修复 `046/047` 的命名歌曲误判 `needs_input`，并对 `025` 添加合作主体关系的事实核验回归。无证据时应正确降级，不应凭引用格式通过。
4. 语义 Faithfulness/Answer Relevancy 的 Judge 评测留到标签补充和确定性故障定位后，以少量高风险样本运行；本轮没有调用 Judge，也没有消耗 Judge API 费用。

局限：这批 Gold 是项目负责人批准的 AI 辅助复核稿，并非逐条独立人工盲标；其中 15 个相关 Parent 无题内专属 span。报告仅能作为固定回放诊断，不应直接据此宣称当前线上真实召回率或 P99 已验收。

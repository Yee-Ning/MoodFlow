# Gold V2 冻结来源审计与固定回放（2026-09-16）

## 版本与判定边界

- 基线是仅修正 046/047 同名歧义结果的 `datasets/rag_eval_gold_v1.outcome_policy.approved.jsonl`；V0、V1 都保留原样。
- 冻结来源 release 为 `knowledge-v2-8608ffc2fa72e441`，SHA256 `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。对 `gold_v1_source_span_candidates.jsonl` 的 18 条原文候选，逐条查看本题 required facts、Parent 原文上下文及来源，不使用在线 rank、score、Final Context 或旧 Silver Top3 决定是否纳入。判定及理由保存在 `gold_v2_source_decisions.jsonl`。
- 产物 `datasets/rag_eval_gold_v2.ai_assisted_source_review.jsonl`，SHA256 `7963e11cf73dab316903b5a7904998852f10e677e5f5c93a479583981c27937d`。30 条 case 与 V1 相同，仅 10 条 case 有证据标签增补。15 条 V0 候选缺口中，046 已因 `needs_input` 不适用；余下 14 个相关 Parent 缺口补全 13 个，040 的一个保留未补。
- 16 条接受：13 条现有相关 Parent 的精确 span、3 条新增相关 Parent+span（003 两条英文论文原文、042 一条 TIME 英文人物文章）。2 条排除：040 的“这一年”片段缺少可独立确认的 2007 年；046 已改为需要先澄清实体，不得附答案证据。042 的 TIME 文章有独立英文原文和来源 URL，对“英文资料”查询相关；旧参考答案的列举并非穷尽相关来源。
- 每条接受的 span 都必须逐字存在于冻结 Parent；新 Parent 的 `source_id` 同步进入 `expected_source_ids`。这个版本是**用户委托的 AI 辅助来源审查**，不是新一轮独立人工盲标，也没有重新核实语料本身的每个历史事实。`required_facts` 与 `reference_answer` 不因补充局部或替代证据而自动改写。

## 原 Trace、同评测函数的对比

两组回放仍使用 V1 报告中同一批 30 条历史 Trace，未重跑 Retrieval、Rerank、Context Adapter、Generation 或 Judge，也未产生新的模型 API 费用。机器可读报告分别为 `gold_v2_source_review_context_baseline_20260916.json` 和 `gold_v2_source_review_context_tuned_20260916.json`。

| 指标 | Gold V1／已调 Context | Gold V2／未调 Context | Gold V2／已调 Context |
| --- | ---: | ---: | ---: |
| Outcome 状态一致率 | 73.33% | 73.33% | 73.33% |
| Top40 精确 Parent Recall | 53.80% | 58.43% | 58.43% |
| Top12 精确 Parent Recall | 23.15% | 27.78% | 27.78% |
| Top40 MRR@10 | 0.1794 | 0.2071 | 0.2071 |
| Top12 MRR@10 | 0.2469 | 0.2618 | 0.2618 |
| Final Context Precision | 12.50% | 9.46% | 13.89% |
| Final Context Recall | 18.98% | 25.00% | 20.37% |
| Supporting Span Coverage | 17.59% | 21.76% | 18.52% |

这些数字变化全部来自证据标签分子/分母的修订，**不能作为模型性能改进**。尤其已调 Context 的 Precision 高于未调配置，但 V2 Span Coverage 反而低 3.24 个百分点；应在稳定证据定义后检查打包选择，而不是只追求 Precision。仍未达到最终质量验收。

按 V2 精确 Parent 标签的 18 条 `answerable` case，首个零命中断点：Top40 为 006、039（2）；Top40 命中但 Top12 全丢为 001、002、004、026、037、040、043（7）；Top12 命中但最终上下文丢失为 042、044（2）；最终上下文仍保留至少一个标注 Parent 为 003、007、023、024、034、036、038（7）。这仅是**精确 Parent ID** 分段诊断：002、037 的 Top12 有尚未完全审查的替代事实证据，不能直接断言精排造成全部证据丢失；042 的新增 TIME 英文资料在 Top12 但未进最终 Context，仍需检查其他英文材料是否满足问题。

## 下一步及风险

1. 固定 V2 标签和 Trace，审查 006/039 的查询计划、过滤条件、索引覆盖；对 7 个 Rerank 断点先比对 best-matched Child 与替代 Parent 的事实支撑，再做可解释的局部调参，不进行全网格搜索。
2. 040 尚有一个“这一年”Parent 缺乏题内年份 span；保留为已知标签不完整项，不用上下文邻居或在线排序反向补标。
3. 若检索/精排真实损失得到验证，再分别做固定集离线实验与延迟对比。现有 P50/P95/P99 是历史小样本记录，不代表生产负载或本次改动结果。
4. `annotation_status=approved` 继承自原主人认可的 AI 辅助 Gold；新增修订由用户委托本代理审查。严肃外部基准仍需独立人工抽样复核，尤其低质量二手资料和跨语言事实表述。

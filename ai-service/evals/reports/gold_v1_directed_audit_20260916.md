# Gold V1 定向证据审计与故障定位（2026-09-16）

## 审计边界

- 基准仍是已批准但**未改动**的 `evals/datasets/rag_eval_gold_v0.approved.jsonl`（30 条）。候选仅从冻结 release `knowledge-v2-8608ffc2fa72e441` 的原始 Parent 文本提取，未把在线 Retrieval/Rerank rank、score、Final Context 或旧 Silver Top3 当作标注依据。
- 机器可读候选位于 `evals/reports/gold_v1_source_span_candidates.jsonl`。`run_rag_gold_candidate_audit.py` 校验 release 哈希、case/Parent/source 关联、动作和 span 是否逐字存在；结果为 18 条来源有效的**待批准提案**：15 条现有相关 Parent 的 span 补全，3 条新增相关 Parent 候选。原 Gold、expected outcome、required facts 和 reference answer 均未变。
- 上述程序只验证来源身份和原文匹配，**不**验证候选是否语义相关、足够支持参考答案、来源本身是否真实可靠。不得把“15/15 缺口有候选”解读为 15/15 已通过人工证据审查。
- 无新增在线检索、生成模型或 Judge 调用，API token 成本为零。阶段指标沿用 `gold_v0_fixed_replay_20260916.md` 的历史固定 Trace，不是重新跑出的提升值。

## 标签与原文问题

| Case | 原文审计结论 | 后续处置 |
| --- | --- | --- |
| 004、024、037、043、044 | 能从 Gold 已选 Parent 找到逐字相关片段；各片段只承担题目的一部分事实，不可单条代替完整参考答案。 | 可作为局部 supporting span，仍需组合核对事实覆盖。 |
| 034 | 3 个已选 Parent 均可提片段，其中一个仅写《青花瓷》“属于中国风”，不足以单独证明《东风破》与《青花瓷》的具体差异。 | 保留候选，按问题逐条核查支撑力度；不能因为字符串匹配而提高 span coverage。 |
| 036 | 已选 Parent 有早期《叶惠美》风格变化片段；同一来源的其他年代叙述存在可疑之处。 | 不扩大引用范围，必要时对照更权威来源。 |
| 040 | 已选 Parent 有《我很忙》及早期专辑列表片段，但“这一年”依赖 Parent 之外的年份上下文，单独引用不能证明 2007 年。 | 对照相邻冻结原文寻找有年份的完整证据；不能以这条短 span 独立验收时间线。 |
| 042 | 已选 Parent 有英文论文中的早期专辑标题片段；另有英文 TIME 人物文章 Parent，可作为额外的英文资料来源候选。它不替代题目要求的官方英文入口和 Puget Sound 论文。 | 不自动扩充 Gold；核对资料来源与 required facts。 |
| 046 | 已选 Parent 明确讲《七里香》歌曲与同名专辑的命名关系，但 Gold outcome=`insufficient_evidence`，Query Understanding 回放=`needs_input`。 | 先决定问题究竟指歌曲还是专辑；暂不据此改路由。 |
| 003 | 冻结论文来源 `jc-v1-025` 有“Chou Style”影响华语流行乐、被他人模仿的直接文本；这两个 Parent 不在旧 Gold。 | 作为 Gold 漏标候选审查，不先把 Top40 未命中归咎于检索。 |
| 006、039 | 本轮原文搜索未找到可确认替代 Gold 的 Parent；039 历史 Top40 中有关 2005 年的资料不能证明题目要求的 2008–2012 年事实。 | 保留真实召回缺口，重点检查资料覆盖与查询/索引。 |
| 047 | “霍元甲”在实体表中存在同名歧义；Query Understanding V1 Golden Set 与 046 一样预期 `needs_input`，但新 Gold 标为 `insufficient_evidence`。 | 是标签/产品语义冲突，不应为了提高指标直接改代码。 |

## 逐阶段诊断（仍使用旧 Trace）

对 18 个 `answerable` case，按旧 Gold 的精确 Parent ID：Top40 至少命中 1 个的 14 个 case，Top12 留存的 7 个，Final Context 留存的 6 个。Top40→Top12 全丢的是 001、002、004、026、037、040、043；Top12→Final Context 丢失的是 044。003、006、039、042 在 Top40 无**已标注** Parent，其中 003/042 可能有漏标替代证据；不能把这四个 case 统一称为检索失败。

Reranker 实际打分输入为各候选 Parent 的 `primary_child_content` 加标题/面包屑，不是完整 Parent。旧 Trace 中匹配 Gold Parent 的 27 行候选里，17 行所属 Parent 有已批准的 supporting span；其中仅 6 行的 best-matched Child 完整包含该 span。这个“完整包含”是严格字符串检查：其余 11 行可能包含部分关键事实，不能直接定性为错误，但表明 Parent 级标签与 Child 级精排输入有明显粒度差。

002 的 Gold Parent 在 Top40 第 5、12 位却均被 Top12 丢弃；但第 5 位 Parent 的已标注文字只是概括性音乐影响，不足以独立说明风格特征，而 Top12 包含其他讨论“Chou Style”的英文论文片段。037 的 Gold《叶惠美》Parent 在 Top40 第 7 位且 Child 包含发行日期，精排后丢失；但 Top12 第 1 位仍提到 2003 年 7 月第四张专辑，第 3 位仍提到 2003 年 TIME 封面。因此两题的**精确 Parent 丢失是真实观察，证据完全丢失尚未证实**；需要先审查替代 Parent 的事实覆盖，才能把差距归因于精排。暂不改 Retrieval/Rerank 架构或调参。

## 025 归因错误与评测隔离

025 问周杰伦与蔡依林是否合作；旧答案把别人的作品或同页共现当成两人合作。引用 ID 合法不等于事实关系成立。离线生成现在增加“合作关系必须有明确双方证据”的提示与 fail-closed 检查：最终上下文无双方直接关系证据时标记 `insufficient_evidence`，且不花钱调用生成模型。这是针对离线评测路径的保护，**不等于线上 WorldLine 回答已修复**；线上有独立生成/grounding 模块，应单独设计同类回归。

另外，旧离线生成按 `case.expected_outcome` 决定是否调用模型，会把答案标签提前注入受评路径。现改为只看 Trace 实际 `query_understanding`/`context` 状态，Gold 只在评分时使用。历史答案文件不会因此自动改变；要比较修复效果，需要在标签确定后固定配置重新生成相同样本。本轮没有做此付费重跑。

## 验收前的明确缺口

1. 对 18 条候选逐条确认语义支撑，特别是 003、034、042、046，并将 046/047 的 outcome 冲突定为产品/Gold 决策。再版本化 V1 Gold，保留 V0 与审计记录。
2. 用稳定标签做固定集的 Top40 与 Top12 分段回放，逐个检查 002/037 的 Child、精排输入与损失原因；单独排查 006 的来源缺失、039 的年代约束。
3. 为线上 WorldLine 的“共现不得推断合作”补回归和真正的关系校验。现有离线保护不能替代线上验收。
4. 稳定后才跑低成本语义 Judge 子集及完整确定性评测。当前数字不能宣称达到检索/精排/上下文质量或 P95/P99 生产验收目标。

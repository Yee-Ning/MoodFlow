# Gold V2：多年份索引投影 V3 对照（2026-09-16）

同一份 `rag_eval_gold_v2.ai_assisted_source_review.jsonl` 中 18 条已审批的 `answerable` 样本；同一 Query Understanding、Top40 检索、Qwen3 本地 Top12 精排配置，均关闭 Query Understanding 的 LLM fallback。唯一索引差异是投影 V3 为原始片段中多个明确提及的年份加入 `min_mentioned_year/max_mentioned_year` 粗粒度时间边界，时间过滤允许区间重叠。未改动 Gold 标签、语料、Dense/BM25 文本、融合或精排参数；未调用付费 Judge。

V2、V3 分别使用 `music_knowledge_v2_8608ffc2fa72e441_v1_v2_v2`、`music_knowledge_v2_8608ffc2fa72e441_v1_v2_v3`，两者各有 1,270 个点，V3 构建失败 0 个。V3 索引报告写入 release 下独立的 `v3/` 目录，没有覆盖 V2 冻结报告。原始 JSON 对照为 `gold_v2_retrieval_v2_20260916.json`、`gold_v2_retrieval_v3_20260916.json`、`gold_v2_retrieval_v2_rerank_20260916.json` 和 `gold_v2_retrieval_v3_rerank_20260916.json`。

| 指标（精确 Parent ID） | V2 | V3 | 差值 |
| --- | ---: | ---: | ---: |
| Top20 Recall | 52.41% | 55.65% | +3.24 pp |
| Top40 Recall | 59.81% | 63.06% | +3.24 pp |
| Top40 MRR@10 | 0.2071 | 0.2270 | +0.0198 |
| Top12 Recall | 27.78% | 32.87% | +5.09 pp |
| Top12 MRR@10 | 0.2618 | 0.2803 | +0.0185 |

Top40 增益来自 036（0.50→0.75）和 037（0.33→0.67）；两例提升传递到 Top12。其他样本的 Top40 Recall 无变化，包括 006、039 仍为 0。039 的 Gold Parent 原始 Child 确有 2009–2012 年提及，V2 时间字段为空，V3 则可通过时间过滤；但仍未进入 Top40，说明本例还存在相关性排序或候选池截断问题，不能只靠元数据修复。006 的 Gold Parent 只标注 `album:qi_li_xiang` 而查询计划硬过滤 `song:qi_li_xiang`，属于不同实体粒度，不在本次时间修复范围。冻结语料 1,270 个 Child 中有 395 个含年份提及而缺少原结构化过滤年份，故该问题不只是一条 Gold 样本。

只读单变量反事实确认：039 在 V3 上保留全部变体、移除纯别名变体、仅保留原问题三种方式，两个标注 Parent 均未进 Top40，因此不能把问题简单归因于别名扩展；此题 query 很宽泛，Gold 两个 Parent 分别是百科的荣誉和餐饮副业段落，部分原文带“需要较佳来源”，仍需独立审查事实质量与排名目标。006 的严格 `song:qi_li_xiang` 过滤仅返回 6 个 Parent；仅移除此过滤或仅改成 `album:qi_li_xiang` 时，标注官方 Parent 均排第 1。这证明了该例的实体标签粒度失配，但不等于可以全局将歌曲过滤改成同名专辑过滤：其他同名对象未必有相同的语义关系。

V3 的年份边界是**召回用粗筛**，若一个片段分别提及 2000 与 2020，也可能对中间年份形成假阳性；不能把边界当作事实证据。精排和证据抽取仍需判断具体年份与事件是否对应。039、006 和后续精排断点需要独立诊断，不做针对 Gold 的硬编码放宽。

本次精排耗时 V2 P50/P95/P99 为 1.62/2.82/4.55 秒，V3 为 2.07/5.51/6.98 秒；18 条本机顺序运行且存在模型预热/系统负载差异，**不能**据此归因 V3 造成线上延迟上升，也不代表生产 P95/P99。需要固定机器、预热和重复负载实验。Final Context 与答案生成未重新运行，不能据此声称端到端指标改善。

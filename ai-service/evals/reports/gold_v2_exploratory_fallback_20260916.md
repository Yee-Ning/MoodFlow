# Gold V2 稀疏实体过滤回退实验（2026-09-16）

固定 `rag_eval_gold_v2.ai_assisted_source_review.jsonl` 的 18 条已审批 `answerable` 查询，固定 V3 索引 `music_knowledge_v2_8608ffc2fa72e441_v1_v2_v3`、Query Understanding 纯规则模式、Top40 和本地 Qwen3 Top12。未改 Gold、未调用答案模型或付费 Judge。机器报告为同目录 `gold_v2_retrieval_v3_fallback_20260916.json` 和 `gold_v2_retrieval_v3_fallback_rerank_20260916.json`；基线见 `gold_v2_retrieval_v3_20260916.json`、`gold_v2_retrieval_v3_rerank_20260916.json`。

改动只有一项：探索类查询若只有一个实体 ID 硬过滤且严格检索结果少于 12 个 Parent，使用相同查询变体和已编码向量补查一次无实体过滤；原候选保留在前，只追加去重候选到 Top40。事实/关系/比较查询及含时间、语言等复合过滤的查询不触发。补查失败保留原结果并记录 warning。配置项 `selection.exploratory_entity_fallback_below=12`，设 0 可关闭。

| 精确 Parent 指标 | V3 基线 | V3 + 稀疏回退 | 变化 |
| --- | ---: | ---: | ---: |
| Top40 Recall | 63.06% | 68.61% | +5.56 pp |
| Top40 MRR@10 | 0.2270 | 0.2349 | +0.0079 |
| Top12 Recall | 32.87% | 38.43% | +5.56 pp |
| Top12 MRR@10 | 0.2803 | 0.3359 | +0.0556 |

确定性触发案例 006“歌曲《七里香》的故事”：严格 `song:qi_li_xiang` 过滤只有 6 个 Parent，官方原文的同名专辑 Parent 因索引实体粒度不同而被过滤；补查后它在 Top40 第 7、Top12 第 1。该来源原文明确提及“第一首主打歌－七里香”和歌曲 MV，因此是题目可用的相关上下文。039 仍为 Top40/Top12 零命中。其余逐例小幅排序变化可能受本地 Qdrant/模型运行波动影响，不归因于本策略。

本轮只证明了候选召回与精确 Parent 指标改善，**并未完成项目质量验收**。项目 `evaluation/config/v0.yaml` 明确尚未给检索/精排质量指标设正式阈值；不能靠事后选取 68.61%/38.43% 以下的线宣称“均达标”。Top12 仍偏低，且 Gold 相关 Parent 标签并非穷尽所有替代证据。补查多执行一次 Qdrant 请求，必须在真实负载下复测 P95/P99；Final Context Recall、Supporting Span Coverage 和答案质量未在本次回放中更新。下一步先审查 039 的宽泛时间线查询与六条 Top12 断点的原始证据，再确定精排和 Context 的合理质量门槛。

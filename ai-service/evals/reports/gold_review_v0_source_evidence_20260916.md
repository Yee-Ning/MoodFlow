# Gold V0 来源证据包生成记录

本次只完善人工审核材料；`rag_eval_gold_v0.pending.jsonl` 保持原样、30/30 条仍为 pending，**没有 finalize 或发布 Gold 指标**。

## 来源与方法

- 冻结知识 Release：`knowledge-v2-8608ffc2fa72e441`。
- 源 Parent 文件：`data/knowledge/v2/knowledge_enriched_chunks.jsonl`；生成前验证 Release manifest 记录的 SHA256 `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。
- 冻结 Parent 数量：677；每条 Query 从 Parent 原文独立词面筛出 5 条，来源最多 2 条/Source；比较题尽量各给一方候选。展示时按 Source/Parent ID 排列，不呈现内部筛选次序。
- 输入只包含待审 Gold 的 `case_id` 和 `query`、冻结 Parent 原文及 Source 元数据；不读取旧 Silver 数据、在线 Trace、Rerank 或答案。待审 `expected_outcome` 种子值只藏于初判后展开区，**不参与筛选**。
- 原文候选的 `suggested_supporting_span` 是 Parent 的精确子串，但**是否支持问题仍须人工判断**。不自动生成 required facts 或 reference answer，以免再次出现片段复制和机械拼接形成的伪标签。

## 产物与核对

- `gold_review_v0_blind_pack.md`：30 个 Query 的可读来源证据和逐字段 APPROVE/CORRECT/REJECT 审核位。
- `gold_review_v0_source_candidates.jsonl`：同一批候选的结构化审计副本，不含 outcome、相关性标签或在线排名/分数。
- 共 150 条候选、97 个不同 Parent；150 条 Source Document 本地路径存在。现有 Gold JSONL 运行前后 SHA256 均为 `371ec4356e4212e2f6d07e68d8bfdce4b8f16ede7f0a8953518de538e8392a1f`。

该方法是低成本的人工候选发现，不是新的检索算法验收。部分问题在当前语料中可能缺少直接证据，例如歌曲创作背景和跨歌手风格对比；候选只表示“值得检查”，不是正确答案。审核员可全部 REJECT、另选 Parent、或改判证据不足/需追问。只有独立人工审核结束后，才另行 finalize Gold 并跑确定性指标。

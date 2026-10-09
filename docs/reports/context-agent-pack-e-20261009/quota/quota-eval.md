# Agent Pack E 离线软配额评测

案例数：382。生产策略 SHA-256：`fc856d3be97c4a5d9d5b7ec7e92a7d451d58759856893652a41e7b242d11624e`。

所有建议仅供 West 决策；未修改生产阈值。冻结回放和合成压力场景分别计分。

| suite | owner / source | cases | 可用重要项保留率 | dropped 案例频率 | 重要项ID丢弃 / 配额误裁 | 当前 → 建议审查 token |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| frozen_context_item_replay | general_chat / history_memory | 30 | 100.0% | 0.0% | 0 / 0 | 600 → 600 |
| frozen_memory_source_replay | general_chat / history_memory | 130 | 100.0% | 0.0% | 0 / 0 | 600 → 600 |
| frozen_memory_source_replay | general_chat / user_profile | 130 | 100.0% | 100.0% | 0 / 0 | 240 → 240 |
| frozen_rag_child_score_replay | explorer / rag | 50 | 97.4% | 62.2% | 1 / 0 | 5000 → 5000 |
| synthetic_projection_pressure | curator / received_envelope | 7 | 81.8% | 33.3% | 14 / 14 | 1024 → 1536 |
| synthetic_projection_pressure | curator / session_view | 7 | 100.0% | 0.0% | 0 / 0 | 600 → 600 |
| synthetic_projection_pressure | explorer / received_envelope | 7 | 92.2% | 33.3% | 6 / 6 | 1280 → 1536 |
| synthetic_projection_pressure | explorer / session_view | 7 | 100.0% | 0.0% | 0 / 0 | 600 → 600 |
| synthetic_projection_pressure | general_chat / received_envelope | 7 | 80.3% | 50.0% | 12 / 12 | 768 → 1024 |
| synthetic_projection_pressure | general_chat / session_view | 7 | 100.0% | 0.0% | 0 / 0 | 600 → 600 |

建议按固定样本可用重要项 token 需求的 P95 向上取整至 128；不因该样本缩小现值。候选上限暂维持。
合成压力建议不能直接用于生产调参；需补充真实业务分布。命中率不等于在线检索或回答质量。

## 指标与限制

可用重要项保留率的分母只包含进入 L1 的有标签候选；总标签捕获率、上游/候选窗口缺失及 supporting span 条件覆盖率另见 JSON。
dropped 频率以非空候选案例为分母；去重、intent、来源配额和整体 L1 原因分别保存。原始文本不写入评测结果。

- Source replay and synthetic projections are scored separately; no production-distribution claim.
- Archived RAG scores are restricted to the first 24 valid retriever candidates, then original top-k; no model calls.
- Gold absent before L1 is reported separately from important items dropped by Pack selection.
- Span recall is conditional on exact supporting text already present in the Child; no Parent expansion.
- Important identity dedup is separate from quota drops; text retained under another ID is counted separately.
- Profile/history excerpted important items are counted; absent span labels cannot establish semantic loss.
- Single-source replay does not measure competition in a full business Pack or provider framing.
- Recommendations are review values only; quotas and production files remain unchanged.

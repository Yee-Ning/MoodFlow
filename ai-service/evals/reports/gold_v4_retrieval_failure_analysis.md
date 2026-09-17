# Gold V4：固定 historical Trace 的 Retrieval Failure Analysis

- 只分析已有 fallback 与 nav_context6 Trace，不重新检索或改动检索链路。评分单位是 V4 evaluator 的 supported evidence unit；旧 case 未标 claim 的 span 保持原有回退语义。
- 50 条 Gold 中新增 049–068 没有 historical Trace，作为 `NO_HISTORICAL_TRACE` 单列，**不计为任何检索失败**。本轮无法判定这些新增 case 的真实失败类别，也无法给出 50-case 全量指标。
- `RETRIEVER_RANKING_MISS` 需要 Top40 以外的候选与名次；现有 Trace 不具备，因此此类一律标记 `insufficient_trace_for_ranking_diagnosis`，不猜测。
- Top12 非 Gold 文档仅列出共现项，不能从 Trace 证明其“挤出”因果；context 逐项删除规则与 reranker score 均未记录。

## fallback

支持证据单位：全 Gold 95，本 Trace 可评 68，未覆盖 27。

| 漏斗阶段 | 数量 |
| --- | ---: |
| Top40 hit / miss | 43 / 25 |
| Top12 hit / 从 Top40 丢失 | 27 / 16 |
| Final hit / Context drop | 15 / 12 |

Retriever / Reranker / Context 生存率：63.24% / 62.79% / 55.56%。

| Failure category | Count |
| --- | ---: |
| RETRIEVER_MISS | 25 |
| RERANK_MISS | 16 |
| CONTEXT_DROP | 12 |
| SUCCESS | 15 |

Retriever miss 根因子类型（可证实）：{'unknown': 25}。`unknown` 表示该 Trace 无法证明语义、实体、时间、词法、chunk 或 filter 中的哪一项是根因。
查询表述层面的**非排他性、未验证假设**：{'query_too_broad': 15, 'entity_mismatch': 2, 'query_too_vague': 7}；其余为无足够线索。按证据单位计数，同一 query 的多个 claim 会重复计入；这不是根因发生率。每条依据见 JSONL。

- fully_answerable：units 47；Top40 30；Top12 17；Final 10；miss/rerank drop/context drop = 17/13/7。
- partially_answerable：units 21；Top40 13；Top12 10；Final 5；miss/rerank drop/context drop = 8/3/5。

### 重点 case

- `rag-v0-003`：3 units；{'RETRIEVER_MISS': 2, 'CONTEXT_DROP': 1}。
  - `rag-v0-003/claim:1` 杰威尔简介称形成所谓“周杰伦现象”；Gold `parent-abd13550e82163b9f04dd8c1`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-35487c91c9077427f4346634 (#13)。
  - `rag-v0-003/claim:2` 研究将中国风术语活跃与《东风破》发布相联系；Gold `parent-bb9810b50e07fbcbb9d00c66`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-690d69d26909699761f868c6 (#1)。
  - `rag-v0-003/claim:3` 英文研究认为其风格被其他艺人模仿；Gold `parent-99cbdb202deb91c31a873be5`；CONTEXT_DROP；Retriever rank 4 / Rerank rank 9；最近 Top40（词面启发式）N/A。
- `rag-v0-034`：4 units；{'CONTEXT_DROP': 2, 'RERANK_MISS': 2}。
  - `rag-v0-034/claim:1` 《东风破》的乐器与中国风特征；Gold `parent-17747bf81bf90411ed3b8a4f`；CONTEXT_DROP；Retriever rank 12 / Rerank rank 5；最近 Top40（词面启发式）N/A。
  - `rag-v0-034/claim:2` 《东风破》歌名来源；Gold `parent-17747bf81bf90411ed3b8a4f`；CONTEXT_DROP；Retriever rank 12 / Rerank rank 5；最近 Top40（词面启发式）N/A。
  - `rag-v0-034/claim:3` 方文山对青花瓷传统文化识别度的评价；Gold `parent-afca7c2d173dc002cca86aef`；RERANK_MISS；Retriever rank 25 / Rerank rank None；最近 Top40（词面启发式）N/A。
  - `rag-v0-034/claim:4` 方文山将《青花瓷》列入中国风作品；Gold `parent-afca7c2d173dc002cca86aef`；RERANK_MISS；Retriever rank 25 / Rerank rank None；最近 Top40（词面启发式）N/A。
- `rag-v0-036`：5 units；{'SUCCESS': 1, 'RETRIEVER_MISS': 3, 'RERANK_MISS': 1}。
  - `rag-v0-036/claim:1` 来源称2000年《Jay》融合多种曲风；Gold `parent-cca31e09c6d05f09267862a1`；SUCCESS；Retriever rank 1 / Rerank rank 2；最近 Top40（词面启发式）N/A。
  - `rag-v0-036/claim:2` 《东风破》收于《叶惠美》；Gold `parent-17747bf81bf90411ed3b8a4f`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-bb9810b50e07fbcbb9d00c66 (#10)。
  - `rag-v0-036/claim:3` 《东风破》的单曲乐器特征；Gold `parent-17747bf81bf90411ed3b8a4f`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-bb9810b50e07fbcbb9d00c66 (#10)。
  - `rag-v0-036/claim:4` 《叶惠美》于2003年发行的时间锚点；Gold `parent-81d3ee5f0e44759bf67d8c7f`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-560250da85f5f81669978790 (#18)。
  - `rag-v0-036/claim:5` 《11月的萧邦》于2005年发行的时间锚点；Gold `parent-738563d68235b4f923c1ac79`；RERANK_MISS；Retriever rank 32 / Rerank rank None；最近 Top40（词面启发式）N/A。
- `rag-v0-039`：2 units；{'RETRIEVER_MISS': 2}。
  - `rag-v0-039/claim:1` 来源记载的2011年商业活动；Gold `parent-17f46282a4d28c6c000e6aff`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-8d59ed7af6579daa7f5d43a8 (#1)。
  - `rag-v0-039/claim:2` 来源记载的2012年商业活动；Gold `parent-17f46282a4d28c6c000e6aff`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-f920c4f8d6dcc713eb4888a7 (#6)。

### 按可观察失败数排列的后续排查优先级

- `rag-v0-040`：7 units，{'CONTEXT_DROP': 1, 'RETRIEVER_MISS': 4, 'RERANK_MISS': 2}。这只是调查优先级，不是优化方案。
- `rag-v0-002`：6 units，{'RETRIEVER_MISS': 1, 'RERANK_MISS': 4, 'CONTEXT_DROP': 1}。这只是调查优先级，不是优化方案。
- `rag-v0-001`：5 units，{'RETRIEVER_MISS': 4, 'CONTEXT_DROP': 1}。这只是调查优先级，不是优化方案。
- `rag-v0-024`：4 units，{'RERANK_MISS': 1, 'RETRIEVER_MISS': 3}。这只是调查优先级，不是优化方案。
- `rag-v0-034`：4 units，{'CONTEXT_DROP': 2, 'RERANK_MISS': 2}。这只是调查优先级，不是优化方案。
- `rag-v0-036`：4 units，{'RETRIEVER_MISS': 3, 'RERANK_MISS': 1}。这只是调查优先级，不是优化方案。
- `rag-v0-003`：3 units，{'RETRIEVER_MISS': 2, 'CONTEXT_DROP': 1}。这只是调查优先级，不是优化方案。
- `rag-v0-004`：3 units，{'RETRIEVER_MISS': 1, 'RERANK_MISS': 2}。这只是调查优先级，不是优化方案。
- `rag-v0-044`：3 units，{'CONTEXT_DROP': 2, 'RETRIEVER_MISS': 1}。这只是调查优先级，不是优化方案。
- `rag-v0-005`：2 units，{'CONTEXT_DROP': 2}。这只是调查优先级，不是优化方案。
- `rag-v0-037`：2 units，{'RETRIEVER_MISS': 1, 'CONTEXT_DROP': 1}。这只是调查优先级，不是优化方案。
- `rag-v0-039`：2 units，{'RETRIEVER_MISS': 2}。这只是调查优先级，不是优化方案。

## nav_context6

支持证据单位：全 Gold 95，本 Trace 可评 68，未覆盖 27。

| 漏斗阶段 | 数量 |
| --- | ---: |
| Top40 hit / miss | 43 / 25 |
| Top12 hit / 从 Top40 丢失 | 27 / 16 |
| Final hit / Context drop | 24 / 3 |

Retriever / Reranker / Context 生存率：63.24% / 62.79% / 88.89%。

| Failure category | Count |
| --- | ---: |
| RETRIEVER_MISS | 25 |
| RERANK_MISS | 16 |
| CONTEXT_DROP | 3 |
| SUCCESS | 24 |

Retriever miss 根因子类型（可证实）：{'unknown': 25}。`unknown` 表示该 Trace 无法证明语义、实体、时间、词法、chunk 或 filter 中的哪一项是根因。
查询表述层面的**非排他性、未验证假设**：{'query_too_broad': 15, 'entity_mismatch': 2, 'query_too_vague': 7}；其余为无足够线索。按证据单位计数，同一 query 的多个 claim 会重复计入；这不是根因发生率。每条依据见 JSONL。

- fully_answerable：units 47；Top40 30；Top12 17；Final 14；miss/rerank drop/context drop = 17/13/3。
- partially_answerable：units 21；Top40 13；Top12 10；Final 10；miss/rerank drop/context drop = 8/3/0。

### 重点 case

- `rag-v0-003`：3 units；{'RETRIEVER_MISS': 2, 'SUCCESS': 1}。
  - `rag-v0-003/claim:1` 杰威尔简介称形成所谓“周杰伦现象”；Gold `parent-abd13550e82163b9f04dd8c1`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-35487c91c9077427f4346634 (#13)。
  - `rag-v0-003/claim:2` 研究将中国风术语活跃与《东风破》发布相联系；Gold `parent-bb9810b50e07fbcbb9d00c66`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-690d69d26909699761f868c6 (#1)。
  - `rag-v0-003/claim:3` 英文研究认为其风格被其他艺人模仿；Gold `parent-99cbdb202deb91c31a873be5`；SUCCESS；Retriever rank 4 / Rerank rank 9；最近 Top40（词面启发式）N/A。
- `rag-v0-034`：4 units；{'SUCCESS': 2, 'RERANK_MISS': 2}。
  - `rag-v0-034/claim:1` 《东风破》的乐器与中国风特征；Gold `parent-17747bf81bf90411ed3b8a4f`；SUCCESS；Retriever rank 12 / Rerank rank 5；最近 Top40（词面启发式）N/A。
  - `rag-v0-034/claim:2` 《东风破》歌名来源；Gold `parent-17747bf81bf90411ed3b8a4f`；SUCCESS；Retriever rank 12 / Rerank rank 5；最近 Top40（词面启发式）N/A。
  - `rag-v0-034/claim:3` 方文山对青花瓷传统文化识别度的评价；Gold `parent-afca7c2d173dc002cca86aef`；RERANK_MISS；Retriever rank 25 / Rerank rank None；最近 Top40（词面启发式）N/A。
  - `rag-v0-034/claim:4` 方文山将《青花瓷》列入中国风作品；Gold `parent-afca7c2d173dc002cca86aef`；RERANK_MISS；Retriever rank 25 / Rerank rank None；最近 Top40（词面启发式）N/A。
- `rag-v0-036`：5 units；{'SUCCESS': 1, 'RETRIEVER_MISS': 3, 'RERANK_MISS': 1}。
  - `rag-v0-036/claim:1` 来源称2000年《Jay》融合多种曲风；Gold `parent-cca31e09c6d05f09267862a1`；SUCCESS；Retriever rank 1 / Rerank rank 2；最近 Top40（词面启发式）N/A。
  - `rag-v0-036/claim:2` 《东风破》收于《叶惠美》；Gold `parent-17747bf81bf90411ed3b8a4f`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-bb9810b50e07fbcbb9d00c66 (#9)。
  - `rag-v0-036/claim:3` 《东风破》的单曲乐器特征；Gold `parent-17747bf81bf90411ed3b8a4f`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-bb9810b50e07fbcbb9d00c66 (#9)。
  - `rag-v0-036/claim:4` 《叶惠美》于2003年发行的时间锚点；Gold `parent-81d3ee5f0e44759bf67d8c7f`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-560250da85f5f81669978790 (#18)。
  - `rag-v0-036/claim:5` 《11月的萧邦》于2005年发行的时间锚点；Gold `parent-738563d68235b4f923c1ac79`；RERANK_MISS；Retriever rank 11 / Rerank rank None；最近 Top40（词面启发式）N/A。
- `rag-v0-039`：2 units；{'RETRIEVER_MISS': 2}。
  - `rag-v0-039/claim:1` 来源记载的2011年商业活动；Gold `parent-17f46282a4d28c6c000e6aff`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-8d59ed7af6579daa7f5d43a8 (#1)。
  - `rag-v0-039/claim:2` 来源记载的2012年商业活动；Gold `parent-17f46282a4d28c6c000e6aff`；RETRIEVER_MISS；Retriever rank None / Rerank rank None；最近 Top40（词面启发式）parent-f920c4f8d6dcc713eb4888a7 (#6)。

### 按可观察失败数排列的后续排查优先级

- `rag-v0-040`：7 units，{'CONTEXT_DROP': 1, 'RETRIEVER_MISS': 4, 'RERANK_MISS': 2}。这只是调查优先级，不是优化方案。
- `rag-v0-002`：5 units，{'RETRIEVER_MISS': 1, 'RERANK_MISS': 4}。这只是调查优先级，不是优化方案。
- `rag-v0-001`：4 units，{'RETRIEVER_MISS': 4}。这只是调查优先级，不是优化方案。
- `rag-v0-024`：4 units，{'RERANK_MISS': 1, 'RETRIEVER_MISS': 3}。这只是调查优先级，不是优化方案。
- `rag-v0-036`：4 units，{'RETRIEVER_MISS': 3, 'RERANK_MISS': 1}。这只是调查优先级，不是优化方案。
- `rag-v0-004`：3 units，{'RETRIEVER_MISS': 1, 'RERANK_MISS': 2}。这只是调查优先级，不是优化方案。
- `rag-v0-044`：3 units，{'CONTEXT_DROP': 2, 'RETRIEVER_MISS': 1}。这只是调查优先级，不是优化方案。
- `rag-v0-003`：2 units，{'RETRIEVER_MISS': 2}。这只是调查优先级，不是优化方案。
- `rag-v0-034`：2 units，{'RERANK_MISS': 2}。这只是调查优先级，不是优化方案。
- `rag-v0-039`：2 units，{'RETRIEVER_MISS': 2}。这只是调查优先级，不是优化方案。
- `rag-v0-043`：2 units，{'RETRIEVER_MISS': 1, 'RERANK_MISS': 1}。这只是调查优先级，不是优化方案。
- `rag-v0-007`：1 units，{'RERANK_MISS': 1}。这只是调查优先级，不是优化方案。

## 解释边界

- 最接近 Top40 是 claim 字符 bigram 重合度启发式，供人工定位，不等同于真实相关性、检索分数或缺失 Gold 的深层 rank。
- `failure_reason=unknown` 不是诊断结论；需要更深候选、索引过滤日志、reranker 分数和 Context Builder 决策日志才能做因果细分。本轮不新增这些 Trace，也不修改 Retrieval。
- 全部逐 unit 明细：`evals/reports/gold_v4_retrieval_failure_analysis.jsonl`，含 Gold span/source、三个阶段命中、rank、近似项及不可判定原因。

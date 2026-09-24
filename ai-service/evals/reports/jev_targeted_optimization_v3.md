# Jev Targeted Optimization v3

## Evaluation contract

- `jev_targeted_v3_splits.jsonl` 在参数搜索前冻结：Router 分任务分层，Memory 按 target ID 奇偶，Context 每个 family 按编号奇偶。所有阈值只在 dev 校准，下面的最终指标只来自 test。Router test 为 16 单意图、7 多意图、1 追问分析；Memory test 为 49 个独立目标；Context test 为 15 个场景。样本仍小，尤其 Router 多意图。
- Jev 请求模型为 `jev-latest`；实际 response model 和 UTC 时间分别见下文及逐 case JSONL。保持 `JEV_SHADOW_MODE=true`。Router 多标签与 Context 阈值策略为 Shadow 评测，未接管生产路由或上下文；Memory Retrieval 的轻量排序已改动生产检索器，但仍走 PostgreSQL profile + pgvector episodic，没有改写入流程或数据库结构。
- 延迟均是同一台本机上真实调用的 wall clock 测量，单位 ms；表中顺序为 avg / P50 / P95 / P99。Router Baseline 可能调用主 LLM fallback，Jev 只进行一次 System One API；下游主 LLM 生成延迟不在本轮。

## 1. Router：统一单/多意图 Jev Shadow

旧 single Choice 最多表达一个 route。v3 用一个 shared state `{query}`，在同一次 System One 请求中对四个 RouteKey 同时请求 Noul；每个分数独立过阈值，允许 0/1/2/3 个 route，没有固定 Top-2。dev 在共同阈值、general_chat 增量阈值和 max_routes=2/3/4 的网格上优化单意图 accuracy 与多意图 micro F1 的均值，不接触 test。
作为设计参照，v2 single Choice 在同一组 16 条单意图 test case 上为 13/16，决策 avg/P50/P95/P99 657.9 / 631.4 / 931.1 / 931.1 ms；其 API 时间 2026-09-23T23:56:55.378516+00:00、实际模型 ['jev-1.13.0']。这属于不同时间的旧请求形式，不能当作本次实时 A/B 的低延迟结论；下表主比较为同轮 Existing vs Multi-label Jev。
冻结策略：threshold=0.55，general_chat 加 0.10，max_routes=3；四路阈值 {'current_song': 0.55, 'tool_calling': 0.55, 'worldline': 0.55, 'general_chat': 0.65}。test 每条 Jev request count 分布 {1: 24}。requested_model=jev-latest，response_model=['jev-1.13.0']，evaluation_timestamp=2026-09-24T00:40:33.668629+00:00。

| Router test 指标 | Existing Hybrid | Jev Multi-label |
| --- | ---: | ---: |
| 单意图 accuracy | 11/16 (68.8%) | 7/16 (43.8%) |
| 多意图 exact set | 4/7 | 1/7 |
| 多意图 micro precision | 100.0% | 81.8% |
| 多意图 micro recall | 64.3% | 64.3% |
| 多意图 micro F1 / macro F1 | 78.3% / 66.7% | 72.0% / 65.7% |
| 多意图 false positive route / missed route | 0 / 5 | 2 / 5 |
| 全量 fallback need（含追问） | 8/24 (33.3%) | 5/24 (20.8%) |
| 调用错误率（含追问） | 0/24 | 0/24 |
| 单+多意图 decision failure rate | 34.8% | 65.2% |

| Router 延迟，ms avg/P50/P95/P99 | 数值 |
| --- | --- |
| Existing total | 1691.4 / 1558.0 / 3633.7 / 4080.4 |
| Existing rule | 0.2 / 0.2 / 0.3 / 0.4 |
| Existing model | 21.8 / 15.5 / 36.6 / 121.0 |
| Existing LLM | 1668.5 / 1533.5 / 3607.5 / 4053.2 |
| Jev API | 555.3 / 532.8 / 613.2 / 886.2 |
| Jev total | 555.4 / 532.9 / 613.4 / 886.4 |

分层计时是各层在同一请求内的累计执行时间；多 segment 与调度开销使其不能直接相加为 total。两侧在相同 Router test query 上串行执行。
Jev total P95/P99 为 613.4 / 886.4 ms，Existing 为 3633.7 / 4080.4 ms，分别缩短 83.1% 和 78.3%；但冻结 test 的单意图 accuracy 和多意图 F1 均低于 Existing，目前**不值得替换三层 Router**。不能为了低延迟牺牲当前路由质量。
Jev test 错误 case 共 15，逐项 gold/Existing/Jev/四路分数如下：

| Case | 输入 | 类型 | Gold | Existing | Jev | 分数 | 原因 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| manual-hard-019 | 先别收藏,我想知道如果这首歌没发行会怎样 | single_intent | ['worldline'] | [] | [] | {'current_song': 0.42, 'tool_calling': 0.03, 'worldline': 0.52, 'general_chat': 0.51} | missing=['worldline']; extra=[] |
| manual-hard-027 | 如果这首歌当年没有发行,今天的华语乐坛会不会不一样 | single_intent | ['worldline'] | ['worldline'] | ['general_chat'] | {'current_song': 0.17, 'tool_calling': 0.01, 'worldline': 0.27, 'general_chat': 0.86} | missing=['worldline']; extra=['general_chat'] |
| manual-multi-015 | 先暂停,再聊聊为什么人会反复听同一首歌 | multi_intent | ['general_chat', 'tool_calling'] | [] | ['general_chat'] | {'current_song': 0.05, 'tool_calling': 0.12, 'worldline': 0.34, 'general_chat': 0.79} | missing=['tool_calling']; extra=[] |
| manual-multi-018 | 收藏这首歌,再跟我说说什么叫概念专辑 | multi_intent | ['general_chat', 'tool_calling'] | ['general_chat', 'tool_calling'] | ['general_chat', 'tool_calling', 'worldline'] | {'current_song': 0.08, 'tool_calling': 0.94, 'worldline': 0.73, 'general_chat': 0.82} | missing=[]; extra=['worldline'] |
| manual-test-general-001 | 很高兴见到你 | single_intent | ['general_chat'] | ['general_chat'] | [] | {'current_song': 0.02, 'tool_calling': 0.01, 'worldline': 0.01, 'general_chat': 0.44} | missing=['general_chat']; extra=[] |
| manual-test-hard-001 | 如果音量太高请降低一点 | single_intent | ['tool_calling'] | ['tool_calling'] | [] | {'current_song': 0.02, 'tool_calling': 0.51, 'worldline': 0.02, 'general_chat': 0.03} | missing=['tool_calling']; extra=[] |
| manual-tool-007 | 音量调到百分之五十 | single_intent | ['tool_calling'] | ['tool_calling'] | [] | {'current_song': 0.02, 'tool_calling': 0.47, 'worldline': 0.01, 'general_chat': 0.02} | missing=['tool_calling']; extra=[] |
| manual-world-002 | 如果这首歌从来没有发行过会发生什么 | single_intent | ['worldline'] | ['worldline'] | ['general_chat'] | {'current_song': 0.22, 'tool_calling': 0.02, 'worldline': 0.23, 'general_chat': 0.65} | missing=['worldline']; extra=['general_chat'] |
| manual-world-007 | 假如这首歌当年没有爆火会怎么样 | single_intent | ['worldline'] | ['worldline'] | ['general_chat'] | {'current_song': 0.3, 'tool_calling': 0.02, 'worldline': 0.15, 'general_chat': 0.77} | missing=['worldline']; extra=['general_chat'] |
| manual-world-022 | 如果这个乐队提前十年出现会怎么样 | single_intent | ['worldline'] | ['worldline'] | ['general_chat'] | {'current_song': 0.06, 'tool_calling': 0.01, 'worldline': 0.23, 'general_chat': 0.84} | missing=['worldline']; extra=['general_chat'] |
| manual-hard-017 | 不用播放任何歌,跟我聊聊R&B是什么 | single_intent | ['general_chat'] | [] | ['general_chat', 'worldline'] | {'current_song': 0.02, 'tool_calling': 0.03, 'worldline': 0.7, 'general_chat': 0.96} | missing=[]; extra=['worldline'] |
| manual-multi-002 | 暂停一下,然后告诉我这首歌歌词什么意思 | multi_intent | ['current_song', 'tool_calling'] | ['current_song', 'tool_calling'] | ['current_song'] | {'current_song': 0.85, 'tool_calling': 0.52, 'worldline': 0.13, 'general_chat': 0.14} | missing=['tool_calling']; extra=[] |
| manual-multi-009 | 如果这首歌从来没有发行过会怎样,然后把它收藏 | multi_intent | ['tool_calling', 'worldline'] | ['tool_calling', 'worldline'] | [] | {'current_song': 0.09, 'tool_calling': 0.34, 'worldline': 0.1, 'general_chat': 0.51} | missing=['tool_calling', 'worldline']; extra=[] |
| manual-multi-010 | 假如这个歌手没有出道会怎样,再跟我聊聊这种假设有什么意思 | multi_intent | ['general_chat', 'worldline'] | ['worldline'] | ['general_chat'] | {'current_song': 0.05, 'tool_calling': 0.01, 'worldline': 0.11, 'general_chat': 0.93} | missing=['worldline']; extra=[] |
| manual-multi-016 | 把音量调小一点,再跟我讲讲R&B和Soul有什么区别 | multi_intent | ['general_chat', 'tool_calling'] | ['general_chat', 'tool_calling'] | ['general_chat', 'tool_calling', 'worldline'] | {'current_song': 0.03, 'tool_calling': 0.86, 'worldline': 0.88, 'general_chat': 0.74} | missing=[]; extra=['worldline'] |

## 2. Memory Retrieval：先定位上游

v2 默认 Top-8 的 44/100 不是 Jev Filter 所致：旧 `_profile_relevance` 在没有 scene/intent 时给所有 profile 0.75；profile 与 episodic 混排后，800 个默认候选出现中 episodic 为 0。合成集每个用户有约 23 个 profile；仅按 importance/recency 截取和统一分数不能区分查询主题。
同一批 test 查询的原始 episodic cosine 诊断共有 12 个 episodic 目标；向量维度 [1024]，query 向量范数范围 1.0000–1.0000，document 1.0000–1.0000。两侧共用 Qwen 1024 维归一化编码（query 加 instruction，document 用原文）；PostgreSQL 索引为 `vector_cosine_ops`、查询排序为 `<=>` 升序。精确扫描、当前 planner 和强制 HNSW 的 Top-K 结果如下；49/49 条 test 原始排序完全一致。强制 HNSW 用 `hnsw.ef_search=100` 并禁用 seq/bitmap/sort 的会话设置，经 EXPLAIN 确认索引扫描；小样本未见 ANN 召回损失。

| episodic raw Recall@K | Exact cosine | Planner | Forced HNSW |
| --- | ---: | ---: | ---: |
| @3 | 12/12 | 12/12 | 12/12 |
| @5 | 12/12 | 12/12 | 12/12 |
| @10 | 12/12 | 12/12 | 12/12 |
| @20 | 12/12 | 12/12 | 12/12 |

优化：profile 用已有 structured subject/topic/scene 的精确命中与内容字符二元组重叠评分；明显询问旧经历的 query 给 episodic semantic 分数有限加成，仍保留 importance/recency、user/status/type/scene 过滤与原 Top-K。未把 Memory 搬到 Qdrant，未调 HNSW 或改 schema。冲突旧事实在 v2 合成数据中仍标为 active，这是数据真实性限制。

| Test Recall@K / MRR | 旧 Retrieval | 新 Retrieval |
| --- | ---: | ---: |
| @3 | 14/49 (28.6%); MRR 0.252 | 44/49 (89.8%); MRR 0.884 |
| @5 | 17/49 (34.7%); MRR 0.266 | 44/49 (89.8%); MRR 0.884 |
| @8 | 22/49 (44.9%); MRR 0.281 | 49/49 (100.0%); MRR 0.986 |
| @10 | 25/49 (51.0%); MRR 0.287 | 49/49 (100.0%); MRR 0.986 |
| @20 | 32/49 (65.3%); MRR 0.298 | 49/49 (100.0%); MRR 0.986 |

Top-8 平均候选数两边均为 8.0；旧检索 avg/P50/P95/P99 767.6 / 760.3 / 847.0 / 881.7 ms，新检索 752.3 / 746.6 / 807.1 / 927.1 ms。新策略 P95 略降、P99 从 881.7 升到 927.1 ms，不能声称尾延迟全面改善。dev 上新策略 Top-5=51/51，但冻结 test Top-5=44/49：漏掉的恰是 5 条 current conflict profile，`max(top_k*4,20)` 的 SQL profile 预取在 K=5 时只取前 20，目标根本未进入应用重排。当前不据 test 调预取参数，维持生产 Top-8；test Top-8=49/49。原始 SQL 的 exact/HNSW 诊断与最终 profile+episodic merge 不能混作同一个 Recall。

## 3. Jev Memory Filter：同候选集 Stage B

在新检索 Top-8 的 49 条 test 上，Baseline 全保留的 Key Memory Recall=100%、Precision=12.5%、False Keep=100%。dev 校准仍选择现有阈值 0.50，未修改 Jev instruction/threshold 配置。Jev test：Key Memory Recall 100.0%、Precision 64.5%、False Drop=0、False Keep 7.9%、Candidate Reduction 80.6%、fallback 0.0%。API latency avg/P50/P95/P99 589.0 / 557.3 / 747.0 / 1032.0 ms；Filter total 589.3 / 557.5 / 747.2 / 1032.3 ms。requested_model=jev-latest，response_model=['jev-1.13.0']，evaluation_timestamp=2026-09-24T01:54:34.270712+00:00。
旧 episodic-only 探针 `memory-v2-episodic_only_probe-120` 的牙医目标确实是该查询的 gold，旧分数 0.46 被阈值 0.5 删除；它不是 retrieval miss。v3 dev 的相同 query/候选 ID 得到 0.53，另对原始候选集连续三次重复得到 [0.49, 0.47, 0.49]，response_model 均为 jev-1.13.0。该项是 threshold 边界上的 Jev 分数漂移/误判，不是 Gold 或跨信息推理问题。dev 主集阈值 0.5 的 Precision 优于 0.45，但单条关键记忆存在重复误删；本轮不据已见 test 再调阈值，继续 Shadow。
冻结 test 的 Jev 关键误删 0 项。

## 4. Context：9 个 false drop 的定向审计

v2 的 9 项全部是 `tool_result`：6 项 `source-index` 仅报告“已找到来源索引”，没有实际 ID、URL 或证据，原 useful gold 过宽，审计后改为 irrelevant；3 项 `version` 明确指出录音室/现场版，对用户任务有用，保留 useful。不是 RAG relevance 与 Context usefulness 的等价错误，也没有 required 被删除。逐项审计：

| Case | 分组 | Item | 旧/新 Gold | Jev 分数 | 分类 |
| --- | --- | --- | --- | ---: | --- |
| context-v2-rag-01 | dev | tool_result:source-index | useful/irrelevant | 0.33 | gold_label_unreasonable |
| context-v2-rag-02 | test | tool_result:source-index | useful/irrelevant | 0.34 | gold_label_unreasonable |
| context-v2-rag-03 | dev | tool_result:source-index | useful/irrelevant | 0.37 | gold_label_unreasonable |
| context-v2-rag-04 | test | tool_result:source-index | useful/irrelevant | 0.39 | gold_label_unreasonable |
| context-v2-rag-05 | dev | tool_result:source-index | useful/irrelevant | 0.40 | gold_label_unreasonable |
| context-v2-rag-06 | test | tool_result:source-index | useful/irrelevant | 0.42 | gold_label_unreasonable |
| context-v2-mixed-03 | dev | tool_result:version | useful/useful | 0.47 | uniform_threshold_too_high_for_tool_result |
| context-v2-mixed-04 | test | tool_result:version | useful/useful | 0.46 | uniform_threshold_too_high_for_tool_result |
| context-v2-mixed-06 | test | tool_result:version | useful/useful | 0.38 | uniform_threshold_too_high_for_tool_result |

dev 仅调整 `tool_result` 阈值并冻结为 0.45，其他 source 仍 0.50；required 仍直接 bypass Jev。以下 test 数字按审计后的同一 Gold、同一 v2 Jev API 分数确定性重放，未再次调用 API；API 时间为 2026-09-24T00:02:03.710378+00:00，策略重放时间为 2026-09-24T01:55:44.523763+00:00，response_model=['jev-1.13.0']。

| Context test | 原 Jev threshold 0.50 | tool_result threshold 调整后 |
| --- | ---: | ---: |
| Required Preservation | 100.0% | 100.0% |
| Useful Recall | 93.9% | 97.0% |
| Irrelevant Retention | 5.9% | 5.9% |
| False Drop | 2 | 1 |
| Token Reduction | 38.4% | 37.6% |
| Jev API latency ms avg/P50/P95/P99 | 631.0 / 618.8 / 742.2 / 742.2 | 631.0 / 618.8 / 742.2 / 742.2 |
| Jev total latency ms avg/P50/P95/P99 | 631.3 / 619.1 / 742.5 / 742.5 | 631.3 / 619.1 / 742.6 / 742.6 |
| Existing Select latency ms avg/P50/P95/P99 | 0.6 / 0.6 / 1.4 / 1.4 | 0.6 / 0.6 / 1.4 / 1.4 |

调整后仍误删 `context-v2-mixed-06` 的 `tool_result:version`（Jev 分数 0.38）；该项说明 0.45 的 dev 最优阈值尚不能安全覆盖所有版本消歧结果。不能根据这个 test case 再降低阈值并重算同一 test。
Context 调整仅在 Shadow 评测重放，生产 `Context Select` 阈值配置未改变；下一步是否写入生产配置取决于更广的人工 Gold 与稳定复评。

## 5. Decision Matrix

| 模块 | Quality | Performance | 判断 |
| --- | --- | --- | --- |
| Router | 单意图 43.8% vs 68.8%；多意图 F1 72.0% vs 78.3% | P95/P99 Jev 613.4 / 886.4 vs Existing 3633.7 / 4080.4 ms | 继续 Shadow；当前不值得替换三层 Router |
| Memory Retrieval | Top-8 49/49 vs 22/49，已达本合成 test 100% | P95/P99 新 807.1 / 927.1 vs 旧 847.0 / 881.7 ms | 本数据集可接受；P99 略增，真实用户长历史待验证 |
| Memory Filter | Key Recall 100.0%，false drop 0 | 额外 P95/P99 747.2 / 1032.3 ms | 继续 Shadow；边界分数漂移和付费延迟仍需观察 |
| Context | Required 100.0%，Useful 97.0% | 额外 API P95/P99 742.2 / 742.2 ms；token 降 37.6% | 继续 Shadow；仍有 1 项 useful 误删 |

## 限制与复现

- 全部 Memory 目标与部分 Context 场景为合成/规则 Gold，缺少真实长期用户历史和双人独立复核。v2 的 conflict former memory 被合成脚本标成 active；真实 superseded/inactive 过滤仍由现有存储合同保证，本轮未改写入流程。
- Memory 的 exact/HNSW 实验只有每用户约 10 条 episodic、整个表 30 条合成 episodic；它排除了此次样本的 ANN 根因，不能证明大规模索引完全没有 ANN 误差。检索端 profile SQL 预截断在更大用户历史上仍有风险。Memory 按目标 ID 奇偶拆分让 conflict 的 former/episodic 都落在 dev、current/profile 都落在 test，造成分布偏移；本轮保留已冻结 split 并如实报告，不再重新划分追求更好数字。
- Router test 仅 7 条多意图，阈值基于 15 单/7 多 dev 校准；2 条追问路径仍未消费 recent_context。v3 仍复用 v2 案例，Router Noul 文案受到 v2 总体失效现象启发；冻结 test 只用于本轮参数选择后的报告，不能视为从未见过的外部基准。Context 阈值重放沿用 v2 一次 API score，不代表重新请求时分数完全一致。
- 完整原始结果、分组、dev 校准和审计 JSONL 均在 `evals/datasets` 与 `evals/reports`；各组件独立判断，没有合成 Jev 总分。

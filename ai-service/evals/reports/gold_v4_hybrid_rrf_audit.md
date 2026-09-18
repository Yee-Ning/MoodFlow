# Gold V4 Hybrid Retrieval / weighted RRF Audit

## 边界与结果口径

本轮只读：固定 Gold V4、冻结语料、index/projection、历史 Query Planner legs、embedding revision、SAFE_COMBINED_V2_CAP_5、Dense/BM25、RRF、每 leg 600 Child 深度诊断预算、Top100 Parent、production document/source cap（broad=4、default=2、source=5）。未修改 production config、reranker 或 Context Builder。

**口径区分：**线上 `HybridRetrievalService.retrieve` 按原始 `candidate_top_k=40` 计算 fused Child limit=80、per-leg limit=80，再选 Top40 Parent；本系列 Gold V4 @100 实验使用每 leg 600 Child、未在 Parent 选择前截断 fused Child、再选 Top100 Parent。因此下表是固定 V2 的深度诊断 Recall，不是线上 Top40 的实测指标。

- Gold SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；corpus：`8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`；projection：`663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`；manifest：`0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。
- Retrieval config SHA-256：`b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`；Planner config：`afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77`；historical plan archive：`dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`。
- V2 report SHA-256：`69ab5feea651e9cd693e797d21c7c4dacfd99d371fb6838cad999c466ba74c06`；document-cap report：`4cf58e5847092723fb41be8bae986480508e2070e8ede4eb4c0554af0d73803e`。

## A. 真实执行顺序与 RRF 实现

1. Query Planner 先产出 `SearchPlan`；线上 `HybridRetrievalService.retrieve` 接收它，本轮 audit 则读取历史归档的同一 plan，不重新规划。`dense_variant_indexes` 只编码 Dense/BOTH query；`build_retrieval_legs` 把每个 query variant 展开成 Dense、BM25 或两个 leg。每个有效 leg 的权重是 `query_variant.weight × fusion.{dense|lexical}_weight`。
2. `compile_qdrant_filter` 编译 metadata 过滤；Qdrant 对每 leg 执行 Dense vector 或 BM25 Document 查询并按返回次序形成 raw Child hits。本轮 V2 fallback 在只读实验层以 exact-first 构造候选，不改变历史 query text、embedding 或 Qdrant 索引。
3. `weighted_rrf` 对每个 leg 的 Child `point_id` 从 rank **1** 起算；同一 leg 重复 point 只计首次，不同 leg 的同一 point 各计一次。公式 `ChildScore(p)=Σ_leg [variant_weight × channel_weight /(60+rank_leg(p))]`。无结果或失败 leg 不贡献分数；不补零也不惩罚。Child 排序键为 `(-score, point_id)`，score 完全相等时按 point_id 决定次序。
4. 线上随后先按 `fused_limit` 截断 Child；本轮 @100 深度诊断不做该步。`select_candidates` 按 post-exclusion、timeline navigation、payload 有效性过滤，再按 Parent ID 去重。Parent score 是同 Parent **最高分 Child** 的 score；其他 Child 的 score 不累加，只合并 matched_child_ids 与 channel evidence。Parent 排序键为 `(-parent_score, winning_point_id)`。
5. 最后按 query type 应用 document cap（broad=4，否则=2）和 source cap=5 选 Parent；未满目标时按原 ranked 顺序从 overflow 回填。诊断目标是 Top100；线上目标来自 `candidate_top_k`，历史计划为 40。
6. 线上服务另有条件分支：若 exploratory 查询仅有单一 entity filter，且初次候选少于阈值 12，会执行一次无过滤检索并追加去重 Parent 直到目标数。本轮固定的 `SAFE_COMBINED_V2_CAP_5` 是**实验候选 filter policy**，在只读 runner 中构造；没有把它接入 production，也没有把线上该条件分支误当作 V2 的结果。

实码位置：`app/rag/retrieval/service.py`（leg 构造、Qdrant 查询、fused Child 截断、条件回退）；`app/rag/retrieval/legs.py`（variant/channel 权重）；`app/rag/retrieval/qdrant.py`（Dense/BM25 request 与 per-leg raw hits）；`app/rag/retrieval/fusion.py`（Child RRF）；`app/rag/retrieval/selection.py`（Parent 去重、document/source cap、overflow）。

### Rank 命名

`raw Child rank` 是单个 Qdrant leg 的返回名次；`fused Child rank` 是 RRF 后的 point 名次；`distinct Parent rank pre-selection` 是 Child 聚合/去重之后、cap 之前名次；`selected Parent rank` 是 document/source cap 和 overflow 之后名次。线上 fused Child limit=80 又是另一截断口径。

## B. 独立 score / rank 重建

Plain-Python audit 独立重算全部 39 case、三个通道的 47448 个 Child ID/score/rank 和 27248 个 Parent ID/max-Child-score/rank；最大绝对 score 差 0，全部与 production `weighted_rrf` 和 Parent collapse 一致。重建复用 production 的 payload 有效性判定，Child RRF 算术及 Parent max-Child score/排序则独立实现。固定同一批 raw results 再重建两轮，共 234 次通道 replay，Child/Parent 顺序和分数完全一致。39/39 case × 3 通道的 selected Top100 顺序也与上一轮 V2 JSONL 一致。

固定随机种子 `20260918` 从每 case 保存的重建样本中抽取 10 个 Child；下表 score 由逐 leg `weight/(60+rank)` 求和，且与 production score 相等。各样本的原始 rank、weight 和单项贡献保存在 JSONL 的 `sample_child_score_reconstructions`。

| case | Child point_id | fused Child rank | legs | 手工求和 | production score | 差值 |
|---|---|---:|---:|---:|---:|---:|
| 031 | `6c0cd387-6fea-51c0-ab4b-6691b7b581a9` | 276 | 2 | 0.013138686131 | 0.013138686131 | 0 |
| 057 | `5be6adac-d89e-532e-aca2-19d092b1d121` | 37 | 3 | 0.021260771261 | 0.021260771261 | 0 |
| 005 | `3fef64ae-cf94-5a7f-b355-1e5114d415f0` | 12 | 4 | 0.050899912204 | 0.050899912204 | 0 |
| 002 | `607e4a71-e88e-57de-b46c-7c9ca9778baa` | 661 | 2 | 0.004876170162 | 0.004876170162 | 0 |
| 058 | `8855684e-811e-5a3b-aeac-e8b00b35ec56` | 507 | 2 | 0.004645191410 | 0.004645191410 | 0 |
| 043 | `59d3d453-dc0a-557c-9764-b8182524e17a` | 71 | 4 | 0.028743683650 | 0.028743683650 | 0 |
| 062 | `03e17269-3478-58f2-9a64-792d3d420c6d` | 435 | 1 | 0.005494505495 | 0.005494505495 | 0 |
| 063 | `9e635b77-b5ee-5ee2-a536-a7d7e8a86f7a` | 6 | 3 | 0.042940626461 | 0.042940626461 | 0 |
| 065 | `88b997f4-6916-5aa1-95ff-1ea3c771dd22` | 144 | 4 | 0.020218784912 | 0.020218784912 | 0 |
| 040 | `9fbb1250-fdfa-507e-8c11-5eebc421c723` | 572 | 1 | 0.005629139073 | 0.005629139073 | 0 |

## C. Dense / BM25 / Hybrid cutoff 对比

Macro 对 39 case 等权；micro 为 evidence-unit hit / 95。hit 使用 Gold V4 现有 group-aware 匹配。

| cutoff | Dense macro | Dense micro | BM25 macro | BM25 micro | Hybrid macro | Hybrid micro |
|---:|---:|---:|---:|---:|---:|---:|
| @10 | 62.94% | 51/95 | 54.30% | 43/95 | 61.72% | 48/95 |
| @20 | 73.18% | 63/95 | 59.67% | 49/95 | 75.20% | 65/95 |
| @40 | 80.15% | 73/95 | 67.33% | 57/95 | 79.41% | 70/95 |
| @100 | 85.82% | 79/95 | 83.13% | 77/95 | 89.29% | 85/95 |

## D. Promotion / demotion（evidence unit）

`dense_only_hit` / `bm25_only_hit` / `both_hit` / `both_miss` 由两个单通道 cutoff 判定；`promoted_by_hybrid`=两通道均 MISS 而 Hybrid HIT；`demoted_by_hybrid`=至少一通道 HIT 而 Hybrid MISS。完整逐 unit rank 与 score 分解在 JSONL。

| cutoff | Dense-only | BM25-only | both-hit | both-miss | promoted by Hybrid | demoted by Hybrid |
|---:|---:|---:|---:|---:|---:|---:|
| @10 | 16 | 8 | 35 | 36 | 1 | 12 |
| @20 | 20 | 6 | 43 | 26 | 0 | 4 |
| @40 | 21 | 5 | 52 | 17 | 0 | 8 |
| @100 | 8 | 6 | 71 | 10 | 0 | 0 |

@40 promotions：无。
@40 demotions：rag-v0-001/claim:1, rag-v0-001/claim:4, rag-v0-001/claim:5, rag-v0-002/claim:1, rag-v0-036/claim:4, rag-v0-037/claim:1, rag-v0-037/claim:2, rag-v0-043/claim:2。

@100 promotions：无。
@100 demotions：无。

### @40 的 8 条 demotion 属于哪个阶段

| case/unit | Dense selected rank | BM25 selected rank | Hybrid Parent pre-selection rank | Hybrid selected rank | cutoff 原因 |
|---|---:|---:|---:|---:|---|
| 001/claim:1、4、5 | 26 | 82 | 84 | 42 | 融合相对排名已超过 40 |
| 002/claim:1 | 57 | 39 | 81 | 51 | 融合相对排名已超过 40 |
| 036/claim:4 | — | 34 | 68 | 48 | 融合相对排名已超过 40 |
| 037/claim:1 | — | 3 | 34 | 62 | source cap 初次阻断，overflow 回填至 #62 |
| 037/claim:2 | 10 | 69 | 31 | 60 | source cap 初次阻断，overflow 回填至 #60 |
| 043/claim:2 | 19 | 50 | 31 | 50 | source cap 初次阻断，overflow 回填至 #50 |

相对 Dense@40，Hybrid **失去 5 条、补进 2 条 BM25 命中**，所以 micro 从 73 降至 70。失去的 5 条里，001 的 3 条在融合后已排到 Parent #84；037/claim:2 与 043/claim:2 则分别从选择前的 #31 被 source cap/overflow selection 推至 #60、#50。其余 3 条 demotion 是 BM25 命中而 Hybrid 未命中。@100 时 Dense-only 8 条、BM25-only 6 条、双方均命中 71 条、双方均未命中 10 条；Hybrid 命中单通道并集的 **85/85**，没有额外超出该并集的命中，也没有丢失其中的 Gold。

两条对 Dense@40 的补进是 002/claim:2（BM25 selected #11 → Hybrid #29）和 031/claim:1（BM25 #5 → Hybrid #18）。037/claim:1、037/claim:2、043/claim:2 在上一轮 document-cap trace 中均标为 `selected_by_overflow_relaxation`，且未列为 document-cap 初次排除的 Gold；固定规则下其初次阻断来自 source cap。这里的 Hybrid@40 下降包含后续 selection 效应，并非 RRF 对这些 Gold 扣分。

## E. 多 leg 与 Parent aggregation

一个 Child 可在 original Dense/lexical、alias lexical、semantic Dense 等不同 leg 中分别得分；同一 leg 的同一 point 会去重。Parent 不把多个 Child 分数相加，因此不会因 Child 数量增大而直接膨胀 Parent score。多 leg 的加成发生在 **Child 层**，不是 Parent 层。

本次 39 case 没有相同 `(channel, normalized query_text)` 的重复 leg，也没有同一 leg 内重复 Child point；跨不同 leg 的同一 Child 累加符合公式。Hybrid 诊断共出现 10,499 个 case–Parent 候选槽位，其中 4,104 个 Parent 有多个匹配 Child，但最终分数只取最高分 Child。70 个可在 Hybrid fused pool 中定位的不同 case–Gold Parent 中，69 个 winning Child 获得至少两个 leg 的贡献；53 个有 alias leg 贡献。alias 对这些 Gold winning score 的平均占比为 18.6%；例如 037/claim:1 为 39.2%。这些是多 leg 累计的真实效果，不是同 leg 重复计分；通用 alias 对语义相关性的影响仍需另行标注，不能只凭分数判错。

多 leg 中等排名理论上可以超过单 leg 第 1 名：四个权重为 1、1、0.85、0.75 的 leg 都排第 50 名时得 `3.60/(60+50)=0.032727`，单个权重 1 的 leg 第 1 名为 `1/61=0.016393`。这是当前加法 RRF 的既定行为。此批 Gold 中，@40 单通道排名特别靠前（037 的 BM25 #3、Dense #10；043 的 Dense #19）却未进入 Hybrid@40 的例子，融合后 Parent 仍在前 40，主要被后续 selection 推出；没有证据支持把它们归咎于异常重复加分。

以下重点 case 的历史 legs、Gold Parent 的 winning Child、各 leg 原始 rank/权重/贡献、其他 Child 分数均见 JSONL。

| case | Gold unit | best Dense raw Child rank | best BM25 raw Child rank | Hybrid fused Child rank | Hybrid Parent preselect rank | Hybrid selected rank | winner Dense contribution | winner BM25 contribution |
|---|---|---:|---:|---:|---:|---:|---:|
| 006 | claim:1 | 8 | 8 | 8 | 7 | 7 | 0.02573529 | 0.02720588 |
| 006 | claim:2 | 8 | 8 | 8 | 7 | 7 | 0.02573529 | 0.02720588 |
| 024 | claim:1 | 1 | 38 | 1 | 1 | 1 | 0.02829820 | 0.01461059 |
| 024 | claim:2 | 567 | 258 | 526 | 310 | — | 0.00159490 | 0.00458778 |
| 024 | claim:3 | 567 | 258 | 526 | 310 | — | 0.00159490 | 0.00458778 |
| 024 | claim:4 | 567 | 258 | 526 | 310 | — | 0.00159490 | 0.00458778 |
| 039 | claim:1 | 198 | 38 | 151 | 47 | — | 0.00675313 | 0.01348593 |
| 039 | claim:2 | 198 | 38 | 151 | 47 | — | 0.00675313 | 0.01348593 |
| 040 | claim:1 | 7 | 25 | 2 | 2 | 2 | 0.02489266 | 0.01783613 |
| 040 | claim:2 | — | 114 | 636 | 263 | — | 0.00000000 | 0.00488506 |
| 040 | claim:3 | 447 | 139 | 284 | 129 | 78 | 0.00334601 | 0.00801808 |
| 040 | claim:4 | 39 | 41 | 21 | 19 | 12 | 0.01738256 | 0.01197456 |
| 040 | claim:5 | 316 | 295 | 357 | 151 | — | 0.00454874 | 0.00513298 |
| 040 | claim:6 | 35 | 29 | 31 | 25 | 16 | 0.01503759 | 0.01123596 |
| 040 | claim:7 | 553 | 199 | 442 | 179 | — | 0.00281585 | 0.00538704 |
| 042 | claim:1 | 84 | 82 | 79 | 52 | 21 | 0.01194444 | 0.01290432 |
| 042 | claim:2 | 21 | 47 | 35 | 23 | 15 | 0.02086841 | 0.01649093 |
| 043 | claim:1 | 165 | 164 | 165 | 83 | 37 | 0.00777778 | 0.00446429 |
| 043 | claim:2 | 38 | 27 | 40 | 31 | 50 | 0.01742574 | 0.01774425 |
| 044 | claim:1 | 5 | 4 | 6 | 6 | 5 | 0.01538462 | 0.02578125 |
| 044 | claim:2 | 25 | 25 | 25 | 19 | 19 | 0.01176471 | 0.02162791 |
| 044 | claim:3 | 5 | 4 | 6 | 6 | 5 | 0.01538462 | 0.02578125 |
| 065 | claim:1 | — | — | — | — | — | — | — |

## F. 039 full RRF trace

Gold 的两条 evidence unit 共享 Parent `parent-17f46282a4d28c6c000e6aff`；以下 score 分解与 rank 对两条 unit 相同。

- winning Child point：`bfa89c8d-7c0c-5399-a281-3d466cd553bd`；Child fused rank：151；Parent pre-selection rank：47；Parent score：0.020239065505。
- Parent matched Child 数：1；其他 Child 分数不累加。winning Child 的 Dense 贡献 0.006753130590，BM25 贡献 0.013485934915。

| leg | channel | raw Child rank | variant × channel weight | RRF contribution |
|---|---|---:|---:|---:|
| `q0:original:dense` | dense | 200 | 1.00 | 0.003846153846 |
| `q0:original:lexical` | lexical | 38 | 1.00 | 0.010204081633 |
| `q1:alias:lexical` | lexical | 199 | 0.85 | 0.003281853282 |
| `q2:semantic:dense` | dense | 198 | 0.75 | 0.002906976744 |

四项相加为 **0.020239065505**；前一名 Parent #46 得 **0.020761491999**，后一名 #48 得 **0.020055670181**，没有浮点同分争议。Gold Child 的 fused Child rank 是 151，按 Parent max-Child 去重后升至 distinct Parent rank **47**。排在它前面的 46 个 Parent 及 score 在 JSONL 的 `focus_hybrid_top50_preselection` 中；selection 后仍 MISS，具体 source cap 与 overflow 见既有 document-cap 审计。

## G. 重点 case 与结论

- **006**：claim:1 D/B/H @40=HIT/HIT/HIT @100=HIT/HIT/HIT, claim:2 D/B/H @40=HIT/HIT/HIT @100=HIT/HIT/HIT。
- **024**：claim:1 D/B/H @40=HIT/MISS/HIT @100=HIT/MISS/HIT, claim:2 D/B/H @40=MISS/MISS/MISS @100=MISS/MISS/MISS, claim:3 D/B/H @40=MISS/MISS/MISS @100=MISS/MISS/MISS, claim:4 D/B/H @40=MISS/MISS/MISS @100=MISS/MISS/MISS。
- **039**：claim:1 D/B/H @40=MISS/MISS/MISS @100=MISS/MISS/MISS, claim:2 D/B/H @40=MISS/MISS/MISS @100=MISS/MISS/MISS。
- **040**：claim:1 D/B/H @40=HIT/HIT/HIT @100=HIT/HIT/HIT, claim:2 D/B/H @40=MISS/MISS/MISS @100=MISS/MISS/MISS, claim:3 D/B/H @40=MISS/MISS/MISS @100=MISS/HIT/HIT, claim:4 D/B/H @40=HIT/HIT/HIT @100=HIT/HIT/HIT, claim:5 D/B/H @40=MISS/MISS/MISS @100=MISS/MISS/MISS, claim:6 D/B/H @40=HIT/MISS/HIT @100=HIT/HIT/HIT, claim:7 D/B/H @40=MISS/MISS/MISS @100=MISS/MISS/MISS。
- **042**：claim:1 D/B/H @40=HIT/HIT/HIT @100=HIT/HIT/HIT, claim:2 D/B/H @40=HIT/MISS/HIT @100=HIT/HIT/HIT。
- **043**：claim:1 D/B/H @40=HIT/HIT/HIT @100=HIT/HIT/HIT, claim:2 D/B/H @40=HIT/MISS/MISS @100=HIT/HIT/HIT。
- **044**：claim:1 D/B/H @40=HIT/HIT/HIT @100=HIT/HIT/HIT, claim:2 D/B/H @40=HIT/HIT/HIT @100=HIT/HIT/HIT, claim:3 D/B/H @40=HIT/HIT/HIT @100=HIT/HIT/HIT。
- **065**：claim:1 D/B/H @40=MISS/MISS/MISS @100=MISS/MISS/MISS。

针对主因：024 的 claim:2–4 共用 Parent，最佳 filtered raw Child 已是 Dense #567 / BM25 #258，随后 fused Child #526、distinct Parent #310，主要是 query/raw coverage 深排；不能将 @100 MISS 归为 RRF 把高排名证据压下。040 的缺失节点同样先在 raw 阶段较深：claim:2 只有 BM25 #114、Parent #263；claim:5 Dense #316 / BM25 #295、Parent #151；claim:7 Dense #553 / BM25 #199、Parent #179。相反 040/claim:3 虽 raw Dense #447 / BM25 #139，Hybrid 最终 selected #78 而进入 @100。039 则 BM25 raw #38，经 Child→Parent 聚合后 pre-selection Parent #47，最后被 source cap/overflow 挡住。065 在本轮固定 filter 下无 Gold raw Child，不能用其 MISS 诊断 RRF。

完整逐 evidence unit 的 rank、winning Child score、各 query leg contribution、Parent max-Child 聚合与 cutoff 命中均在 JSONL。

## H. Implementation bugs / no-bug conclusion

独立重建与当前深度诊断逐 Child、逐 Parent 一致；**RRF implementation validated**。参数 `k=60` 与 Dense/BM25 channel weight 由 config 进入运行中的 leg weight；浮点同分以 point_id，再以 winning_point_id 排序。此审计没有发现 RRF、Child→Parent max aggregation 或 Parent dedup 的实现偏差。

| 审计问题 | 结论 |
|---|---|
| weighted RRF 与 config 是否生效 | 是；从 config 到 leg effective weight、`k=60` 到逐 score 完全重建一致。当前 channel weights 均为 1.0。 |
| 多 leg 是否异常重复加分 | 未发现同文本同通道 leg 或同 leg 同 point 重复；不同 leg 的加法是现有公式。通用 alias 可产生明显占比，属相关性风险而非重复计分 bug。 |
| Child→Parent 与 dedup | Parent score 为最高分 Child，不求和；先按 point 做 RRF，再按 Parent ID 去重，未见实现异常。 |
| Hybrid 是否有互补召回 | 有；@100 保住 Dense-only 8 条和 BM25-only 6 条，85/95 高于 Dense 79/95 与 BM25 77/95。 |
| Hybrid@40 为什么低于 Dense@40 | Hybrid 对 Dense 失去 5 条、从 BM25 补进 2 条；8 条单通道→Hybrid demotion 中 5 条发生在融合相对排名、3 条发生在 selection。 |
| 是否发现 implementation bug | 本轮没有发现 RRF/Parent 聚合 bug；诊断 @100 与线上 Top40 的 Child budget/截断不同，是必须保留的口径限制。 |
| 是否支持立刻调 RRF 参数 | 不支持仅凭本轮做调参；@40 部分损失来自后续 selection，需先隔离因果且人工审查相关性。 |
| 是否支持继续研究 source cap | 支持作为**单独只读实验**：039 在 no-document-cap 时仍由 source cap/overflow 阻断，037、043 的部分 Gold 也在融合后前 40 被 selection 推出。本轮未改 source cap。 |

本轮只审计，不更改 RRF、selection、source cap、Query Planner、reranker 或 Context Builder。不能把诊断 @100 的收益直接外推到线上 Top40；是否开展后续 RRF 或 source-cap 实验，应以本报告的 cutoff 与逐 case 证据人工决定。

## 验证与限制

- 固定 historical plans、frozen Gold/corpus/index/config 指纹；39/39 case 的 Dense/BM25/Hybrid Top100 Parent ID 与上一轮 V2 完全一致。独立逐 unit 重算 @10/@20/@40/@100、Parent max-Child 分数和 039 score/rank，全部通过。
- 固定 raw results 进行两轮重复重建，234 次通道 replay 的 Child/Parent ID、score、rank 完全相同。随机样本与 039 的逐 leg 算术也与 production RRF 一致。
- `pytest tests/test_retrieval_fusion_qdrant.py tests/test_retrieval_service.py tests/test_retrieval_encoding_filters.py tests/test_retrieval_contracts.py tests/test_retrieval_evaluation.py tests/test_run_rag_hybrid_shadow_baseline.py -q`：**34 passed，1 skipped**。
- 非 Gold Parent 不能自动等同于无关结果；“通用 alias 是否促成不相关 Parent 抢位”需要内容相关性标注，本轮不凭 score 猜测。线上 fused Child 截断与本次深度诊断不同，二者不可混作同一指标。

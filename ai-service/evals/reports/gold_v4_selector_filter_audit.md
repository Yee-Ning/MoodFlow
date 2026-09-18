# Gold V4 Selector / Filter Audit

## 范围与口径

本报告只读汇总冻结 Gold V4 环境中 **Filter V2 `SAFE_COMBINED_V2_CAP_5` 的 Hybrid Top100** 剩余未命中。基线为 39 个 eligible case、95 个正向 evidence unit，Hybrid macro Recall@100 **89.29%**，micro **85/95**；本报告逐项审计其余 **10 个 unit**。Filter V2 是既有实验候选政策，**尚非 production filter**。本次没有重跑 Retrieval，也没有更改 Gold、语料、索引、Planner、RRF、selector、过滤规则或生产配置。

本报告沿用既有同环境只读实验的结果：同一 Gold SHA-256 `1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`、corpus `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`、projection `663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`、manifest `0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`、retrieval config `b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`、历史 Planner archive `dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`。前述实验使用历史 Planner legs、每 leg 600 Child 的深度诊断和 Top100 Parent；这不等同于 production Top40。统计单位是 Gold evidence unit，同一 Parent 可支持不同 claim，039 因此计两条。

证据来源：[`gold_v4_metadata_filter_v2_ablation.md`](gold_v4_metadata_filter_v2_ablation.md)、[`gold_v4_metadata_filter_ablation.md`](gold_v4_metadata_filter_ablation.md)、[`gold_v4_document_cap_ablation.md`](gold_v4_document_cap_ablation.md)、[`gold_v4_source_cap_ablation.md`](gold_v4_source_cap_ablation.md)、[`gold_v4_selection_joint_cap_audit.md`](gold_v4_selection_joint_cap_audit.md)、[`gold_v4_query_planner_coverage_audit.md`](gold_v4_query_planner_coverage_audit.md) 及各自的 JSONL。逐 unit 对齐结果在 [`gold_v4_selector_filter_audit.jsonl`](gold_v4_selector_filter_audit.jsonl)。

这里区分三个概念：**初次 cap 延后**指 Parent 在 selector 第一遍进入 overflow，不等于最终 MISS；**因 cap 最终未命中**要求相关 Parent 已在过滤后的融合前 100 位、最终未入选，并有 cap 反事实支持；**无过滤命中**只说明解除全部 metadata 条件后的技术可达性，不能单独证明某一条件应放宽，也不能证明 Gold 范围无争议。

## 结论与计数

| 阻断点 | 当前 10 条中 affected | 固定其余条件单独解除后恢复 | 补充判断 |
|---|---:|---:|---|
| document cap | 6 条在第一遍被延后；其中 2 条是 preselection Top100 内的最终 MISS | no-document-cap：0 | 024×3、040/claim:5 虽触发延后，但融合前排名分别为 310、151；039×2 的 Parent 排名 47，却仍受 source cap／overflow 约束。 |
| source cap | baseline 第一遍直接触发 **0** | no-source-cap：0 | 当前 projection 中 56 个 source 与 56 个 document 一一对应，document cap≤4 先于 source cap=5 生效。单独解除 document cap 后，039 改由 source cap 阻断。 |
| overflow / joint cap | 2 条最终因 overflow 回填不足而 MISS；另 4 条虽被初次延后，但原融合排名 >100 | 两种 cap **同时全解除**：恢复 039×2；单独解除任一种：0 | 039 位于 overflow #23，仅回填 18 个槽；同时全解除恢复它，却在全局损失 6 条原命中 Gold。 |
| content type metadata | 1 条（045） | 全部 metadata filter 取消：1；现有保守 content-type fallback：0 | Gold 是 `chart_event` 榜单背景，问题要“乐评”；属于已标记的 scope dispute，不能认定 filter 错误。 |
| temporal metadata | 1 条（065） | 全部 metadata filter 取消：1；上一轮 soft temporal fallback：0 | Gold Parent 三个 Child 的年份／区间／提及年份字段全缺失；单独 temporal-only 反事实未运行，不能把全取消结果当成安全修复。 |

**互斥主因**：候选排序／query 覆盖过深 **6**（024×3、040×3）；联合 document/source cap 与 overflow **2**（039×2）；metadata 阻断 **2**（045、065）。10/10 的 Gold span 和 Parent 已在冻结 projection／index 中核实；本批未证实 corpus/index 缺失。上述 affected 行有交叉，不能相加，例如 024 被初次 document cap 延后，但其主要问题是融合排名 310。

历史严格 filter 的 80/95 基线里有 7 个 filter 类 dual-miss；既有 Filter V2 已恢复其中 5 个且未丢 Gold：006×2（entity）、042（language）、043 与 044（content type）。**当前 85/95 基线仍剩的 filter 候选是 045 和 065 各 1 条**。这两个计数阶段不同，不能把历史 7 条当作当前仍未命中。

## 逐 unit 证据与裁断

表中 `pre` 是过滤后融合、Parent 去重、selector 前的全球名次；`—` 表示 metadata filter 后无法进入候选。`no-doc`／`no-source`／`no-both` 使用既有 joint-cap 实验；`no-filter` 使用既有全 metadata 取消实验。Gold Parent ID 与完整原始 leg rank 在 JSONL。

| Case / unit | Gold Parent | 最小 Gold evidence | pre | 当前第一遍 / 最终阻断 | no-doc | no-source | no-both | no-filter |
|---|---|---|---:|---|:---:|:---:|:---:|:---:|
| 024/claim:2 | `parent-790e32e522e2ba2f480a77ea` | `\| 3. \| 藉口 \| 周杰伦 \| 周杰伦 \| 4:18 \|` | 310 | 初次 document cap；最终排名过深 | MISS | MISS | MISS | MISS |
| 024/claim:3 | 同上 | `\| 5. \| 将军 \| 黄俊郎 \| 洪敬尧 \| 3:22 \|` | 310 | 同上 | MISS | MISS | MISS | MISS |
| 024/claim:4 | 同上 | `\| 1. \| 我的地盘 \| 方文山 \| 洪敬尧 \| 4:02 \|` | 310 | 同上 | MISS | MISS | MISS | MISS |
| 039/claim:1 | `parent-17f46282a4d28c6c000e6aff` | `2011年，曾投資「DejaVu音樂魔術餐廳」。` | 47 | document cap → overflow #23，回填 18 | MISS | MISS | **HIT** | MISS |
| 039/claim:2 | 同上 | `2012年初，與西安真愛投資集團合作在西安開設「真愛范特西」KTV` | 47 | 同上 | MISS | MISS | **HIT** | MISS |
| 040/claim:2 | `parent-81d3ee5f0e44759bf67d8c7f` | `《葉惠美》…于2003年7月31日發行…` | 263 | Top100 先填满，未到达 | MISS | MISS | MISS | MISS |
| 040/claim:5 | `parent-a83673d2b967ec913e6b0ef3` | `2007年，周杰倫第一部自編自導自演的電影《不能說的·秘密》上映…` | 151 | 初次 document cap；最终排名过深 | MISS | MISS | MISS | MISS |
| 040/claim:7 | `parent-95160d6644f27840faf6b97d` | `十一月的蕭邦 ＋ 霍元甲（2LP）…（發行日期2005/11/01）` | 179 | Top100 先填满，未到达 | MISS | MISS | MISS | MISS |
| 045/claim:1 | `parent-e46f773645df47165d634134` | `《晴天》在2003年HitFM联播网年度百首單曲中名列亚军` | — | `content_type=chart_event`，query 要 `review` | MISS | MISS | MISS | **HIT** |
| 065/claim:1 | `parent-659d449391ffb5694ba9fdf0` | `我的很多快歌都是看动作片写出来的。` | — | 年份 metadata 缺失，query 要 2004 | MISS | MISS | MISS | **HIT** |

040 的两个长句在表中仅为可读摘要，**完整 Gold span 未截断**，见 JSONL `evidence_text`。全部 10 条的确切 evidence、claim、support_level、Parent/Document、per-leg raw rank、filter admission、selection 轨迹与反事实结果均在 JSONL。

## Selector：039 的真实路径与解除限制的代价

039 的 Gold Parent `parent-17f46282a4d28c6c000e6aff` 同时支持 **两个不同 claim**，不是重复证据。Filter V2 下，原始 BM25 Child 最佳 #38、Dense #198；融合前 Parent 全球 #47，同 document/source 的 local #27。production 选择规则对 timeline 使用 broad document cap=4、source cap=5：第一遍先因 document cap 延后至 overflow #23；第一遍选中 82 个 Parent，仅有 18 个回填槽，所以它未入 Top100。虽然 #47 的融合排名优于一些最终入选 Parent，后者在第一遍未触发 cap 而先占槽位；既有审计没有发现 overflow 内逆序回填。

| 039 反事实 | 首次阻断 | overflow 位置 / 回填槽 | 两个 Gold unit |
|---|---|---:|---|
| document=4，source=5 | document cap | 23 / 18 | MISS |
| no document cap，source=5 | source cap | 22 / 11 | MISS |
| document=4，no source cap | document cap | 23 / 18 | MISS |
| no document cap，no source cap | 不触发 | — | **HIT；Parent 最终 #47** |
| broad document=source=27 | 不触发 | — | **HIT；Parent 最终 #47** |

Joint-cap 连续扫描中，**27** 是首次恢复 039 的共同有限 cap；这超过 production schema 上限 20。该配置全局恢复 2 条、丢失 6 条、micro 从 85/95 降到 81/95，macro 从 89.29% 降到 88.24%，新增 373 个非 Gold case–Parent 槽位；双 cap 全取消亦恢复 2、丢失 6，新增 977 个非 Gold 槽位。因此“解除限制能恢复 039”成立，“低风险提升全局 cap”不成立。仅提高 document cap 到 5/6/8/12／no-cap 或仅提高 source cap 到 6/8/10/15／no-cap，都没有恢复任何 Gold unit。

## Metadata 与 temporal：是否误过滤

- **045：范围争议，不能认定误过滤。** Query 明确要《晴天》“乐评资料，不含翻唱版”；冻结 Gold 正向 `claim:1` 只给出 HitFM 榜单亚军，Parent 的 content type 是 `chart_event`。严格 `review` 条件排除它；全部 metadata filter 取消后可命中，但榜单不等于乐评。既有 Filter V2 特意没有将 `chart_event` 纳入 review-compatible 类型。报告保留 Gold 的 partial 标注，不修改它，也不把这次无过滤 HIT 当作通用放宽依据。
- **065：明确的 metadata 覆盖缺口，是否属于年份误过滤仍不足以确认。** Parent 在索引中有 3 个 Child，内容是访谈并包含受访者原话；`year/start_year/end_year/min_mentioned_year/max_mentioned_year` 都为 null。查询要求“2004年访谈”，硬时间条件使三个 Child 全部退出。无过滤时原始 Dense #2、BM25 #1，最终 Hybrid HIT；但这同时取消了其他条件。冻结 Gold 的支持 claim 只证明“很多快歌受动作片启发”，当前字段本身不能验证该访谈确属 2004 年。将 unknown year 放行是否合理需另行核实时间来源与候选噪声；既有 V1 temporal soft fallback 未恢复这条，V2 刻意保持 hard temporal policy。本报告不推断发布日期。

因此，按“条件机械排除了 Gold Parent”计是 **2 条**；按“已有证据足以确认 filter 语义错误”计是 **0 条已确认**、**2 条待审**（045 scope，065 年份 provenance）。在历史严格 filter → V2 的对照中，5 条确实由保守 fallback 恢复，但那是**另一基线**。全取消 metadata filter 并非安全上界：历史 filter ablation 对 39 case 恢复 5 条同时丢失 8 条，micro 从 80/95 降到 77/95。

## 验证与限制

- 将 39 case 的 Filter V2 Top100 与 joint-cap baseline 逐 unit 对齐，均一致；从 95 条 positive evidence 重算出 85 hit、10 miss。
- 10/10 条 JSONL 记录已逐一与冻结 Gold 的 claim、supporting span、Parent 对齐；count 检查：case 分布 3/2/3/1/1，metadata blocked=2，初次 document cap defer=6，初次 source cap defer=0，preselection≤100 且因 cap／overflow 最终 MISS=2。
- `python -m pytest tests/test_rag_gold_v4_audit.py tests/test_run_rag_hybrid_shadow_baseline.py tests/test_retrieval_fusion_qdrant.py tests/test_retrieval_encoding_filters.py tests/test_retrieval_service.py -q`：**38 passed、1 skipped、34 subtests passed**。
- 本报告使用既有只读 ablation 与保存的深排轨迹，没有生成新 query leg 或重新运行 Qdrant。`no-filter` 取消全部 metadata 条件，**不是** content-type-only 或 temporal-only 的安全反事实；039 的 joint cap 反事实也伴随其他 Gold 被挤出。
- 未修改 Gold V4、production filter／selector、document/source cap、RRF、reranker 或 Context Builder。045 与 065 的语义争议需人工审查，本次停止于诊断。

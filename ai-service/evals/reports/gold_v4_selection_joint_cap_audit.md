# Gold V4 Selection Policy Joint Cap / Overflow Audit

## 实验边界

只读重放固定 Gold V4、frozen corpus/index/projection、embedding revision、历史 Query Planner legs、SAFE_COMBINED_V2_CAP_5、Dense/BM25、weighted RRF k=60、每 leg 600 Child、Top100 Parent。只改变 document/source cap，并逐步重建 production overflow refill；未修改 production config、reranker 或 Context Builder。39 eligible cases，95 正向 evidence units。

线上 selection 仅对 timeline/comparison/influence_style/exploratory 使用 broad_document_cap=4，其余使用 default_document_cap=2。有限 doc=N 配置只替换 broad cap，default 始终为 2；doc=None 诊断同时解除 broad/default document cap。source cap 为统一配置。039 的 query type 是 timeline，实际使用 broad cap。None 和超过 production schema cap 上限的连续边界扫描仅用于诊断，不是部署建议。

Macro Recall@100 为 39 case 等权平均；micro 为 evidence-unit hit/95，使用 Gold V4 现有 group-aware `_unit_hit`。本系列 @100 是深度诊断：每 leg 600 Child，不在 Parent 选择前截断 fused Child；不是线上 Top40。

- Gold SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；corpus：`8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`；projection：`663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`；manifest：`0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。
- Retrieval config：`b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`；Planner config：`afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77`；historical archive：`dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`。
- Filter V2 JSONL：`69ab5feea651e9cd693e797d21c7c4dacfd99d371fb6838cad999c466ba74c06`；source-cap JSONL：`dce37eb82a93a5474612240ad4002b02fafb7138d7fd84a4d7a47a6b7954742d`。
- Baseline Dense/BM25/Hybrid Parent IDs 在全部 39 case 与 Filter V2 相同；Hybrid 与上一轮 source-cap baseline 相同。Projection 中 56 source 与 56 document 一一对应。

## Joint cap grid 主表

Parent churn 单位为 case–Parent 槽位；new non-Gold 用本 case 的 Gold Parent ID 集合作候选噪声代理。Diversity 为每 case Top100 不同 document/source 数的均值。

| config | doc | source | Dense@100 | BM25@100 | Hybrid@100 | micro | recovered | lost | net | admitted | displaced | Gold displaced | non-Gold admitted | doc diversity | source diversity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A_BASELINE | 4 | 5 | 85.82% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 0 | 0 | 31.05 | 31.05 |
| B_DOC5_SOURCE5 | 5 | 5 | 86.18% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 98 | 98 | 0 | 98 | 30.67 | 30.67 |
| C_DOC5_SOURCE6 | 5 | 6 | 86.18% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 98 | 98 | 0 | 98 | 30.67 | 30.67 |
| D_DOC6_SOURCE6 | 6 | 6 | 85.82% | 83.13% | 88.43% | 84/95 | 0 | 1 | -1 | 150 | 150 | 1 | 150 | 30.41 | 30.41 |
| E_DOC6_SOURCE8 | 6 | 8 | 85.82% | 83.13% | 88.43% | 84/95 | 0 | 1 | -1 | 150 | 150 | 1 | 150 | 30.41 | 30.41 |
| F_DOC8_SOURCE8 | 8 | 8 | 85.16% | 82.27% | 86.55% | 81/95 | 0 | 4 | -4 | 213 | 213 | 3 | 213 | 29.87 | 29.87 |
| G_DOC8_SOURCE10 | 8 | 10 | 85.16% | 82.27% | 86.55% | 81/95 | 0 | 4 | -4 | 213 | 213 | 3 | 213 | 29.87 | 29.87 |
| H_DOC12_SOURCE15 | 12 | 15 | 85.16% | 80.73% | 86.55% | 81/95 | 0 | 4 | -4 | 277 | 277 | 3 | 277 | 29.41 | 29.41 |
| I_DOC_NONE_SOURCE5 | None | 5 | 86.18% | 83.77% | 89.29% | 85/95 | 0 | 0 | +0 | 544 | 544 | 0 | 544 | 26.77 | 26.77 |
| J_DOC4_SOURCE_NONE | 4 | None | 85.82% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 0 | 0 | 31.05 | 31.05 |
| K_DOC8_SOURCE_NONE | 8 | None | 85.16% | 82.27% | 86.55% | 81/95 | 0 | 4 | -4 | 213 | 213 | 3 | 213 | 29.87 | 29.87 |
| L_DOC_NONE_SOURCE10 | None | 10 | 86.01% | 81.37% | 86.55% | 81/95 | 0 | 4 | -4 | 769 | 769 | 3 | 769 | 24.74 | 24.74 |
| M_DOC_NONE_SOURCE_NONE | None | None | 83.08% | 84.60% | 88.24% | 81/95 | 2 | 6 | -4 | 978 | 978 | 5 | 977 | 21.23 | 21.23 |

## 039：逐 joint config 选择轨迹

039 的两个 Gold evidence units `claim:1` 与 `claim:2` 是同一 Parent 支持的两项不同事实；Parent 命中会同时恢复两项。

Gold Parent `parent-17f46282a4d28c6c000e6aff`；global preselection rank **47**；document-local rank **27**；source-local rank **27**；融合分数 0.020239065505。

| config | doc | source | first block reason | overflow pos | total overflow | refill slots | selected? | selected rank |
|---|---:|---:|---|---:|---:|---:|---|---:|
| A_BASELINE | 4 | 5 | document_cap | 23 | 53 | 18 | no | — |
| B_DOC5_SOURCE5 | 5 | 5 | document_and_source_cap | 22 | 46 | 11 | no | — |
| C_DOC5_SOURCE6 | 5 | 6 | document_cap | 22 | 46 | 11 | no | — |
| D_DOC6_SOURCE6 | 6 | 6 | document_and_source_cap | 21 | 43 | 8 | no | — |
| E_DOC6_SOURCE8 | 6 | 8 | document_cap | 21 | 43 | 8 | no | — |
| F_DOC8_SOURCE8 | 8 | 8 | document_and_source_cap | 19 | 37 | 2 | no | — |
| G_DOC8_SOURCE10 | 8 | 10 | document_cap | 19 | 37 | 2 | no | — |
| H_DOC12_SOURCE15 | 12 | 15 | document_cap | 15 | 16 | 0 | no | — |
| I_DOC_NONE_SOURCE5 | None | 5 | source_cap | 22 | 46 | 11 | no | — |
| J_DOC4_SOURCE_NONE | 4 | None | document_cap | 23 | 53 | 18 | no | — |
| K_DOC8_SOURCE_NONE | 8 | None | document_cap | 19 | 37 | 2 | no | — |
| L_DOC_NONE_SOURCE10 | None | 10 | source_cap | 17 | 18 | 0 | no | — |
| M_DOC_NONE_SOURCE_NONE | None | None | selected_under_caps | — | 0 | 0 | yes | 47 |

### Overflow refill 真实实现与顺序

`selection.py::select_candidates` 按 Parent 融合分数降序（同分按 winning Child point_id）遍历 ranked Parent。触发 document/source cap 的 Parent 立即按该遍历顺序追加到同一个 overflow 列表，不按 document、source 或 block type 分组。第一次扫描停止于 selected 达到 target 或 ranked 耗尽；未满 target 时，直接 `selected.extend(overflow[: target - len(selected)])`。因此已进入 overflow 的 A、B 若 A 的 fused global rank 优于 B，B 不会先于 A 回填；本轮对全部 39 case、三通道、全部主配置及边界扫描逐项检查了此顺序，未发现逆序。

下表是 039 的 **A baseline** overflow 前 30 项；所有主配置的前 30 项保存在 JSONL。`selected rank` 是最终 Top100 中的位置。

| pos | Parent ID | fused global rank | document | source | score | initial block | selected rank |
|---:|---|---:|---|---|---:|---|---:|
| 1 | `parent-62e917bc84f9d2a722710d2c` | 9 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.036042459098 | document_cap | 83 |
| 2 | `parent-1f6478d4e349644dc27f77fb` | 10 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.032930415477 | document_cap | 84 |
| 3 | `parent-cfdde7bca9bed77c7a31e203` | 11 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.032227319670 | document_cap | 85 |
| 4 | `parent-b6ac2fdc90d65cc57ab66a97` | 13 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.031704346237 | document_cap | 86 |
| 5 | `parent-572b33233e9a30a06ab6d630` | 15 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.031161477411 | document_cap | 87 |
| 6 | `parent-24920b368d1cce60591f58bf` | 16 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.030831924067 | document_cap | 88 |
| 7 | `parent-d82cde6546735fc36141ed20` | 17 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.030625268521 | document_cap | 89 |
| 8 | `parent-933e3cef0982686002c36e63` | 18 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.030193027769 | document_cap | 90 |
| 9 | `parent-eaf0efd7d4e8dc4078771f80` | 19 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.028079105640 | document_cap | 91 |
| 10 | `parent-34ec1041fe58b4d90395e32d` | 21 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.027566384736 | document_cap | 92 |
| 11 | `parent-3467f6e492469ee31b498273` | 23 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.027184179062 | document_cap | 93 |
| 12 | `parent-09a09073bd75874b3cc5b72a` | 24 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.026233445217 | document_cap | 94 |
| 13 | `parent-358c0f16040a986866b78139` | 25 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.026123964437 | document_cap | 95 |
| 14 | `parent-05cfbd1996cc2409e2d42b0c` | 26 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.026016454183 | document_cap | 96 |
| 15 | `parent-dfdce4940f5ffc7523b51532` | 28 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.025648979585 | document_cap | 97 |
| 16 | `parent-deb4f852a206b75236fa201e` | 30 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.025314078911 | document_cap | 98 |
| 17 | `parent-09893f769ae5ca2bd9dc419c` | 32 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.025040414193 | document_cap | 99 |
| 18 | `parent-5e614cf3933d7f2fd173b0b7` | 35 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.024826159367 | document_cap | 100 |
| 19 | `parent-d11ce63cb408443735bba811` | 36 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.023044543046 | document_cap | — |
| 20 | `parent-f14a9d7435fced970dc4a6f8` | 40 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.022581874240 | document_cap | — |
| 21 | `parent-b8c7a37f2fd91f4f445413fe` | 41 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.022546313211 | document_cap | — |
| 22 | `parent-a8a549bd39b809a102b566e8` | 42 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.022093248979 | document_cap | — |
| 23 | `parent-17f46282a4d28c6c000e6aff` | 47 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.020239065505 | document_cap | — |
| 24 | `parent-a83673d2b967ec913e6b0ef3` | 49 | `doc-jc-v1-030-00f0479d20c219dffad5` | `jc-v1-030` | 0.019678992180 | document_cap | — |
| 25 | `parent-8b2ae34de7dea8929afdef07` | 61 | `doc-jc-v2-026-ab3e9caca94892ec4686` | `jc-v2-026` | 0.017416124812 | document_cap | — |
| 26 | `parent-82607017d88b6faa7b0a601f` | 83 | `doc-jc-v1-034-6c5fcbb5090ae4138248` | `jc-v1-034` | 0.014078988838 | document_cap | — |
| 27 | `parent-783134afb2ed899b455de9b7` | 92 | `doc-jc-v1-032-61a064fea9994b0bbc35` | `jc-v1-032` | 0.013292806498 | document_cap | — |
| 28 | `parent-690d69d26909699761f868c6` | 103 | `doc-jc-v2-045-9dd5c92d2bc2e22c6f17` | `jc-v2-045` | 0.012249662454 | document_cap | — |
| 29 | `parent-3c55f79965e4be5b3dc60016` | 109 | `doc-jc-v1-025-c9da4510ee57c431ccfe` | `jc-v1-025` | 0.011963992835 | document_cap | — |
| 30 | `parent-eb34362a79bcc15f8bf2ea55` | 110 | `doc-jc-v1-025-c9da4510ee57c431ccfe` | `jc-v1-025` | 0.011490877286 | document_cap | — |

Baseline 中 global rank 比 Gold #47 更差却最终入选的 Parent 有 **58** 个；例如 `parent-a9e61c7f43a2b5a0e8d86459` (global #48, selected #25), `parent-d47ccb9dda4ad7eb659cace9` (global #50, selected #26), `parent-bb9810b50e07fbcbb9d00c66` (global #51, selected #27), `parent-d30fcf754ead47597efb7cc1` (global #52, selected #28), `parent-69be3e2d5f415936f61a9a23` (global #53, selected #29)。这些 Parent 在 first pass 未触发 cap，可先占 selected 槽；Gold #47 在 overflow #23，只有 18 个回填槽。**没有** fused global rank 数值大于 47 的 Parent 在 overflow 中被回填、而 Gold #47 被跳过。区别是：较差 rank 可通过 first pass 入选，较好 rank 因 cap 进入 overflow。

## 039 最小 joint cap 边界扫描

为确定最小有限联合阈值，在保持其余条件不变时对 broad doc=source=N 的 N=4..27 逐值重放；下表记录 039 与全局 Hybrid。default document cap 仍为 2。N>20 绕过 schema 的 production cap 上限，仅作离线边界诊断。

| doc=source | Gold selected? | selected rank | Hybrid@100 | micro | recovered | lost | non-Gold admitted | doc diversity |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 4 | no | — | 89.29% | 85/95 | 0 | 0 | 0 | 31.05 |
| 5 | no | — | 89.29% | 85/95 | 0 | 0 | 98 | 30.67 |
| 6 | no | — | 88.43% | 84/95 | 0 | 1 | 150 | 30.41 |
| 7 | no | — | 88.43% | 84/95 | 0 | 1 | 185 | 30.13 |
| 8 | no | — | 86.55% | 81/95 | 0 | 4 | 213 | 29.87 |
| 9 | no | — | 86.55% | 81/95 | 0 | 4 | 236 | 29.72 |
| 10 | no | — | 86.55% | 81/95 | 0 | 4 | 254 | 29.56 |
| 11 | no | — | 86.55% | 81/95 | 0 | 4 | 268 | 29.54 |
| 12 | no | — | 86.55% | 81/95 | 0 | 4 | 277 | 29.41 |
| 13 | no | — | 86.55% | 81/95 | 0 | 4 | 288 | 29.28 |
| 14 | no | — | 86.04% | 80/95 | 0 | 5 | 296 | 29.21 |
| 15 | no | — | 86.04% | 80/95 | 0 | 5 | 306 | 29.10 |
| 16 | no | — | 85.67% | 79/95 | 0 | 6 | 314 | 29.05 |
| 17 | no | — | 85.67% | 79/95 | 0 | 6 | 321 | 28.97 |
| 18 | no | — | 85.67% | 79/95 | 0 | 6 | 324 | 28.92 |
| 19 | no | — | 85.67% | 79/95 | 0 | 6 | 332 | 28.82 |
| 20 | no | — | 85.67% | 79/95 | 0 | 6 | 336 | 28.77 |
| 21 | no | — | 85.67% | 79/95 | 0 | 6 | 343 | 28.67 |
| 22 | no | — | 85.67% | 79/95 | 0 | 6 | 346 | 28.64 |
| 23 | no | — | 85.67% | 79/95 | 0 | 6 | 354 | 28.64 |
| 24 | no | — | 85.67% | 79/95 | 0 | 6 | 359 | 28.56 |
| 25 | no | — | 85.67% | 79/95 | 0 | 6 | 363 | 28.54 |
| 26 | no | — | 85.67% | 79/95 | 0 | 6 | 369 | 28.49 |
| 27 | yes | 47 | 88.24% | 81/95 | 2 | 6 | 373 | 28.46 |

连续扫描中最小恢复阈值：**27**。由于 source/document 在本 index 一一对应，039 的有限 cap 只受两者较小值控制；该扫描确定的是共同下限，不把 None 视作可上线值。

## Preselection Top100 内的 Gold Parent 最终 MISS

逐 Gold Parent 列表仅计 preselection global rank≤100 且 baseline final Top100 未入选的 Parent。若 evidence group 的另一个 acceptable Parent 已命中，这仍是 Parent 级 MISS，不等同于该 evidence unit MISS。根因按单独解除 document/source cap 与同时解除两者的反事实结果分类；overflow ordering 另记为直接选择机制。

| case | Gold Parent | units | pre rank | doc/source local | initial block | overflow pos / filled | no doc | no source | no both | root cause |
|---|---|---|---:|---:|---|---:|---|---|---|---|
| rag-v0-039 | `parent-17f46282a4d28c6c000e6aff` | claim:1, claim:2 | 47 | 27/27 | document_cap | 23/18 | MISS | MISS | HIT | joint_cap_interaction |

### Selection failure root-cause summary

| root cause | Gold Parent count |
|---|---:|
| joint_cap_interaction | 1 |

合计 **1** 个 baseline preselection≤100 却最终 MISS 的 Gold Parent；其中 **1** 个经过 overflow 队列但位置超过回填槽。post-filter 已在 preselection Parent ranking 前应用，所以这些 Parent 的 MISS 不归因于 post-filter。

全部 13 个主配置也逐一枚举了 preselection≤100 的 Gold Parent MISS：

| config | count | Parent |
|---|---:|---|
| A_BASELINE | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| B_DOC5_SOURCE5 | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| C_DOC5_SOURCE6 | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| D_DOC6_SOURCE6 | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| E_DOC6_SOURCE8 | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| F_DOC8_SOURCE8 | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| G_DOC8_SOURCE10 | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| H_DOC12_SOURCE15 | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| I_DOC_NONE_SOURCE5 | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| J_DOC4_SOURCE_NONE | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| K_DOC8_SOURCE_NONE | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| L_DOC_NONE_SOURCE10 | 1 | rag-v0-039/`parent-17f46282a4d28c6c000e6aff` |
| M_DOC_NONE_SOURCE_NONE | 0 | none |

其他配置造成的新增 Gold unit 损失均对应 preselection rank>100 的 Parent：基线 cap 原本通过多样性选择把它们提升进 Top100，放宽 cap 后它们被更高融合排名的同 document/source Parent 挤出。该现象属于放宽 cap 的副作用，不能归入‘preselection≤100 仍 MISS’清单。

## Gold 增损与 Pareto 判断

### B_DOC5_SOURCE5

Recovered: none。
Lost: none。

### C_DOC5_SOURCE6

Recovered: none。
Lost: none。

### D_DOC6_SOURCE6

Recovered: none。
Lost: rag-v0-004/claim:1。

### E_DOC6_SOURCE8

Recovered: none。
Lost: rag-v0-004/claim:1。

### F_DOC8_SOURCE8

Recovered: none。
Lost: rag-v0-003/claim:1, rag-v0-004/claim:1, rag-v0-036/claim:2, rag-v0-036/claim:3。

### G_DOC8_SOURCE10

Recovered: none。
Lost: rag-v0-003/claim:1, rag-v0-004/claim:1, rag-v0-036/claim:2, rag-v0-036/claim:3。

### H_DOC12_SOURCE15

Recovered: none。
Lost: rag-v0-003/claim:1, rag-v0-004/claim:1, rag-v0-036/claim:2, rag-v0-036/claim:3。

### I_DOC_NONE_SOURCE5

Recovered: none。
Lost: none。

### J_DOC4_SOURCE_NONE

Recovered: none。
Lost: none。

### K_DOC8_SOURCE_NONE

Recovered: none。
Lost: rag-v0-003/claim:1, rag-v0-004/claim:1, rag-v0-036/claim:2, rag-v0-036/claim:3。

### L_DOC_NONE_SOURCE10

Recovered: none。
Lost: rag-v0-003/claim:1, rag-v0-004/claim:1, rag-v0-036/claim:2, rag-v0-036/claim:3。

### M_DOC_NONE_SOURCE_NONE

Recovered: rag-v0-039/claim:1, rag-v0-039/claim:2。
Lost: rag-v0-001/claim:2, rag-v0-003/claim:1, rag-v0-004/claim:1, rag-v0-036/claim:2, rag-v0-036/claim:3, rag-v0-040/claim:3。

主 grid 中没有恢复 039 且 lost Gold=0 的有限配置。None 仅是诊断，不作为 production 建议；若需未来研究 adaptive/score-aware diversity selection，必须另设实验，本轮不实现。

连续扫描中 doc=source=27 才首次恢复 039 的 2 条 Gold，但同时丢失 6 条、净损失 4 条，新增 373 个非 Gold case–Parent 槽位，document/source diversity 从 31.05 降到 28.46；且 broad document cap=27 超出 production schema 上限 20。双 cap 全解除也恢复 039，但丢失 6 条、引入 977 个非 Gold 槽位，diversity 降到 21.23。因此没有低风险、零 regression 的 joint cap 候选，不建议简单提高全局 cap。

这些 Gold 损失是 evidence-unit 数；同一 Parent 可支持多个 claim，所以 Gold displaced Parent 数可能小于 lost evidence-unit 数。

未尝试用 selection policy 修复 preselection rank>100 的 024/040 Gold 或 065 的 raw retrieval miss。未修改 Gold、Filter V2、RRF、Planner、embedding、reranker、Context Builder 或 production serving。

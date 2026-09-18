# Gold V4 Source Cap Coverage Ablation

## 固定条件与计分口径

本轮只读诊断只改变 source cap。固定 Gold V4、frozen corpus/index/projection、embedding revision、历史 Query Planner legs、SAFE_COMBINED_V2_CAP_5、Dense/BM25、weighted RRF k=60、每 leg 600 Child、Top100 Parent、document cap（broad=4，default=2）、reranker 和 Context Builder。未改 production config。39 个 eligible case、95 个正向 evidence unit。source cap 的 production 值为统一的 5，没有 query-type 特例。no-cap 仅为诊断上界。

本系列 Top100 是深度诊断：每 leg 600 Child，融合 Child 未先截断，最后选 100 Parent。附带 @40 使用这一 Top100 列表的前 40 Parent；不等同于线上候选 Top40（线上 per-leg/fused 限额更小）。Macro 是 39 个 case 的等权平均；micro 是命中 evidence unit / 95。Gold group-aware `_unit_hit` 与前两轮一致。

- Gold SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；corpus：`8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`；projection：`663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`；manifest：`0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。
- Retrieval config SHA-256：`b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`；Planner config：`afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77`；historical archive：`dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`。
- V2 JSONL SHA-256：`69ab5feea651e9cd693e797d21c7c4dacfd99d371fb6838cad999c466ba74c06`；document-cap JSONL：`4cf58e5847092723fb41be8bae986480508e2070e8ede4eb4c0554af0d73803e`。
- BASELINE 的 Dense/BM25/Hybrid Top100 Parent ID 与 V2 SAFE_COMBINED_V2_CAP_5 在全部 39 case 完全一致。固定 raw leg 重放的每个 cap 选择也重复两次，Parent 顺序一致。

- Projection 中 56 个 source 与 56 个 document 严格一一对应（每个 source 仅一个 document，每个 document 仅一个 source）。固定 document cap 最大 4，低于 source cap baseline 5，因此 source cap 在这份 index/配置中无法先于 document cap 触发。

## 主表

`recovered` / `lost` 按 evidence unit 对 baseline 比较；candidate churn 按 case–Parent 槽位计。Diversity 是每 case Top100 中不同 source/document 的平均数。

| source cap | Dense@100 | BM25@100 | Hybrid@100 | micro hits | recovered | lost | net | new non-Gold Parent | Gold displaced | source diversity | document diversity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE | 85.82% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 31.05 | 31.05 |
| SOURCE_CAP_6 | 85.82% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 31.05 | 31.05 |
| SOURCE_CAP_8 | 85.82% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 31.05 | 31.05 |
| SOURCE_CAP_10 | 85.82% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 31.05 | 31.05 |
| SOURCE_CAP_15 | 85.82% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 31.05 | 31.05 |
| NO_SOURCE_CAP_DIAGNOSTIC | 85.82% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 31.05 | 31.05 |

## Source concentration 与 candidate churn

`source-cap 初次排除` 是生产选择器第一次扫描时放入 overflow 的 Parent；当 Top100 未填满时可按 overflow 顺序回填，所以不是最终失去的 Parent。`cap-hit sources` 是至少一个 Parent 在初次扫描时触发 source cap 的 source。JSONL 保存每 case 的 source/document Parent 分布、Top100 Parent IDs、初次排除数、Gold trace、churn IDs。

| cap | retained | newly admitted | displaced | new same-source | displaced other-source | Gold displaced other-source | cap-hit sources | source-cap 初次排除 Parent | 初次排除 Gold Parent | max source concentration |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 48 |
| SOURCE_CAP_6 | 3024 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 48 |
| SOURCE_CAP_8 | 3024 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 48 |
| SOURCE_CAP_10 | 3024 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 48 |
| SOURCE_CAP_15 | 3024 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 48 |
| NO_SOURCE_CAP_DIAGNOSTIC | 3024 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 48 |

## rag-v0-039 完整选择轨迹

该 Parent 支持两个不同 Gold claim：来源记载的 2011 年、2012 年商业活动。因此 Parent 命中可恢复两个 evidence unit；它们不是重复 evidence。

- Gold Parent：`parent-17f46282a4d28c6c000e6aff`；document：`doc-jc-v1-030-00f0479d20c219dffad5`；source：`jc-v1-030`。
- Selection 前全局 Parent rank：**47**；source-local rank：**27**；fusion score：0.020239065505。
- 同 source 排在它前面的 Parent：`parent-8d59ed7af6579daa7f5d43a8` (global #2, baseline selected 2), `parent-bd4bed6c3e90a92dce9b690e` (global #6, baseline selected 6), `parent-b5ad8865418368572e7d4d92` (global #7, baseline selected 7), `parent-f920c4f8d6dcc713eb4888a7` (global #8, baseline selected 8), `parent-62e917bc84f9d2a722710d2c` (global #9, baseline selected 83), `parent-1f6478d4e349644dc27f77fb` (global #10, baseline selected 84), `parent-cfdde7bca9bed77c7a31e203` (global #11, baseline selected 85), `parent-b6ac2fdc90d65cc57ab66a97` (global #13, baseline selected 86), `parent-572b33233e9a30a06ab6d630` (global #15, baseline selected 87), `parent-24920b368d1cce60591f58bf` (global #16, baseline selected 88), `parent-d82cde6546735fc36141ed20` (global #17, baseline selected 89), `parent-933e3cef0982686002c36e63` (global #18, baseline selected 90), `parent-eaf0efd7d4e8dc4078771f80` (global #19, baseline selected 91), `parent-34ec1041fe58b4d90395e32d` (global #21, baseline selected 92), `parent-3467f6e492469ee31b498273` (global #23, baseline selected 93), `parent-09a09073bd75874b3cc5b72a` (global #24, baseline selected 94), `parent-358c0f16040a986866b78139` (global #25, baseline selected 95), `parent-05cfbd1996cc2409e2d42b0c` (global #26, baseline selected 96), `parent-dfdce4940f5ffc7523b51532` (global #28, baseline selected 97), `parent-deb4f852a206b75236fa201e` (global #30, baseline selected 98), `parent-09893f769ae5ca2bd9dc419c` (global #32, baseline selected 99), `parent-5e614cf3933d7f2fd173b0b7` (global #35, baseline selected 100), `parent-d11ce63cb408443735bba811` (global #36, baseline selected no), `parent-f14a9d7435fced970dc4a6f8` (global #40, baseline selected no), `parent-b8c7a37f2fd91f4f445413fe` (global #41, baseline selected no), `parent-a8a549bd39b809a102b566e8` (global #42, baseline selected no)。

| cap | Gold selected? | source-local rank | global rank | initial block reason | overflow position | overflow filled slots | final selected rank |
|---|---|---:|---:|---|---:|---:|---:|
| BASELINE | no | 27 | 47 | document_cap | 23 | 18 | — |
| SOURCE_CAP_6 | no | 27 | 47 | document_cap | 23 | 18 | — |
| SOURCE_CAP_8 | no | 27 | 47 | document_cap | 23 | 18 | — |
| SOURCE_CAP_10 | no | 27 | 47 | document_cap | 23 | 18 | — |
| SOURCE_CAP_15 | no | 27 | 47 | document_cap | 23 | 18 | — |
| NO_SOURCE_CAP_DIAGNOSTIC | no | 27 | 47 | document_cap | 23 | 18 | — |

### @40 辅助诊断

下表取本轮深度诊断 Top100 Parent 列表的前 40；不重跑线上 Top40。重点 unit 另列各 cap 命中及 Parent 最早 selected rank。

| cap | Hybrid macro @40 | Hybrid micro @40 | 037/claim:1 | 037/claim:2 | 043/claim:2 |
|---|---:|---:|---|---|---|
| BASELINE | 79.41% | 70/95 | MISS (Parent #62) | MISS (Parent #60) | MISS (Parent #50) |
| SOURCE_CAP_6 | 79.41% | 70/95 | MISS (Parent #62) | MISS (Parent #60) | MISS (Parent #50) |
| SOURCE_CAP_8 | 79.41% | 70/95 | MISS (Parent #62) | MISS (Parent #60) | MISS (Parent #50) |
| SOURCE_CAP_10 | 79.41% | 70/95 | MISS (Parent #62) | MISS (Parent #60) | MISS (Parent #50) |
| SOURCE_CAP_15 | 79.41% | 70/95 | MISS (Parent #62) | MISS (Parent #60) | MISS (Parent #50) |
| NO_SOURCE_CAP_DIAGNOSTIC | 79.41% | 70/95 | MISS (Parent #62) | MISS (Parent #60) | MISS (Parent #50) |

## Recovered / lost Gold 明细

### SOURCE_CAP_6

Recovered: none。
Lost: none。

### SOURCE_CAP_8

Recovered: none。
Lost: none。

### SOURCE_CAP_10

Recovered: none。
Lost: none。

### SOURCE_CAP_15

Recovered: none。
Lost: none。

### NO_SOURCE_CAP_DIAGNOSTIC

Recovered: none。
Lost: none。

## 判断与限制

所测有限 source cap 中没有同时恢复 039 两条 Gold、且 lost Gold=0 的配置；不能建议简单上调全局 cap。

本轮六个配置的完整 Top100 Parent 顺序逐 case 完全相同；source cap 初次触发数为 0，candidate churn、Gold 增损均为 0。因此当前 source cap 对这份冻结 index 和固定 document cap 是冗余约束，无法判断它一般是否有助于防止同 source 冗余，也没有证据显示它在本轮误拦同 source 的有效证据。

039 的 source-local rank 27，但初次阻断始终是 document cap=4；overflow 位置 23，只有 18 个回填槽。取消 source cap 后仍然如此。单独提高 source cap 不存在恢复 039 的最小值。这不排除 document cap 与 source cap 的交互：上一轮提高 document cap 后，source cap 才可能成为下一道阻断；本轮固定 document cap，不做联合调参。

**归因更正：**前一份 weighted RRF audit 将 037/claim:1、037/claim:2、043/claim:2 的 Top40 下移归因于 source cap。按本轮与 production selector 完全一致的初次决策重放，这三条分别被 document cap 初次拦下，随后由 overflow 回填到 Parent #62、#60、#50。此前的 source-cap 归因不成立；Gold 命中数和 RRF score 验证不受此更正影响。

Source cap 的价值和成本应以初次阻断、最终 Gold、Top100 候选替换及 diversity 同时判断。即使初次被某个 cap 拦下，overflow 仍可回填；因此初次排除数不能直接解释最终 Recall。

NO_SOURCE_CAP_DIAGNOSTIC 是在 document cap 保持原值条件下的 source-cap 移除诊断，不是取消全部 cap 的上界，也不是 production 建议。没有修改 metadata policy、document cap、Planner、RRF、reranker、Context Builder 或 production serving。

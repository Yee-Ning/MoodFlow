# Gold V4 Document Cap Coverage Ablation

## 实验边界

本实验固定 Gold V4、frozen corpus、index/projection、embedding revision、历史 Query Planner legs、Dense/BM25、weighted RRF、per-leg Child budget、Top100 Parent budget、source cap、SAFE_COMBINED_V2_CAP_5、reranker 与 Context Builder。仅在只读诊断中改变 document cap；没有改 production config。39 个 eligible case、95 个 positive evidence unit。

Production selection 仅在 timeline/comparison/influence_style/exploratory query 上采用 broad_document_cap=4；其他 query type 采用 default_document_cap=2。本轮 CAP_5/6/8/12 只改变 broad cap，其他 query type 保持 2。NO_DOCUMENT_CAP_DIAGNOSTIC 同时解除两类 document cap，仅供诊断；source cap=5 和所有其他选择规则仍保持原值。

- Gold SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；corpus SHA-256：`8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。
- Projection SHA-256：`663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`；index manifest SHA-256：`0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。
- Retrieval config SHA-256：`b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`；Planner config SHA-256：`afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77`。
- Filter V2 JSONL SHA-256：`69ab5feea651e9cd693e797d21c7c4dacfd99d371fb6838cad999c466ba74c06`；historical archive SHA-256：`dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`。
- 本轮 cap=4 的 Dense/BM25/Hybrid Top100 Parent ID 顺序，39/39 case 与上一轮 SAFE_COMBINED_V2_CAP_5 完全相同。

## 主表

Macro Recall@100 为 39 个 case 等权平均；micro 为命中 evidence unit / 95。Diversity 为每 case Top100 中不同 document 数的平均值。Parent churn 单位为 case–Parent 槽位。

| document cap | Dense@100 | BM25@100 | Hybrid@100 | micro hits | recovered | lost | net | new non-Gold Parent | Gold displaced | document diversity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE | 85.82% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 31.05 |
| CAP_5 | 86.18% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 98 | 0 | 30.67 |
| CAP_6 | 86.18% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 98 | 0 | 30.67 |
| CAP_8 | 86.18% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 98 | 0 | 30.67 |
| CAP_12 | 86.18% | 83.13% | 89.29% | 85/95 | 0 | 0 | +0 | 98 | 0 | 30.67 |
| NO_DOCUMENT_CAP_DIAGNOSTIC | 86.18% | 83.77% | 89.29% | 85/95 | 0 | 0 | +0 | 544 | 0 | 26.77 |

## Candidate churn 与 document concentration

Cap 排除数是生产算法初次扫描时因 document cap 被放入 overflow 的 Parent；若最终 Top100 未满，算法会从 overflow 回填，因此它不等于最终损失数。`cap_hit documents` 只计确有 Parent 因 cap 被拦下的 document。逐 case document 分布、Top100/churn Parent ID 和 Gold Parent block reason 在 JSONL；039 另保留完整选择决策。

| cap | retained | newly admitted | displaced | new same-document | displaced other-document | Gold displaced other-document | cap-hit documents | cap-excluded Parents | cap-excluded Gold Parents | max concentration |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE | 0 | 0 | 0 | 0 | 0 | 0 | 551 | 4603 | 3 | 48 |
| CAP_5 | 2926 | 98 | 98 | 98 | 98 | 0 | 508 | 4380 | 2 | 48 |
| CAP_6 | 2926 | 98 | 98 | 98 | 98 | 0 | 435 | 3605 | 1 | 48 |
| CAP_8 | 2926 | 98 | 98 | 98 | 98 | 0 | 435 | 3605 | 1 | 48 |
| CAP_12 | 2926 | 98 | 98 | 98 | 98 | 0 | 435 | 3605 | 1 | 48 |
| NO_DOCUMENT_CAP_DIAGNOSTIC | 2480 | 544 | 544 | 544 | 544 | 0 | 0 | 0 | 0 | 29 |

## rag-v0-039 detailed trace

Gold 的两条 evidence unit 共用同一个 Parent，因此该 Parent 入选会同时恢复两条；计分仍以两条 Gold evidence unit 为单位。

- Gold Parent：`parent-17f46282a4d28c6c000e6aff`；document：`doc-jc-v1-030-00f0479d20c219dffad5`；source：`jc-v1-030`。
- Selection 前 fused distinct Parent global rank：**47**；同 document 的 local rank：**27**；同 source 的 local rank：**27**；fusion score：0.02023907。
- 同 document 先于 Gold 的 Parent：`parent-8d59ed7af6579daa7f5d43a8` (global #2, cap4 selected), `parent-bd4bed6c3e90a92dce9b690e` (global #6, cap4 selected), `parent-b5ad8865418368572e7d4d92` (global #7, cap4 selected), `parent-f920c4f8d6dcc713eb4888a7` (global #8, cap4 selected), `parent-62e917bc84f9d2a722710d2c` (global #9, cap4 selected), `parent-1f6478d4e349644dc27f77fb` (global #10, cap4 selected), `parent-cfdde7bca9bed77c7a31e203` (global #11, cap4 selected), `parent-b6ac2fdc90d65cc57ab66a97` (global #13, cap4 selected), `parent-572b33233e9a30a06ab6d630` (global #15, cap4 selected), `parent-24920b368d1cce60591f58bf` (global #16, cap4 selected), `parent-d82cde6546735fc36141ed20` (global #17, cap4 selected), `parent-933e3cef0982686002c36e63` (global #18, cap4 selected), `parent-eaf0efd7d4e8dc4078771f80` (global #19, cap4 selected), `parent-34ec1041fe58b4d90395e32d` (global #21, cap4 selected), `parent-3467f6e492469ee31b498273` (global #23, cap4 selected), `parent-09a09073bd75874b3cc5b72a` (global #24, cap4 selected), `parent-358c0f16040a986866b78139` (global #25, cap4 selected), `parent-05cfbd1996cc2409e2d42b0c` (global #26, cap4 selected), `parent-dfdce4940f5ffc7523b51532` (global #28, cap4 selected), `parent-deb4f852a206b75236fa201e` (global #30, cap4 selected), `parent-09893f769ae5ca2bd9dc419c` (global #32, cap4 selected), `parent-5e614cf3933d7f2fd173b0b7` (global #35, cap4 selected), `parent-d11ce63cb408443735bba811` (global #36, cap4 not selected), `parent-f14a9d7435fced970dc4a6f8` (global #40, cap4 not selected), `parent-b8c7a37f2fd91f4f445413fe` (global #41, cap4 not selected), `parent-a8a549bd39b809a102b566e8` (global #42, cap4 not selected)。
- 当前 cap=4 在该 document 最终 Top100 实际选中 **22 个 Parent**（包括 overflow 回填）：`parent-8d59ed7af6579daa7f5d43a8`, `parent-bd4bed6c3e90a92dce9b690e`, `parent-b5ad8865418368572e7d4d92`, `parent-f920c4f8d6dcc713eb4888a7`, `parent-62e917bc84f9d2a722710d2c`, `parent-1f6478d4e349644dc27f77fb`, `parent-cfdde7bca9bed77c7a31e203`, `parent-b6ac2fdc90d65cc57ab66a97`, `parent-572b33233e9a30a06ab6d630`, `parent-24920b368d1cce60591f58bf`, `parent-d82cde6546735fc36141ed20`, `parent-933e3cef0982686002c36e63`, `parent-eaf0efd7d4e8dc4078771f80`, `parent-34ec1041fe58b4d90395e32d`, `parent-3467f6e492469ee31b498273`, `parent-09a09073bd75874b3cc5b72a`, `parent-358c0f16040a986866b78139`, `parent-05cfbd1996cc2409e2d42b0c`, `parent-dfdce4940f5ffc7523b51532`, `parent-deb4f852a206b75236fa201e`, `parent-09893f769ae5ca2bd9dc419c`, `parent-5e614cf3933d7f2fd173b0b7`。

| cap | Gold Parent selected? | document-local rank | global Parent rank | initial block reason | overflow position / filled slots | selected rank |
|---|---|---:|---:|---|---:|---:|
| BASELINE | no | 27 | 47 | document_cap | 23 / 18 | — |
| CAP_5 | no | 27 | 47 | document_and_source_cap | 22 / 11 | — |
| CAP_6 | no | 27 | 47 | source_cap | 22 / 11 | — |
| CAP_8 | no | 27 | 47 | source_cap | 22 / 11 | — |
| CAP_12 | no | 27 | 47 | source_cap | 22 / 11 | — |
| NO_DOCUMENT_CAP_DIAGNOSTIC | no | 27 | 47 | source_cap | 22 / 11 | — |

旧诊断的 fused rank ≈89 属于先前的候选政策；固定 SAFE_COMBINED_V2_CAP_5 后，本轮重新计算的同一 Gold Parent 排名为 47。即使它进入 selection 前 Top100，source cap 与 overflow 回填顺序仍可使其最终 MISS。

039 的两条 Gold claim 分别是来源记载的 2011 年与 2012 年商业活动，属于**同一 Parent 支持的两个不同事实**，不能按重复 Parent 将它们从 Gold 分母合并或删除。

cap=6、8、12 与 NO_DOCUMENT_CAP_DIAGNOSTIC 在 039 的 Hybrid Top100 Parent 顺序完全相同；仅提高 document cap 不存在可恢复这两条 Gold 的最小值。

### 其他被 document cap 初次排除的 Gold Parent

| case | Parent | selection 前全局 rank | cap=4 结果 | no-document-cap 结果 | 解释 |
|---|---|---:|---|---|---|
| rag-v0-024 | `parent-790e32e522e2ba2f480a77ea` | 310 | document_cap | top100_filled_before_reached | 全局候选排名本身超过 100；解除文档 cap 后仍无法入选。 |
| rag-v0-040 | `parent-a83673d2b967ec913e6b0ef3` | 151 | document_cap | top100_filled_before_reached | 全局候选排名本身超过 100；解除文档 cap 后仍无法入选。 |

## 逐 case 变化

### CAP_5

Recovered evidence units: none。
Lost evidence units: none。

### CAP_6

Recovered evidence units: none。
Lost evidence units: none。

### CAP_8

Recovered evidence units: none。
Lost evidence units: none。

### CAP_12

Recovered evidence units: none。
Lost evidence units: none。

### NO_DOCUMENT_CAP_DIAGNOSTIC

Recovered evidence units: none。
Lost evidence units: none。

## 判断与限制

本轮所有 broad cap 配置及 no-document-cap 诊断的 Hybrid Recall 均为 89.29%、85/95；没有恢复或损失 Gold。cap=5 相对基线替换了 98 个 case–Parent 槽位，均为同文档新 Parent 挤出其他文档 Parent；平均 document diversity 由 31.05 降至 30.67。no-document-cap 的 churn 达 544，diversity 降至 26.77。该变化未带来 Hybrid Gold 增益，因此当前证据不支持提高全局 document cap。

若 039 两条证据全部恢复且没有损失，理论值约为 91.85%、87/95；实测所有配置均为 89.29%、85/95，理论增益未发生。

Selection cap 不是硬性上限：Top100 未满时生产算法会按融合顺序从 overflow 回填。039 的 cap=4 最终已有同文档 22 个 Parent，远超初次扫描上限 4。cap 增大时回填槽位也会变化，因此最终同文档 Parent 数不保证单调上升。

039 是同文档中的不同有效 evidence 被最终选择链路挡住，但 document cap 不是唯一瓶颈；固定 source cap 和回填顺序继续阻断该 Parent。本轮不改 source cap，后续若要研究只能另设独立受控实验。

NO_DOCUMENT_CAP_DIAGNOSTIC 只衡量上界，不作为 production 建议。本轮不实现 query-aware/adaptive/evidence-diversity 策略，不修改 reranker、Context Builder 或 production config。

## 验证

- 39/39 case 的 cap=4 Dense/BM25/Hybrid Top100 Parent 顺序与上一轮 Filter V2 报告一致；Gold/corpus/index/config SHA 均已核对。
- 独立从 JSONL 重算 39 case、95 evidence unit 的 macro/micro、recovered/lost、Parent churn 与 document 分布，全部一致。
- `pytest tests/test_run_rag_hybrid_shadow_baseline.py tests/test_retrieval_fusion_qdrant.py tests/test_retrieval_encoding_filters.py tests/test_retrieval_service.py -q`：27 passed，1 skipped。

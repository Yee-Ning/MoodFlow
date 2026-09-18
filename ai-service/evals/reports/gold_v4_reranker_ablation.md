# Gold V4 Reranker Ablation

## 固定口径

只读 2×2 实验。固定 Gold V4、historical SearchPlan、frozen corpus/index/projection、embedding revision、每 leg 600 Child、Filter V2 SAFE_COMBINED_V2_CAP_5、weighted RRF k=60、document/source cap 与 selector。只比较 reranker 输入文本和候选预算 40/60。模型为 Qwen/Qwen3-Reranker-0.6B，revision e61197ed45024b0ed8a2d74b80b4d909f1255473；instruction、score 公式、1024 token 上限和 Top12 不变。生产代码和配置未修改。

Top40/60 候选来自同一固定深度的 Filter V2 候选池，并逐 case 对照既有 Candidate Budget Ablation 的 Parent ID 顺序与 baseline Top12。历史正式 Top40→Top12 为 69/95→53/95，与本实验的 V2 70/95→55/95 是不同检索口径，不拼成一条漏斗。Top60 超过线上 reranker 的 40 候选 contract，离线直接调用同一模型 scorer。

Parent+Child 保留原 Evidence: best Child、Title、Breadcrumb 在前，再追加 deferred Parent content。所有候选统一增强，仍受 12,000 字符字段限制与 1024 token 模型上限。每 case 对两种输入各评分一次，分数跨 Top40/60 复用。

39 个 full/partial eligible case、95 个 positive evidence unit。Equivalent 任一 acceptable Parent 即命中，complementary 要求全部。Recall@12 macro 为各 case unit recall 等权平均，micro 为 unit 命中数/95。

冻结输入 SHA-256：Gold `1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；index manifest `0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`；retrieval config `b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`；reranker config `aa09b6e80ed52defb97ad6dadeed2c48ef3c014d4126713b25b1afed012ef9fb`；historical Planner archive `dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`；先前 Candidate Budget JSONL `9798e505043405503e3f79a811bd2753740b2b97585f6e19bbca00ef7d562538`。

## 2×2 结果

| 输入 | Candidate budget | Candidate hit | Recall@12 micro | Recall@12 macro | vs baseline40 recovered | lost | net |
|---|---:|---:|---:|---:|---:|---:|---:|
| BASELINE_40 | 40 | 70/95 | 55/95 | 68.43% | 0 | 0 | +0 |
| PARENT_CHILD_40 | 40 | 70/95 | 57/95 | 70.08% | 2 | 0 | +2 |
| BASELINE_60 | 60 | 78/95 | 56/95 | 69.29% | 1 | 0 | +1 |
| PARENT_CHILD_60 | 60 | 78/95 | 57/95 | 69.65% | 2 | 0 | +2 |

固定 candidate budget 后单独比较输入变化，并单独比较候选扩容：

| comparison | recovered | lost | net | recovered units | lost units |
|---|---:|---:|---:|---|---|
| BASELINE_40 → PARENT_CHILD_40 | 2 | 0 | +2 | rag-v0-007/claim:1, rag-v0-040/claim:6 | none |
| BASELINE_40 → BASELINE_60 | 1 | 0 | +1 | rag-v0-037/claim:2 | none |
| BASELINE_60 → PARENT_CHILD_60 | 1 | 0 | +1 | rag-v0-040/claim:6 | none |
| PARENT_CHILD_40 → PARENT_CHILD_60 | 1 | 1 | +0 | rag-v0-037/claim:2 | rag-v0-007/claim:1 |
| BASELINE_40 → PARENT_CHILD_60 | 2 | 0 | +2 | rag-v0-037/claim:2, rag-v0-040/claim:6 | none |

## Baseline40 失败分解

互斥分类取最早可观察阻断阶段。baseline_hit 的 55 条用于对账，其余五类合计为 40 条未命中。input_enhancement_recovered 是固定 Top40 候选、仅换输入后恢复；input_expression_suspected 指 Parent 有 exact Gold span 而最佳 Child 无字面匹配，增强仍未恢复；reranker_ordering 指最佳 Child 含 exact span 但模型排在 Top12 外。exact span 的格式差异可能造成假阴性；后两类不等同于因果证明。

| category | evidence units |
|---|---:|
| candidate_cutoff_40 | 8 |
| outside_top60 | 17 |
| baseline_hit | 55 |
| reranker_ordering | 10 |
| input_expression_suspected | 3 |
| input_enhancement_recovered | 2 |

下表为所有 baseline Top40 未命中 unit。Gold rank 对 equivalent 取最佳可接受 Parent，对 complementary 取最差必需 Parent。Score margin = Top12 最低分 − Gold 分。候选缺席时无 Gold 分数，标为“—”；正 margin 表示 Gold 分低于 cutoff。

| case / unit | category | candidate rank | rerank rank | Gold score | Top12 floor | margin | Parent+Child rank | Top60 baseline rank |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| rag-v0-001/claim:1 | candidate_cutoff_40 | — | — | — | 0.577495 | — | — | 22 |
| rag-v0-001/claim:2 | outside_top60 | — | — | — | 0.577495 | — | — | — |
| rag-v0-001/claim:4 | candidate_cutoff_40 | — | — | — | 0.577495 | — | — | 22 |
| rag-v0-001/claim:5 | candidate_cutoff_40 | — | — | — | 0.577495 | — | — | 22 |
| rag-v0-002/claim:1 | candidate_cutoff_40 | — | — | — | 0.182426 | — | — | 32 |
| rag-v0-002/claim:2 | reranker_ordering | 29 | 26 | 0.031144 | 0.182426 | 0.151282 | 25 | 29 |
| rag-v0-002/claim:3 | reranker_ordering | 20 | 19 | 0.067547 | 0.182426 | 0.114879 | 24 | 21 |
| rag-v0-002/claim:4 | reranker_ordering | 6 | 14 | 0.148047 | 0.182426 | 0.034378 | 17 | 15 |
| rag-v0-002/claim:7 | reranker_ordering | 20 | 19 | 0.067547 | 0.182426 | 0.114879 | 24 | 21 |
| rag-v0-003/claim:1 | outside_top60 | — | — | — | 0.893309 | — | — | — |
| rag-v0-003/claim:2 | candidate_cutoff_40 | — | — | — | 0.893309 | — | — | 37 |
| rag-v0-004/claim:1 | outside_top60 | — | — | — | 0.679179 | — | — | — |
| rag-v0-004/claim:2 | input_expression_suspected | 11 | 30 | 0.063715 | 0.679179 | 0.615464 | 31 | 35 |
| rag-v0-004/claim:3 | reranker_ordering | 19 | 28 | 0.085099 | 0.679179 | 0.594080 | 27 | 32 |
| rag-v0-007/claim:1 | input_enhancement_recovered | 12 | 14 | 0.362969 | 0.468791 | 0.105821 | 12 | 17 |
| rag-v0-023/claim:1 | reranker_ordering | 12 | 21 | 0.132964 | 0.743168 | 0.610204 | 29 | 30 |
| rag-v0-024/claim:2 | outside_top60 | — | — | — | 0.268941 | — | — | — |
| rag-v0-024/claim:3 | outside_top60 | — | — | — | 0.268941 | — | — | — |
| rag-v0-024/claim:4 | outside_top60 | — | — | — | 0.268941 | — | — | — |
| rag-v0-026/group:026-award | reranker_ordering | 7 | 14 | 0.020332 | 0.044681 | 0.024349 | 16 | 14 |
| rag-v0-034/claim:3 | input_expression_suspected | 25 | 29 | 0.027585 | 0.437824 | 0.410238 | 26 | 36 |
| rag-v0-034/claim:4 | input_expression_suspected | 25 | 29 | 0.027585 | 0.437824 | 0.410238 | 26 | 36 |
| rag-v0-036/claim:2 | outside_top60 | — | — | — | 0.256832 | — | — | — |
| rag-v0-036/claim:3 | outside_top60 | — | — | — | 0.256832 | — | — | — |
| rag-v0-036/claim:4 | candidate_cutoff_40 | — | — | — | 0.256832 | — | — | 57 |
| rag-v0-036/claim:5 | reranker_ordering | 11 | 26 | 0.031144 | 0.256832 | 0.225688 | 16 | 29 |
| rag-v0-037/claim:1 | outside_top60 | — | — | — | 0.348645 | — | — | — |
| rag-v0-037/claim:2 | candidate_cutoff_40 | — | — | — | 0.348645 | — | — | 7 |
| rag-v0-039/claim:1 | outside_top60 | — | — | — | 0.182426 | — | — | — |
| rag-v0-039/claim:2 | outside_top60 | — | — | — | 0.182426 | — | — | — |
| rag-v0-040/claim:2 | outside_top60 | — | — | — | 0.500000 | — | — | — |
| rag-v0-040/claim:3 | outside_top60 | — | — | — | 0.500000 | — | — | — |
| rag-v0-040/claim:5 | outside_top60 | — | — | — | 0.500000 | — | — | — |
| rag-v0-040/claim:6 | input_enhancement_recovered | 16 | 14 | 0.281406 | 0.500000 | 0.218594 | 10 | 14 |
| rag-v0-040/claim:7 | outside_top60 | — | — | — | 0.500000 | — | — | — |
| rag-v0-042/claim:1 | reranker_ordering | 21 | 30 | 0.015906 | 0.075858 | 0.059952 | 35 | 44 |
| rag-v0-042/claim:2 | reranker_ordering | 15 | 14 | 0.044681 | 0.075858 | 0.031177 | 17 | 17 |
| rag-v0-043/claim:2 | candidate_cutoff_40 | — | — | — | 0.766294 | — | — | 30 |
| rag-v0-045/claim:1 | outside_top60 | — | — | — | — | — | — | — |
| rag-v0-065/claim:1 | outside_top60 | — | — | — | 0.014957 | — | — | — |

## 变化 case 与输入上限

- rag-v0-007: BASELINE_40 1/2; PARENT_CHILD_40 2/2; BASELINE_60 1/2; PARENT_CHILD_60 1/2；问题：歌曲《青花瓷》体现了什么风格
- rag-v0-037: BASELINE_40 1/3; PARENT_CHILD_40 1/3; BASELINE_60 2/3; PARENT_CHILD_60 2/3；问题：周杰伦2003年的重要经历是什么
- rag-v0-040: BASELINE_40 2/7; PARENT_CHILD_40 3/7; BASELINE_60 2/7; PARENT_CHILD_60 3/7；问题：周杰伦的音乐生涯时间线

Parent+Child 在 Top60 的 1969 个 case–Parent 输入中，有 1964 个编码长度高于 baseline；这只证明 Parent 至少部分进入模型输入，不等于 Gold span 一定保留。逐 Parent 的 exact span 包含情况、token delta、分数和排名在 JSONL。

## Top40→Top60 新增候选是否被利用

Top60 新进入候选层的 8 条 Gold unit 中，只有 037/claim:2 进入 Top12。001 的 3 条 unit 共用一个 Parent，因此这里的 8 条并非 8 个独立 Gold Parent。

| case / unit | Top60 candidate rank | rerank rank | Gold score | cutoff − Gold | Top12? |
|---|---:|---:|---:|---:|---|
| 001/claim:1,4,5 | 42 | 22 | 0.245085 | +0.332410 | 否 |
| 002/claim:1 | 51 | 32 | 0.020332 | +0.181481 | 否 |
| 003/claim:2 | 41 | 37 | 0.019124 | +0.874185 | 否 |
| 036/claim:4 | 48 | 57 | 0.000392 | +0.256440 | 否 |
| 037/claim:2 | 60 | 7 | 0.936285 | −0.084332 | 是 |
| 043/claim:2 | 50 | 30 | 0.392337 | +0.405850 | 否 |

## 判断与限制

**候选 cutoff 与排序同时限制增益。** Baseline Top40 有 25/95 条 unit 未进入候选；其中 8 条在 Top60 可找到，17 条仍不在 Top60。新增的 8 条有 7 条被模型排在 Top12 外，且 score margin 为正。因而加大候选池本身只把 Recall@12 从 55/95 提到 56/95；这解释了“新增候选没有等比例转成答案证据”。另有 10 条已经在 Top40、且最佳 Child 字面包含 Gold span，却被排到 Top12 外，属于可观察的排序问题；不能以这些结果推断模型内部为何打低分。

**Parent+Child 带来有限且依赖预算的恢复。** 固定 Top40 时 007/claim:1 从 #14→#12、040/claim:6 从 #14→#10，Recall@12 为 57/95。007 的最佳 Child 原本已含 exact Gold span，增强使分数从 0.362969 到 0.562177，属于输入形式改变后的排序改善；不能将它归为 Child 缺证据。040 的最佳 Child 无 exact span、Parent 有，增强使分数从 0.281406 到 0.651355，支持输入表达不足的解释。另有 004/claim:2、034/claim:3 与 claim:4 的 Parent 有 exact span、Child 无，但追加 Parent 后仍未进 Top12；因此不能认为全文追加普遍解决表达问题。

**Top60 与增强不能简单相加。** Top60 baseline 恢复 037/claim:2（候选 #60→rerank #7）；Parent+Child Top40 恢复的 007 在 Parent+Child Top60 被新竞争者挤到 #15。相对 Parent+Child Top40，Parent+Child Top60 是恢复 1、丢失 1，micro 同为 57/95，macro 从 70.08% 降到 69.65%。所有 2×2 arm 相对 Baseline40 原有命中均未回退，但 arm 之间确有这条非单调损失。

本实验不评估最终 Context 或生产时延；Top60 绕过线上 40 候选 contract，仅为离线诊断。Parent 内容在 1024 token 截断之后具体保留了哪些句子未逐条证明，exact span 的字面检查也可能受空白与格式影响。由于固定 Filter V2 深度候选池与历史正式检索不同，表中 68.43% 不能替代历史正式 67.42%。结果不足以直接改生产 reranker 输入或候选上限。

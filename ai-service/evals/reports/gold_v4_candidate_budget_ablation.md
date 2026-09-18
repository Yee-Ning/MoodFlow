# Gold V4 Hybrid Candidate Budget Ablation

## 实验边界与口径

只读实验：固定冻结 Gold V4 39 个正向 case / 95 个 supported evidence unit、历史 SearchPlan/query、corpus/index/projection、embedding revision、Filter V2 `SAFE_COMBINED_V2_CAP_5`、Dense/BM25 结果、weighted RRF k=60、document/source cap、reranker model/revision/1024 token 输入、Top12、Context Builder。对每 case 只查询一次固定 Filter V2 raw legs（每 leg 600 Child），一次融合且**不预截断 fused Child**，随后仅改变 `candidate_top_k=40/50/60/80` 重放 selector；同一 Parent 的 Qwen reranker score 只计算一次并在四档复用，最后用现有 Context Builder 逐档构建 Context。未修改生产配置或参数。

这是一组固定深度候选池的**受控 budget ablation**。正式线上 `candidate_top_k` 还会改变 per-leg 查询深度与 fused Child 截断（Top40 为 80）；本实验将二者固定，以隔离 Parent candidate budget 的效应。结果不可直接当成生产改为 Top50/60/80 的指标。现有 reranker 输入 contract 只接受 ≤40 个候选，离线评分在不改模型/输入文本/参数的前提下直接调用同一 scorer；Top12 后以相同 Context Builder 重放。本轮 Top40 的 70/55/52 漏斗与历史正式 Top40 的 69/53/50 不同，原因是本轮固定 Filter V2 和深度候选池，二者不可拼接。

Gold/corpus/projection/manifest SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e` / `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a` / `663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0` / `0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。Retrieval/Planner/Reranker config SHA-256：`b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3` / `afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77` / `aa09b6e80ed52defb97ad6dadeed2c48ef3c014d4126713b25b1afed012ef9fb`。

Recall 采用 Gold V4 group-aware unit 匹配：micro 为 95 条中的命中数，macro 为 39 case 等权平均。Context Recall 是 Gold 到达最终 Context 的比例，不是 Context Precision。

## Budget 收益曲线

| Candidate budget | 实际候选槽位/39 case | Candidate micro | Candidate macro | Rerank@12 micro | Rerank@12 macro | Final Context micro | Final Context macro |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 40 | 1385 | 70/95 | 79.41% | 55/95 | 68.43% | 52/95 | 65.44% |
| 50 | 1683 | 76/95 | 83.60% | 55/95 | 68.43% | 52/95 | 65.44% |
| 60 | 1969 | 78/95 | 84.82% | 56/95 | 69.29% | 52/95 | 63.73% |
| 80 | 2521 | 81/95 | 86.89% | 57/95 | 70.14% | 52/95 | 63.73% |

Top40→Top80 实际多评分 **1,136** 个 case–Parent 候选槽位（2,521 对 1,385）；Candidate 多命中 **11** 条 Gold、Rerank Top12 多命中 **2** 条，Final Context 的 micro 净增 **0**。因此预算确实限制候选层 Recall，但在本次固定模型与 Context Builder 下没有提高最终 micro Recall。Top60/80 的 Context macro 反而较 Top40 低 1.71 个百分点，因为不同 case 的增失权重不同。

相邻档位的净变化与非单调损失如下。新增候选可能改变 selector 选取和 reranker Top12，因此逐档检查 recovered/lost；相同 Parent 多个 claim 分别计 unit。

| 变化 | Candidate +/− | Rerank@12 +/− | Final Context +/− |
|---|---:|---:|---:|
| 40→50 | +6/−0 | +0/−0 | +0/−0 |
| 50→60 | +2/−0 | +1/−0 | +1/−1 |
| 60→80 | +3/−0 | +1/−0 | +1/−1 |

## 新增恢复与原有 Gold 下降

### Top40 → Top50

- Candidate：新增 6 条：`rag-v0-001/claim:1`, `rag-v0-001/claim:4`, `rag-v0-001/claim:5`, `rag-v0-003/claim:2`, `rag-v0-036/claim:4`, `rag-v0-043/claim:2`；丢失 0 条：无。
- Rerank Top12：新增 0 条：无；丢失 0 条：无。
- Final Context：新增 0 条：无；丢失 0 条：无。

### Top40 → Top60

- Candidate：新增 8 条：`rag-v0-001/claim:1`, `rag-v0-001/claim:4`, `rag-v0-001/claim:5`, `rag-v0-002/claim:1`, `rag-v0-003/claim:2`, `rag-v0-036/claim:4`, `rag-v0-037/claim:2`, `rag-v0-043/claim:2`；丢失 0 条：无。
- Rerank Top12：新增 1 条：`rag-v0-037/claim:2`；丢失 0 条：无。
- Final Context：新增 1 条：`rag-v0-037/claim:2`；丢失 1 条：`rag-v0-066/claim:1`。

### Top40 → Top80

- Candidate：新增 11 条：`rag-v0-001/claim:1`, `rag-v0-001/claim:4`, `rag-v0-001/claim:5`, `rag-v0-002/claim:1`, `rag-v0-003/claim:1`, `rag-v0-003/claim:2`, `rag-v0-036/claim:4`, `rag-v0-037/claim:1`, `rag-v0-037/claim:2`, `rag-v0-040/claim:3`, `rag-v0-043/claim:2`；丢失 0 条：无。
- Rerank Top12：新增 2 条：`rag-v0-003/claim:1`, `rag-v0-037/claim:2`；丢失 0 条：无。
- Final Context：新增 2 条：`rag-v0-003/claim:1`, `rag-v0-037/claim:2`；丢失 2 条：`rag-v0-003/claim:3`, `rag-v0-066/claim:1`。

## 结论与限制

候选层增量是否传递到 Top12 与 Context，应以上表分阶段实测为准；不能将 Candidate Recall 的增加直接当成最终 Recall。逐 case 的候选 ID、Rerank Top12、Final Context、每条 Gold 的四档命中与模型分数在 JSONL。

本实验只隔离 candidate budget。就当前目标而言，单独加大 budget **不值得直接改生产配置**：Top80 的候选收益被 Top12 与 Context 阶段基本吸收。若仍考虑部署，需要另做同一固定 Filter V2 下、按线上 `fused_limit=2×candidate_top_k` 与 per-leg limit 重放的完整链路验证，并检查运行时延迟与 reranker contract 扩容。本轮不改生产配置。

- 39/39 case 的固定 V2 Top100 Parent IDs 与先前 Filter V2 审计逐项一致；每档候选复用同一融合结果和 selector。
- 对每个 case 的同一 Parent，四档候选对象完全一致且模型分数只计算一次；同一输入在 reranker 排序键上确定性复用。
- 离线重放绕过了现有 `RerankInput.candidate_index≤39` 与 `RerankedCandidate.retrieval_rank≤40` 的数据校验上限，以便表达 50/60/80 候选；模型权重、文本模板、token 上限、score 公式和 Context Builder 代码未改。模型只推理一次，未测量跨批次或重复推理的微小分数波动。
- 上述重放仅覆盖 39 个正向 case；11 个无 supported evidence unit 的 Gold case 不进入 Recall 分母。

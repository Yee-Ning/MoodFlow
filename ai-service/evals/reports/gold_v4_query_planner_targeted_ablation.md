# Gold V4 Query Planner Targeted Ablation — rag-v0-024

## 实验边界

只读、仅 rag-v0-024。Gold V4、frozen corpus/index/projection、embedding model/revision、Dense/BM25 参数、每 leg 600 Child 的既有深度诊断预算、Filter V2 SAFE_COMBINED_V2_CAP_5、weighted RRF k=60、document/source cap、selector、reranker model/revision/1024 token 上限/Top12 全部固定。没有修改生产 Planner、Retrieval、RRF、reranker 或配置，也没有修改 Gold。

原问题：**谁为周杰伦的歌曲作词**。Baseline 使用历史 Planner 的 4 个 retrieval legs：

| leg | query |
|---|---|
| original Dense / BM25 | 谁为周杰伦的歌曲作词 |
| alias BM25 | 周杰伦 周杰倫 周董 |
| semantic Dense | 谁为周杰伦的歌曲作词 合作 关系 创作 |

Decomposed arm 只额外增加同一个通用字段词 query：**周杰伦 曲目表 词作者**，分别走 Dense 与 BM25；新 variant 使用既有 decomposed 权重 0.8。query 仅来自原问题和通用 schema synonym，未写入 Gold 歌曲名、作词人或证据文本。这里的“decomposed”是新增一条 schema-aware query variant，并非替换原问题，也不是多意图拆解。

Top40/60 从同一固定深度融合结果由相同 selector 选取。线上 reranker contract 最多接收 40 个候选；Top60 离线直接调用同一 Qwen scorer，不修改线上上限。Baseline Top40/60 的候选 Parent ID 顺序及 Top12 与既有 Candidate Budget Ablation 归档逐项一致。Top100 只计算 candidate coverage，没有送入 reranker。

Gold V4 将本 case 的 4 个 supported evidence unit 作为分母。其中 claim:2、claim:3、claim:4 共用一个 Gold Parent；以下 3 条 unit 的恢复只代表**一个新的 Gold Parent**被找到。

## 候选与 reranker 对照

| arm | Candidate@40 | Rerank@12（40） | Candidate@60 | Rerank@12（60） | Candidate@100 |
|---|---:|---:|---:|---:|---:|
| Baseline | 1/4 | 1/4 | 1/4 | 1/4 | 1/4 |
| Decomposed | 1/4 | 1/4 | 4/4 | 3/4 | 4/4 |
| 变化 | 0 | 0 | +3 | +2 净值 | +3 |

Top60 的 Rerank@12 变化是 **新增命中 3 条、失去原有命中 1 条**，净增 2 条。Candidate@60 和 Candidate@100 的 +3 均来自同一个 Parent。

新增 leg 的 Gold Parent 原始检索排名为 Dense **#280**、BM25 **#19**。该 Parent 的融合后、selector 前 Parent rank 从 baseline **#310** 到 decomposed **#120**；Top40 仍未选入，Top60 与 Top100 都以最终候选 **#45** 选入。这说明 query 改写提高了候选覆盖，但候选预算 40 仍挡住这一个 Parent。

## 逐条 Gold 轨迹

| unit | Gold claim | Baseline Top40/60 | Decomposed Top40 | Decomposed Top60 | Top60 rerank rank | Top60 Gold score | Top12 floor − Gold |
|---|---|---|---|---|---:|---:|---:|
| claim:1 | 《落雨声》是周杰伦与方文山的早期合作作品 | 候选 #1；Top12 #11 | 候选 #1；Top12 #12 | 候选 #1；Top12 **MISS** | #16 | 0.320821 | +0.194799 |
| claim:2 | 《藉口》由周杰伦作词 | 候选 MISS；Top12 MISS | 候选 MISS；Top12 MISS | 候选 #45；Top12 HIT | #8 | 0.607663 | −0.092043 |
| claim:3 | 《将军》由黄俊郎作词 | 候选 MISS；Top12 MISS | 候选 MISS；Top12 MISS | 候选 #45；Top12 HIT | #8 | 0.607663 | −0.092043 |
| claim:4 | 《我的地盘》由方文山作词 | 候选 MISS；Top12 MISS | 候选 MISS；Top12 MISS | 候选 #45；Top12 HIT | #8 | 0.607663 | −0.092043 |

正 score margin 表示 Gold 分数低于 Top12 最低分。原有 claim:1 的 Parent 没有从 Top60 候选中丢失，而是被新竞争者挤出模型 Top12；它在 decomposed Top60 的 rerank rank 为 #16。新增 Parent 的 #8 表明 reranker 能利用这条新候选，但无法保证保留所有原有 Gold。

## 候选变化与结论

相对 baseline，decomposed Top40 新进入 5 个 Parent，均非 Gold；Top60 新进入 12 个 Parent，其中 1 个是支持 claim:2–4 的 Gold Parent、11 个非 Gold。Top60 没有 Gold Parent 从候选池被挤出。Top100 新进入 14 个 Parent，其中 1 个 Gold、13 个非 Gold。

**结论：**对 024，新增 schema-aware query 确实将原本在 Top60 外的 Gold Parent 提升到候选 #45，并使其在固定 reranker 下进入 Top12 #8。单独增加 query 在 Top40 没有改善；Top60 虽恢复 3 个 evidence unit，也使原命中的 claim:1 从 Top12 落选，最终净增 2/4。改写、候选预算与 reranker 排序存在交互，不能把 Candidate Recall 的 +3 直接当成 Top12 的 +3。这里只测试一个 case、一条此前已筛选出的诊断 rewrite，不能推断跨 case 泛化或生产收益。

数据源 SHA-256：Gold 1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e；index manifest 0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033；retrieval config b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3；reranker config aa09b6e80ed52defb97ad6dadeed2c48ef3c014d4126713b25b1afed012ef9fb；historical Planner archive dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736。逐 Parent ID、全部候选与模型分数、Gold ranks、score margins 均在 JSONL。

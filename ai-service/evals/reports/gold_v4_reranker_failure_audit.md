# Gold V4 Reranker Failure Audit

## 口径与冻结边界

只读审计；没有修改生产 reranker、retrieval、planner、RRF、selector、Context Builder 或配置。Gold、corpus/index/projection、embedding 与 reranker revision 均沿用冻结归档。实际 reranker 失败用历史正式 Top40→Top12→Context Trace；Filter V2 的 Top100 是独立深度诊断归档，**尚无同口径 V2 Top40 reranker Trace**。因此不能把 V2 的 85/95 与历史的 67.42% 拼成一条漏斗。

Gold SHA-256 `1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；corpus/index projection/index manifest 分别为 `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`、`663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`、`0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`；retrieval/reranker config 分别为 `b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`、`aa09b6e80ed52defb97ad6dadeed2c48ef3c014d4126713b25b1afed012ef9fb`。历史 retrieval/trace/rerank diagnostics SHA-256 分别为 `4818bd4baf790255c9052e076f9d96a99baae4b4ee600316154887bc409e8b9d`、`d8540b47e605d93a97568ff21348181440aeaa9de5ef92f52290cd06c97ae036`、`aab31064e7f2be01c6ed9c084cf81d2325c256205a19bda519233e0ed1a806c2`；Filter V2 JSONL SHA-256 `69ab5feea651e9cd693e797d21c7c4dacfd99d371fb6838cad999c466ba74c06`。模型 `Qwen/Qwen3-Reranker-0.6B` revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`，Top40 输入、Top12 输出、1024 token 上限。

95 个 evidence unit 来自 39 个 full/partial case；evidence group 按原 group-aware 规则计数：equivalent 任一 Parent 命中，complementary 需全部命中。micro 是 unit 计数；67.42% 是 39 case 等权宏平均。表中 group rank 对 equivalent 取最佳 Parent，对 complementary 取最差必需 Parent。

## 漏斗与失败分布

| 阶段 | micro evidence units | 口径 |
|---|---:|---|
| 历史正式 Hybrid Top40 / reranker input | 69/95 | 实际候选 |
| 历史 Rerank Top12 | 53/95 | 实际模型输出 |
| 历史最终 Context | 50/95 | 实际 Context Builder |
| Filter V2 Hybrid Top100 | 85/95 | 独立深度诊断，非 reranker 输入 |

进入 reranker 的 Gold 为 **69**，Top12 成功 **53**、失败 **16**；条件 micro 保留率 **76.81%**，条件损失率 **23.19%**。全体 39 case 的 Rerank Recall@12（macro）为 **67.42%**，与归档 67.42% 一致。

| 原因（互斥、按 unit） | 数量 |
|---|---:|
| reranker 排序失败（Child 已含 exact Gold span） | 10 |
| candidate 不足 / 输入前丢失（历史正式 Top40 外） | 26 |
| chunk/Parent 表达问题（Parent 有 exact span，选中 Child 无） | 6 |
| Context 阶段丢失 | 3 |

上述 chunk 类是可观察的输入文本缺口，不代表已证明换输入可恢复。Top40 外的 26 条不属于 reranker miss；其原因包含检索、融合与选择，本轮不再细分。Context 阶段的 3 条在 Top12 内，不计入 reranker 的 16 条损失。

## 逐项 reranker miss

下表每行一条 Gold evidence unit；Fusion score 是正式 Top40 中对应 Parent 的加权 RRF score；Rerank score/rank 为保存的全部候选真实模型分数和 1–40 完整排名。完整 Parent 明细、equivalent/complementary 关系及 probe 结果见 JSONL。

| Case | Gold unit / claim | Hybrid rank | Hybrid score | Rerank rank | Rerank score | Input | Context | 分类 | Probe A rank | V2 Top100 rank |
|---|---|---:|---:|---:|---:|:---:|:---:|---|---:|---:|
| rag-v0-002 | `claim:2` 周杰伦在采访中把音乐理解为依靠想象力的广阔空间 | 31 | 0.01562500 | 31 | 0.035145 | Y | N | reranker_ordering | 31 | 29 |
| rag-v0-002 | `claim:3` 研究将《中国风》一词在华语乐坛活跃与 2003 年《东风破》发布相联系 | 12 | 0.02551802 | 24 | 0.067547 | Y | N | reranker_ordering | 30 | 20 |
| rag-v0-002 | `claim:4` 研究认为周杰伦作品中中国风歌曲占比较高 | 5 | 0.03017913 | 20 | 0.148047 | Y | N | reranker_ordering | 22 | 6 |
| rag-v0-002 | `claim:7` 中国风的五声音阶和民族乐器与电子音效结合 | 12 | 0.02551802 | 24 | 0.067547 | Y | N | reranker_ordering | 30 | 20 |
| rag-v0-004 | `claim:2` 杰威尔采访记载杨峻荣在 2000 年见到周杰伦时他已在录音室住了一年多 | 13 | 0.02534344 | 26 | 0.063715 | Y | N | chunk_parent_representation | 27 | 11 |
| rag-v0-004 | `claim:3` 人物报道记载杨俊荣听到《可爱女人》后推动公司推出周杰伦首张专辑 | 20 | 0.01930047 | 25 | 0.085099 | Y | N | reranker_ordering | 25 | 19 |
| rag-v0-007 | `claim:1` 方文山认为青花瓷具有中国传统文化的识别度 | 12 | 0.04097436 | 14 | 0.362969 | Y | N | reranker_ordering | 12 | 12 |
| rag-v0-023 | `claim:1` 报道将《东风破》《菊花台》《千里之外》列为方文山与周杰伦共同作品 | 11 | 0.02493499 | 29 | 0.125923 | Y | N | reranker_ordering | 33 | 12 |
| rag-v0-024 | `claim:1` 资料记载《落雨声》是周杰伦与方文山的早期合作作品 | 5 | 0.03624213 | 20 | 0.348645 | Y | N | chunk_parent_representation | 9 | 1 |
| rag-v0-026 | `group:026-award` 洪敬尧凭《蓝色风暴》获最佳编曲人提名 | 7 | 0.05212766 | 14 | 0.020332 | Y | N | reranker_ordering | 16 | 7 |
| rag-v0-034 | `claim:3` 方文山对青花瓷传统文化识别度的评价 | 25 | 0.04608006 | 30 | 0.027585 | Y | N | chunk_parent_representation | 28 | 25 |
| rag-v0-034 | `claim:4` 方文山将《青花瓷》列入中国风作品 | 25 | 0.04608006 | 30 | 0.027585 | Y | N | chunk_parent_representation | 28 | 25 |
| rag-v0-036 | `claim:5` 《11月的萧邦》于2005年发行的时间锚点 | 11 | 0.03378988 | 26 | 0.037327 | Y | N | reranker_ordering | 18 | 11 |
| rag-v0-040 | `claim:4` 2007年《我很忙》专辑年表 | 13 | 0.02579841 | 13 | 0.692642 | Y | N | reranker_ordering | 18 | 12 |
| rag-v0-040 | `claim:6` 2019年《说好不哭》发布 | 12 | 0.02627355 | 21 | 0.281406 | Y | N | chunk_parent_representation | 17 | 16 |
| rag-v0-043 | `claim:2` 周杰伦在直接问答中表示看电影会刺激创作灵感 | 35 | 0.03517000 | 26 | 0.392337 | Y | N | chunk_parent_representation | 19 | 50 |

## 三个只读 probe

**A — Parent+Child 输入。** 对出现正式 reranker miss 的全部 10 个 case，给每个 Top40 候选原输入文本末尾追加 `Parent: <parent_context.content>`，其余 query、模型/revision、参数、40 个候选及排序键不变；文本按现有 12,000 字符字段上限与 1024 token 模型上限截断。重新评分全部候选，而非只改 Gold。16 个 miss 中 **2** 个升入 Top12；同一批 case 原已命中的 unit 有 **0** 个跌出 Top12。此为输入结构敏感性实验，不是生产 Recall，也不能把单向恢复直接算净收益。逐 unit 新分数/排名见 JSONL。

**B — 40→60 候选。** Filter V2 固定 Top100 诊断列表的前缀覆盖：Top40 **70/95**、Top60 **78/95**、Top100 **85/95**；Top40→60 只新增 **8** 个 Gold unit。对本次 16 个历史 reranker miss，增加候选并不能使其从输入前恢复，因为它们已经在实际 Top40。此 probe 只能观察候选截断的检索上界：V2 诊断采用每 leg 600 Child，正式 Top40 使用每 leg Top80 且融合前截断；V2 Top60 未实际送入 reranker，不能据此声称 Top60 的 Rerank Recall。现有 reranker contract 上限也是 40，故本轮未改其参数去伪造 Top60 模型运行。

**C — 全量模型排序。** 历史诊断记录了每个正式输入的模型 score 和真实 1–40 排名，并与实际 Top12 输出逐项重放校验。逐 unit 的真实位置在上表与 JSONL `probe_c_full_rank`；无 Top12 截断造成的 rank 不可见问题。

## Context 阶段与下一步

- `rag-v0-040/claim:1`：Rerank #6，未进最终 Context；机制 `token_or_source_cap_applied`；pack warnings=['context_token_budget_applied', 'context_source_cap_applied']，pack tokens=2771/3000。
- `rag-v0-044/claim:1`：Rerank #9，未进最终 Context；机制 `max_items_6`；pack warnings=[]，pack tokens=2507/3000。
- `rag-v0-044/claim:3`：Rerank #9，未进最终 Context；机制 `max_items_6`；pack warnings=[]，pack tokens=2507/3000。
040 的 warnings 是 pack 全局信息，无法仅凭它区分该 Parent 究竟受 token 还是 source cap 阻断。

主要瓶颈判断：在历史正式链路里，Hybrid Top40 输入前缺 26/95，reranker 再丢 16/69；按**当前 16 条已召回却落选**的目标，reranker 排序/输入表达是直接瓶颈。Filter V2 Top100 的 85/95 说明深度检索上界较高，但没有验证 Filter V2 正式 Top40→Top12 是否仍有相同分布。

理论空间：固定历史 Top40 且理想 reranker 选中全部已到达 Gold，micro Top12 最多从 53/95 到 69/95（+16 unit、+16.84 个百分点）；对应 macro 从 67.42% 最多到历史 Top40 的 78.13%（+10.71 个百分点）。这是 oracle 上界，不是可实现收益。Filter V2 Top100 85/95 若全部送入并完美排序，其 micro 上界为 89.47%，但与正式 Top40 输入不兼容，不可作为 reranker 单独收益。

推荐下一步先**调 reranker 的离线排序评估**：针对真实 rank 13–31 的 Gold 检查分数边界和竞争候选；同时将 6 条 Child 缺失 exact span 的输入结构作为定向子实验。直接把 Parent 拼到所有输入末尾仅恢复 2/16，尚不支持生产改动。Context Engineering 值得在其独立的 3 条 Top12→Context 丢失上做针对性审计，但不应作为修复 16 条 Top12 miss 的第一步。当前证据不足以修改生产 reranker 或 context 配置。本轮到此停止。

## 验证与限制

- 对 39/39 正向 eligible case 校验 retrieval 与 trace 的 Top40 Parent ID 一致；对有候选 case 校验保存的 1–N reranker score/rank 完整且排序重放与 Top12 输出一致；Context Builder 以保存分数重放，最终 Parent ID 与 Trace 一致。
- 95/95 unit 重新按冻结 group-aware 口径评分；69/53/50 漏斗及 67.42% macro 与历史报告吻合。
- Probe A 为事后针对 10 个失败 case 的实验；没有独立验证集，可能改变原成功证据排名。Probe B 只有 V2 深度检索前缀覆盖，没有同口径 60 候选模型分数。
- 本次仅生成两个报告文件；生产代码、参数与配置未改。

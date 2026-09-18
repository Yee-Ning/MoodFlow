# Gold V4 Retrieval Miss Audit

## 边界与口径

本轮只读复核**历史正式 Hybrid Top40 未命中的 26/95 个 Gold evidence unit**。历史正式 Top40 来自每 leg Top80、融合 Child 截断后选 Parent 的归档；下文 Filter V2、Dense/BM25、RRF Parent rank 均来自每 leg 600 Child、Top100 Parent 的固定深度诊断。两者不是同一次 Top40 运行，不能将 V2 的前 40 名冒充生产 Top40，也不能把相关性当成已证明的线上因果。未运行 reranker，未改变语料、索引、projection、embedding、query planner、检索、RRF、selector 或配置。

Gold/corpus/projection/manifest SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e` / `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a` / `663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0` / `0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。Retrieval/Planner config SHA-256：`b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3` / `afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77`。Filter V2 JSONL SHA-256：`69ab5feea651e9cd693e797d21c7c4dacfd99d371fb6838cad999c466ba74c06`。

BM25/Dense 的“召回”同时记录两种含义：固定 Filter V2 下每 leg 600 Child 内是否出现 Gold（raw），以及该单通道经 RRF/selector 后是否进入 Parent Top100。表内 D/B 使用后者；raw 最佳 Child rank 和历史深度 rank 在 JSONL。RRF rank 是去重后的 Parent **preselection** 名次，selected rank 是 document/source cap 之后的 Top100 名次。无可计算名次写 `—`，不猜测。evidence group 沿用冻结 V4 group-aware 匹配。

## 26 条的阶段统计

- Filter V2 深度诊断 Hybrid Top100 内：**16/26**；不在 Top100：**10/26**。V2 诊断 selected rank 41–100：**13**；rank ≤40：**3**。

- V2 raw Dense 命中 **22/26**，raw BM25 命中 **24/26**；单通道 selected Parent Top100 分别为 Dense **11/26**、BM25 **16/26**。通道覆盖是交叉统计，不与主因数量相加。

- **26/26** Gold exact span 在冻结 projection 的索引 Child 正文及至少一种 Dense/BM25 检索输入文本中可定位；本批没有已证实的漏索引或 chunk 内容缺失。

- 归入候选预算的 12 条在历史与 Filter V2 两套**深度诊断**中的 Hybrid selected rank 完全一致，均为 #41–100；这强化了候选位次判断，但仍不是正式 Top40 同预算 replay。

## 互斥主因

用户列出的五类之外，增加 `filter/selector` 两类，因为把过滤或 cap 排除硬归给单路、RRF、chunk 或 Planner 会误导。这里的主因指最早有证据支持的可操作阻断点；同一证据 unit 只计一次。

| 主因 | unit 数 | 说明 |
|---|---:|---|
| Top40 候选预算/诊断深度 | 12 | V2 selected #41–100；正式 Top40 缺失，需同预算 replay 确认精确阻断 |
| selector document/source cap 与 overflow | 3 | 037/claim:1 pre #34→selected #62；039 两条 pre #47→未选 |
| 历史 filter 排除，V2 诊断已恢复 | 3 | 042/043/044 各一条；原 filter 排除，V2 selected 均 ≤40 |
| query 表达覆盖不足 | 3 | 024 三条共享 Parent #310；非 Gold 定向拆解 query probe 恢复三条 |
| Dense/BM25 原始深排或 Parent 排名过深 | 3 | 040 三条 raw Child 较深、fused Parent #151–263；既有 Planner probe 未恢复 |
| 固定 metadata filter 仍排除 | 2 | 045 的 chart_event 与乐评范围有争议；065 因年份 metadata 缺失被固定 temporal filter 排除 |
| 单路召回失败（确认为最终主因） | 0 | 部分单通道 MISS，但另一通道或更深 raw leg 已找到；不能据此归因单路模型。 |
| RRF 融合实现问题（确认） | 0 | 既有逐 Child/Parent score 重建一致；rank 下降是排序现象，未发现公式 bug。 |
| chunk/Parent 表达缺失（确认） | 0 | 26/26 exact span 已在索引 Child；不等于所有表达方式都最优。 |

## 逐 evidence unit

D/B 为固定 V2 单通道 **selected Parent Top100** 命中；raw Child 最佳 rank、历史诊断及每个可接受 Parent 的 score/cap 明细见 JSONL。

| Case / Gold unit | D@100 | B@100 | raw D/B rank | RRF Parent pre rank / score | V2 selected rank | V2@100 | 主因 |
|---|:---:|:---:|---:|---:|---:|:---:|---|
| `rag-v0-001/claim:1` | Y | Y | 61/65 | 84 / 0.01708207 | 42 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-001/claim:2` | N | Y | 467/117 | 167 / 0.01179294 | 81 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-001/claim:4` | Y | Y | 61/65 | 84 / 0.01708207 | 42 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-001/claim:5` | Y | Y | 61/65 | 84 / 0.01708207 | 42 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-002/claim:1` | Y | Y | 111/80 | 81 / 0.01622383 | 51 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-003/claim:1` | Y | Y | 226/183 | 148 / 0.01305449 | 78 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-003/claim:2` | Y | Y | 189/34 | 67 / 0.01668986 | 41 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-004/claim:1` | N | Y | —/61 | 158 / 0.01314952 | 89 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-024/claim:2` | N | N | 567/258 | 310 / 0.00618267 | — | N | query 表达覆盖不足 |
| `rag-v0-024/claim:3` | N | N | 567/258 | 310 / 0.00618267 | — | N | query 表达覆盖不足 |
| `rag-v0-024/claim:4` | N | N | 567/258 | 310 / 0.00618267 | — | N | query 表达覆盖不足 |
| `rag-v0-036/claim:2` | Y | Y | 278/151 | 123 / 0.01272213 | 84 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-036/claim:3` | Y | Y | 278/151 | 123 / 0.01272213 | 84 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-036/claim:4` | N | Y | 305/25 | 68 / 0.01880310 | 48 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-037/claim:1` | N | Y | 188/19 | 34 / 0.02744998 | 62 | Y | selector document/source cap 与 overflow |
| `rag-v0-039/claim:1` | N | N | 198/38 | 47 / 0.02023907 | — | N | selector document/source cap 与 overflow |
| `rag-v0-039/claim:2` | N | N | 198/38 | 47 / 0.02023907 | — | N | selector document/source cap 与 overflow |
| `rag-v0-040/claim:2` | N | N | —/114 | 263 / 0.00488506 | — | N | Dense/BM25 原始深排或 Parent 排名过深 |
| `rag-v0-040/claim:3` | N | Y | 447/139 | 129 / 0.01136410 | 78 | Y | Top40 候选预算/诊断深度 |
| `rag-v0-040/claim:5` | N | N | 316/295 | 151 / 0.00968172 | — | N | Dense/BM25 原始深排或 Parent 排名过深 |
| `rag-v0-040/claim:7` | N | N | 553/199 | 179 / 0.00820288 | — | N | Dense/BM25 原始深排或 Parent 排名过深 |
| `rag-v0-042/claim:1` | Y | Y | 84/82 | 52 / 0.02484877 | 21 | Y | 历史 filter 排除，V2 诊断已恢复 |
| `rag-v0-043/claim:1` | Y | Y | 165/164 | 83 / 0.01224206 | 37 | Y | 历史 filter 排除，V2 诊断已恢复 |
| `rag-v0-044/claim:2` | Y | Y | 25/25 | 19 / 0.03339261 | 19 | Y | 历史 filter 排除，V2 诊断已恢复 |
| `rag-v0-045/claim:1` | N | N | —/— | — | — | N | 固定 metadata filter 仍排除 |
| `rag-v0-065/claim:1` | N | N | —/— | — | — | N | 固定 metadata filter 仍排除 |

## 下一步建议

1. **Query Planner：仅优先处理 024 的 3 条。** 同一 Gold Parent 的原始 Dense/BM25 Child 最佳 rank 为 567/258，固定 V2 preselection Parent #310。已有不抄 Gold 标题/年份的 `claim_decomposition` probe 恢复 3/3，适合在独立样本上评估通用“歌曲—作词人—曲目表”拆解；040 的三个 timeline probe 未恢复其 3 条深排缺失，不宜作为已验证 Planner 收益。

2. **Hybrid/RRF：先做同预算候选深度与 selector replay，再决定是否调权。** 12 条 V2 selected #41–100 可比较正式 Top40 的 per-leg Top80、fused Child limit=80 与诊断 600/无前截断；037/039 共 3 条需单独观察 cap/overflow。040 的 3 条 raw 虽命中但 Parent #151–263，应检查查询匹配与 Parent 相对排名。现有证据不支持 RRF 实现修复，也不支持直接改 k 或通道权重。

3. **Chunk/Parent：本批不建议先重切块。** 26 条 exact span 均在对应 indexed Child；可在上述候选深度 replay 后定向检查 winning Child 是否覆盖该 span，但当前没有“Gold 只在 Parent、Child 缺失”的证据。

4. **Filter：分开处理历史与固定 V2。** 042/043/044 三条在 V2 诊断已恢复，应验证 V2 正式 Top40 是否同样保留。045 的 Gold chart_event 与问题要求的乐评范围不一致，先做范围裁定；065 需评估缺失年份 metadata 的可信回退，不能靠改 query 绕过固定 temporal filter。

## 验证与限制

- 对 26/26 行核对历史正式 Top40 未命中，V2 selected Parent IDs 与 RRF 审计逐 case 完全一致；V2 hit 与 selected rank 一致。
- 26/26 Gold span 在索引 Child 正文及检索输入中核对；冻结 Gold、corpus、projection、manifest、Retrieval/Planner config SHA-256 均与已有审计一致。
- RRF pre rank、cap/overflow、Planner probe 和 Filter V2 恢复均来自已保存的只读审计；本轮没有新 Qdrant 查询。不同预算的历史 Top40 与 V2 Top100 不能作逐位因果比较。
- 024 的 query probe 是观察失败后的定向诊断；同 case 恢复属于工作归因，尚需独立问题验证。045 的 Gold 范围争议未在本轮重新裁定。
- 本轮只生成两份报告，未修改生产代码、配置、模型或参数。

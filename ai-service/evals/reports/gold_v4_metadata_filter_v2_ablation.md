# Gold V4 Metadata Filter Policy V2 Ablation

## 实验边界与方法

本轮仅改变 metadata filter application policy。Gold V4、冻结语料与索引、embedding model/revision、历史 Query Planner legs、Dense/BM25 参数、weighted RRF、每 leg 600 Child 诊断预算、Top100 Parent 预算、document/source cap 均保持不变。所有配置在同一批 39 个 eligible case、95 个 evidence unit 上使用同一现存索引进行只读查询。A 的 Dense/BM25/Hybrid Top100 Parent 排序逐 case 与历史检索归档完全一致。未修改生产配置或下游链路。

- Gold SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；corpus SHA-256：`8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。
- Projection SHA-256：`663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`；index manifest SHA-256：`0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。
- Retrieval config SHA-256：`b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`；Planner config SHA-256：`afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77`；历史检索档案 SHA-256：`dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`。
- 实验政策在每个 case 上重复构建两次并严格比较 Child 顺序、fallback 原因和计数：312 / 312 次一致。

## 全配置对比

macro Recall@100 是 39 个 case 等权平均；micro 为命中 evidence unit 数 / 95。Recovered/lost 相对 A，噪声为新进入 Top100 且不在该 case Gold Parent 集的 fallback Parent 槽位。

| Config | Dense@100 | BM25@100 | Hybrid@100 | micro hits | recovered | lost | net | new non-Gold | Gold displaced |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BASELINE | 79.84% | 77.14% | 83.30% | 80/95 | 0 | 0 | +0 | 0 | 0 |
| CONTENT_TYPE_ONLY | 81.97% | 79.28% | 85.44% | 82/95 | 2 | 0 | +2 | 0 | 0 |
| LANGUAGE_ONLY | 81.12% | 78.42% | 84.58% | 81/95 | 1 | 0 | +1 | 20 | 0 |
| ENTITY_ONLY_CAP_5 | 82.40% | 79.71% | 85.87% | 82/95 | 2 | 0 | +2 | 4 | 0 |
| ENTITY_ONLY_CAP_10 | 82.40% | 79.71% | 85.87% | 82/95 | 2 | 0 | +2 | 9 | 0 |
| ENTITY_ONLY_CAP_20 | 82.40% | 79.71% | 85.87% | 82/95 | 2 | 0 | +2 | 19 | 0 |
| SAFE_COMBINED_V2_CAP_5 | 85.82% | 83.13% | 89.29% | 85/95 | 5 | 0 | +5 | 24 | 0 |
| SAFE_COMBINED_V2_CAP_10 | 85.82% | 83.13% | 89.29% | 85/95 | 5 | 0 | +5 | 29 | 0 |
| SAFE_COMBINED_V2_CAP_20 | 85.82% | 83.13% | 89.29% | 85/95 | 5 | 0 | +5 | 39 | 0 |

**SAFE_COMBINED_V2 使用实验子配置 cap=5。** 选择规则在评分前固定：entity-only 必须恢复 006 两条证据且不损失既有 Gold；满足条件者取 Top100 fallback 非 Gold 最少的 cap，平局取较小 cap。此选择仅为本轮对照，不是 production policy 决策。

## Entity Parent cap 子配置

Cap 限制每 case 可准入的 **entity fallback Parent 数**，只作用于 entity policy；全局 per-leg/Top100/document/source budget 均未改变。Parent 按其无过滤原始 legs 的最优 Child rank 排序，平局按 Parent ID，完全不使用 Gold 排名选 Parent。

| entity fallback cap | Hybrid Recall@100 | recovered | lost | noise：fallback 非 Gold Top100 Parent | 006 restored |
|---:|---:|---:|---:|---:|---:|
| 5 | 85.87% | 2 | 0 | 4 | 2/2 |
| 10 | 85.87% | 2 | 0 | 9 | 2/2 |
| 20 | 85.87% | 2 | 0 | 19 | 2/2 |

## Candidate churn（相对 A）

单位为 case–Parent 槽位；同一 Parent 在不同 case 各计一次。Gold displaced Parent 与 lost evidence unit 是不同计数粒度。完整 Parent ID 列表在 JSONL。

| Config | retained | newly admitted | displaced | Gold displaced | newly admitted non-Gold | fallback Top100 | fallback non-Gold |
|---|---:|---:|---:|---:|---:|---:|---:|
| CONTENT_TYPE_ONLY | 2996 | 2 | 0 | 0 | 0 | 2 | 0 |
| LANGUAGE_ONLY | 2996 | 21 | 0 | 0 | 20 | 21 | 20 |
| ENTITY_ONLY_CAP_5 | 2996 | 5 | 0 | 0 | 4 | 5 | 4 |
| ENTITY_ONLY_CAP_10 | 2996 | 10 | 0 | 0 | 9 | 10 | 9 |
| ENTITY_ONLY_CAP_20 | 2996 | 20 | 0 | 0 | 19 | 20 | 19 |
| SAFE_COMBINED_V2_CAP_5 | 2996 | 28 | 0 | 0 | 24 | 28 | 24 |
| SAFE_COMBINED_V2_CAP_10 | 2996 | 33 | 0 | 0 | 29 | 33 | 29 |
| SAFE_COMBINED_V2_CAP_20 | 2996 | 43 | 0 | 0 | 39 | 43 | 39 |

## Filter policy V2 的规则

- Content type：`interview` 精确点优先，只兼容正文有采访语境和直接回答标记的 `commentary`；`news` 只兼容 `source_type=media_article` 的 `artist_biography`。`review` 不兼容榜单 `chart_event`。
- Language：`en` 精确点优先；仅当 `language=und` 的正文有至少 8 个英文词且拉丁字母占字母数 ≥50% 时准入。
- Entity：仅 exact entity Parent 少于现有阈值 12 且目标为 song 时，准入带兼容 album/artist entity、正文明确出现歌曲标题的 Child；另受上述固定 Parent cap 约束。
- Temporal：完全保持 baseline 的 hard filter。**没有 `temporal_unknown` fallback**；已知年份冲突和缺失年份均不得借 V2 放行。
- Exact-filter Child 在各 leg 内始终优先，符合规则的 fallback 按无过滤原始 rank 接在后面，总数仍不超过每 leg 600。filter confidence 不影响 RRF 权重。

## 重点 case 与所有损失

### 006

歌曲《七里香》的故事

A / content / language / entity cap 5 / combined V2 Hybrid case recall：0.00% / 0.00% / 0.00% / 100.00% / 100.00%。

- claim:1：MISS → HIT；索尼资料称团队讨论后用首波主打《七里香》作为第五张专辑名称。
- claim:2：MISS → HIT；索尼资料称《七里香》MV 在日本乡间取景。
- V2 Top100 fallback Parent 5，其中非 Gold 4。

### 037

周杰伦2003年的重要经历是什么

A / content / language / entity cap 5 / combined V2 Hybrid case recall：100.00% / 100.00% / 100.00% / 100.00% / 100.00%。

- claim:1：HIT → HIT；《叶惠美》于 2003 年 7 月 31 日发行。
- claim:2：HIT → HIT；2003 年 HitFM 年度榜单同时列有《晴天》和《东风破》。
- claim:3：HIT → HIT；杰威尔资料记载周杰伦于 2003 年登上《时代》亚洲版封面。
- V2 Top100 fallback Parent 0，其中非 Gold 0。

### 042

周杰伦的英文资料

A / content / language / entity cap 5 / combined V2 Hybrid case recall：50.00% / 50.00% / 100.00% / 50.00% / 100.00%。

- claim:1：MISS → HIT；英文专辑列表资料示例。
- claim:2：HIT → HIT；英文正文资料示例。
- V2 Top100 fallback Parent 21，其中非 Gold 20。

### 043

周杰伦的采访资料

A / content / language / entity cap 5 / combined V2 Hybrid case recall：50.00% / 100.00% / 50.00% / 50.00% / 100.00%。

- claim:1：MISS → HIT；人物报道转述周杰伦在 CNN 采访中希望听他歌曲的孩子更孝顺父母。
- claim:2：HIT → HIT；周杰伦在直接问答中表示看电影会刺激创作灵感。
- V2 Top100 fallback Parent 1，其中非 Gold 0。

### 044

周杰伦的新闻资料，不要现场版

A / content / language / entity cap 5 / combined V2 Hybrid case recall：66.67% / 100.00% / 66.67% / 66.67% / 100.00%。

- claim:1：HIT → HIT；杰威尔官方新闻列出周杰伦挑选公开展示的五首 DEMO。
- claim:2：MISS → HIT；人物报道记载周杰伦的新专辑发行间隔逐渐变长。
- claim:3：HIT → HIT；早期 DEMO 完成度较高。
- V2 Top100 fallback Parent 1，其中非 Gold 0。

### 045 · disputed_scope_case

《晴天》的乐评资料，不含翻唱版

A / content / language / entity cap 5 / combined V2 Hybrid case recall：0.00% / 0.00% / 0.00% / 0.00% / 0.00%。

- claim:1：MISS → MISS；《晴天》在 2003 年 HitFM 年度百首单曲中名列亚军。
- V2 Top100 fallback Parent 0，其中非 Gold 0。
- 045 的榜单背景证据与“乐评”问题存在范围争议；V2 未让 `chart_event` 作为 review fallback，不能用该 case 决定通用策略。

### 065

周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？

A / content / language / entity cap 5 / combined V2 Hybrid case recall：0.00% / 0.00% / 0.00% / 0.00% / 0.00%。

- claim:1：MISS → MISS；周杰伦自述很多快歌受到动作片启发。
- V2 Top100 fallback Parent 0，其中非 Gold 0。
- 065 继续按 baseline temporal filter，缺失 year 的 Gold Parent 仍 MISS；标记 unresolved，本轮不扩大时间过滤。

### SAFE_COMBINED_V2 的全部 lost Gold

**0 条。**

## Pareto 与判断

按 Hybrid macro 越高越好、lost Gold 越少越好、Top100 fallback 非 Gold 越少越好，以下是本轮非支配配置（同分时可能并列）：

`CONTENT_TYPE_ONLY`, `ENTITY_ONLY_CAP_5`, `SAFE_COMBINED_V2_CAP_5`。

选定的 V2 组合较 A 增加 5.98 个百分点 macro recall，micro 由 80/95 增至 85/95；恢复 5 条且损失 0 条。与 V1 soft（87.58%、83/95、恢复 5、损失 2、fallback 非 Gold 216）相比，V2 的 fallback 非 Gold 为 24，减少 192 个 case–Parent 槽位。

按单项政策拆分，content type 恢复 2 条且新增非 Gold 0；language 恢复 1 条且新增非 Gold 20；entity cap 5 恢复 2 条且新增非 Gold 4。Temporal fallback 本轮禁用，037 保持全命中而 065 仍未恢复。此 trace 证实 metadata filter 阻断了五条可恢复 Gold，但剩余未命中不全由本轮过滤策略解释。

该 Pareto 分析不自动选择生产策略；以上只是同一历史 query legs 与索引上的候选政策观察。

## 审计限制

- 本轮使用历史计划的原始 query text，不重新生成或修改 Planner legs；A 与历史 Parent 排序完全一致。
- Gold Parent precision / fallback 非 Gold 是候选噪声代理；不在 Gold Parent 集不必然意味着事实错误。
- 045 单列 disputed scope，065 单列 unresolved；二者都未驱动 V2 放宽规则。
- 此报告和 JSONL 为实验产物。未修改 production filter，也未执行 document-cap、Planner、RRF、reranker 或 Context Builder 优化。

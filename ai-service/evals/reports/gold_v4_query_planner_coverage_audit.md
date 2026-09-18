# Gold V4 Query Planner Coverage Audit

## 边界与口径

本轮只读：Gold V4、冻结语料/index/projection、embedding revision、Dense/BM25、weighted RRF k=60、SAFE_COMBINED_V2_CAP_5、document/source cap、Top100 Parent budget 均固定。历史 Planner legs 来自同一归档；生产 Planner、retrieval、reranker、Context Builder 与配置未修改。诊断 query 每次仅额外加入一个 decomposed variant（Dense 和 BM25 各一 leg，沿用现有 decomposed 权重 0.8），重新用相同 Filter V2/RRF/selector 评分，不改已有 query text。

诊断 query 仅由原始问题、历史已解析实体及通用措辞推导；不以 Gold claim、具体歌曲标题、答案年份或 evidence text 作为 query 输入。040 的“2000年代”是粗年代探针，不能当成已验证的用户时间范围。11 个 probe 都保持原有四个 leg 的 Filter V2 Child 顺序不变，仅加入新 leg。Probe 是看到失败后人工构造的诊断，存在事后选择偏差；单个 probe 的提升不能当成生产泛化指标，也不能把各 probe 的最佳结果拼成生产指标。

- Gold SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；corpus：`8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`；projection：`663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`；manifest：`0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。
- Retrieval config：`b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`；Planner config：`afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77`；historical archive：`dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`；RRF audit：`b09e1b41503293807aa17169e0eb6d5b4b5ec60fa7b9b95a0b8f457503cbd337`；V2：`69ab5feea651e9cd693e797d21c7c4dacfd99d371fb6838cad999c466ba74c06`。
- 39 eligible case、95 positive evidence unit；当前 Hybrid macro Recall@100=89.29%，micro=85/95，MISS=10。10 条 MISS 的 Gold exact span 均能在对应 frozen indexed Parent text 中逐字定位。

## 历史 Planner leg coverage

Raw leg coverage 指已通过固定 Filter V2、进入每 leg 600 Child 诊断结果，并由现有 group-aware 规则判断完整 evidence unit 是否命中。单个 leg 对 complementary unit 必须具有所需全部 Parent；Dense/BM25 channel coverage 是该通道多个 leg 的并集。Selected Top100 是 RRF 与 selector 之后，二者不是同一个口径。

| category | raw channel union /95 | selected Top100 /95 |
|---|---:|---:|
| Dense 与 BM25 均命中 | 89 | 71 |
| 仅 Dense | 2 | 8 |
| 仅 BM25 | 2 | 6 |
| 双方均未命中 | 2 | 10 |

| historical leg | full Gold evidence units hit in raw leg |
|---|---:|
| `q0:original:dense` | 91 |
| `q0:original:lexical` | 90 |
| `q1:alias:lexical` | 73 |
| `q1:semantic:dense` | 5 |
| `q2:decomposed:dense` | 5 |
| `q2:decomposed:lexical` | 5 |
| `q2:semantic:dense` | 57 |

## 当前 10 条 MISS：证据、legs、最早失败阶段

A=Planner 未覆盖，B=query 表达不足，C=retrieval/ranking，D=selection。045/065 固定 filter 排除不能诚实归入 A-D，另标 E；045 还有既有 Gold scope 争议。初始 C 标记不是确认 Planner 有错，须结合下方诊断结果。

### rag-v0-024

原始 question：谁为周杰伦的歌曲作词

| leg | channel | query text | weight | filtered Child count |
|---|---|---|---:|---:|
| `q0:original:dense` | dense | 谁为周杰伦的歌曲作词 | 1.0 | 600 |
| `q0:original:lexical` | lexical | 谁为周杰伦的歌曲作词 | 1.0 | 600 |
| `q1:alias:lexical` | lexical | 周杰伦 周杰倫 周董 | 0.85 | 600 |
| `q2:semantic:dense` | dense | 谁为周杰伦的歌曲作词 合作 关系 创作 | 0.75 | 600 |

| unit | Gold evidence text（frozen） | per-leg best raw Child rank | Dense/BM25/Hybrid Top100 | Hybrid preselection→selected | earliest failure / class |
|---|---|---|---|---|---|
| claim:2 | `parent-790e32e522e2ba2f480a77ea`: &#124; 3. &#124; 藉口 &#124; 周杰伦 &#124; 周杰伦 &#124; 4:18 &#124; | q0:original:dense=567; q0:original:lexical=258; q1:alias:lexical=529; q2:semantic:dense=MISS | M/M/M | parent-790e32e522e2ba2f480a77ea: 310→None | raw_rank_to_fused_parent_top100 / C_retrieval_ranking_or_query_expression |
| claim:3 | `parent-790e32e522e2ba2f480a77ea`: &#124; 5. &#124; 将军 &#124; 黄俊郎 &#124; 洪敬尧 &#124; 3:22 &#124; | q0:original:dense=567; q0:original:lexical=258; q1:alias:lexical=529; q2:semantic:dense=MISS | M/M/M | parent-790e32e522e2ba2f480a77ea: 310→None | raw_rank_to_fused_parent_top100 / C_retrieval_ranking_or_query_expression |
| claim:4 | `parent-790e32e522e2ba2f480a77ea`: &#124; 1. &#124; 我的地盘 &#124; 方文山 &#124; 洪敬尧 &#124; 4:02 &#124; | q0:original:dense=567; q0:original:lexical=258; q1:alias:lexical=529; q2:semantic:dense=MISS | M/M/M | parent-790e32e522e2ba2f480a77ea: 310→None | raw_rank_to_fused_parent_top100 / C_retrieval_ranking_or_query_expression |

Fixed Filter V2 Gold Parent admission：`parent-35487c91c9077427f4346634` indexed children=3, exact=3, compatible=0, content types=career_event; `parent-790e32e522e2ba2f480a77ea` indexed children=1, exact=1, compatible=0, content types=discography

#### 只读 query probe（一次只加一个 variant）

| probe | query | new Dense/BM25 best raw Gold rank | recovered | lost | Hybrid case hit count | admitted / displaced Parent |
|---|---|---|---|---|---:|---:|
| claim_decomposition | 周杰伦 歌曲 作词人 曲目表 词作者 | claim:2 q3:decomposed:dense=322/q3:decomposed:lexical=11<br>claim:3 q3:decomposed:dense=322/q3:decomposed:lexical=11<br>claim:4 q3:decomposed:dense=322/q3:decomposed:lexical=11 | claim:2, claim:3, claim:4 | none | 4/4 | 16/16 |
| lyric_synonyms | 周杰伦 专辑 歌曲 作词 歌词 | claim:2 q3:decomposed:dense=230/q3:decomposed:lexical=100<br>claim:3 q3:decomposed:dense=230/q3:decomposed:lexical=100<br>claim:4 q3:decomposed:dense=230/q3:decomposed:lexical=100 | none | none | 1/4 | 10/10 |
| entity_alias | Jay Chou lyrics song credits songwriter | claim:2 q3:decomposed:dense=390/q3:decomposed:lexical=544<br>claim:3 q3:decomposed:dense=390/q3:decomposed:lexical=544<br>claim:4 q3:decomposed:dense=390/q3:decomposed:lexical=544 | none | none | 1/4 | 15/15 |

### rag-v0-039

原始 question：从2008至2012年周杰伦有哪些变化

| leg | channel | query text | weight | filtered Child count |
|---|---|---|---:|---:|
| `q0:original:dense` | dense | 从2008至2012年周杰伦有哪些变化 | 1.0 | 315 |
| `q0:original:lexical` | lexical | 从2008至2012年周杰伦有哪些变化 | 1.0 | 315 |
| `q1:alias:lexical` | lexical | 周杰伦 周杰倫 周董 | 0.85 | 315 |
| `q2:semantic:dense` | dense | 从2008至2012年周杰伦有哪些变化 时间线 发展 阶段 | 0.75 | 315 |

| unit | Gold evidence text（frozen） | per-leg best raw Child rank | Dense/BM25/Hybrid Top100 | Hybrid preselection→selected | earliest failure / class |
|---|---|---|---|---|---|
| claim:1 | `parent-17f46282a4d28c6c000e6aff`: 2011年，曾投資「DejaVu音樂魔術餐廳」。 | q0:original:dense=200; q0:original:lexical=38; q1:alias:lexical=199; q2:semantic:dense=198 | M/M/M | parent-17f46282a4d28c6c000e6aff: 47→None | parent_selection / D_selection |
| claim:2 | `parent-17f46282a4d28c6c000e6aff`: 2012年初，與西安真愛投資集團合作在西安開設「真愛范特西」KTV | q0:original:dense=200; q0:original:lexical=38; q1:alias:lexical=199; q2:semantic:dense=198 | M/M/M | parent-17f46282a4d28c6c000e6aff: 47→None | parent_selection / D_selection |

Fixed Filter V2 Gold Parent admission：`parent-17f46282a4d28c6c000e6aff` indexed children=3, exact=1, compatible=0, content types=career_event

#### 只读 query probe（一次只加一个 variant）

| probe | query | new Dense/BM25 best raw Gold rank | recovered | lost | Hybrid case hit count | admitted / displaced Parent |
|---|---|---|---|---|---:|---:|
| temporal_expansion | 周杰伦 2008 2009 2010 2011 2012 年 变化 | claim:1 q3:decomposed:dense=216/q3:decomposed:lexical=49<br>claim:2 q3:decomposed:dense=216/q3:decomposed:lexical=49 | none | none | 0/2 | 3/3 |
| temporal_facets | 周杰伦 2008年至2012年 音乐 事业 发展 | claim:1 q3:decomposed:dense=182/q3:decomposed:lexical=81<br>claim:2 q3:decomposed:dense=182/q3:decomposed:lexical=81 | none | none | 0/2 | 4/4 |

039 的 baseline Gold 在 fused Parent #47，最终被联合 cap/overflow 阻断；本轮 query probe 若改变 selected rank，只表示新增 leg 对原有 selection 的间接影响，不是证明 Planner 原本缺失该证据。

### rag-v0-040

原始 question：周杰伦的音乐生涯时间线

| leg | channel | query text | weight | filtered Child count |
|---|---|---|---:|---:|
| `q0:original:dense` | dense | 周杰伦的音乐生涯时间线 | 1.0 | 600 |
| `q0:original:lexical` | lexical | 周杰伦的音乐生涯时间线 | 1.0 | 600 |
| `q1:alias:lexical` | lexical | 周杰伦 周杰倫 周董 | 0.85 | 600 |
| `q2:semantic:dense` | dense | 周杰伦的音乐生涯时间线 时间线 发展 阶段 | 0.75 | 600 |

| unit | Gold evidence text（frozen） | per-leg best raw Child rank | Dense/BM25/Hybrid Top100 | Hybrid preselection→selected | earliest failure / class |
|---|---|---|---|---|---|
| claim:2 | `parent-81d3ee5f0e44759bf67d8c7f`: 《葉惠美》是台灣歌手周杰倫的第四張录音室專輯，由台灣新力音樂于2003年7月31日發行，專輯名稱周杰倫以自己的母親名字來命名。 | q0:original:dense=MISS; q0:original:lexical=MISS; q1:alias:lexical=114; q2:semantic:dense=MISS | M/M/M | parent-81d3ee5f0e44759bf67d8c7f: 263→None | raw_rank_to_fused_parent_top100 / C_retrieval_ranking_or_query_expression |
| claim:5 | `parent-a83673d2b967ec913e6b0ef3`: 2007年，周杰倫第一部自編自導自演的電影《不能說的·秘密》上映，電影裡大部分的音樂也由他創作。 | q0:original:dense=316; q0:original:lexical=295; q1:alias:lexical=307; q2:semantic:dense=337 | M/M/M | parent-a83673d2b967ec913e6b0ef3: 151→None | raw_rank_to_fused_parent_top100 / C_retrieval_ranking_or_query_expression |
| claim:7 | `parent-95160d6644f27840faf6b97d`: 十一月的蕭邦 ＋ 霍元甲（2LP） 『在十一月聽見蕭邦，掀起一陣浪漫的夜曲風暴。』 （發行日期2005/11/01） | q0:original:dense=568; q0:original:lexical=199; q1:alias:lexical=497; q2:semantic:dense=553 | M/M/M | parent-95160d6644f27840faf6b97d: 179→None | raw_rank_to_fused_parent_top100 / C_retrieval_ranking_or_query_expression |

Fixed Filter V2 Gold Parent admission：`parent-0ddd0ca8d88f0672b314c240` indexed children=1, exact=1, compatible=0, content types=discography; `parent-531672f53462b6956869e0eb` indexed children=3, exact=3, compatible=0, content types=career_event,news; `parent-81d3ee5f0e44759bf67d8c7f` indexed children=1, exact=1, compatible=0, content types=album_description; `parent-95160d6644f27840faf6b97d` indexed children=2, exact=2, compatible=0, content types=release_event; `parent-a83673d2b967ec913e6b0ef3` indexed children=2, exact=2, compatible=0, content types=career_event; `parent-cca31e09c6d05f09267862a1` indexed children=4, exact=4, compatible=0, content types=news,release_event; `parent-e46f773645df47165d634134` indexed children=1, exact=1, compatible=0, content types=chart_event

#### 只读 query probe（一次只加一个 variant）

| probe | query | new Dense/BM25 best raw Gold rank | recovered | lost | Hybrid case hit count | admitted / displaced Parent |
|---|---|---|---|---|---:|---:|
| timeline_facets | 周杰伦 音乐生涯 历年 专辑 发行 电影 | claim:2 q3:decomposed:dense=579/q3:decomposed:lexical=320<br>claim:5 q3:decomposed:dense=126/q3:decomposed:lexical=86<br>claim:7 q3:decomposed:dense=MISS/q3:decomposed:lexical=229 | none | claim:3 | 3/7 | 16/16 |
| coarse_temporal | 周杰伦 2000年代 音乐专辑 发行 时间线 | claim:2 q3:decomposed:dense=559/q3:decomposed:lexical=446<br>claim:5 q3:decomposed:dense=366/q3:decomposed:lexical=MISS<br>claim:7 q3:decomposed:dense=504/q3:decomposed:lexical=356 | none | claim:3 | 3/7 | 12/12 |
| timeline_synonyms | 周杰伦 各年音乐作品 年表 唱片 | claim:2 q3:decomposed:dense=551/q3:decomposed:lexical=296<br>claim:5 q3:decomposed:dense=363/q3:decomposed:lexical=186<br>claim:7 q3:decomposed:dense=535/q3:decomposed:lexical=MISS | none | none | 4/7 | 17/17 |

### rag-v0-045

原始 question：《晴天》的乐评资料，不含翻唱版

| leg | channel | query text | weight | filtered Child count |
|---|---|---|---:|---:|
| `q0:original:dense` | dense | 《晴天》的乐评资料，不含翻唱版 | 1.0 | 0 |
| `q0:original:lexical` | lexical | 《晴天》的乐评资料，不含翻唱版 | 1.0 | 0 |
| `q1:alias:lexical` | lexical | 晴天 晴天歌曲 | 0.85 | 0 |
| `q2:semantic:dense` | dense | 《晴天》的乐评资料，不含翻唱版 背景 经历 影响 | 0.75 | 0 |

| unit | Gold evidence text（frozen） | per-leg best raw Child rank | Dense/BM25/Hybrid Top100 | Hybrid preselection→selected | earliest failure / class |
|---|---|---|---|---|---|
| claim:1 | `parent-e46f773645df47165d634134`: 《晴天》在2003年HitFM联播网年度百首單曲中名列亚军 | q0:original:dense=MISS; q0:original:lexical=MISS; q1:alias:lexical=MISS; q2:semantic:dense=MISS | M/M/M | parent-e46f773645df47165d634134: None→None | fixed_filter_candidate_admission / E_fixed_filter_or_disputed_scope |

Fixed Filter V2 Gold Parent admission：`parent-e46f773645df47165d634134` indexed children=1, exact=0, compatible=0, content types=chart_event

#### 只读 query probe（一次只加一个 variant）

| probe | query | new Dense/BM25 best raw Gold rank | recovered | lost | Hybrid case hit count | admitted / displaced Parent |
|---|---|---|---|---|---:|---:|
| review_synonyms | 晴天 歌曲 乐评 评论 不含翻唱 | claim:1 q3:decomposed:dense=MISS/q3:decomposed:lexical=MISS | none | none | 0/1 | 0/0 |

045 的 Gold Parent 是 `chart_event`，而历史问题要求“乐评”，并且既有审计标记 disputed_scope_case；恢复它不应作为通用 Planner 策略成功依据。

### rag-v0-065

原始 question：周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？

| leg | channel | query text | weight | filtered Child count |
|---|---|---|---:|---:|
| `q0:original:dense` | dense | 周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？ | 1.0 | 247 |
| `q0:original:lexical` | lexical | 周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？ | 1.0 | 247 |
| `q1:alias:lexical` | lexical | 周杰伦 周杰倫 周董 | 0.85 | 247 |
| `q2:semantic:dense` | dense | 周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？ 合作 关系 创作 | 0.75 | 247 |

| unit | Gold evidence text（frozen） | per-leg best raw Child rank | Dense/BM25/Hybrid Top100 | Hybrid preselection→selected | earliest failure / class |
|---|---|---|---|---|---|
| claim:1 | `parent-659d449391ffb5694ba9fdf0`: 我的很多快歌都是看动作片写出来的。 | q0:original:dense=MISS; q0:original:lexical=MISS; q1:alias:lexical=MISS; q2:semantic:dense=MISS | M/M/M | parent-659d449391ffb5694ba9fdf0: None→None | fixed_temporal_filter_candidate_admission / E_fixed_filter |

Fixed Filter V2 Gold Parent admission：`parent-659d449391ffb5694ba9fdf0` indexed children=3, exact=0, compatible=0, content types=interview

#### 只读 query probe（一次只加一个 variant）

| probe | query | new Dense/BM25 best raw Gold rank | recovered | lost | Hybrid case hit count | admitted / displaced Parent |
|---|---|---|---|---|---:|---:|
| question_decomposition | 周杰伦 2004年 访谈 动作片 快歌 创作 灵感 | claim:1 q3:decomposed:dense=MISS/q3:decomposed:lexical=MISS | none | none | 0/1 | 5/5 |
| topic_synonyms | 周杰伦 动作电影 快歌 创作 访谈 | claim:1 q3:decomposed:dense=MISS/q3:decomposed:lexical=MISS | none | none | 0/1 | 5/5 |

065 的 Gold Parent 年份 metadata 缺失；固定 V2 禁止 temporal_unknown fallback。即使换 query text，同一 filter 仍会排除该 Parent。

## 归因与 92% 可行性

| baseline miss cause | evidence units | proportion of 10 |
|---|---:|---:|
| 024/040：query 表达或 raw/fused 排名候选问题 | 6 | 60% |
| 039：已进入 preselection Top100，selection 联合 cap/overflow | 2 | 20% |
| 045：固定 filter 与 Gold 乐评范围争议 | 1 | 10% |
| 065：固定 temporal filter 排除 | 1 | 10% |

60% 是 Planner 可能影响的上界，不能直接称为已证实的 Planner 失败。历史 legs 的 raw channel 并集已覆盖 93/95 个 evidence unit；其中仍有 8 个最终 Hybrid Top100 MISS，说明多数缺失属于排名/选择，而非 raw leg 完全无命中。

| case | 审计后归因 | evidence units | 依据 |
|---|---|---:|---|
| 024 | B：query 表达覆盖不足 | 3 | 通用、未抄 Gold 的作词曲目表 probe 一次恢复 3 条且本 case 零损失。 |
| 040 | C：现有证据指向 retrieval 排名/表示，Planner 改善未证实 | 3 | 三种通用 timeline probe 均未恢复；其中两种丢失原命中。 |
| 039 | D：selection | 2 | Gold 已在 fused Parent #47；额外年份 query 未恢复。 |
| 045 | E：固定 filter / Gold 范围争议 | 1 | Gold chart_event 与乐评要求不一致，V2 不放行。 |
| 065 | E：固定 temporal filter | 1 | Gold 年份 metadata 缺失，V2 不放行。 |

因此**实验证实可由非 Gold 泄漏的 query expansion 恢复的是 3/10=30%**；另 3/10=30%（040）仍可能受更好 Planner 表达影响，但本轮没有证明。024 单 case 反事实若只用于该 case，macro 89.29%→**91.21%**、micro 85/95→**88/95**；这是只读 probe 结果，不是生产策略指标。

诊断中出现 Gold 恢复的单 probe：

- rag-v0-024 / claim_decomposition：recovered claim:2, claim:3, claim:4; lost none。

最有实测依据的低成本候选是为泛化的“谁写词”问题增加曲目表/作词人词汇 probe；024 的新增 BM25 leg 把 Gold Child 排到 #11，最终 Parent 进入 Hybrid selected #46，但融合后 Parent preselection 仍为 #117，说明恢复依赖固定 selector 的多样性提升。该 probe 未跨其他问题验证零回归，不能直接上线。040 的年份/时间线 probe 未证明可恢复，且有回归风险。

若仅补齐 024 的 3 条与 040 的 3 条，且其他 case 无损失，macro 理论上为 89.2857% + (3/4 + 3/7)/39 = **92.31%**，micro 为 **91/95**。这是固定 Gold 分母上的乐观上界，不是本轮实测或可上线预测；需要这 6 条全部恢复且零回归。**当前证据不支持**声称一个低成本新增 leg 方案可达到 ≥92%。039、045、065 不计入该低成本 Planner 机会集合。

本轮不改 production Planner、filter、RRF、selection、模型、Gold 或历史 Trace，也不把离线单 probe 的最优结果拼成正式 Retrieval 指标。

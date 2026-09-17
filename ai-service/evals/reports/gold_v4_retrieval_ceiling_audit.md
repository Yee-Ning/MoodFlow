# Gold V4 Retrieval Ceiling / Top100 Miss Audit

## 审计边界与结论

本报告只使用冻结 Gold、冻结 Parent 语料、固定 index manifest / projection、既有 50-case 历史检索诊断，以及针对同一索引和相同 Query Planner legs 的只读深排探针。未修改或重跑 production Retrieval、Rerank、Context Builder，也未用新探针替代历史评分。深排 rank 是原始 **Child leg rank**；历史 Recall@100 是融合后、去重和选择后的 **Parent rank**，两者不能混用。

- Gold SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；语料 SHA-256：`8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。
- Index manifest SHA-256：`0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`；projection SHA-256：`663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`；索引点数 1270。
- 历史检索档案：`gold_v4_hybrid_shadow_retrieval_50_20260917.jsonl.gz`；深排探针仅供因果诊断，输出保存在本地忽略的 `evals/artifacts/gold_v4_retrieval_ceiling_deep_rank_probe.json`。
- 15 条 dual-miss 对应 11 个不同 Parent、9 个 case。15/15 Gold exact span 在冻结 Parent 中，15/15 在索引 Child dense/BM25 文本中；相关 24/24 个点在当前 Qdrant 集合且 `parent_id/chunk_id/doc_id/index_input_hash` 与 projection 一致。未发现证据缺失、漏索引或 Parent 映射错误。

### Raw hit 与 Recall@100 的分母

Gold V4 的 50 个 case 中，29 full + 10 partial = **39 个正向 eligible case**；7 insufficient 和 4 needs_input 不进入该正向分母。这 39 个 case 共有 **95 个 supported evidence unit**。`80/95 = 84.21%` 是逐 evidence unit 的 **micro raw-hit 比例**。报告中的 `Hybrid Recall@100 = 83.30%` 是 39 个 case 等权的 **macro recall**：`(1/39) × Σ_case(该 case 命中 unit 数 / 该 case supported unit 数)`。Dense/BM25 的 79.84%/77.14% 也是同一口径；74/95 与 72/95 是各自 micro raw hit，不应与 macro 百分比相除。

| Channel | Raw hit / 95 (micro) | case macro Recall@100 |
|---|---:|---:|
| Dense | 74 / 95 (77.89%) | 79.84% |
| BM25 | 72 / 95 (75.79%) | 77.14% |
| Hybrid | 80 / 95 (84.21%) | 83.30% |

历史 raw-hit funnel：Dense/BM25 **both-hit 66**，仅 Dense **8**，仅 BM25 **6**，两路均未命中 **15**；66+8+6=80。此处 hit 仍按 Gold evidence unit 而非不同 Parent 计数。

## Root-cause summary

下表按 evidence unit 计数；同一 Parent 上多个独立 claim 不合并，因此同时列出 Parent 数。主因是可观察的最邻近阻断点；次要因素和不确定性见逐项记录。

| 类别 | 含义 | missed units | unique Parents |
|---|---|---:|---:|
| A | Gold/corpus/index 一致性问题 | 0 | 0 |
| B | Query Planner 覆盖不足（工作诊断） | 6 | 4 |
| C | Dense 单独语义失败 | 0 | 0 |
| D | BM25 单独词面失败 | 0 | 0 |
| E | Dense + BM25 表示/切块失败 | 0 | 0 |
| F | 候选选择 / budget / 截断 | 2 | 1 |
| G | 元数据 / filter 排除 | 7 | 6 |
| H | Parent/Child 映射问题 | 0 | 0 |
| I | 其他 | 0 | 0 |

**G=7**：006×2、042、043、044、045、065。**B=6**：024×3、040×3。**F=2**：039×2。G 中 045 是范围争议：query 要“乐评”，Gold 的 partial 正向证据却是榜单背景；review filter 排除它符合字面约束，不能直接当作应放宽 filter 的例子。042 即便取消 `language=en`，相关点最好仍在 Dense 373/BM25 399，尚需另一个召回改进。

## 15 条 dual-miss 明细

统一说明：下文“rank —”指该 Parent 的任何 Child 在相应 filtered leg 的全量 1270 点探针中均未返回；若过滤失败，不能解读为 Dense 或 BM25 的相关性能力不足。每条的**完整 indexed dense_text 和 bm25_text、全部 Child 元数据与逐 leg rank**均在同名 JSONL 中。

### 006 · claim:1

- Claim：索尼资料称团队讨论后用首波主打《七里香》作为第五张专辑名称（support_level=full）。
- Gold Parent / Document：`parent-d3a5bad1fa461976a1129b06` / `doc-jc-v1-007-ac10d36fc7fa8b8930b3`；Gold span、投影和 live index 均存在；3 个 Child，映射一致。
- Gold evidence：
  > 幾經討論，最後大家定案回歸音樂的純粹，聚焦在專輯的音樂性上，於是第一首主打歌－七里香，就定名為專輯名稱，這也是周杰倫五張專輯以來，第一次以主打歌為專輯名稱。

- 实际证据 Child：`child-895834b34647e3b52d167e3e`，point `8a720cd8-c233-5d21-82fa-696093e32c00`，filter_pass=false。Indexed dense text：

```text
七里香

![七里香](https://cdn-p.smehost.net/sites/d563907ee0c84f41b15553bfb3eb554c/wp-content/uploads/2016/08/afd0429-b-1.jpg)

彷彿是一種約定，每年這個時候，醞釀一整年的必定準時引爆。緣於信賴，產生信仰，無論是媒體或消費者，對於周杰倫的專輯曲風、創意取材、甚至專輯名稱…等，都充滿好奇與期待，2004周杰倫最新專輯，必定又是話題營造與網路討論最激烈的暑期作業。＊專輯名稱[七里香] 嗅出杰倫音樂的自信氣味* 從[周杰倫同名專輯]，到[范特西]，到[八度空間]，到[葉惠美]，周杰倫每一張專輯的名稱都饒富創意，第五張專輯該賦予她什麼名字，曾經困擾著所有專輯團隊。幾經討論，最後大家定案回歸音樂的純粹，聚焦在專輯的音樂性上，於是第一首主打歌－七里香，就定名為專輯名稱，這也是周杰倫五張專輯以來，第一次以主打歌為專輯名稱。
```

- 计划：`multi_query_hybrid`；原 query：歌曲《七里香》的故事。
  - `q0:original:dense` · `歌曲《七里香》的故事` · filtered Child rank **—**，无 metadata filter 时 **1**。
  - `q0:original:lexical` · `歌曲《七里香》的故事` · filtered Child rank **—**，无 metadata filter 时 **3**。
  - `q1:alias:lexical` · `七里香 七里香歌曲` · filtered Child rank **—**，无 metadata filter 时 **7**。
  - `q2:semantic:dense` · `歌曲《七里香》的故事 背景 经历 影响` · filtered Child rank **—**，无 metadata filter 时 **1**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=—、BM25=—。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：entity_ids 缺 song:qi_li_xiang，只有 album:qi_li_xiang。融合/选择：Gold Child 被过滤或不在已选 Top100。
- Root cause **G**（high）：实体过滤要求 song:qi_li_xiang；三个相关 Child 只标有 album:qi_li_xiang / artist:jay_chou。未过滤时 Dense 原始 leg 第 1、BM25 第 3，证实过滤排除。

### 006 · claim:2

- Claim：索尼资料称《七里香》MV 在日本乡间取景（support_level=full）。
- Gold Parent / Document：`parent-d3a5bad1fa461976a1129b06` / `doc-jc-v1-007-ac10d36fc7fa8b8930b3`；Gold span、投影和 live index 均存在；3 个 Child，映射一致。
- Gold evidence：
  > 「七里香」完整版MV－特地前往日本鄉間取景，營造音樂的浪漫詩境

- 实际证据 Child：`child-7e82283e22e2b47df07c730f`，point `22b0ec9b-a01f-51fc-8741-8f580205ffb1`，filter_pass=false。Indexed dense text：

```text
七里香

孩子眼中的希望是什麼形狀?封面彷彿有一個待續的故事，或許，你可以從音樂中去尋找！－加贈珍藏VCD－「七里香」完整版MV－特地前往日本鄉間取景，營造音樂的浪漫詩境，一幅幅唯美畫面，讓你一次看盡。「止戰之殤」完整版MV－至海參威拍攝製作的大成本音樂錄影帶，杰倫一人飾演說書人與軍官兩種角色，像一部藝術性濃郁的戰地史詩電影，值得反覆品味珍藏。
```

- 计划：`multi_query_hybrid`；原 query：歌曲《七里香》的故事。
  - `q0:original:dense` · `歌曲《七里香》的故事` · filtered Child rank **—**，无 metadata filter 时 **1**。
  - `q0:original:lexical` · `歌曲《七里香》的故事` · filtered Child rank **—**，无 metadata filter 时 **3**。
  - `q1:alias:lexical` · `七里香 七里香歌曲` · filtered Child rank **—**，无 metadata filter 时 **7**。
  - `q2:semantic:dense` · `歌曲《七里香》的故事 背景 经历 影响` · filtered Child rank **—**，无 metadata filter 时 **1**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=—、BM25=—。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：entity_ids 缺 song:qi_li_xiang，只有 album:qi_li_xiang。融合/选择：Gold Child 被过滤或不在已选 Top100。
- Root cause **G**（high）：实体过滤要求 song:qi_li_xiang；三个相关 Child 只标有 album:qi_li_xiang / artist:jay_chou。未过滤时 Dense 原始 leg 第 1、BM25 第 3，证实过滤排除。

### 024 · claim:2

- Claim：《藉口》由周杰伦作词（support_level=full）。
- Gold Parent / Document：`parent-790e32e522e2ba2f480a77ea` / `doc-jc-v1-035-33ac74f389ffa5266db8`；Gold span、投影和 live index 均存在；1 个 Child，映射一致。
- Gold evidence：
  > | 3. | 藉口 | 周杰伦 | 周杰伦 | 4:18 |

- 实际证据 Child：`child-44b78ca9fdb441d297c58a7e`，point `9df75960-627b-581f-89ec-70f5637a085c`，filter_pass=true。Indexed dense text：

```text
七里香（專輯） > 七里香 (專輯) > 曲目

| CD |
| 曲序 | 曲目 | 作词 | 编曲 | 时长 |
| 1. | 我的地盘 | 方文山 | 洪敬尧 | 4:02 |
| 2. | 七里香 | 方文山 | 钟兴民 | 4:57 |
| 3. | 藉口 | 周杰伦 | 周杰伦 | 4:18 |
| 4. | 外婆（女声：张欣瑜） | 周杰伦 | 周杰伦 | 4:02 |
| 5. | 将军 | 黄俊郎 | 洪敬尧 | 3:22 |
| 6. | 搁浅 | 宋健彰 | 钟兴民 | 3:58 |
| 7. | 乱舞春秋 | 方文山 | 钟兴民 | 4:37 |
| 8. | 困兽之斗 | 刘畊宏 | 蔡科俊 | 4:27 |
| 9. | 园游会 | 方文山 | 洪敬尧 | 4:13 |
| 10. | 止战之殇 | 方文山 | 周杰伦 | 4:34 |
| 总时长： | 42:30 |
```

- 计划：`multi_query_hybrid`；原 query：谁为周杰伦的歌曲作词。
  - `q0:original:dense` · `谁为周杰伦的歌曲作词` · filtered Child rank **567**，无 metadata filter 时 **567**。
  - `q0:original:lexical` · `谁为周杰伦的歌曲作词` · filtered Child rank **258**，无 metadata filter 时 **268**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **529**，无 metadata filter 时 **529**。
  - `q2:semantic:dense` · `谁为周杰伦的歌曲作词 合作 关系 创作` · filtered Child rank **632**，无 metadata filter 时 **632**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=567、BM25=258。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：Gold 证据 Child 通过过滤。融合/选择：{'fused_child_rank': 526, 'distinct_parent_rank': 310}。
- Root cause **B**（medium）：泛问作词人，但计划的 alias leg 只有艺人别名，semantic leg 为宽泛的“合作 关系 创作”；没有指向曲目表/具体歌曲的 leg。Gold 曲目表在已过滤原始 BM25 最好第 258、Dense 第 567，属于深排。该因果归类仍需独立查询实验验证。

### 024 · claim:3

- Claim：《将军》由黄俊郎作词（support_level=full）。
- Gold Parent / Document：`parent-790e32e522e2ba2f480a77ea` / `doc-jc-v1-035-33ac74f389ffa5266db8`；Gold span、投影和 live index 均存在；1 个 Child，映射一致。
- Gold evidence：
  > | 5. | 将军 | 黄俊郎 | 洪敬尧 | 3:22 |

- 实际证据 Child：`child-44b78ca9fdb441d297c58a7e`，point `9df75960-627b-581f-89ec-70f5637a085c`，filter_pass=true。Indexed dense text：

```text
七里香（專輯） > 七里香 (專輯) > 曲目

| CD |
| 曲序 | 曲目 | 作词 | 编曲 | 时长 |
| 1. | 我的地盘 | 方文山 | 洪敬尧 | 4:02 |
| 2. | 七里香 | 方文山 | 钟兴民 | 4:57 |
| 3. | 藉口 | 周杰伦 | 周杰伦 | 4:18 |
| 4. | 外婆（女声：张欣瑜） | 周杰伦 | 周杰伦 | 4:02 |
| 5. | 将军 | 黄俊郎 | 洪敬尧 | 3:22 |
| 6. | 搁浅 | 宋健彰 | 钟兴民 | 3:58 |
| 7. | 乱舞春秋 | 方文山 | 钟兴民 | 4:37 |
| 8. | 困兽之斗 | 刘畊宏 | 蔡科俊 | 4:27 |
| 9. | 园游会 | 方文山 | 洪敬尧 | 4:13 |
| 10. | 止战之殇 | 方文山 | 周杰伦 | 4:34 |
| 总时长： | 42:30 |
```

- 计划：`multi_query_hybrid`；原 query：谁为周杰伦的歌曲作词。
  - `q0:original:dense` · `谁为周杰伦的歌曲作词` · filtered Child rank **567**，无 metadata filter 时 **567**。
  - `q0:original:lexical` · `谁为周杰伦的歌曲作词` · filtered Child rank **258**，无 metadata filter 时 **268**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **529**，无 metadata filter 时 **529**。
  - `q2:semantic:dense` · `谁为周杰伦的歌曲作词 合作 关系 创作` · filtered Child rank **632**，无 metadata filter 时 **632**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=567、BM25=258。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：Gold 证据 Child 通过过滤。融合/选择：{'fused_child_rank': 526, 'distinct_parent_rank': 310}。
- Root cause **B**（medium）：泛问作词人，但计划的 alias leg 只有艺人别名，semantic leg 为宽泛的“合作 关系 创作”；没有指向曲目表/具体歌曲的 leg。Gold 曲目表在已过滤原始 BM25 最好第 258、Dense 第 567，属于深排。该因果归类仍需独立查询实验验证。

### 024 · claim:4

- Claim：《我的地盘》由方文山作词（support_level=full）。
- Gold Parent / Document：`parent-790e32e522e2ba2f480a77ea` / `doc-jc-v1-035-33ac74f389ffa5266db8`；Gold span、投影和 live index 均存在；1 个 Child，映射一致。
- Gold evidence：
  > | 1. | 我的地盘 | 方文山 | 洪敬尧 | 4:02 |

- 实际证据 Child：`child-44b78ca9fdb441d297c58a7e`，point `9df75960-627b-581f-89ec-70f5637a085c`，filter_pass=true。Indexed dense text：

```text
七里香（專輯） > 七里香 (專輯) > 曲目

| CD |
| 曲序 | 曲目 | 作词 | 编曲 | 时长 |
| 1. | 我的地盘 | 方文山 | 洪敬尧 | 4:02 |
| 2. | 七里香 | 方文山 | 钟兴民 | 4:57 |
| 3. | 藉口 | 周杰伦 | 周杰伦 | 4:18 |
| 4. | 外婆（女声：张欣瑜） | 周杰伦 | 周杰伦 | 4:02 |
| 5. | 将军 | 黄俊郎 | 洪敬尧 | 3:22 |
| 6. | 搁浅 | 宋健彰 | 钟兴民 | 3:58 |
| 7. | 乱舞春秋 | 方文山 | 钟兴民 | 4:37 |
| 8. | 困兽之斗 | 刘畊宏 | 蔡科俊 | 4:27 |
| 9. | 园游会 | 方文山 | 洪敬尧 | 4:13 |
| 10. | 止战之殇 | 方文山 | 周杰伦 | 4:34 |
| 总时长： | 42:30 |
```

- 计划：`multi_query_hybrid`；原 query：谁为周杰伦的歌曲作词。
  - `q0:original:dense` · `谁为周杰伦的歌曲作词` · filtered Child rank **567**，无 metadata filter 时 **567**。
  - `q0:original:lexical` · `谁为周杰伦的歌曲作词` · filtered Child rank **258**，无 metadata filter 时 **268**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **529**，无 metadata filter 时 **529**。
  - `q2:semantic:dense` · `谁为周杰伦的歌曲作词 合作 关系 创作` · filtered Child rank **632**，无 metadata filter 时 **632**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=567、BM25=258。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：Gold 证据 Child 通过过滤。融合/选择：{'fused_child_rank': 526, 'distinct_parent_rank': 310}。
- Root cause **B**（medium）：泛问作词人，但计划的 alias leg 只有艺人别名，semantic leg 为宽泛的“合作 关系 创作”；没有指向曲目表/具体歌曲的 leg。Gold 曲目表在已过滤原始 BM25 最好第 258、Dense 第 567，属于深排。该因果归类仍需独立查询实验验证。

### 039 · claim:1

- Claim：来源记载的2011年商业活动（support_level=full）。
- Gold Parent / Document：`parent-17f46282a4d28c6c000e6aff` / `doc-jc-v1-030-00f0479d20c219dffad5`；Gold span、投影和 live index 均存在；3 个 Child，映射一致。
- Gold evidence：
  > 2011年，曾投資「DejaVu音樂魔術餐廳」。

- 实际证据 Child：`child-a28274735aaaf2cc58979737`，point `bfa89c8d-7c0c-5399-a281-3d466cd553bd`，filter_pass=true。Indexed dense text：

```text
周杰倫 > 周杰倫 > 副业 > 餐飲事業

2011年，曾投資「DejaVu音樂魔術餐廳」。

2012年初，與西安真愛投資集團合作在西安開設「真愛范特西」KTV，總共投資一億五千多萬。

2007年搭配電影《不能說的秘密》開設與電影情節類似的意大利餐廳「Mr.J 義法廚房」，投資600萬元，目的是為了讓朋友有地方聚會，直到2013年宣布歇業。而「Mr.J 義法廚房 II」，已於2020年7月3日歇業。

由於日本的「藤原豆腐店」關店，2011年周杰倫決定在台灣重現，開設「Mr.J藤原豆腐店」，盡可能仿照電影中藤原豆腐店原先風貌，讓進入店內的顧客彷彿置身於電影中。該店於2015年9月30日歇業。

原本店址是昆凌所開的「Machi doggie咖啡店」，但已於2019年2月28日結束營業，而店面為母親葉惠美的名下，後改開手搖飲料店「麥吉machi machi」，周杰倫也常在IG上大力推薦其奶茶，更讓歌曲〈說好不哭〉的MV場景選在日本原宿店，也造成上海店開幕時有大量排隊人潮。
```

- 计划：`multi_query_hybrid`；原 query：从2008至2012年周杰伦有哪些变化。
  - `q0:original:dense` · `从2008至2012年周杰伦有哪些变化` · filtered Child rank **200**，无 metadata filter 时 **405**。
  - `q0:original:lexical` · `从2008至2012年周杰伦有哪些变化` · filtered Child rank **38**，无 metadata filter 时 **104**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **199**，无 metadata filter 时 **132**。
  - `q2:semantic:dense` · `从2008至2012年周杰伦有哪些变化 时间线 发展 阶段` · filtered Child rank **198**，无 metadata filter 时 **415**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=198、BM25=38。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：Gold 证据 Child 通过过滤。融合/选择：{'fused_child_rank': 151, 'distinct_parent_rank': 89, 'selection_block': 'document cap 4 of 4'}。
- Root cause **F**（high）：Gold Child 已通过时间过滤，BM25 原始 leg 第 38、融合 Child 第 151 / distinct Parent 第 89；Parent 选择时同文档已取满 broad_document_cap=4，因此 Top100 被排除。

### 039 · claim:2

- Claim：来源记载的2012年商业活动（support_level=full）。
- Gold Parent / Document：`parent-17f46282a4d28c6c000e6aff` / `doc-jc-v1-030-00f0479d20c219dffad5`；Gold span、投影和 live index 均存在；3 个 Child，映射一致。
- Gold evidence：
  > 2012年初，與西安真愛投資集團合作在西安開設「真愛范特西」KTV

- 实际证据 Child：`child-a28274735aaaf2cc58979737`，point `bfa89c8d-7c0c-5399-a281-3d466cd553bd`，filter_pass=true。Indexed dense text：

```text
周杰倫 > 周杰倫 > 副业 > 餐飲事業

2011年，曾投資「DejaVu音樂魔術餐廳」。

2012年初，與西安真愛投資集團合作在西安開設「真愛范特西」KTV，總共投資一億五千多萬。

2007年搭配電影《不能說的秘密》開設與電影情節類似的意大利餐廳「Mr.J 義法廚房」，投資600萬元，目的是為了讓朋友有地方聚會，直到2013年宣布歇業。而「Mr.J 義法廚房 II」，已於2020年7月3日歇業。

由於日本的「藤原豆腐店」關店，2011年周杰倫決定在台灣重現，開設「Mr.J藤原豆腐店」，盡可能仿照電影中藤原豆腐店原先風貌，讓進入店內的顧客彷彿置身於電影中。該店於2015年9月30日歇業。

原本店址是昆凌所開的「Machi doggie咖啡店」，但已於2019年2月28日結束營業，而店面為母親葉惠美的名下，後改開手搖飲料店「麥吉machi machi」，周杰倫也常在IG上大力推薦其奶茶，更讓歌曲〈說好不哭〉的MV場景選在日本原宿店，也造成上海店開幕時有大量排隊人潮。
```

- 计划：`multi_query_hybrid`；原 query：从2008至2012年周杰伦有哪些变化。
  - `q0:original:dense` · `从2008至2012年周杰伦有哪些变化` · filtered Child rank **200**，无 metadata filter 时 **405**。
  - `q0:original:lexical` · `从2008至2012年周杰伦有哪些变化` · filtered Child rank **38**，无 metadata filter 时 **104**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **199**，无 metadata filter 时 **132**。
  - `q2:semantic:dense` · `从2008至2012年周杰伦有哪些变化 时间线 发展 阶段` · filtered Child rank **198**，无 metadata filter 时 **415**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=198、BM25=38。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：Gold 证据 Child 通过过滤。融合/选择：{'fused_child_rank': 151, 'distinct_parent_rank': 89, 'selection_block': 'document cap 4 of 4'}。
- Root cause **F**（high）：Gold Child 已通过时间过滤，BM25 原始 leg 第 38、融合 Child 第 151 / distinct Parent 第 89；Parent 选择时同文档已取满 broad_document_cap=4，因此 Top100 被排除。

### 040 · claim:2

- Claim：2003年《叶惠美》发行（support_level=full）。
- Gold Parent / Document：`parent-81d3ee5f0e44759bf67d8c7f` / `doc-jc-v1-034-6c5fcbb5090ae4138248`；Gold span、投影和 live index 均存在；1 个 Child，映射一致。
- Gold evidence：
  > 《葉惠美》是台灣歌手周杰倫的第四張录音室專輯，由台灣新力音樂于2003年7月31日發行，專輯名稱周杰倫以自己的母親名字來命名。

- 实际证据 Child：`child-123681e7db995be1ba38d339`，point `b848a111-aa59-5d0f-beb2-e1c2fdfb5f88`，filter_pass=true。Indexed dense text：

```text
葉惠美 > 葉惠美

《葉惠美》是台灣歌手周杰倫的第四張录音室專輯，由台灣新力音樂于2003年7月31日發行，專輯名稱周杰倫以自己的母親名字來命名。
```

- 计划：`multi_query_hybrid`；原 query：周杰伦的音乐生涯时间线。
  - `q0:original:dense` · `周杰伦的音乐生涯时间线` · filtered Child rank **604**，无 metadata filter 时 **606**。
  - `q0:original:lexical` · `周杰伦的音乐生涯时间线` · filtered Child rank **674**，无 metadata filter 时 **702**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **114**，无 metadata filter 时 **114**。
  - `q2:semantic:dense` · `周杰伦的音乐生涯时间线 时间线 发展 阶段` · filtered Child rank **674**，无 metadata filter 时 **679**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=604、BM25=114。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：Gold 证据 Child 通过过滤。融合/选择：{'fused_child_rank': 636, 'distinct_parent_rank': 338}。
- Root cause **B**（medium）：计划只有宽泛“音乐生涯时间线”及艺人别名，没有逐年/专辑/电影节点 leg；各 Gold Parent 的原始两路最佳 rank 均超过 100，融合 distinct Parent rank 分别为 338、209、247。此为有依据的覆盖不足诊断，恢复效果未实验。

### 040 · claim:5

- Claim：2007年电影上映（support_level=full）。
- Gold Parent / Document：`parent-a83673d2b967ec913e6b0ef3` / `doc-jc-v1-030-00f0479d20c219dffad5`；Gold span、投影和 live index 均存在；2 个 Child，映射一致。
- Gold evidence：
  > 2007年，周杰倫第一部自編自導自演的電影《不能說的·秘密》上映，電影裡大部分的音樂也由他創作。

- 实际证据 Child：`child-ad5bffa6a83866be0f14d8c2`，point `8090cb2c-383f-51d5-b651-26231e48d2fb`，filter_pass=true。Indexed dense text：

```text
周杰倫 > 周杰倫 > 電影事業 > 2003年—2007年: 《头文字D》《满城尽带黄金甲》《不能说的·秘密》

2006年，周杰倫在他第二部电影《滿城盡帶黃金甲》中飾演二王子元杰，在電影中他試圖表現中國的傳統美德——「孝」，也是他個性的縮影。這一部在國際上發行的電影讓北美觀眾首次看到周杰倫。中國大陸的電影評論家對他的表演的評論從「缺乏層次及張力」 [58]到「未失水準」[59]，西方影評則認為其演技十分一般[60][61][62][63][64]。他在《滿城盡帶黃金甲》的表現被提名2007年第26屆香港電影金像獎最佳男配角[65]。

![](https://thumb.wikimedia.org/wikipedia/commons/thumb/5/5d/Jay_Chou_in_Seoul.jpg/250px-Jay_Chou_in_Seoul.jpg?utm_source=zh.wikipedia.org&utm_campaign=parser&utm_content=thumbnail)

2007年，周杰倫第一部自編自導自演的電影《不能說的·秘密》上映，電影裡大部分的音樂也由他創作。2008年1月10日在韓國上映。

![](https://thumb.wikimedia.org/wikipedia/commons/thumb/7/7a/Kungfudunkmoviepromo1.jpg/250px-Kungfudunkmoviepromo1.jpg?utm_source=zh.wikipedia.org&utm_campaign=parser&utm_content=thumbnail)
```

- 计划：`multi_query_hybrid`；原 query：周杰伦的音乐生涯时间线。
  - `q0:original:dense` · `周杰伦的音乐生涯时间线` · filtered Child rank **316**，无 metadata filter 时 **316**。
  - `q0:original:lexical` · `周杰伦的音乐生涯时间线` · filtered Child rank **295**，无 metadata filter 时 **305**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **307**，无 metadata filter 时 **307**。
  - `q2:semantic:dense` · `周杰伦的音乐生涯时间线 时间线 发展 阶段` · filtered Child rank **337**，无 metadata filter 时 **337**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=316、BM25=295。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：Gold 证据 Child 通过过滤。融合/选择：{'fused_child_rank': 357, 'distinct_parent_rank': 209}。
- Root cause **B**（medium）：计划只有宽泛“音乐生涯时间线”及艺人别名，没有逐年/专辑/电影节点 leg；各 Gold Parent 的原始两路最佳 rank 均超过 100，融合 distinct Parent rank 分别为 338、209、247。此为有依据的覆盖不足诊断，恢复效果未实验。

### 040 · claim:7

- Claim：索尼复刻商品资料将《十一月的萧邦》的发行日期标为 2005 年 11 月 1 日（support_level=full）。
- Gold Parent / Document：`parent-95160d6644f27840faf6b97d` / `doc-jc-v1-008-f4bf7554543dfc770775`；Gold span、投影和 live index 均存在；2 个 Child，映射一致。
- Gold evidence：
  > 十一月的蕭邦 ＋ 霍元甲（2LP） 『在十一月聽見蕭邦，掀起一陣浪漫的夜曲風暴。』 （發行日期2005/11/01）

- 实际证据 Child：`child-15c417c937ac0626e41dcf7a`，point `e78c2fdf-7047-5b45-8fa6-5e44aecc9c00`，filter_pass=true。Indexed dense text：

```text
Jay Chou / NOVEMBER’S CHOPIN Vinyl (2LP)

![Jay Chou / NOVEMBER’S CHOPIN Vinyl (2LP)](https://cdn-p.smehost.net/sites/d563907ee0c84f41b15553bfb3eb554c/wp-content/uploads/2020/06/19439768291.jpg)

我們的青春 都有周杰倫！ Jay Chou 20

十一月的蕭邦 ＋ 霍元甲（2LP） 『在十一月聽見蕭邦，掀起一陣浪漫的夜曲風暴。』 （發行日期2005/11/01）

預購期間 6/30 (中午12:00) 至 7/14 (24:00) 限量收單 2020/11/6開始取貨

預購連結 https://jaychou.lnk.to/JayChou20_NC

紐約母帶後期錄音室Sterling Sound（NYC）原始母帶重新高音質製作黑膠母源 德國黑膠刻片大廠optimal media GmbH 升級DMM刻版 180 GRAMS 33 1/3RPM
```

- 计划：`multi_query_hybrid`；原 query：周杰伦的音乐生涯时间线。
  - `q0:original:dense` · `周杰伦的音乐生涯时间线` · filtered Child rank **568**，无 metadata filter 时 **569**。
  - `q0:original:lexical` · `周杰伦的音乐生涯时间线` · filtered Child rank **199**，无 metadata filter 时 **207**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **497**，无 metadata filter 时 **497**。
  - `q2:semantic:dense` · `周杰伦的音乐生涯时间线 时间线 发展 阶段` · filtered Child rank **553**，无 metadata filter 时 **556**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=553、BM25=199。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：Gold 证据 Child 通过过滤。融合/选择：{'fused_child_rank': 442, 'distinct_parent_rank': 247}。
- Root cause **B**（medium）：计划只有宽泛“音乐生涯时间线”及艺人别名，没有逐年/专辑/电影节点 leg；各 Gold Parent 的原始两路最佳 rank 均超过 100，融合 distinct Parent rank 分别为 338、209、247。此为有依据的覆盖不足诊断，恢复效果未实验。

### 042 · claim:1

- Claim：英文专辑列表资料示例（support_level=full）。
- Gold Parent / Document：`parent-b1e841c66f298a811026f08e` / `doc-jc-v1-025-c9da4510ee57c431ccfe`；Gold span、投影和 live index 均存在；1 个 Child，映射一致。
- Gold evidence：
  > **Title of Music Albums** _Jay_ (2000) 杰伦专辑 _Fantasy_ (2001) 范特西

- 实际证据 Child：`child-8c9d321e806ae9514aae28b9`，point `b5244def-b2d6-582d-bb91-d5d1b9e3f08f`，filter_pass=false。Indexed dense text：

```text
Style and Stardom: The Iconicity of Jay Chou > Appendix A

**Title of Music Albums** _Jay_ (2000) 杰伦专辑 _Fantasy_ (2001) 范特西 _The Eight Dimensions_ (2002) 八�空间 _Ye Hui Mei_ (2003) �惠妹 _Common Orange Jasmine_ (2004) 七里香 _November’s Chopin_ (2005) 十一月的萧邦 _Still Fantasy_ (2006) 依然范特西 _On The Run_ (2007) 我很忙 _Capricorn_ (2008) 魔杰� _The Era_ (2010) 跨时代 _Exclamation_ (2011) 惊叹�

**Song Titles** _All the way North_ 一路向北 _Blue and White Porcelain_ 青花瓷 _Bullfight_ 斗牛 _Chrysanthemum Terrace_ 菊花台 _Compendium of Materia Medica_ 本草纲目 _Dragon Fist_ 龙拳 _East Wind Break_ 东风破 _Grandfather’s Tea_ ��泡的茶 _Grandmother_ 外婆 _Hair like Snow_ 发如雪 _Hidden Track_ 轨迹 _In the Name of Father_ 以�之名 _It Rains All Night_ 雨下一整晚 _Lady_ 娘子 _Listen to your Mother_ 听妈妈的话 _Nunchunks_ 双节棍 _Red Imitation_ 红模仿 _Sailor Afraid of Water_ 水手怕水 _Second Class of Year Three_ 三年二班 _Snail_ 蜗牛

35
```

- 计划：`multi_query_hybrid`；原 query：周杰伦的英文资料。
  - `q0:original:dense` · `周杰伦的英文资料` · filtered Child rank **—**，无 metadata filter 时 **373**。
  - `q0:original:lexical` · `周杰伦的英文资料` · filtered Child rank **—**，无 metadata filter 时 **399**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **—**，无 metadata filter 时 **577**。
  - `q2:semantic:dense` · `周杰伦的英文资料 背景 经历 影响` · filtered Child rank **—**，无 metadata filter 时 **489**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=—、BM25=—。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：language=und，要求 en。融合/选择：Gold Child 被过滤或不在已选 Top100。
- Root cause **G**（high）：过滤要求 language=en，但英文专辑列表的索引语言为 und；移除过滤后 Dense 最好第 373、BM25 第 399，说明过滤是必要障碍而非唯一障碍，跨语查询/排序仍待核实。

### 043 · claim:1

- Claim：人物报道转述周杰伦在 CNN 采访中希望听他歌曲的孩子更孝顺父母（support_level=full）。
- Gold Parent / Document：`parent-396d8237183dea741ebce052` / `doc-jc-v2-007-d65a5eb1d97ff8ebd12b`；Gold span、投影和 live index 均存在；4 个 Child，映射一致。
- Gold evidence：
  > 后来，在接受CNN采访时，主持人问他，现在影响力这么大，会不会感觉到身上多了一些责任感。
  >
  > 几乎没有任何迟疑，周杰伦回答：“当然会有责任感，我希望孩子们在听过我的歌后，能够更加孝顺，更加爱父母。”

- 实际证据 Child：`child-465f11eea9e4ed91962d7b80`，point `f15f1be0-cfac-53ff-8161-d21c4d0a0469`，filter_pass=false。Indexed dense text：

```text
41岁周杰伦：时代变了，但我的时代不会过去 > 41岁周杰伦：时代变了，但我的时代不会过去

的确，无论是充满恋爱情愫的《开不了口》、《七里香》，还是送给家人的《外婆》、《听妈妈的话》，抑或是鼓励大家不要轻易放弃的《蜗牛》、《稻香》。

在周杰伦的歌曲里，人们似乎总能在任何一个人生阶段，找到符合自己心境的歌曲。

后来，在接受CNN采访时，主持人问他，现在影响力这么大，会不会感觉到身上多了一些责任感。

几乎没有任何迟疑，周杰伦回答：“当然会有责任感，我希望孩子们在听过我的歌后，能够更加孝顺，更加爱父母。”

这似乎正是周杰伦的迷人之处，一方面，他会告诉我们，不要退缩，不要害怕特殊，另一方面，他又希望大家在坚持个性的同时，是一个正直、孝顺的人。

![](data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==)

毋庸置疑，周杰伦的歌曲给华语乐坛带来了洗牌式的转变。
```

- 计划：`multi_query_hybrid`；原 query：周杰伦的采访资料。
  - `q0:original:dense` · `周杰伦的采访资料` · filtered Child rank **—**，无 metadata filter 时 **10**。
  - `q0:original:lexical` · `周杰伦的采访资料` · filtered Child rank **—**，无 metadata filter 时 **30**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **—**，无 metadata filter 时 **170**。
  - `q2:semantic:dense` · `周杰伦的采访资料 背景 经历 影响` · filtered Child rank **—**，无 metadata filter 时 **27**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=—、BM25=—。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：content_type=commentary，要求 interview。融合/选择：Gold Child 被过滤或不在已选 Top100。
- Root cause **G**（high）：Gold 是人物文章转述 CNN 问答，证据 Child 标为 commentary；计划要求 content_type=interview。移除过滤后 Dense 第 10、BM25 第 30。

### 044 · claim:2

- Claim：人物报道记载周杰伦的新专辑发行间隔逐渐变长（support_level=full）。
- Gold Parent / Document：`parent-19653b37f18f4b5a753a5b6e` / `doc-jc-v2-007-d65a5eb1d97ff8ebd12b`；Gold span、投影和 live index 均存在；3 个 Child，映射一致。
- Gold evidence：
  > 近些年，周杰伦放缓了发片速度，从过去的一年两张，到两年一张，再到如今的四年没推出新专辑。

- 实际证据 Child：`child-128c22f7d442883f8ba8620f`，point `99de6dee-3b09-51ea-8676-d946d9630d14`，filter_pass=false。Indexed dense text：

```text
41岁周杰伦：时代变了，但我的时代不会过去 > 41岁周杰伦：时代变了，但我的时代不会过去

原创 最人物出品 最人物

![](data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==)

谁叫我是周杰伦。”

——周杰伦

![](data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==)

在这四年间，他发行了5首单曲，参演了2部电影，举办了138场演唱会，但每次出现在采访中时，总免不了被问到同一个问题：

“下一张专辑什么时候出？”

近些年，周杰伦放缓了发片速度，从过去的一年两张，到两年一张，再到如今的四年没推出新专辑。

可是，纵使如此，每一次他推出新歌，都总能引起巨大讨论。

![](data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==)

今年，是周杰伦出道的第20年。

时间回到20年前的11月7日，周杰伦推出第一张同名专辑《JAY》，在当天的发片记者会前，经纪人问他：“你害怕没有人来参加哦。”

周杰伦摆弄着手中的手机，点了点头。

那时他大概未曾想到，从这一天起，自己将成为一个时代的开启者。
```

- 计划：`multi_query_hybrid`；原 query：周杰伦的新闻资料，不要现场版。
  - `q0:original:dense` · `周杰伦的新闻资料，不要现场版` · filtered Child rank **—**，无 metadata filter 时 **41**。
  - `q0:original:lexical` · `周杰伦的新闻资料，不要现场版` · filtered Child rank **—**，无 metadata filter 时 **194**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **—**，无 metadata filter 时 **426**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=—、BM25=—。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：content_type=artist_biography，要求 news。融合/选择：Gold Child 被过滤或不在已选 Top100。
- Root cause **G**（high）：Gold 发片节奏段落所在 Child 标为 artist_biography；计划要求 content_type=news。移除过滤后 Dense 第 41，且该段不含现场版。

### 045 · claim:1

- Claim：《晴天》在 2003 年 HitFM 年度百首单曲中名列亚军（support_level=partial）。
- Gold Parent / Document：`parent-e46f773645df47165d634134` / `doc-jc-v1-034-6c5fcbb5090ae4138248`；Gold span、投影和 live index 均存在；1 个 Child，映射一致。
- Gold evidence：
  > 《晴天》在2003年HitFM联播网年度百首單曲中名列亚军

- 实际证据 Child：`child-5a2103dd773ebc381e4755a1`，point `b4411889-413f-57aa-aa00-decb7e3c05d2`，filter_pass=false。Indexed dense text：

```text
葉惠美 > 葉惠美 > 反响

专辑歌曲《晴天》在2003年HitFM联播网年度百首單曲中名列亚军，《她的睫毛》名列第15名，《東風破》名列第41名。专辑被國際唱片業協會香港会认证为2003年度十大銷量國語唱片之一和2003年度最高銷量國語唱片。[6] 在2004年度台灣第15屆金曲獎頒獎典禮中，周杰伦凭借该专辑获得“最佳流行音乐演唱专辑奖”、邝盛凭借歌曲《三年二班》MV获得“最佳音乐录像带导演奖”。[7]

2003年7月16日，全亞洲超過五十家電台同步播放周董《葉惠美》專輯中的第一波主打歌〈以父之名〉，估計當時有超過八億人同時收聽，網友為紀念周杰伦的音樂成就，將每年的7月16日稱為「周杰倫日」。[7]
```

- 计划：`multi_query_hybrid`；原 query：《晴天》的乐评资料，不含翻唱版。
  - `q0:original:dense` · `《晴天》的乐评资料，不含翻唱版` · filtered Child rank **—**，无 metadata filter 时 **1**。
  - `q0:original:lexical` · `《晴天》的乐评资料，不含翻唱版` · filtered Child rank **—**，无 metadata filter 时 **31**。
  - `q1:alias:lexical` · `晴天 晴天歌曲` · filtered Child rank **—**，无 metadata filter 时 **3**。
  - `q2:semantic:dense` · `《晴天》的乐评资料，不含翻唱版 背景 经历 影响` · filtered Child rank **—**，无 metadata filter 时 **1**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=—、BM25=—。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：content_type=chart_event，要求 review；Gold 只把榜单作为部分背景。融合/选择：Gold Child 被过滤或不在已选 Top100。
- Root cause **G**（medium）：计划按“乐评”过滤 review，Gold 的正向部分证据实际是 chart_event 榜单背景；过滤后 0 个候选，移除过滤后 Dense 第 1。是否应为背景性 partial claim 放宽来源类型须人工裁决，不能把它当成乐评。

### 065 · claim:1

- Claim：周杰伦自述很多快歌受到动作片启发（support_level=partial）。
- Gold Parent / Document：`parent-659d449391ffb5694ba9fdf0` / `doc-jc-v1-017-c26a58b5ea39e98dd100`；Gold span、投影和 live index 均存在；3 个 Child，映射一致。
- Gold evidence：
  > 我的很多快歌都是看动作片写出来的。

- 实际证据 Child：`child-7831a26ae40e87a611c5a3f0`，point `ec7e2bef-82e3-54b2-b876-a323f35148fa`，filter_pass=false。Indexed dense text：

```text
大男孩要做NO.1——与周杰伦对话

col_1 | col_2 | col_3

 电影激发创作灵感 问：在你出道之初，吴宗宪曾怀疑过你是否能当歌手，现在回想，觉得他当时是不是看走了眼？ 周：可能是由于我的个性吧，我不爱说话，当时也不知道怎么和媒体应对，反正就豁出去了。我自己也觉得挺纳闷的，为什么我会当一个歌手？好好做一个创作人不是很好吗？话说回来，做歌手可以唱自己喜欢的作品，这与以前写给别人去唱的感觉不一样，所以，我还是蛮感谢他的。 问：作为一个当红创作型歌手，私人时间所剩无几，也不太能和普通人一样生活，如何保证源源不断的创作灵感？ 周：看电影，我会固定一些时间去看电影。 问：在别人的故事里去找灵感？ 周：对。看电影蛮容易刺激我的创作灵感。听着那些动听的电影配乐，我有时就会想，如果这里换成另外一段旋律会怎样？我的很多快歌都是看动作片写出来的。 问：比如《忍者》、《双截棍》了。
```

- 计划：`multi_query_hybrid`；原 query：周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？。
  - `q0:original:dense` · `周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？` · filtered Child rank **—**，无 metadata filter 时 **2**。
  - `q0:original:lexical` · `周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？` · filtered Child rank **—**，无 metadata filter 时 **1**。
  - `q1:alias:lexical` · `周杰伦 周杰倫 周董` · filtered Child rank **—**，无 metadata filter 时 **752**。
  - `q2:semantic:dense` · `周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？ 合作 关系 创作` · filtered Child rank **—**，无 metadata filter 时 **2**。
- 历史 Parent Top100：Dense/BM25/Hybrid 均 MISS；filtered 最佳原始 Child rank Dense=—、BM25=—。生产每 leg 80 Child、历史诊断每 leg 600 Child、深排探针 1270 Child；Parent 去重/映射异常：否。
- 过滤：year/start/end/min_mentioned_year/max_mentioned_year 均缺失，要求 2004。融合/选择：Gold Child 被过滤或不在已选 Top100。
- Root cause **G**（high）：计划按问题中的 2004 年加时间过滤；Gold 采访 Child 虽有相关原文，但 year/时间区间元数据均缺失。移除过滤后 Dense 第 2、BM25 第 1。

## 问题判断与下一步

1. **Top100 低召回主因**：7 个 unit 受到 filter / metadata 阻断；6 个在宽泛规划 legs 下进入深排（024、040）；2 个 039 的 Gold Parent 虽有 BM25 rank 38、融合 distinct Parent rank 89，仍被同文档 4 条配额排除。

2. **Gold / 语料 / 索引一致性**：已核查的 15 条中，**0 条**可证实为 Gold span 不在 frozen Parent、Parent 不在 manifest/projection、点漏入 live index 或映射错误。045 有 **1 条 Gold 部分背景与 query 所要求“乐评”类型的范围争议**，需人工审查；不能在本轮认定 Gold 应改或 filter 应放宽。042 的 `und` 来源是混合语种列表，亦不能直接断定索引标注错误。

3. **可恢复数量**：**7/15** 有明确的技术机制且未见 Gold 范围冲突：006×2、043、044、065 的相关点在无过滤只读探针中进入至少一个原始 leg Top100；039×2 在过滤下 BM25 rank 38、融合 distinct Parent rank 89，但被文档配额排除。045 另有 **1/15** 无过滤后原始 Dense rank 1，但是否应作为“乐评”正例需要人工裁决。024×3、040×3 和 042 共 **7/15** 只确认深排/过滤阻断，尚无受控实验证明能恢复。这里的“可恢复”指机械反事实可达，**不是**实际改动后的 Top100 承诺；改变过滤或配额可能使其他 Parent 被挤出。

4. **Query Planner 是否主要瓶颈**：024/040 的 6/15 是有依据的覆盖不足工作诊断；未做改写 ablation，因此不能断言 Planner 是全部 15 条或整个 95-unit 集合的主要瓶颈。039 是选择配额问题，006 等是 filter 问题。

5. **Dense/BM25 系统性失败**：证据不足。所有相关 span 都进入索引文本；006/043/045/065 在无过滤时有靠前 Dense 或 BM25 rank。024/040 两路深排说明这些宽泛查询的排序弱，但不能据此单独归因 embedding 语义能力或 BM25 分词；042 有跨语言词面不匹配的可能，需要剥离过滤后再验证。

6. **≥92% 判断**：从 83.30% 出发，若上述 7 条机制明确的 unit 全部被恢复，且其余历史命中保持不变，则按既有 39-case macro 口径的**条件性算术值为 93.13%**；该条件尚未通过单一受控实验，且真实改动可能重排候选，因此当前证据**不足以确认** Hybrid Recall@100 可稳定达到 ≥92%。不能把 80/95 micro 当作该目标的基线。

7. **下一轮单一实验建议**：在相同 frozen index、Gold 和 query legs 上做**只读、受控的 metadata filter coverage ablation**，逐 case 对比现有 filter 与仅对证据类型冲突的条件性放行，保持 Dense/BM25、RRF、Top100 和其他配置不变；同时记录恢复的 Gold unit、丢失的既有 hit 和新增非相关 Parent。先将 045 单列人工裁决，不以它驱动放宽规则。该实验优先处理 7 条 G 类，其中 6 条已有无过滤原始 Top100 线索；本轮不执行。

## 可审计性与限制

- JSONL 一行一个 evidence unit，包含全部 15 条原文、Parent/Document、indexed text、Planner variants/legs、filtered/unfiltered 深排 rank、过滤 AST、live-point 校验和根因。
- 历史 Recall 指标完全来自归档的 `gold_v4_hybrid_shadow_baseline_20260917.json` 和 50-case retrieval archive；深排探针是诊断查询，不改变或重算正式历史分数。
- B 类是基于 leg 内容和 >100 排位的工作判断；缺少针对改写查询的 A/B 实验，具体恢复量不足以确认。045 和 042 的语义范围/语种标签需人工审查。
- 本轮没有修改 Gold、corpus、index、embedding、Retriever、Reranker、Context Builder 或任何加权 RRF 参数。

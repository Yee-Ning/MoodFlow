# Frozen Gold V4 Hybrid Shadow Baseline

Gold 50 cases（full 29、partial 10、insufficient 7、needs_input 4）；supported evidence units 95。

Gold SHA-256 `1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；corpus `data/knowledge/v2/knowledge_enriched_chunks.jsonl` SHA-256 `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。

Index manifest: `D:\code\ProjectsAll\MoodFlow\ai-service\data\indexing\v1\knowledge-v2-8608ffc2fa72e441\v3\index_build_manifest.json` (SHA-256 `0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`)；collection `music_knowledge_v2_8608ffc2fa72e441_v1_v2_v3`，1270 points。

Dense `Qwen/Qwen3-Embedding-0.6B` revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`；BM25 `qdrant/bm25`；reranker `Qwen/Qwen3-Reranker-0.6B` revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`。

实际配置：RRF k=60，dense/BM25 权重 1.0/1.0，per-leg Top80，候选 Top40，rerank Top12；诊断另查 Top100，未送入 reranker。

阶段状态：{"query_understanding": {"ready": 46, "needs_input": 4}, "retrieval": {"success": 45, "empty": 1, "not_run": 4}, "rerank": {"success": 45, "empty": 1, "not_run": 4}, "context": {"success": 45, "empty": 1, "not_run": 4}}。

| Metric | Dense | BM25 | Hybrid diagnostic |
| --- | ---: | ---: | ---: |
| Evidence Recall@10 | 0.6037 | 0.4963 | 0.5872 |
| Evidence Recall@20 | 0.6976 | 0.5689 | 0.6836 |
| Evidence Recall@40 | 0.7417 | 0.6247 | 0.7049 |
| Evidence Recall@100 | 0.7417 | 0.8067 | 0.8274 |

Actual Hybrid Recall@40: 0.7813
Rerank Recall@12: 0.6742
Final Context Precision: 0.1662

Top100 是独立诊断查询，不保证与正式 Top40 构成严格前缀。有 3 个正式 Top40 evidence unit 不在诊断 Hybrid Top100，详见 JSON。上表 @10/20/40/100 均取诊断排名；正式 Hybrid @40 单列。

## Funnel

{'dense_top100': 68, 'bm25_top100': 73, 'hybrid_top100': 76, 'hybrid_top40': 69, 'rerank_top12': 53, 'final_context': 50}

## Failure counts

{'FUSION_RANKING_MISS': 10, 'BOTH': 64, 'HYBRID_PROMOTED_PAST_40': 2, 'HYBRID_DEMOTED_PAST_40': 6, 'DENSE_AND_BM25_MISS': 16, 'NEITHER': 18, 'SUCCESS': 50, 'BM25_ONLY_RECOVERY': 9, 'RERANK_MISS': 16, 'DENSE_ONLY_RECOVERY': 4, 'CONTEXT_DROP': 3}

A/B/C/D 是诊断 Top100 的通道覆盖；E/F/G 以正式 Top40→Top12→final context 计。分类互斥计数为 DENSE_AND_BM25_MISS、FUSION_RANKING_MISS、RERANK_MISS、CONTEXT_DROP、SUCCESS；Dense-only、BM25-only、BOTH、NEITHER 是另一个可交叉维度。

从当前计数看，下一轮应先核查 Retriever 入口和融合/选择：16 个 evidence unit 在两通道诊断 Top100 均未命中，另有 10 个至少一通道命中但正式 Top40 未命中。此处只指出检查顺序，不推断词法或语义根因。

## Per-claim differences

| Case | Claim / unit | Dense rank | BM25 rank | Hybrid rank | Actual Top40 | Top12 | Final | Pattern / failure |
| --- | --- | ---: | ---: | ---: | :---: | :---: | :---: | --- |
| rag-v0-001 | 杰威尔称周杰伦早期 DEMO 已有较高完成度 | 28 | 77 | 69 | N | N | N | BOTH / FUSION_RANKING_MISS |
| rag-v0-001 | 2003 年 HitFM 年度榜单同时列有《晴天》和《东风破》 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-001 | 2000年首张《Jay》发行 | 11 | 82 | 14 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-001 | 从幕后走向幕前 | 28 | 77 | 69 | N | N | N | BOTH / FUSION_RANKING_MISS |
| rag-v0-001 | DEMO 保留创作初期的画面感 | 28 | 77 | 69 | N | N | N | BOTH / FUSION_RANKING_MISS |
| rag-v0-002 | 杰威尔称首张《Jay》融合多种曲风 | — | 65 | 78 | N | N | N | BM25_ONLY_RECOVERY / FUSION_RANKING_MISS |
| rag-v0-002 | 周杰伦在采访中把音乐理解为依靠想象力的广阔空间 | — | 5 | — | Y | N | N | BM25_ONLY_RECOVERY / RERANK_MISS |
| rag-v0-002 | 研究将《中国风》一词在华语乐坛活跃与 2003 年《东风破》发布相联系 | 25 | 8 | 13 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-002 | 研究认为周杰伦作品中中国风歌曲占比较高 | 17 | 44 | 5 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-002 | 英文研究认为周杰伦创造了混合多种音乐风格的个人风格 | 5 | — | 12 | Y | Y | Y | DENSE_ONLY_RECOVERY / SUCCESS |
| rag-v0-002 | 英文研究把中国风音乐描述为传统旋律与乐器和 R&B、嘻哈等流行风格的并置 | 3 | — | 11 | Y | Y | Y | DENSE_ONLY_RECOVERY / SUCCESS |
| rag-v0-002 | 中国风的五声音阶和民族乐器与电子音效结合 | 25 | 8 | 13 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-003 | 杰威尔简介称形成所谓“周杰伦现象” | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-003 | 研究将中国风术语活跃与《东风破》发布相联系 | — | 36 | 55 | N | N | N | BM25_ONLY_RECOVERY / FUSION_RANKING_MISS |
| rag-v0-003 | 英文研究认为其风格被其他艺人模仿 | 4 | — | 4 | Y | Y | Y | DENSE_ONLY_RECOVERY / SUCCESS |
| rag-v0-004 | 《叶惠美》专辑以周杰伦母亲的名字命名 | — | 67 | 90 | N | N | N | BM25_ONLY_RECOVERY / FUSION_RANKING_MISS |
| rag-v0-004 | 杰威尔采访记载杨峻荣在 2000 年见到周杰伦时他已在录音室住了一年多 | 29 | 3 | 7 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-004 | 人物报道记载杨俊荣听到《可爱女人》后推动公司推出周杰伦首张专辑 | 16 | — | 22 | Y | N | N | DENSE_ONLY_RECOVERY / RERANK_MISS |
| rag-v0-005 | 《晴天》列入周杰伦挑选公开展示的五首 DEMO | 12 | 4 | 7 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-005 | 早期 DEMO 完成度较高 | 12 | 4 | 7 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-006 | 索尼资料称团队讨论后用首波主打《七里香》作为第五张专辑名称 | — | — | — | Y | Y | Y | NEITHER / SUCCESS |
| rag-v0-006 | 索尼资料称《七里香》MV 在日本乡间取景 | — | — | — | Y | Y | Y | NEITHER / SUCCESS |
| rag-v0-007 | 方文山认为青花瓷具有中国传统文化的识别度 | 17 | 6 | 12 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-007 | 学术资料把《青花瓷》归为中国风歌曲 | 9 | 5 | 5 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-008 | 研究将《中国风》一词在华语乐坛活跃与 2003 年《东风破》发布相联系 | 8 | 4 | 7 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-008 | 《东风破》由周杰伦演唱、方文山作词，收录于《叶惠美》 | 1 | 3 | 2 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-008 | 《东风破》使用二胡和琵琶 | 1 | 3 | 2 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-023 | 报道将《东风破》《菊花台》《千里之外》列为方文山与周杰伦共同作品 | 15 | 13 | 10 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-023 | 采访记载两人合作的多数歌曲先有曲再有词，方文山配合周杰伦 | 4 | 62 | 2 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-024 | 资料记载《落雨声》是周杰伦与方文山的早期合作作品 | 2 | 9 | 1 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-024 | 《藉口》由周杰伦作词 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-024 | 《将军》由黄俊郎作词 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-024 | 《我的地盘》由方文山作词 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-026 | 洪敬尧凭《蓝色风暴》获最佳编曲人提名 | 11 | 3 | 7 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-026 | 《蓝色风暴》的编曲署名为洪敬尧 | 3 | 8 | 5 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-026 | 《四面楚歌》的编曲署名为洪敬尧 | 3 | 8 | 5 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-026 | 《浪漫手机》的编曲署名为洪敬尧 | 3 | 8 | 5 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-026 | 《麦芽糖》的编曲署名为洪敬尧 | 3 | 8 | 5 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-031 | 研究将林俊杰列为参与中国风歌曲创作的音乐人之一 | — | 5 | 20 | Y | Y | Y | BM25_ONLY_RECOVERY / SUCCESS |
| rag-v0-034 | 《东风破》的乐器与中国风特征 | 1 | 60 | 12 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-034 | 《东风破》歌名来源 | 1 | 60 | 12 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-034 | 方文山对青花瓷传统文化识别度的评价 | 23 | 23 | 25 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-034 | 方文山将《青花瓷》列入中国风作品 | 23 | 23 | 25 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-036 | 来源称2000年《Jay》融合多种曲风 | 2 | 2 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-036 | 《东风破》收于《叶惠美》 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-036 | 《东风破》的单曲乐器特征 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-036 | 《叶惠美》于2003年发行的时间锚点 | — | 34 | 48 | N | N | N | BM25_ONLY_RECOVERY / FUSION_RANKING_MISS |
| rag-v0-036 | 《11月的萧邦》于2005年发行的时间锚点 | 8 | 77 | 11 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-037 | 《叶惠美》于 2003 年 7 月 31 日发行 | — | 3 | 62 | N | N | N | BM25_ONLY_RECOVERY / FUSION_RANKING_MISS |
| rag-v0-037 | 2003 年 HitFM 年度榜单同时列有《晴天》和《东风破》 | 10 | 57 | 53 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-037 | 杰威尔资料记载周杰伦于 2003 年登上《时代》亚洲版封面 | 4 | 11 | 2 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-039 | 来源记载的2011年商业活动 | — | 63 | 66 | N | N | N | BM25_ONLY_RECOVERY / FUSION_RANKING_MISS |
| rag-v0-039 | 来源记载的2012年商业活动 | — | 63 | 66 | N | N | N | BM25_ONLY_RECOVERY / FUSION_RANKING_MISS |
| rag-v0-040 | 2000年首专发行 | 7 | 5 | 1 | Y | Y | N | BOTH / CONTEXT_DROP |
| rag-v0-040 | 2003年《叶惠美》发行 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-040 | 2003年《晴天》榜单节点 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-040 | 2007年《我很忙》专辑年表 | 14 | 60 | 13 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-040 | 2007年电影上映 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-040 | 2019年《说好不哭》发布 | 17 | 33 | 12 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-040 | 索尼复刻商品资料将《十一月的萧邦》的发行日期标为 2005 年 11 月 1 日 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-042 | 英文专辑列表资料示例 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-042 | 英文正文资料示例 | 9 | 44 | 15 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-043 | 人物报道转述周杰伦在 CNN 采访中希望听他歌曲的孩子更孝顺父母 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-043 | 周杰伦在直接问答中表示看电影会刺激创作灵感 | 19 | 46 | 45 | Y | N | N | BOTH / RERANK_MISS |
| rag-v0-044 | 杰威尔官方新闻列出周杰伦挑选公开展示的五首 DEMO | 6 | 9 | 7 | Y | Y | N | BOTH / CONTEXT_DROP |
| rag-v0-044 | 人物报道记载周杰伦的新专辑发行间隔逐渐变长 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-044 | 早期 DEMO 完成度较高 | 6 | 9 | 7 | Y | Y | N | BOTH / CONTEXT_DROP |
| rag-v0-045 | 《晴天》在 2003 年 HitFM 年度百首单曲中名列亚军 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-049 | 《爱在西元前》的最佳作曲人奖得主是周杰伦 | 2 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-049 | 《威廉古堡》的最佳作詞人獎得主是方文山 | 2 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-049 | 《双截棍》的最佳编曲人奖得主是钟兴民 | 2 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-050 | 20周年黑胶套装合计157首曲目 | 1 | 11 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-051 | Chris Gehringer负责黑胶母带音源制作 | 1 | 4 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-051 | 德国Optimal Media GmbH负责DMM刻板 | 1 | 4 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-052 | 杰威尔公告称《Mojito》官方YouTube上线7天突破1000万点阅 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-053 | 《纽约地铁》是公告中此前未发表的DEMO | 2 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-054 | 索尼称《稻香》结合嘻哈民谣和周式饶舌 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-054 | 索尼称《稻香》描绘都市压力与返乡向往 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-055 | 《花海》的歌词由古小力和黄凌嘉共同完成 | 1 | 27 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-056 | 索尼称《喬克叔叔》的灵感来自电影《蝙蝠侠：黑暗骑士》 | 1 | 2 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-057 | 《魔術先生》的编曲由钟兴民完成 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-057 | 索尼称《魔術先生》的编曲带有爵士风味 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-058 | 《流浪詩人》由方文山作词 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-058 | 《流浪詩人》是周杰伦与杨瑞代的国台语对唱 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-059 | 《蘭亭序》由方文山作词 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-059 | 索尼称《蘭亭序》取材于同名书法作品 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-060 | 索尼称《龍戰騎士》采用电子摇滚风格 | 1 | 2 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-060 | 索尼称《龍戰騎士》设定在百年后的世界 | 1 | 2 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-061 | 方文山主张以中国元素结合流行形式面向年轻群体传承传统文化 | 1 | 14 | 3 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-062 | 《山水合璧》歌词融入《富春山居图》故事、画风与富春江景色 | 1 | 1 | 1 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-063 | 环球音乐日本称《最伟大的作品》在IFPI统计中获2022年全球专辑销量第一 | 2 | 4 | 3 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-064 | 研究选取《菊花台》和《千里之外》作为分析歌曲 | 3 | 71 | 7 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-064 | 研究采用多种定性分析方法 | 3 | 71 | 7 | Y | Y | Y | BOTH / SUCCESS |
| rag-v0-065 | 周杰伦自述很多快歌受到动作片启发 | — | — | — | N | N | N | NEITHER / DENSE_AND_BM25_MISS |
| rag-v0-066 | 凤凰访谈称方周合作多数先曲后词 | 3 | 49 | 49 | Y | Y | Y | BOTH / SUCCESS |

# Gold V4 answerability-aware：同 Trace 三口径对照

- Gold V3 SHA-256：`5d2888542042af16bf38d3ae63f45c956c8e37f658397e2f5d3a78c3759236e9`；Gold V4 candidate SHA-256：`1a3de179c82cd5e6d3c2cb49fc76ef82a947dc02ba4449536349dbce7297850a`。
- 两份历史 Trace 经 SHA-256 核对，只读取既有候选集、Top12 与 final context；没有重新运行 Retrieval。
- 旧 legacy 与 group-aware 路径及其既有报告逐值一致；V3 兼容性由既有同 Trace 报告和相关测试保持。

## 评分语义

- 仅 approved 且 `answerability_level` 为 fully 或 partially 的 case 进入正样本。insufficient 和 needs_input 不进入三项 evidence 指标。`expected_outcome` 仍用于旧口径，不参与本口径 eligibility。
- 每个显式 evidence_group 是一个单位；未分组且有相同 claim 标签的同 Parent spans 合成一个单位；没有 claim 标签的 supporting span 各算一个证据单位。`support_level=insufficient` 不计。无证据映射的 required_facts / forbidden_claims 不计。
- 每 case 的 Evidence Recall@40 = Top40 命中单位数 / 该 case 支持单位数；Evidence Rerank Recall@12 同理。Final Context Precision = final context 中属于已满足单位的唯一 Parent 数 / final context 唯一 Parent 数。三指标再对 eligible case 等权宏平均。
- 单位命中调用既有 group-aware `score_stage`：exact 按 Parent ID，duplicate/equivalent 任一可接受 Parent，complementary 必须全部 Parent。
- Schema 限制：31 个 span 没有 claim 标签，无法证明它们之间的 claim 同一性；按独立证据单位回退。此数是评分代理单位数，不等于人工核定的真实 claim 数。
- 逐 case 的 unsupported_claims 原文取自 Gold V4 的 required_facts / forbidden_claims，仅用于审计展示，从不参加分母。

## fallback

Trace：`evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl`，SHA-256 `7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e`。
full=13，partial=8，insufficient=6，needs_input=3；评分证据单位 65（full 44、partial 21；按类型 {'unlabeled_span': 31, 'claim': 32, 'group': 2}）。

| Metric | Legacy V4 | Group-aware V4 | Answerability-aware + Group-aware V4 |
| --- | ---: | ---: | ---: |
| Evidence Recall@40 | 0.6868131868 | 0.6868131868 | 0.6413832200 |
| Evidence Rerank Recall@12 | 0.3827838828 | 0.3827838828 | 0.4283446712 |
| Final Context Precision | 0.1153846154 | 0.1153846154 | 0.1190476190 |

### 逐条 partial case

#### rag-v0-003

- 问题：Jay Chou 对华语音乐有什么影响
- 支持单位 3；Top40 命中 1，Top12 命中 1，final context 命中 0。Case 分数：0.3333 / 0.3333 / 0.0000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：不得将来源评价写成已独立证实的行业共识、影响规模或因果贡献；不得用歌曲主题举例直接证明对华语音乐的影响。
- `claim:1` 杰威尔简介称形成所谓“周杰伦现象”；Parent `parent-abd13550e82163b9f04dd8c1`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-abd13550e82163b9f04dd8c1`：2000年出道，隨即引起華語音樂界重大注目，並形成所謂的「周杰倫現象」
- `claim:2` 研究将中国风术语活跃与《东风破》发布相联系；Parent `parent-bb9810b50e07fbcbb9d00c66`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-bb9810b50e07fbcbb9d00c66`：21世纪初，真正的“中国风”音乐风格逐渐明晰，直至周杰伦的《东风破》（2003年）歌曲一经发布，“中国风”一词便在华语乐坛中活跃起来。
- `claim:3` 英文研究认为其风格被其他艺人模仿；Parent `parent-99cbdb202deb91c31a873be5`；Gold support_level=['full']；Top40/Top12/Final=True/True/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-99cbdb202deb91c31a873be5`：His unique style of music has been imitated by other artistes as they hope to repeat his success<sup>28</sup> .

#### rag-v0-005

- 问题：歌曲《晴天》的创作背景
- 支持单位 2；Top40 命中 2，Top12 命中 2，final context 命中 0。Case 分数：1.0000 / 1.0000 / 0.0000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：缺少《晴天》专属创作缘起证据。
- `span:1` [Gold 未标注 claim；见下列 span]；Parent `parent-0276a6cd536dfaa02e3b39eb`；Gold support_level=['partial']；Top40/Top12/Final=True/True/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-0276a6cd536dfaa02e3b39eb`：周杰倫的歌曲席捲無數樂迷的心，音樂創作最初始的模樣不免引人好奇，杰威爾音樂聯手北京「薄盒App」，推出「周杰倫限定珍藏DEMO空間」，由周杰倫挑選的5首DEMO組成，包含「青花瓷」、「晴天」、「擱淺」、「愛在西元前」，以及一首從未發表且完成度極高如新歌的「紐約地鐵」，這些向來不輕易示人的DEMO即將曝光！
- `claim:1` 早期 DEMO 完成度较高；Parent `parent-0276a6cd536dfaa02e3b39eb`；Gold support_level=['partial']；Top40/Top12/Final=True/True/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-0276a6cd536dfaa02e3b39eb`：與其他音樂創作人最大的不同是，周杰倫的音樂在創作初期的DEMO就是以高完整度的模樣呈現，許多合作過的編曲老師都為之驚嘆！

#### rag-v0-008

- 问题：歌曲《东风破》的创作背景
- 支持单位 3；Top40 命中 3，Top12 命中 3，final context 命中 3。Case 分数：1.0000 / 1.0000 / 0.5000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：冻结材料未提供其具体创作动机、灵感来源或过程。
- `span:1` [Gold 未标注 claim；见下列 span]；Parent `parent-bb9810b50e07fbcbb9d00c66`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-bb9810b50e07fbcbb9d00c66`：21世纪初，真正的“中国风”音乐风格逐渐明晰，直至周杰伦的《东风破》（2003年）歌曲一经发布，“中国风”一词便在华语乐坛中活跃起来。
- `span:2` [Gold 未标注 claim；见下列 span]；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：〈东风破〉是一首由周杰伦演唱，方文山作词的歌曲，收录在周杰伦的专辑《叶惠美》中。
- `claim:1` 《东风破》使用二胡和琵琶；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：这首歌运用了二胡和琵琶两种中国民族乐器，是中国风的代表之作。

#### rag-v0-031

- 问题：比较周杰伦和林俊杰的音乐风格
- 支持单位 1；Top40 命中 1，Top12 命中 1，final context 命中 1。Case 分数：1.0000 / 1.0000 / 0.2500。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：缺少林俊杰独立风格证据，不能可靠比较。
- `span:1` [Gold 未标注 claim；见下列 span]；Parent `parent-bb9810b50e07fbcbb9d00c66`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-bb9810b50e07fbcbb9d00c66`：华语乐坛中越来越多的音乐人纷纷加入到创作“中国风”歌曲的队伍中，如林俊杰、陶喆、胡彦斌、刘珂矣、李玉刚等，创作了无数首经典歌曲，如《曹操》（2006年）、《醉赤壁》（2008 年 ）、《蝴 蝶》（2006 年 ）、《兰 亭 序》（2008 年）、《一尘梦》（2020年）等。

#### rag-v0-034

- 问题：《东风破》与《青花瓷》相比有什么差异
- 支持单位 4；Top40 命中 4，Top12 命中 2，final context 命中 0。Case 分数：1.0000 / 0.5000 / 0.0000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：现有证据只分别说明特征，不能确认两曲的直接差异或程度。
- `claim:1` 《东风破》的乐器与中国风特征；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['full']；Top40/Top12/Final=True/True/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：这首歌运用了二胡和琵琶两种中国民族乐器，是中国风的代表之作。
- `claim:2` 《东风破》歌名来源；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['full']；Top40/Top12/Final=True/True/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：歌曲的名字，来自古代民间一种曲的形式，叫做曲破[1]。
- `claim:3` 方文山对青花瓷传统文化识别度的评价；Parent `parent-afca7c2d173dc002cca86aef`；Gold support_level=['full']；Top40/Top12/Final=True/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-afca7c2d173dc002cca86aef`：方文山认为，中国的传统文化具有特殊的文化识别度，比如水墨画、京剧、青花瓷，不用解释，大家一看就知道来自哪个民族。
- `claim:4` 方文山将《青花瓷》列入中国风作品；Parent `parent-afca7c2d173dc002cca86aef`；Gold support_level=['full']；Top40/Top12/Final=True/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-afca7c2d173dc002cca86aef`：像《本草纲目》《青花瓷》等作品，就是他有意无意地去经营、创作“中国风”。

#### rag-v0-036

- 问题：周杰伦 2000 到 2005 年的音乐风格如何演变
- 支持单位 5；Top40 命中 2，Top12 命中 1，final context 命中 1。Case 分数：0.4000 / 0.2000 / 0.2500。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：不得以2005年专辑发行日期推断2005年的音乐风格变化；不得把《东风破》单曲特征推广为整张专辑或2000至2005年整体风格。
- `claim:1` 来源称2000年《Jay》融合多种曲风；Parent `parent-cca31e09c6d05f09267862a1`；Gold support_level=['full']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-cca31e09c6d05f09267862a1`：2000年11月6日，周杰倫首張個人創作專輯「Jay」發行，融合多元曲風的音樂風格為流行樂壇帶來嶄新革命，從此也開啟了華語樂壇新紀元！
- `claim:2` 《东风破》收于《叶惠美》；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：〈东风破〉是一首由周杰伦演唱，方文山作词的歌曲，收录在周杰伦的专辑《叶惠美》中。
- `claim:3` 《东风破》的单曲乐器特征；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：这首歌运用了二胡和琵琶两种中国民族乐器，是中国风的代表之作。
- `claim:4` 《叶惠美》于2003年发行的时间锚点；Parent `parent-81d3ee5f0e44759bf67d8c7f`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-81d3ee5f0e44759bf67d8c7f`：《葉惠美》是台灣歌手周杰倫的第四張录音室專輯，由台灣新力音樂于2003年7月31日發行，專輯名稱周杰倫以自己的母親名字來命名。
- `claim:5` 《11月的萧邦》于2005年发行的时间锚点；Parent `parent-738563d68235b4f923c1ac79`；Gold support_level=['full']；Top40/Top12/Final=True/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-738563d68235b4f923c1ac79`：2005年11月周杰倫發行個人第6張音樂專輯《11月的蕭邦》

#### rag-v0-039

- 问题：从2008至2012年周杰伦有哪些变化
- 支持单位 2；Top40 命中 0，Top12 命中 0，final context 命中 0。Case 分数：0.0000 / 0.0000 / 0.0000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：局部事件不能证明2008至2012年完整变化或事业转型。
- `claim:1` 来源记载的2011年商业活动；Parent `parent-17f46282a4d28c6c000e6aff`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17f46282a4d28c6c000e6aff`：2011年，曾投資「DejaVu音樂魔術餐廳」。
- `claim:2` 来源记载的2012年商业活动；Parent `parent-17f46282a4d28c6c000e6aff`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17f46282a4d28c6c000e6aff`：2012年初，與西安真愛投資集團合作在西安開設「真愛范特西」KTV

#### rag-v0-045

- 问题：《晴天》的乐评资料，不含翻唱版
- 支持单位 1；Top40 命中 0，Top12 命中 0，final context 命中 0。Case 分数：0.0000 / 0.0000 / 0.0000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：缺少针对《晴天》音乐风格/审美/编曲的乐评文本。
- `span:1` [Gold 未标注 claim；见下列 span]；Parent `parent-e46f773645df47165d634134`；Gold support_level=['partial']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-e46f773645df47165d634134`：专辑歌曲《晴天》在2003年HitFM联播网年度百首單曲中名列亚军，《她的睫毛》名列第15名，《東風破》名列第41名。

## nav_context6

Trace：`evals/artifacts/gold_v2_trace_v3_nav_context6_20260916.jsonl`，SHA-256 `9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967`。
full=13，partial=8，insufficient=6，needs_input=3；评分证据单位 65（full 44、partial 21；按类型 {'unlabeled_span': 31, 'claim': 32, 'group': 2}）。

| Metric | Legacy V4 | Group-aware V4 | Answerability-aware + Group-aware V4 |
| --- | ---: | ---: | ---: |
| Evidence Recall@40 | 0.6868131868 | 0.6868131868 | 0.6413832200 |
| Evidence Rerank Recall@12 | 0.3827838828 | 0.3827838828 | 0.4283446712 |
| Final Context Precision | 0.1307692308 | 0.1307692308 | 0.1396825397 |

### 逐条 partial case

#### rag-v0-003

- 问题：Jay Chou 对华语音乐有什么影响
- 支持单位 3；Top40 命中 1，Top12 命中 1，final context 命中 1。Case 分数：0.3333 / 0.3333 / 0.1667。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：不得将来源评价写成已独立证实的行业共识、影响规模或因果贡献；不得用歌曲主题举例直接证明对华语音乐的影响。
- `claim:1` 杰威尔简介称形成所谓“周杰伦现象”；Parent `parent-abd13550e82163b9f04dd8c1`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-abd13550e82163b9f04dd8c1`：2000年出道，隨即引起華語音樂界重大注目，並形成所謂的「周杰倫現象」
- `claim:2` 研究将中国风术语活跃与《东风破》发布相联系；Parent `parent-bb9810b50e07fbcbb9d00c66`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-bb9810b50e07fbcbb9d00c66`：21世纪初，真正的“中国风”音乐风格逐渐明晰，直至周杰伦的《东风破》（2003年）歌曲一经发布，“中国风”一词便在华语乐坛中活跃起来。
- `claim:3` 英文研究认为其风格被其他艺人模仿；Parent `parent-99cbdb202deb91c31a873be5`；Gold support_level=['full']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-99cbdb202deb91c31a873be5`：His unique style of music has been imitated by other artistes as they hope to repeat his success<sup>28</sup> .

#### rag-v0-005

- 问题：歌曲《晴天》的创作背景
- 支持单位 2；Top40 命中 2，Top12 命中 2，final context 命中 2。Case 分数：1.0000 / 1.0000 / 0.2000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：缺少《晴天》专属创作缘起证据。
- `span:1` [Gold 未标注 claim；见下列 span]；Parent `parent-0276a6cd536dfaa02e3b39eb`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-0276a6cd536dfaa02e3b39eb`：周杰倫的歌曲席捲無數樂迷的心，音樂創作最初始的模樣不免引人好奇，杰威爾音樂聯手北京「薄盒App」，推出「周杰倫限定珍藏DEMO空間」，由周杰倫挑選的5首DEMO組成，包含「青花瓷」、「晴天」、「擱淺」、「愛在西元前」，以及一首從未發表且完成度極高如新歌的「紐約地鐵」，這些向來不輕易示人的DEMO即將曝光！
- `claim:1` 早期 DEMO 完成度较高；Parent `parent-0276a6cd536dfaa02e3b39eb`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-0276a6cd536dfaa02e3b39eb`：與其他音樂創作人最大的不同是，周杰倫的音樂在創作初期的DEMO就是以高完整度的模樣呈現，許多合作過的編曲老師都為之驚嘆！

#### rag-v0-008

- 问题：歌曲《东风破》的创作背景
- 支持单位 3；Top40 命中 3，Top12 命中 3，final context 命中 3。Case 分数：1.0000 / 1.0000 / 0.3333。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：冻结材料未提供其具体创作动机、灵感来源或过程。
- `span:1` [Gold 未标注 claim；见下列 span]；Parent `parent-bb9810b50e07fbcbb9d00c66`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-bb9810b50e07fbcbb9d00c66`：21世纪初，真正的“中国风”音乐风格逐渐明晰，直至周杰伦的《东风破》（2003年）歌曲一经发布，“中国风”一词便在华语乐坛中活跃起来。
- `span:2` [Gold 未标注 claim；见下列 span]；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：〈东风破〉是一首由周杰伦演唱，方文山作词的歌曲，收录在周杰伦的专辑《叶惠美》中。
- `claim:1` 《东风破》使用二胡和琵琶；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：这首歌运用了二胡和琵琶两种中国民族乐器，是中国风的代表之作。

#### rag-v0-031

- 问题：比较周杰伦和林俊杰的音乐风格
- 支持单位 1；Top40 命中 1，Top12 命中 1，final context 命中 1。Case 分数：1.0000 / 1.0000 / 0.2000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：缺少林俊杰独立风格证据，不能可靠比较。
- `span:1` [Gold 未标注 claim；见下列 span]；Parent `parent-bb9810b50e07fbcbb9d00c66`；Gold support_level=['partial']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-bb9810b50e07fbcbb9d00c66`：华语乐坛中越来越多的音乐人纷纷加入到创作“中国风”歌曲的队伍中，如林俊杰、陶喆、胡彦斌、刘珂矣、李玉刚等，创作了无数首经典歌曲，如《曹操》（2006年）、《醉赤壁》（2008 年 ）、《蝴 蝶》（2006 年 ）、《兰 亭 序》（2008 年）、《一尘梦》（2020年）等。

#### rag-v0-034

- 问题：《东风破》与《青花瓷》相比有什么差异
- 支持单位 4；Top40 命中 4，Top12 命中 2，final context 命中 2。Case 分数：1.0000 / 0.5000 / 0.1667。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：现有证据只分别说明特征，不能确认两曲的直接差异或程度。
- `claim:1` 《东风破》的乐器与中国风特征；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['full']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：这首歌运用了二胡和琵琶两种中国民族乐器，是中国风的代表之作。
- `claim:2` 《东风破》歌名来源；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['full']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：歌曲的名字，来自古代民间一种曲的形式，叫做曲破[1]。
- `claim:3` 方文山对青花瓷传统文化识别度的评价；Parent `parent-afca7c2d173dc002cca86aef`；Gold support_level=['full']；Top40/Top12/Final=True/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-afca7c2d173dc002cca86aef`：方文山认为，中国的传统文化具有特殊的文化识别度，比如水墨画、京剧、青花瓷，不用解释，大家一看就知道来自哪个民族。
- `claim:4` 方文山将《青花瓷》列入中国风作品；Parent `parent-afca7c2d173dc002cca86aef`；Gold support_level=['full']；Top40/Top12/Final=True/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-afca7c2d173dc002cca86aef`：像《本草纲目》《青花瓷》等作品，就是他有意无意地去经营、创作“中国风”。

#### rag-v0-036

- 问题：周杰伦 2000 到 2005 年的音乐风格如何演变
- 支持单位 5；Top40 命中 2，Top12 命中 1，final context 命中 1。Case 分数：0.4000 / 0.2000 / 0.1667。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：不得以2005年专辑发行日期推断2005年的音乐风格变化；不得把《东风破》单曲特征推广为整张专辑或2000至2005年整体风格。
- `claim:1` 来源称2000年《Jay》融合多种曲风；Parent `parent-cca31e09c6d05f09267862a1`；Gold support_level=['full']；Top40/Top12/Final=True/True/True；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-cca31e09c6d05f09267862a1`：2000年11月6日，周杰倫首張個人創作專輯「Jay」發行，融合多元曲風的音樂風格為流行樂壇帶來嶄新革命，從此也開啟了華語樂壇新紀元！
- `claim:2` 《东风破》收于《叶惠美》；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：〈东风破〉是一首由周杰伦演唱，方文山作词的歌曲，收录在周杰伦的专辑《叶惠美》中。
- `claim:3` 《东风破》的单曲乐器特征；Parent `parent-17747bf81bf90411ed3b8a4f`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17747bf81bf90411ed3b8a4f`：这首歌运用了二胡和琵琶两种中国民族乐器，是中国风的代表之作。
- `claim:4` 《叶惠美》于2003年发行的时间锚点；Parent `parent-81d3ee5f0e44759bf67d8c7f`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-81d3ee5f0e44759bf67d8c7f`：《葉惠美》是台灣歌手周杰倫的第四張录音室專輯，由台灣新力音樂于2003年7月31日發行，專輯名稱周杰倫以自己的母親名字來命名。
- `claim:5` 《11月的萧邦》于2005年发行的时间锚点；Parent `parent-738563d68235b4f923c1ac79`；Gold support_level=['full']；Top40/Top12/Final=True/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-738563d68235b4f923c1ac79`：2005年11月周杰倫發行個人第6張音樂專輯《11月的蕭邦》

#### rag-v0-039

- 问题：从2008至2012年周杰伦有哪些变化
- 支持单位 2；Top40 命中 0，Top12 命中 0，final context 命中 0。Case 分数：0.0000 / 0.0000 / 0.0000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：局部事件不能证明2008至2012年完整变化或事业转型。
- `claim:1` 来源记载的2011年商业活动；Parent `parent-17f46282a4d28c6c000e6aff`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17f46282a4d28c6c000e6aff`：2011年，曾投資「DejaVu音樂魔術餐廳」。
- `claim:2` 来源记载的2012年商业活动；Parent `parent-17f46282a4d28c6c000e6aff`；Gold support_level=['full']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-17f46282a4d28c6c000e6aff`：2012年初，與西安真愛投資集團合作在西安開設「真愛范特西」KTV

#### rag-v0-045

- 问题：《晴天》的乐评资料，不含翻唱版
- 支持单位 1；Top40 命中 0，Top12 命中 0，final context 命中 0。Case 分数：0.0000 / 0.0000 / 0.0000。
- 参与的 evidence_groups：无。
- 不支持的结论（Gold 原文，均未进入分母）：缺少针对《晴天》音乐风格/审美/编曲的乐评文本。
- `span:1` [Gold 未标注 claim；见下列 span]；Parent `parent-e46f773645df47165d634134`；Gold support_level=['partial']；Top40/Top12/Final=False/False/False；计入原因：Gold supporting span has support_level full/partial; only this local evidence unit is scored.
  - 原文证据 `parent-e46f773645df47165d634134`：专辑歌曲《晴天》在2003年HitFM联播网年度百首單曲中名列亚军，《她的睫毛》名列第15名，《東風破》名列第41名。

## 风险与异常

- 自动校验没有发现无证据的正样本、无支持 span 的 group、跨 Parent 同 claim 却缺少 group 关系等结构异常；这些情况会直接报错，而非猜测。
- 031、005、008、045 等 case 含 claim 未标注的 span；对应评分单位是可定位证据，并非自动推断的完整 claim。
- 新口径与旧口径的数值不能直接解释为 Retrieval 改善：eligibility 从 13 条变为 21 条，且 Recall 由 Parent 单位变为证据单位。
- Gold V4 保持 candidate；本轮没有更改 Gold、旧 evaluator、历史 Trace 或检索链路。

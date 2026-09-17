# Gold V4 扩充候选审计

- 冻结 seed：30 条；SHA-256 `75dc8910eab396ac7815ec303a126c14dc44b85bf97924f416f1278eee4ee038`；新候选前 30 行逐字节相同。
- 冻结语料：`knowledge-v2-8608ffc2fa72e441`；Parent SHA-256 `sha256:8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。未使用网络或 Trace。
- 新增 20 条；总计 50 条；full 16、partial 2、insufficient 1、needs_input 1。
- 新增 supported claims 27；unsupported claims 3；evidence groups 3。
- 新增 case validator 通过；warning：['068: needs_input requires referent/scope clarification', 'Semantic alignment and source reliability remain human-review judgments.']。065–067 已完成冻结语料复核，结论只适用于该 release，不声称外部不存在证据。

## 逐条核对

### rag-v0-049：第十三届金曲奖中，《范特西》的《爱在西元前》《威廉古堡》《双截棍》分别对应哪些创作奖项和得奖者？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：《爱在西元前》的最佳作曲人奖得主是周杰伦; 《威廉古堡》的最佳作詞人獎得主是方文山; 《双截棍》的最佳编曲人奖得主是钟兴民。
- unsupported claims：无。
- 最小证据：parent-2e6c60113d8a12a45bb69d5e (jc-v1-011，第十三屆金曲獎得獎名單)：流行音樂作品類／最佳作曲人獎 得獎者：周杰倫 得獎作品：愛在西元前／〔周杰倫范特西〕; parent-2e6c60113d8a12a45bb69d5e (jc-v1-011，第十三屆金曲獎得獎名單)：流行音樂作品類／最佳作詞人獎 得獎者：方文山 得獎作品：威廉古堡／〔周杰倫范特西〕; parent-2e6c60113d8a12a45bb69d5e (jc-v1-011，第十三屆金曲獎得獎名單)：流行音樂作品類／最佳編曲人獎 得獎者：鍾興民 得獎作品：雙截棍／(周杰倫范特西)
- 事实/不确定性：仅报告官方获奖表中列出的奖项，不把入围误说成获奖。；forbidden claims：无。
- duplicate/equivalent/complementary：[('rag-v0-049-claim-1', 'duplicate', ['parent-2e6c60113d8a12a45bb69d5e', 'parent-8d6fb6a17ca9c6a0c7a56e15']), ('rag-v0-049-claim-2', 'duplicate', ['parent-2e6c60113d8a12a45bb69d5e', 'parent-8d6fb6a17ca9c6a0c7a56e15']), ('rag-v0-049-claim-3', 'duplicate', ['parent-2e6c60113d8a12a45bb69d5e', 'parent-8d6fb6a17ca9c6a0c7a56e15'])]；人工复核：否。

### rag-v0-050：杰威尔的20周年黑胶套装总计收录多少首曲目？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：20周年黑胶套装合计157首曲目。
- unsupported claims：无。
- 最小证据：parent-6b75664aa255959035d1af99 (jc-v1-002，周杰倫20年音樂軌跡：14張經典專輯變身黑膠唱片)：全套黑膠大碟總計收錄了周杰倫157首曲目。
- 事实/不确定性：数字仅限这份20周年黑胶套装，不外推为周杰伦全部作品数。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-051：周杰伦20周年黑胶复刻的母带制作和刻片分别由谁负责？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：Chris Gehringer负责黑胶母带音源制作; 德国Optimal Media GmbH负责DMM刻板。
- unsupported claims：无。
- 最小证据：parent-cca31e09c6d05f09267862a1 (jc-v1-002，周杰倫20年音樂軌跡：14張經典專輯變身黑膠唱片)：工程師Chris Gehringer負責黑膠母帶音源製作; parent-cca31e09c6d05f09267862a1 (jc-v1-002，周杰倫20年音樂軌跡：14張經典專輯變身黑膠唱片)：德國黑膠刻片大廠Optimal Media GmbH升級DMM(金屬直刻)刻板
- 事实/不确定性：仅陈述公告中的制作分工，不推断所有专辑原始录音地点。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-052：杰威尔公告称《Mojito》官方YouTube上线前七天达到什么播放量？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：杰威尔公告称《Mojito》官方YouTube上线7天突破1000万点阅。
- unsupported claims：无。
- 最小证据：parent-6b75664aa255959035d1af99 (jc-v1-002，周杰倫20年音樂軌跡：14張經典專輯變身黑膠唱片)：周杰倫本月推出最新單曲「Mojito」，官方YouTube上線7天即突破1000萬點閱次數！
- 事实/不确定性：点阅量是公告当时的说法，不作为实时播放量。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-053：杰威尔公布的周杰伦珍藏DEMO中，哪首此前未发表？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：《纽约地铁》是公告中此前未发表的DEMO。
- unsupported claims：无。
- 最小证据：parent-0276a6cd536dfaa02e3b39eb (jc-v1-003，周杰倫限定珍藏DEMO空間：创作过程回顾)：未發表過的「紐約地鐵」DEMO宛如新歌
- 事实/不确定性：不把‘未发表过的DEMO’升级为歌曲此后从未发行。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-054：索尼《魔杰座》介绍如何描述《稻香》的曲风与主题？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：索尼称《稻香》结合嘻哈民谣和周式饶舌; 索尼称《稻香》描绘都市压力与返乡向往。
- unsupported claims：无。
- 最小证据：parent-7a93e3f27e89700594417c6a (jc-v2-004，魔杰座)：周杰倫填詞作曲的「稻香｣，是一首嘻哈民謠搭配周式饒舌曲風; parent-7a93e3f27e89700594417c6a (jc-v2-004，魔杰座)：周杰倫填詞作曲的「稻香｣，是一首嘻哈民謠搭配周式饒舌曲風，描述現代都市人對社會、生活的壓力過重，開始嚮往鄉下樸實的生活，回到最初的美好；
- 事实/不确定性：曲风和主题均归于唱片公司文案，不称独立乐评共识。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-055：《魔杰座》中《花海》的歌词署名是哪两位？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：《花海》的歌词由古小力和黄凌嘉共同完成。
- unsupported claims：无。
- 最小证据：parent-85c23603441b162614c986e9 (jc-v2-004，魔杰座)：這首「花海」，歌詞由兩位杰威爾的同事合力完成，也是周杰倫給公司同事的福利，丟出一首歌讓全公司的同事比稿，由古小力和黃凌嘉兩位的歌詞脫穎而出
- 事实/不确定性：两人共同署名作为一个合作关系 claim，不拆成两个重复计分单位。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-056：索尼称《喬克叔叔》的创作灵感来自哪部电影？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：索尼称《喬克叔叔》的灵感来自电影《蝙蝠侠：黑暗骑士》。
- unsupported claims：无。
- 最小证据：parent-617c80bffcb611c7aba3f145 (jc-v2-004，魔杰座)：這首「喬克叔叔」是周杰倫看的電影「蝙蝠俠-黑暗騎士」之後得到的全新靈感
- 事实/不确定性：创作灵感来自唱片公司描述，不推断导演或MV制作细节。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-057：《魔術先生》的编曲是谁，索尼怎样描述其编曲风味？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：《魔術先生》的编曲由钟兴民完成; 索尼称《魔術先生》的编曲带有爵士风味。
- unsupported claims：无。
- 最小证据：parent-617c80bffcb611c7aba3f145 (jc-v2-004，魔杰座)：魔術先生在華麗的美式紐奧良曲風優雅出場，整首歌的編曲以充滿爵士風味樂團演出的方式呈現，如魔術般的炫爛奪目，也是名編曲家鍾興民老師的又一力作！; parent-617c80bffcb611c7aba3f145 (jc-v2-004，魔杰座)：整首歌的編曲以充滿爵士風味樂團演出的方式呈現
- 事实/不确定性：编曲者与编曲风味拆为两个 claim；表述为索尼文案。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-058：《流浪詩人》由谁作词，采用什么对唱形式？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：《流浪詩人》由方文山作词; 《流浪詩人》是周杰伦与杨瑞代的国台语对唱。
- unsupported claims：无。
- 最小证据：parent-e1cae0d40bb18a5ee6334ac6 (jc-v2-004，魔杰座)：流浪詩人 詞 方文山; parent-e1cae0d40bb18a5ee6334ac6 (jc-v2-004，魔杰座)：這首由周杰倫和楊瑞代國台語混音對唱的藍調小品
- 事实/不确定性：国台语表述取自专辑文案，不推断具体录音版本。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-059：《蘭亭序》的歌词由谁写，索尼称它取材于什么？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：《蘭亭序》由方文山作词; 索尼称《蘭亭序》取材于同名书法作品。
- unsupported claims：无。
- 最小证据：parent-e1cae0d40bb18a5ee6334ac6 (jc-v2-004，魔杰座)：蘭亭序 詞 方文山; parent-e1cae0d40bb18a5ee6334ac6 (jc-v2-004，魔杰座)：方文山繼「菊花台」、「青花瓷」之後，再以書法極品「蘭亭序」為主題寫中國風！
- 事实/不确定性：只标正文中明示的作词和题材，不推断书法史细节。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-060：索尼《魔杰座》文案如何描述《龍戰騎士》的曲风与故事时间设定？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：索尼称《龍戰騎士》采用电子摇滚风格; 索尼称《龍戰騎士》设定在百年后的世界。
- unsupported claims：无。
- 最小证据：parent-124172c91dc3903b46ad8310 (jc-v2-004，魔杰座)：周杰倫以前所未有的電子搖滾風格打造出充滿未來世界的奇幻與電影感的「龍戰騎士」; parent-124172c91dc3903b46ad8310 (jc-v2-004，魔杰座)：這首歌曲設定在100年之後的世界
- 事实/不确定性：‘一百年后’是歌曲叙事设定，不是实际预言。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-061：方文山在采访中如何解释用流行歌曲传承传统文化？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：方文山主张以中国元素结合流行形式面向年轻群体传承传统文化。
- unsupported claims：无。
- 最小证据：parent-7d0259a9845c71058b642c61 (jc-v1-020，让“中国风”成为人生坐标——专访流行词作家方文山)：面对传统文化的继承，方文山说：“一直以来我就是一个民族意识很重的人，长期关注跟民族、传统、文化相关联的议题。当我的这种意识跟音乐相结合起来时，就自然而然地孕育出所谓‘中国风’的歌词。”他认为自己的作品找到了非常好的契合点，通过中国元素的融入，善用流行的形式推向更年轻的群体，去传承中国的传统文化，这便是其核心价值。
- 事实/不确定性：观点归属方文山，不升级为社会效果已被独立证实。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-062：《山水合璧》的歌词融入了哪些《富春山居图》相关素材？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：《山水合璧》歌词融入《富春山居图》故事、画风与富春江景色。
- unsupported claims：无。
- 最小证据：parent-afca7c2d173dc002cca86aef (jc-v1-020，让“中国风”成为人生坐标——专访流行词作家方文山)：作为传世名画《富春山居图》合璧展览的主题曲，方文山把名画颠沛流离的故事、画家的画风、富春江的景色融入到歌词里，希望这首歌能延长展览的生命力。
- 事实/不确定性：只报道明确写入歌词的三类素材，不推断画作历史细节。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-063：环球音乐日本的艺人简介怎样描述2022年《最伟大的作品》的全球专辑销售排名？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：环球音乐日本称《最伟大的作品》在IFPI统计中获2022年全球专辑销量第一。
- unsupported claims：无。
- 最小证据：parent-1010d70ee8806e5d0e6a3c4f (jc-v2-001，Jay Chou / 周杰倫 biography)：2022年にリリースしたアルバム『最偉大的作品（Greatest Works of Art）』は、国際レコード産業連盟（IFPI）の集計で世界アルバムセールス1位を獲得
- 事实/不确定性：仅归述唱片公司简介对IFPI的说法，不独立核实IFPI原榜。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-064：马来亚大学的《中国风》研究摘要分析了哪两首周杰伦歌曲，使用了哪些研究方法？

- 结论：`fully_answerable` / `answerable`；问题对象明确：是。
- 原子 supported claims：研究选取《菊花台》和《千里之外》作为分析歌曲; 研究采用多种定性分析方法。
- unsupported claims：无。
- 最小证据：parent-b7a257305b7fdb3423361194 (jc-v2-011，Jay Chou's Zhongguo Feng: Songs and identity)：The selected songs Chrysanthemums Terrace (菊花台) and Faraway (千里之外) were analysed.; parent-b7a257305b7fdb3423361194 (jc-v2-011，Jay Chou's Zhongguo Feng: Songs and identity)：This study is based on a qualitative research theoretical framework and methods include literature review, transcription, score analysis, lyrics analysis, listening analysis, video analysis and virtual fieldwork.
- 事实/不确定性：仅陈述研究摘要的样本与方法，不推断论文正文结论。；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-065：周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？

- 结论：`partially_answerable` / `insufficient_evidence`；问题对象明确：是。
- 原子 supported claims：周杰伦自述很多快歌受到动作片启发。
- unsupported claims：每首快歌分别对应哪部具体动作电影。
- 最小证据：parent-659d449391ffb5694ba9fdf0 (jc-v1-017，大男孩要做NO.1——与周杰伦对话)：我的很多快歌都是看动作片写出来的。
- 事实/不确定性：frozen_review_completed: 逐首电影映射未获充分证据；详见 gold_v4_065_067_frozen_review.md。；forbidden claims：不得从‘看动作片写快歌’反推出《忍者》《双截棍》等各自对应的电影片名。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-066：方文山与周杰伦合作时通常是先有曲还是先有词？《娘子》的具体分歧发生在哪些词句？

- 结论：`partially_answerable` / `insufficient_evidence`；问题对象明确：是。
- 原子 supported claims：凤凰访谈称方周合作多数先曲后词。
- unsupported claims：《娘子》合作分歧对应的具体歌词句子。
- 最小证据：parent-fd7a3c5f16d4ad223cd1e529 (jc-v2-009，[台风]第24期：专访《听见下雨的声音》导演方文山_娱乐频道_凤凰网)：合作关系中，八成以上的歌是先有曲再有词，多是方文山配合周杰伦。
- 事实/不确定性：frozen_review_completed: 《娘子》具体分歧词句未获充分证据；详见 gold_v4_065_067_frozen_review.md。；forbidden claims：不得编造《娘子》具体争议词句。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-067：周杰伦2001年桃园巨蛋首次个人演唱会的完整曲目顺序是什么？

- 结论：`insufficient_evidence` / `insufficient_evidence`；问题对象明确：是。
- 原子 supported claims：无。
- unsupported claims：2001年桃园巨蛋首场个人演唱会的完整曲目顺序。
- 最小证据：无正向证据
- 事实/不确定性：frozen_review_completed: 桃园首演完整曲目顺序未获充分证据；详见 gold_v4_065_067_frozen_review.md。；forbidden claims：不得把《范特西》专辑曲目顺序当作该场演唱会的演出顺序。
- duplicate/equivalent/complementary：无；人工复核：否。

### rag-v0-068：上一张专辑里的那首歌是谁编曲的？

- 结论：`needs_input` / `needs_input`；问题对象明确：否，需补充上下文。
- 原子 supported claims：无。
- unsupported claims：无。
- 最小证据：无正向证据
- 事实/不确定性：missing_input: 缺少上一张专辑的参照对象及具体歌曲
why_not_inferable: 当前 query 没有会话上下文；冻结语料含多张专辑和多首歌曲，不能唯一确定指代
clarification_needed: 请给出专辑名和歌曲名，或提供你所说的上一张专辑的上下文；forbidden claims：无。
- duplicate/equivalent/complementary：无；人工复核：否。

## 自动校验边界

- Parent ID、source ID、原文子串、span 长度和 claim 一一对应由当前 Gold V4 validator 核验。
- 自动校验不能证明全库负事实或外部来源真实性；065–067 的独立检索与裁决详见 `gold_v4_065_067_frozen_review.md`。

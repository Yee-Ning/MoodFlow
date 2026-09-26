# Real corpus document audit

All 56 documents retain source URLs, original response SHA and native chunk locators. False context fields stay missing.

| Source ID | URL | Publisher | Title | SHA verified | Locator | Article | Question | Answer | Speaker | Bound packets | Claim gaps |
|---|---|---|---|---|---|---|---|---|---|---:|---:|
| jc-v1-001 | [source](https://www.jvrmusic.com.tw/artist/profile/1150822038412333056?lang=zh_TW) | 杰威爾音樂 | 周杰倫藝人介紹與作品年表 | true | DOM p[3]; decoded text [238:428] | true | false | false | false | 1 | 0 |
| jc-v1-002 | [source](https://www.jvrmusic.com.tw/artist/news/detail/1275679152900542464?lang=zh_TW) | 杰威爾音樂 | 周杰倫20年音樂軌跡：14張經典專輯變身黑膠唱片 | true | DOM p[5]; decoded text [363:2065] | true | false | false | false | 3 | 0 |
| jc-v1-003 | [source](https://jvrmusic.com.tw/news/detail/1564134148413722624) | 杰威爾音樂 | 周杰倫限定珍藏DEMO空間：创作过程回顾 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-004 | [source](https://sonymusic.com.tw/album/jay-chou-fantasy-vinyl-2lp/) | 台灣索尼音樂娛樂 | Jay Chou / FANTASY Vinyl (2LP) | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-005 | [source](https://sonymusic.com.tw/album/%E5%85%AB%E5%BA%A6%E7%A9%BA%E9%96%93-%E5%91%A8%E6%9D%B0%E5%80%AB-jay-chou-74321959972/) | 台灣索尼音樂娛樂 | 八度空間 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-006 | [source](https://www.sonymusic.com.tw/album/%E8%91%89%E6%83%A0%E7%BE%8E-%E5%91%A8%E6%9D%B0%E5%80%AB-jay-chou-afd0322/) | 台灣索尼音樂娛樂 | 葉惠美 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-007 | [source](https://sonymusic.com.tw/album/%E4%B8%83%E9%87%8C%E9%A6%99-%E5%91%A8%E6%9D%B0%E5%80%AB-jay-chou-afd0429/) | 台灣索尼音樂娛樂 | 七里香 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-008 | [source](https://sonymusic.com.tw/album/jay-chou-novembers-chopin-vinyl-2lp/) | 台灣索尼音樂娛樂 | Jay Chou / NOVEMBER’S CHOPIN Vinyl (2LP) | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-009 | [source](https://www.sonymusic.co.jp/artist/Jay/info/543947) | Sony Music Japan | 『最偉大的作品』発売記念スペシャル座談会 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-010 | [source](https://www.youtube.com/watch?v=qct0JLjaHDc) | 周杰倫 Jay Chou Official Artist Channel | 周杰倫《東風破》Official Music Video | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-011 | [source](https://www.bamid.gov.tw/News_Content.aspx?n=3539&s=124208) | 文化部影視及流行音樂產業局 | 第十三屆金曲獎得獎名單 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 4 |
| jc-v1-012 | [source](https://www.bamid.gov.tw/News_Content.aspx?n=3539&s=124207) | 文化部影視及流行音樂產業局 | 第十三屆金曲獎入圍名單 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-013 | [source](https://time.com/archive/6952553/cool-jay-2/) | TIME | Cool Jay: How Jay Chou became Asia's hottest star | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-016 | [source](https://ent.sina.com.cn/avworld/2002-11-06/1703111333.html) | 新浪娱乐 | 发布现场魔术做秀 周杰伦改行做大卫？ | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-017 | [source](https://news.sina.com.cn/c/2004-10-24/07494018202s.shtml) | 新浪新闻 | 大男孩要做NO.1——与周杰伦对话 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 1 |
| jc-v1-018 | [source](https://ent.sina.com.cn/y/o/2005-03-22/ba683104.shtml) | 新浪娱乐 | 周杰伦御用作词人方文山作客新浪聊新书 | true | DOM p[29] + p[30]; decoded text [1962:2133] | false | true | true | true | 1 | 0 |
| jc-v1-020 | [source](https://www.taiwan.cn/jl/wh/201112/t20111216_2215869.htm) | 中国台湾网 | 让“中国风”成为人生坐标——专访流行词作家方文山 | true | DOM p[4]; decoded text [486:640] | true | false | false | false | 3 | 0 |
| jc-v1-021 | [source](https://news.uestc.edu.cn/info/1007/16045.htm) | 四川日报 / 电子科技大学新闻中心 | 方文山：“梦想是可以转弯的” | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-023 | [source](https://www.scmp.com/lifestyle/entertainment/article/3220140/jay-chou-king-mandopop-talks-about-music-his-love-fine-art-his-world-tour-and-why-hes-not-worried) | South China Morning Post | Jay Chou on music, fine art, touring and AI | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-025 | [source](https://webspace.pugetsound.edu/facultypages/perry/co-curri/Style_and_Stardom_The_Iconicity_of_Jay_C.pdf) | University of Puget Sound | Style and Stardom: The Iconicity of Jay Chou | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-029 | [source](https://cjournal.hep.com.cn/1673-1646/CN/PDF/1200838552003728279) | 高等教育出版社期刊平台 | 论当代“中国风”歌曲创作的艺术探索 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 1 |
| jc-v1-030 | [source](https://zh.wikipedia.org/wiki/%E5%91%A8%E6%9D%B0%E5%80%AB) | Wikipedia | 周杰倫 | true | DOM p[2]; decoded text [4194:4404] | true | false | false | false | 12 | 8 |
| jc-v1-031 | [source](https://zh.wikipedia.org/wiki/%E6%96%B9%E6%96%87%E5%B1%B1) | Wikipedia | 方文山 | true | DOM p[1]; decoded text [1896:1991] | true | false | false | false | 3 | 0 |
| jc-v1-032 | [source](https://zh.wikipedia.org/wiki/%E8%8C%83%E7%89%B9%E8%A5%BF) | Wikipedia | 范特西 | true | DOM p[2]; decoded text [1318:1567] | true | false | false | false | 2 | 2 |
| jc-v1-033 | [source](https://zh.wikipedia.org/wiki/%E5%85%AB%E5%BA%A6%E7%A9%BA%E9%96%93_%28%E5%B0%88%E8%BC%AF%29) | Wikipedia | 八度空間（專輯） | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-034 | [source](https://zh.wikipedia.org/wiki/%E8%91%89%E6%83%A0%E7%BE%8E) | Wikipedia | 葉惠美 | true | DOM p[2]; decoded text [1715:1905] | true | false | false | false | 1 | 0 |
| jc-v1-035 | [source](https://zh.wikipedia.org/wiki/%E4%B8%83%E9%87%8C%E9%A6%99_%28%E5%B0%88%E8%BC%AF%29) | Wikipedia | 七里香（專輯） | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-036 | [source](https://zh.wikipedia.org/wiki/11%E6%9C%88%E7%9A%84%E8%95%AD%E9%82%A6) | Wikipedia | 11月的蕭邦 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 6 |
| jc-v1-037 | [source](https://zh.wikipedia.org/wiki/%E6%9D%B1%E9%A2%A8%E7%A0%B4) | Wikipedia | 東風破 | true | DOM p[1]; decoded text [751:871] | true | false | false | false | 2 | 0 |
| jc-v1-038 | [source](https://www.wikidata.org/wiki/Special:EntityData/Q238819.json) | Wikidata | Wikidata entity Q238819：周杰伦 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-039 | [source](https://www.wikidata.org/wiki/Special:EntityData/Q699613.json) | Wikidata | Wikidata entity Q699613：方文山 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-040 | [source](https://www.wikidata.org/wiki/Special:EntityData/Q595532.json) | Wikidata | Wikidata entity Q595532：范特西 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-041 | [source](https://www.wikidata.org/wiki/Special:EntityData/Q680715.json) | Wikidata | Wikidata entity Q680715：八度空间 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-042 | [source](https://www.wikidata.org/wiki/Special:EntityData/Q699622.json) | Wikidata | Wikidata entity Q699622：叶惠美 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-043 | [source](https://www.wikidata.org/wiki/Special:EntityData/Q262153.json) | Wikidata | Wikidata entity Q262153：七里香 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v1-044 | [source](https://www.wikidata.org/wiki/Special:EntityData/Q715325.json) | Wikidata | Wikidata entity Q715325：十一月的萧邦 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-001 | [source](https://www.universal-music.co.jp/jay-chou/biography/) | Universal Music Japan | Jay Chou / 周杰倫 biography | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-002 | [source](https://www.universal-music.co.jp/jay-chou/) | Universal Music Japan | Jay Chou / 周杰倫 official artist page | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-003 | [source](https://sonymusic.com.tw/album/jay-chou-common-jasmine-orange-vinyl-2lp/) | 台灣索尼音樂娛樂 | 周杰倫／七里香經典復刻黑膠 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-004 | [source](https://sonymusic.com.tw/album/%E9%AD%94%E6%9D%B0%E5%BA%A7-%E5%91%A8%E6%9D%B0%E5%80%AB-jay-chou-88697403232/) | 台灣索尼音樂娛樂 | 魔杰座 | true | DOM p[1]; decoded text [184:4011] | true | false | false | false | 3 | 0 |
| jc-v2-006 | [source](https://m.thepaper.cn/baijiahao_14405827) | 澎湃新闻 | 周杰伦：不退流行的未来主义收藏家 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-007 | [source](https://www.thepaper.cn/newsDetail_forward_10549480) | 澎湃新闻 | 41岁周杰伦：时代变了，但我的时代不会过去 | true | DOM p[49]; decoded text [1856:1899] | true | false | false | false | 4 | 0 |
| jc-v2-008 | [source](https://m.thepaper.cn/newsDetail_forward_3717102) | 澎湃新闻 | 请回答2000：天才周杰伦 | true | DOM p[5]; decoded text [652:931] | true | false | false | false | 1 | 0 |
| jc-v2-009 | [source](https://ent.ifeng.com/movie/special/xiayudeshengyin/) | 凤凰新媒体 | [台风]第24期：专访《听见下雨的声音》导演方文山_娱乐频道_凤凰网 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 1 |
| jc-v2-011 | [source](https://knova.um.edu.my/student_works_2010s/5342/) | University of Malaya | Jay Chou's Zhongguo Feng: Songs and identity | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-012 | [source](https://scholarship.claremont.edu/scripps_theses/1694/) | Claremont Colleges Library | Jay Chou’s Kuso Music: Cultural Fusion in the Age of the Internet | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-016 | [source](https://zh.wikipedia.org/wiki/%E5%8F%8C%E6%88%AA%E6%A3%8D) | 维基媒体基金会 | 雙節棍 - 维基百科，自由的百科全书 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-020 | [source](https://zh.wikipedia.org/wiki/%E5%8D%8A%E5%B2%9B%E9%93%81%E7%9B%92) | 维基媒体基金会 | 八度空間 (專輯) - 维基百科，自由的百科全书 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-022 | [source](https://zh.wikipedia.org/wiki/%E6%93%B1%E6%B7%BA) | 维基媒体基金会 | 擱淺 - 维基百科，自由的百科全书 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-025 | [source](https://zh.wikipedia.org/wiki/%E5%8F%91%E5%A6%82%E9%9B%AA) | 维基媒体基金会 | 11月的蕭邦 - 维基百科，自由的百科全书 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 6 |
| jc-v2-026 | [source](https://zh.wikipedia.org/wiki/%E9%9D%92%E8%8A%B1%E7%93%B7_(%E6%AD%8C%E6%9B%B2)) | 维基媒体基金会 | 青花瓷 (歌曲) - 维基百科，自由的百科全书 | true | DOM p[1]; decoded text [915:989] | true | false | false | false | 3 | 0 |
| jc-v2-029 | [source](https://zh.wikipedia.org/wiki/%E4%BB%A5%E7%88%B6%E4%B9%8B%E5%90%8D) | 维基媒体基金会 | 以父之名 - 维基百科，自由的百科全书 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-033 | [source](https://zh.wikipedia.org/wiki/%E4%BE%9D%E7%84%B6%E8%8C%83%E7%89%B9%E8%A5%BF) | 维基媒体基金会 | 依然范特西 - 维基百科，自由的百科全书 | true | DOM p[2]; decoded text [1221:1337] | true | false | false | false | 1 | 0 |
| jc-v2-045 | [source](https://core.ac.uk/download/268879767.pdf) | UNIVERSITY OF MALAYA | JAY CHOU'S ZHONGGUO FENG: SONGS AND IDENTITY | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 0 |
| jc-v2-046 | [source](https://www.wsp-publishing.com/rc-pub/front/front-article/download/29055690/lowqualitypdf/%E4%B8%AD%E5%9B%BD%E9%A3%8E%E6%AD%8C%E6%9B%B2%E5%88%86%E6%9E%90%E2%80%94%E2%80%94%E4%BB%A5%E5%91%A8%E6%9D%B0%E4%BC%A6%E7%9A%84%E3%80%8A%E9%9C%8D%E5%85%83%E7%94%B2%E3%80%8B%E4%B8%BA%E4%BE%8B.pdf) | Porcelain Publishing International Limited | 中国风歌曲分析——以周杰伦的《霍元甲》为例 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 8 |
| jc-v2-048 | [source](https://epaper.voc.com.cn/jtdb/page/60/2010-11/30/16/2010113016_pdf.pdf) | 湖南日报报业集团 | 周杰伦：为妈妈和外婆而歌唱 | true | existing chunk section_path/breadcrumb; no context locator fabricated | false | false | false | false | 0 | 2 |

## Missing claim contexts

| Source ID | Claim ID | Reason |
|---|---|---|
| jc-v1-011 | claim:be1440026b77d47e7128acee | no_unique_verbatim_HTML_paragraph |
| jc-v1-011 | claim:6ebc142e3b7898c2248f16ca | no_unique_verbatim_HTML_paragraph |
| jc-v1-011 | claim:b538ac19da2623a0064d6eb8 | no_unique_verbatim_HTML_paragraph |
| jc-v1-011 | claim:da6aa7f5c67edb59d0f3726f | no_unique_verbatim_HTML_paragraph |
| jc-v1-017 | claim:86a3e34bf1be60a8764cc199 | no_unique_verbatim_HTML_paragraph |
| jc-v1-029 | claim:39cbe3738386b4de1ed3aeff | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v1-030 | claim:e5cec61accdad8b2e24b05dc | document_capture_limit |
| jc-v1-030 | claim:0798ae7202365f747c5bd7cc | document_capture_limit |
| jc-v1-030 | claim:db5916f3c539e95c81c3383d | document_capture_limit |
| jc-v1-030 | claim:182947bc5f7efe11709d66f0 | document_capture_limit |
| jc-v1-030 | claim:cbdfdab50dc07d6184cb2bcf | no_unique_verbatim_HTML_paragraph |
| jc-v1-030 | claim:d6e9e8191798f8603d24579c | no_unique_verbatim_HTML_paragraph |
| jc-v1-030 | claim:28d627a3a686ad7dd4d55342 | no_unique_verbatim_HTML_paragraph |
| jc-v1-030 | claim:3cbc48940c4935a43f9980fc | no_unique_verbatim_HTML_paragraph |
| jc-v1-032 | claim:aa1b6184404d35621ec84db5 | no_unique_verbatim_HTML_paragraph |
| jc-v1-032 | claim:bb03be374d6ce7ce23b1ed77 | no_unique_verbatim_HTML_paragraph |
| jc-v1-036 | claim:14442c5fd6e511d1ec3afa1a | no_unique_verbatim_HTML_paragraph |
| jc-v1-036 | claim:383bcd24b5339e5f24cb2e25 | no_unique_verbatim_HTML_paragraph |
| jc-v1-036 | claim:5d8212125ba941fd6840d47c | no_unique_verbatim_HTML_paragraph |
| jc-v1-036 | claim:8347e00738a520e80cc71ce3 | no_unique_verbatim_HTML_paragraph |
| jc-v1-036 | claim:945a806887204379ee8094f7 | no_unique_verbatim_HTML_paragraph |
| jc-v1-036 | claim:df88d18f644805bea56e73ea | no_unique_verbatim_HTML_paragraph |
| jc-v2-009 | claim:dbb48445e58f6b7cc3820d58 | no_unique_verbatim_HTML_paragraph |
| jc-v2-025 | claim:03cf20e2dc5112ed1bc656d1 | no_unique_verbatim_HTML_paragraph |
| jc-v2-025 | claim:1bc971ad2d27bbccc8bae156 | no_unique_verbatim_HTML_paragraph |
| jc-v2-025 | claim:3e62f33224a62854832d9bbd | no_unique_verbatim_HTML_paragraph |
| jc-v2-025 | claim:420e6bfa93b35a3cb2fef813 | no_unique_verbatim_HTML_paragraph |
| jc-v2-025 | claim:6f8ca6df6428af84d01f44eb | no_unique_verbatim_HTML_paragraph |
| jc-v2-025 | claim:abfb21db0c080566c1d324e4 | no_unique_verbatim_HTML_paragraph |
| jc-v2-046 | claim:e395f612ac8d9b15697a809b | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v2-046 | claim:4ae482d982544957daf150f3 | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v2-046 | claim:4efcbcd7d2f0093c6e6c7f17 | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v2-046 | claim:54ee708f79f38305218552cf | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v2-046 | claim:98578007c8e9548345eff980 | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v2-046 | claim:56b4c9de6beeb29cbce4868d | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v2-046 | claim:eea01f937c26fe6c7259eaa1 | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v2-046 | claim:e22f4cd56a8125edd5d1d213 | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v2-048 | claim:9b6a36ea033c15bdbf0a8782 | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |
| jc-v2-048 | claim:217c9e0609104466d789e270 | no_verbatim_HTML_paragraph; preserve_native_formatted_claim_without_context |

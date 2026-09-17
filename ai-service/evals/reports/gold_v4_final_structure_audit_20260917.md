# Gold V4 最终结构审计

冻结 Parent SHA-256：`sha256:8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。Gold V3 SHA-256：`5d2888542042af16bf38d3ae63f45c956c8e37f658397e2f5d3a78c3759236e9`；Gold V4 修改前 Git blob SHA-256 `8258a00016be2f0b57258c496634ec70c9089bd0d159378f986d82058dcf4c88`（CRLF 工作树 `5a6e972ee7ffe4a94b2b35974d59b406613c696026264af0e35e1056863f585f`），修改后工作树 SHA-256 `b64bcbabc6789589cbf51895de549025f4c975cd06995ecbab4c5411dde1ca41`。
只读取原有 historical Trace 并调用未修改的 V4-aware evaluator。

## 046 / 047 / 048：维持 needs_input

### rag-v0-046

- 原始 query：七里香的创作背景。原始及 V4 `current_song_id=null`；V4 reference answer 为空。两份历史 Trace 均为 needs_input，没有独立查询、候选 Parent 或 final context。
- 原始 V0 裁决：`insufficient_evidence`；V0 reference answer：冻结语料能说明《七里香》的部分企划背景：第五张专辑最终以首波主打《七里香》命名，这是周杰伦前五张专辑中首次以主打歌作为专辑名；MV还赴日本乡间取景以营造浪漫诗意的视觉氛围。但这些材料没有直接说明歌曲本身为什么创作、词曲灵感来自哪里或创作过程，因此严格回答“《七里香》的创作背景”时证据不足。。已批准的 V1 outcome policy 改为 `needs_input`；本轮不撤销该输入澄清要求。
- 冻结语料中可见的对象线索 Parent：['parent-d3a5bad1fa461976a1129b06', 'parent-790e32e522e2ba2f480a77ea']。这些线索不能恢复用户指代。
- `missing_input`：明确《七里香》是歌曲还是同名专辑，并说明创作背景是歌曲缘起还是专辑企划背景。
- `why_not_inferable`：原始 query 仅有裸标题，current_song_id 为空；冻结语料同时含歌曲曲目和同名专辑命名资料，历史 Trace 无补充上下文。
- `clarification_needed`：请确认歌曲或专辑，以及想问的具体创作背景范围。
- 裁决：维持 needs_input；冻结语料不能替用户指定提问对象。

### rag-v0-047

- 原始 query：霍元甲的创作背景。原始及 V4 `current_song_id=null`；V4 reference answer 为空。两份历史 Trace 均为 needs_input，没有独立查询、候选 Parent 或 final context。
- 原始 V0 裁决：`insufficient_evidence`；V0 reference answer：现有资料可以分析《霍元甲》的作品内容与风格：它使用古筝、二胡、锣鼓等传统乐器，演唱中模仿京剧唱腔，并加入武术/练拳意象；题名还关联历史人物霍元甲及其爱国文化联想。但这些证据没有直接说明歌曲的创作契机、具体灵感来源或创作过程，因此严格按“创作背景”问题应判为证据不足。。已批准的 V1 outcome policy 改为 `needs_input`；本轮不撤销该输入澄清要求。
- 冻结语料中可见的对象线索 Parent：['parent-6a331e40f29ee8eefba08d2e', 'parent-38d4a3cac6f40af923af4881']。这些线索不能恢复用户指代。
- `missing_input`：明确《霍元甲》是歌曲还是同名迷你专辑，并说明创作背景是歌曲缘起还是作品主题及曲风。
- `why_not_inferable`：原始 query 仅有裸标题，current_song_id 为空；冻结语料含歌曲分析及同名迷你专辑条目，历史 Trace 无补充上下文。
- `clarification_needed`：请确认作品对象及所需的创作背景范围。
- 裁决：维持 needs_input；冻结语料不能替用户指定提问对象。

### rag-v0-048

- 原始 query：这首歌的创作背景。原始及 V4 `current_song_id=null`；V4 reference answer 为空。两份历史 Trace 均为 needs_input，没有独立查询、候选 Parent 或 final context。
- 原始 V0 裁决：`needs_input`；V0 reference answer：无。已批准的 V1 outcome policy 改为 `needs_input`；本轮不撤销该输入澄清要求。
- 冻结语料中可见的对象线索 Parent：无法绑定任何特定歌曲。这些线索不能恢复用户指代。
- `missing_input`：提供“这首歌”所指的歌名、歌曲 ID 或当前播放歌曲上下文。
- `why_not_inferable`：原始 query 只有指示代词，current_song_id 为空；历史 Trace 没有可恢复的前文或歌曲实体。
- `clarification_needed`：请提供歌名或当前播放歌曲信息。
- 裁决：维持 needs_input；冻结语料不能替用户指定提问对象。

## 026：四个独立曲目事实

`parent-cfb9f049a0cfdf86866257fd` 与 `parent-d0c34fdc20c0a8d545deea06` 的曲目表内容逐字相同；`parent-a734651048b7159210d5c4a6` 与 `parent-287a536a7936d114db25a509` 的奖项表内容也逐字相同。重复的是来源文本，四首歌曲的编曲署名则分别是四个独立事实。没有 equivalent 或 complementary 关系。

| 歌曲 | 冻结曲目表原文 |
| --- | --- |
| 《蓝色风暴》的编曲署名为洪敬尧 | `\| 2 \| 藍色風暴 \| 作詞:方文山 作曲:周杰倫 編曲:洪敬堯 \|` |
| 《四面楚歌》的编曲署名为洪敬尧 | `\| 5 \| 四面楚歌 \| 作詞:周杰倫 作曲:周杰倫 編曲:洪敬堯 \|` |
| 《浪漫手机》的编曲署名为洪敬尧 | `\| 7 \| 浪漫手機 \| 作詞:方文山 作曲:周杰倫 編曲:洪敬堯 \|` |
| 《麦芽糖》的编曲署名为洪敬尧 | `\| 9 \| 麥芽糖 \| 作詞:方文山 作曲:周杰倫 編曲:洪敬堯 \|` |

已移除把四首歌曲合成一项的 `026-tracklist` group；四个 span 各作为一个 atomic claim。奖项提名仍是一个 claim，保留 `026-award` duplicate group。026 从 **2 个 claim/group 单位、2 个 group** 改为 **5 个 claim/group 单位、1 个 group**。reference answer 仅列这些可核实例子，未扩展合作目录。

**评分表示限制：**现有 evaluator 拒绝同一对 Parent 出现在四个重叠 group 中。本轮按要求保持 evaluator 不变，因此四个曲目 claim 仅以原 supporting Parent 精确匹配；另一份逐字重复 Parent 已在 Gold notes 与本报告记录，但当前评分不会将它视为这四个 claim 的替代命中。奖项 claim 的 duplicate matching 保留。该限制不影响本次 Trace 中四个曲目 claim 的命中，因为原 supporting Parent 已出现。

## 总量与 SAME TRACE 指标

full=13，partial=8，insufficient=6，needs_input=3。supported claims **68**（修改前 65）；evidence groups **1**（修改前 2）；无 claim 正向 span **0**。

### fallback

Trace `evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl`；SHA-256 `7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e`。单位 65 → 68。

| Metric | 修改前 | 修改后 | 差值 |
| --- | ---: | ---: | ---: |
| Evidence Recall@40 | 0.6413832200 | 0.6413832200 | +0.0000000000 |
| Evidence Rerank Recall@12 | 0.4283446712 | 0.4426303855 | +0.0142857143 |
| Final Context Precision | 0.1190476190 | 0.1190476190 | +0.0000000000 |

唯一变化为 026：证据单位 2 → 5；Top12 命中比例 0.5000 → 0.8000。Top12 有曲目表 supporting Parent，却无奖项 Parent：旧口径 1/2，新口径 4/5。宏平均差值为 (4/5 − 1/2) / 21；Top40 和 Final Context Precision 未变化。

### nav_context6

Trace `evals/artifacts/gold_v2_trace_v3_nav_context6_20260916.jsonl`；SHA-256 `9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967`。单位 65 → 68。

| Metric | 修改前 | 修改后 | 差值 |
| --- | ---: | ---: | ---: |
| Evidence Recall@40 | 0.6413832200 | 0.6413832200 | +0.0000000000 |
| Evidence Rerank Recall@12 | 0.4283446712 | 0.4426303855 | +0.0142857143 |
| Final Context Precision | 0.1396825397 | 0.1396825397 | +0.0000000000 |

唯一变化为 026：证据单位 2 → 5；Top12 命中比例 0.5000 → 0.8000。Top12 有曲目表 supporting Parent，却无奖项 Parent：旧口径 1/2，新口径 4/5。宏平均差值为 (4/5 − 1/2) / 21；Top40 和 Final Context Precision 未变化。

## 校验

Gold validator 通过；warning：['046: needs_input requires referent/scope clarification', '047: needs_input requires referent/scope clarification', '048: needs_input requires referent/scope clarification', 'Semantic alignment and source reliability remain human-review judgments.']。没有修改 Gold V3、历史 Trace、Retrieval、Rerank 或 evaluator 评分规则。

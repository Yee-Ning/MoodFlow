# Gold V4 Query Planner Rewrite Ablation

## 受控边界与口径

Gold V4、frozen corpus/index/projection、embedding revision、Dense/BM25、SAFE_COMBINED_V2_CAP_5、weighted RRF k=60、document/source cap 与 selector 不变。历史 Planner legs 从同一归档读取；每次只加一个 rewrite 配置，互不组合最优结果。使用每 leg 600 Child、融合后 Top100 Parent 的既有深度诊断口径；没有修改 production Planner、retrieval config、reranker 或 Context Builder。

触发规则在实验前固定：A 在原问题含“作词/填词”且有解析实体时运行；B 在 historical timeline case 且有解析实体时运行；C 在有解析实体的 case 运行。未触发的 case 保持 baseline 并纳入同一 39-case macro 分母。A 的三种字段词、B 的四个意图分别测试；B_ALL_FOUR 仅组合 B 的四个 lexical leg，单独标为超出当前 production maximum_legs=6 的诊断，不可直接上线。C 是一条实体属性 Dense+BM25 双通道 variant。

Rewrite 文本只使用原问题、query type、历史已解析实体及预设通用属性词；没有把 Gold 答案、证据标题、年份或文本写入 query。024 的具体曲目标题与作词人不在 probe 中。A/B/C 均沿用既有 decomposed variant 权重 0.8；这是诊断构造，并不代表生产 Planner 会产生相同结果。

- Gold SHA-256 `1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；corpus `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`；projection `663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`；manifest `0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。
- Retrieval config `b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`；Planner config `afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77`；historical archive `dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`；Filter V2 report `69ab5feea651e9cd693e797d21c7c4dacfd99d371fb6838cad999c466ba74c06`。

## 全局主表

Macro 为 39 eligible case 等权；micro 为命中 evidence unit / 95。Churn、non-Gold 是相对 baseline 的 case–Parent 槽位。Gold displaced 是被替换的 Gold Parent ID 槽位，不等同于丢失 evidence unit 数。

| config | applied cases | Hybrid macro@100 | micro | recovered | lost | net | admitted | displaced | non-Gold admitted | Gold displaced | regression cases |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| BASELINE | 39 | 89.29% | 85/95 | 0 | 0 | +0 | 0 | 0 | 0 | 0 | none |
| A_SCHEMA_LYRIC_AUTHOR | 1 | 89.29% | 85/95 | 0 | 0 | +0 | 10 | 10 | 10 | 0 | none |
| A_SCHEMA_TRACKLIST | 1 | 91.21% | 88/95 | 3 | 0 | +3 | 14 | 14 | 13 | 0 | none |
| A_SCHEMA_EN_CREDITS | 1 | 89.29% | 85/95 | 0 | 0 | +0 | 11 | 11 | 11 | 0 | none |
| B_INTENT_ALBUM | 3 | 89.29% | 85/95 | 0 | 0 | +0 | 24 | 24 | 24 | 0 | none |
| B_INTENT_FILM | 3 | 88.41% | 83/95 | 0 | 2 | -2 | 23 | 23 | 23 | 2 | rag-v0-036, rag-v0-040 |
| B_INTENT_EVENTS | 3 | 90.82% | 85/95 | 2 | 2 | +0 | 24 | 24 | 23 | 2 | rag-v0-036 |
| B_INTENT_AWARDS | 3 | 88.26% | 83/95 | 0 | 2 | -2 | 30 | 30 | 30 | 2 | rag-v0-036 |
| B_ALL_FOUR_DIAGNOSTIC | 3 | 88.26% | 83/95 | 0 | 2 | -2 | 35 | 35 | 35 | 2 | rag-v0-036 |
| C_ENTITY_ATTRIBUTES | 32 | 87.04% | 81/95 | 0 | 4 | -4 | 248 | 248 | 248 | 4 | rag-v0-002, rag-v0-004, rag-v0-036 |

新增 leg 的 raw 检索命中按 evidence unit 计；Hybrid preselection 表示 Gold Parent 进入融合候选，尚未通过 selector。各配置触发 case 数不同，因此此表不能直接当作全局 Dense/BM25 Recall 比较。

| config | added-leg raw Dense hits | added-leg raw BM25 hits | fused preselection Gold hits | final selected Gold hits (all cases) |
|---|---:|---:|---:|---:|
| A_SCHEMA_LYRIC_AUTHOR | 4 | 4 | 4 | 85/95 |
| A_SCHEMA_TRACKLIST | 4 | 4 | 4 | 88/95 |
| A_SCHEMA_EN_CREDITS | 1 | 4 | 4 | 85/95 |
| B_INTENT_ALBUM | 12 | 11 | 14 | 85/95 |
| B_INTENT_FILM | 9 | 10 | 14 | 83/95 |
| B_INTENT_EVENTS | 9 | 11 | 14 | 85/95 |
| B_INTENT_AWARDS | 9 | 10 | 14 | 83/95 |
| B_ALL_FOUR_DIAGNOSTIC | 0 | 14 | 14 | 83/95 |
| C_ENTITY_ATTRIBUTES | 60 | 76 | 83 | 81/95 |

## 024 / 040 / 其余 MISS 的逐单位轨迹

完整 39 case、所有新增 leg 的无过滤 raw Parent rank、Filter V2 后 rank、融合中 Gold Parent 的最早 Child rank、Parent preselection rank 与最终 selected rank 在 JSONL。下表优先列 baseline MISS；baseline HIT 的回归另在后文列出。单个 query leg 的 raw hit 以该 leg 内是否包含完整 Gold evidence unit 计算。

### rag-v0-024

Question：谁为周杰伦的歌曲作词

| rewrite | query text | unit | new Dense/BM25 raw Gold rank | fused Child/Parent pre rank | selected rank | outcome |
|---|---|---|---|---|---:|---|
| A_SCHEMA_LYRIC_AUTHOR | 周杰伦 歌曲 作词人 | claim:2 | D q3:decomposed:dense:390<br>B q3:decomposed:lexical:127 | 387 / 239 | — | MISS |
| A_SCHEMA_LYRIC_AUTHOR | 周杰伦 歌曲 作词人 | claim:3 | D q3:decomposed:dense:390<br>B q3:decomposed:lexical:127 | 387 / 239 | — | MISS |
| A_SCHEMA_LYRIC_AUTHOR | 周杰伦 歌曲 作词人 | claim:4 | D q3:decomposed:dense:390<br>B q3:decomposed:lexical:127 | 387 / 239 | — | MISS |
| A_SCHEMA_TRACKLIST | 周杰伦 曲目表 词作者 | claim:2 | D q3:decomposed:dense:294<br>B q3:decomposed:lexical:19 | 184 / 122 | 47 | HIT recovered |
| A_SCHEMA_TRACKLIST | 周杰伦 曲目表 词作者 | claim:3 | D q3:decomposed:dense:294<br>B q3:decomposed:lexical:19 | 184 / 122 | 47 | HIT recovered |
| A_SCHEMA_TRACKLIST | 周杰伦 曲目表 词作者 | claim:4 | D q3:decomposed:dense:294<br>B q3:decomposed:lexical:19 | 184 / 122 | 47 | HIT recovered |
| A_SCHEMA_EN_CREDITS | 周杰伦 song credit songwriter | claim:2 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:128 | 460 / 275 | — | MISS |
| A_SCHEMA_EN_CREDITS | 周杰伦 song credit songwriter | claim:3 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:128 | 460 / 275 | — | MISS |
| A_SCHEMA_EN_CREDITS | 周杰伦 song credit songwriter | claim:4 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:128 | 460 / 275 | — | MISS |
| C_ENTITY_ATTRIBUTES | 周杰伦 专辑 电影 演唱会 奖项 创作 发行 | claim:2 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:222 | 588 / 324 | — | MISS |
| C_ENTITY_ATTRIBUTES | 周杰伦 专辑 电影 演唱会 奖项 创作 发行 | claim:3 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:222 | 588 / 324 | — | MISS |
| C_ENTITY_ATTRIBUTES | 周杰伦 专辑 电影 演唱会 奖项 创作 发行 | claim:4 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:222 | 588 / 324 | — | MISS |

### rag-v0-040

Question：周杰伦的音乐生涯时间线

| rewrite | query text | unit | new Dense/BM25 raw Gold rank | fused Child/Parent pre rank | selected rank | outcome |
|---|---|---|---|---|---:|---|
| B_INTENT_ALBUM | 周杰伦 专辑 发行 年份 | claim:2 | D q3:decomposed:dense:403<br>B q3:decomposed:lexical:235 | 550 / 232 | — | MISS |
| B_INTENT_ALBUM | 周杰伦 专辑 发行 年份 | claim:5 | D q3:decomposed:dense:373<br>B q3:decomposed:lexical:MISS | 471 / 201 | — | MISS |
| B_INTENT_ALBUM | 周杰伦 专辑 发行 年份 | claim:7 | D q3:decomposed:dense:500<br>B q3:decomposed:lexical:276 | 433 / 187 | — | MISS |
| B_INTENT_FILM | 周杰伦 电影 作品 时间 | claim:2 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:382 | 657 / 280 | — | MISS |
| B_INTENT_FILM | 周杰伦 电影 作品 时间 | claim:5 | D q3:decomposed:dense:17<br>B q3:decomposed:lexical:75 | 84 / 53 | — | MISS |
| B_INTENT_FILM | 周杰伦 电影 作品 时间 | claim:7 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:MISS | 587 / 241 | — | MISS |
| B_INTENT_EVENTS | 周杰伦 音乐事业 事件 | claim:2 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:MISS | 743 / 330 | — | MISS |
| B_INTENT_EVENTS | 周杰伦 音乐事业 事件 | claim:5 | D q3:decomposed:dense:379<br>B q3:decomposed:lexical:MISS | 418 / 171 | — | MISS |
| B_INTENT_EVENTS | 周杰伦 音乐事业 事件 | claim:7 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:355 | 501 / 196 | — | MISS |
| B_INTENT_AWARDS | 周杰伦 奖项 活动 | claim:2 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:475 | 680 / 297 | — | MISS |
| B_INTENT_AWARDS | 周杰伦 奖项 活动 | claim:5 | D q3:decomposed:dense:289<br>B q3:decomposed:lexical:MISS | 468 / 192 | — | MISS |
| B_INTENT_AWARDS | 周杰伦 奖项 活动 | claim:7 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:296 | 517 / 213 | — | MISS |
| B_ALL_FOUR_DIAGNOSTIC | 周杰伦 专辑 发行 年份<br>周杰伦 电影 作品 时间<br>周杰伦 音乐事业 事件<br>周杰伦 奖项 活动 | claim:2 | D —<br>B q3:decomposed:lexical:235; q4:decomposed:lexical:382; q5:decomposed:lexical:MISS; q6:decomposed:lexical:475 | 627 / 319 | — | MISS |
| B_ALL_FOUR_DIAGNOSTIC | 周杰伦 专辑 发行 年份<br>周杰伦 电影 作品 时间<br>周杰伦 音乐事业 事件<br>周杰伦 奖项 活动 | claim:5 | D —<br>B q3:decomposed:lexical:MISS; q4:decomposed:lexical:75; q5:decomposed:lexical:MISS; q6:decomposed:lexical:MISS | 420 / 219 | — | MISS |
| B_ALL_FOUR_DIAGNOSTIC | 周杰伦 专辑 发行 年份<br>周杰伦 电影 作品 时间<br>周杰伦 音乐事业 事件<br>周杰伦 奖项 活动 | claim:7 | D —<br>B q3:decomposed:lexical:276; q4:decomposed:lexical:MISS; q5:decomposed:lexical:355; q6:decomposed:lexical:296 | 447 / 225 | — | MISS |
| C_ENTITY_ATTRIBUTES | 周杰伦 专辑 电影 演唱会 奖项 创作 发行 | claim:2 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:313 | 666 / 285 | — | MISS |
| C_ENTITY_ATTRIBUTES | 周杰伦 专辑 电影 演唱会 奖项 创作 发行 | claim:5 | D q3:decomposed:dense:239<br>B q3:decomposed:lexical:372 | 384 / 150 | — | MISS |
| C_ENTITY_ATTRIBUTES | 周杰伦 专辑 电影 演唱会 奖项 创作 发行 | claim:7 | D q3:decomposed:dense:MISS<br>B q3:decomposed:lexical:MISS | 571 / 228 | — | MISS |

### rag-v0-039

Question：从2008至2012年周杰伦有哪些变化

| rewrite | query text | unit | new Dense/BM25 raw Gold rank | fused Child/Parent pre rank | selected rank | outcome |
|---|---|---|---|---|---:|---|
| B_INTENT_ALBUM | 周杰伦 专辑 发行 年份 | claim:1 | D q3:decomposed:dense:415<br>B q3:decomposed:lexical:MISS | 206 / 76 | — | MISS |
| B_INTENT_ALBUM | 周杰伦 专辑 发行 年份 | claim:2 | D q3:decomposed:dense:415<br>B q3:decomposed:lexical:MISS | 206 / 76 | — | MISS |
| B_INTENT_FILM | 周杰伦 电影 作品 时间 | claim:1 | D q3:decomposed:dense:222<br>B q3:decomposed:lexical:MISS | 178 / 59 | — | MISS |
| B_INTENT_FILM | 周杰伦 电影 作品 时间 | claim:2 | D q3:decomposed:dense:222<br>B q3:decomposed:lexical:MISS | 178 / 59 | — | MISS |
| B_INTENT_EVENTS | 周杰伦 音乐事业 事件 | claim:1 | D q3:decomposed:dense:83<br>B q3:decomposed:lexical:13 | 57 / 24 | 94 | HIT recovered |
| B_INTENT_EVENTS | 周杰伦 音乐事业 事件 | claim:2 | D q3:decomposed:dense:83<br>B q3:decomposed:lexical:13 | 57 / 24 | 94 | HIT recovered |
| B_INTENT_AWARDS | 周杰伦 奖项 活动 | claim:1 | D q3:decomposed:dense:239<br>B q3:decomposed:lexical:MISS | 196 / 65 | — | MISS |
| B_INTENT_AWARDS | 周杰伦 奖项 活动 | claim:2 | D q3:decomposed:dense:239<br>B q3:decomposed:lexical:MISS | 196 / 65 | — | MISS |
| B_ALL_FOUR_DIAGNOSTIC | 周杰伦 专辑 发行 年份<br>周杰伦 电影 作品 时间<br>周杰伦 音乐事业 事件<br>周杰伦 奖项 活动 | claim:1 | D —<br>B q3:decomposed:lexical:MISS; q4:decomposed:lexical:MISS; q5:decomposed:lexical:13; q6:decomposed:lexical:MISS | 131 / 66 | — | MISS |
| B_ALL_FOUR_DIAGNOSTIC | 周杰伦 专辑 发行 年份<br>周杰伦 电影 作品 时间<br>周杰伦 音乐事业 事件<br>周杰伦 奖项 活动 | claim:2 | D —<br>B q3:decomposed:lexical:MISS; q4:decomposed:lexical:MISS; q5:decomposed:lexical:13; q6:decomposed:lexical:MISS | 131 / 66 | — | MISS |
| C_ENTITY_ATTRIBUTES | 周杰伦 专辑 电影 演唱会 奖项 创作 发行 | claim:1 | D q3:decomposed:dense:436<br>B q3:decomposed:lexical:483 | 180 / 57 | — | MISS |
| C_ENTITY_ATTRIBUTES | 周杰伦 专辑 电影 演唱会 奖项 创作 发行 | claim:2 | D q3:decomposed:dense:436<br>B q3:decomposed:lexical:483 | 180 / 57 | — | MISS |

### rag-v0-045

Question：《晴天》的乐评资料，不含翻唱版

| rewrite | query text | unit | new Dense/BM25 raw Gold rank | fused Child/Parent pre rank | selected rank | outcome |
|---|---|---|---|---|---:|---|
| C_ENTITY_ATTRIBUTES | 晴天 歌曲 专辑 乐评 评论 创作 发行 | claim:1 | D q3:decomposed:dense:1<br>B q3:decomposed:lexical:1 | — / — | — | MISS |

### rag-v0-065

Question：周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？

| rewrite | query text | unit | new Dense/BM25 raw Gold rank | fused Child/Parent pre rank | selected rank | outcome |
|---|---|---|---|---|---:|---|
| C_ENTITY_ATTRIBUTES | 周杰伦 专辑 电影 演唱会 奖项 创作 发行 | claim:1 | D q3:decomposed:dense:306<br>B q3:decomposed:lexical:15 | — / — | — | MISS |

## Recovered / lost 明细

### A_SCHEMA_LYRIC_AUTHOR

Recovered: none。
Lost: none。

### A_SCHEMA_TRACKLIST

Recovered: rag-v0-024/claim:2, rag-v0-024/claim:3, rag-v0-024/claim:4。
Lost: none。

### A_SCHEMA_EN_CREDITS

Recovered: none。
Lost: none。

### B_INTENT_ALBUM

Recovered: none。
Lost: none。

### B_INTENT_FILM

Recovered: none。
Lost: rag-v0-036/claim:5, rag-v0-040/claim:3。

### B_INTENT_EVENTS

Recovered: rag-v0-039/claim:1, rag-v0-039/claim:2。
Lost: rag-v0-036/claim:4, rag-v0-036/claim:5。

### B_INTENT_AWARDS

Recovered: none。
Lost: rag-v0-036/claim:4, rag-v0-036/claim:5。

### B_ALL_FOUR_DIAGNOSTIC

Recovered: none。
Lost: rag-v0-036/claim:4, rag-v0-036/claim:5。

### C_ENTITY_ATTRIBUTES

Recovered: none。
Lost: rag-v0-002/claim:2, rag-v0-004/claim:1, rag-v0-036/claim:4, rag-v0-036/claim:5。

## 判断

关键阶段核对：024 的 `曲目表 词作者` leg 对同一 Gold Parent 的无过滤 rank 为 Dense #294 / BM25 #19；Filter V2 后为 #294 / #16；融合 Parent preselection #122，selector 最终 #47。040 的电影 leg 对 claim:5 的 Parent 是 Dense #17 / BM25 #75，Filter V2 后 #17 / #63，融合 Parent #53，但 selector 未选中。因此 040 的这一条并非新增 leg 完全找不到，而是进入候选后未进入最终 Top100。这里的 Child rank 是该 Gold Parent 下最早出现的 Child；Gold 评分单位仍为 Parent/evidence unit。

回退也有可追踪的阶段原因：`B_INTENT_FILM` 下 036/claim:5 的 Gold Parent 融合 preselection #12，却被 selector 挤出；040/claim:3 从 baseline 最终 #78 变为融合 preselection #149，最终未选。`B_INTENT_EVENTS` 下 036 两个原命中 unit 的 Parent 仍在融合候选（#84 / #16），但未被 selector 选中。宽泛 C 的 002、004、036 回退同样可在 JSONL 中定位到被替换的 Gold Parent 与新 preselection rank。这里的“挤出”仅描述固定 selector 在改写后候选排序变化的结果，不归咎于 RRF/selector 实现错误。

1. **024 的 schema-aware `曲目表 词作者` 有效，但稳定性尚未证明。** 单独一条双通道诊断 variant 让该 case 的 3 个 Gold unit（同一 Parent）全部进入最终 Top100；新增 BM25 leg 中 Gold Parent 排名比 Dense 靠前，说明字段词消除了部分词汇差距。其他两个 schema variant 均未恢复。当前仅是一份冻结 trace 上的一次确定性实验，不能声称对新问题稳定泛化。

2. **040 的四种意图探针均未恢复其 3 个 baseline MISS。** 电影 leg 对其中一条取得较高 Dense raw rank，但没有通过最终选择；同时该配置挤掉 040 原有 claim:3。四 leg 的 lexical 组合也没有净收益。现有证据不支持直接增加固定的 multi-intent 时间线模板。

3. **宽泛实体属性扩展没有收益。** C 在 32 个 case 生效，新增 248 个 non-Gold Parent 槽位，丢失 4 个已有 Gold unit。039 的事件 leg 单独恢复两个 unit，但同时挤掉 036 两个，micro 不变；且与此前 joint cap/overflow 诊断一致，039 的选择结果对 leg 排序敏感。

4. **可作为下一轮候选的低成本 rewrite 只有定向字段词 `曲目表 词作者`。** 本轮在 024 恢复 3 个 unit、无 Gold regression，macro 89.29%→91.21%，micro 85/95→88/95，新增 13 个 non-Gold Parent 槽位。它是受控实验结果，不是生产变更建议；若考虑上线，还需独立样本验证触发条件和候选噪声。

5. **未触及的 miss 仍有其他瓶颈。** 045 的 Gold 范围有争议，065 受固定 temporal filter 约束；024/040 之外的 miss 不能都归因于 Planner。

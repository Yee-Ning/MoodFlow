# Gold V4 Schema-aware Query Rewrite Generalization Audit

## 固定条件与防泄漏

使用相同的 39 eligible case、95 Gold evidence unit、historical Planner legs、frozen Gold/corpus/index/projection、embedding revision、每 leg 600 Child、Filter V2 SAFE_COMBINED_V2_CAP_5、weighted RRF k=60、production selector caps 与 Top100 Parent。仅对命中预先固定触发规则的 case 加一条 Dense+BM25 诊断 query variant，使用既有 decomposed 权重 0.8。生产 Planner、检索配置、reranker、Context Builder 均未修改。每 case 最多一条 rewrite。

Subject 仅使用原问题明示的书名号标题、historical plan 已解析实体，或问题开头的表面主题；新增词只来自下表通用 schema synonym。没有使用 Gold 答案、evidence 文本、隐藏标题或年份。原问题明示的标题可保留；未新增任何年份。

| category | 原问题触发词 | 通用 schema synonym |
|---|---|---|
| lyric_credit | 作词 / 歌词署名 / 歌词由谁写 | 曲目表 词作者 |
| arrangement_credit | 编曲 | 曲目表 编曲者 |
| production_credit | 母带且刻片 | 母带 刻片 制作人员 |
| tracklist_count | 曲目且问总计/多少/收录 | 曲目表 收录曲数 |
| award_credit | 创作奖项且得奖者 | 得奖名单 创作奖项 |
| collaboration_credit | 合作关系 | 合作作品 作词 曲目表 |
| album_sales | 专辑且问销售/排名/榜 | 专辑 销售 榜单 排名 |
| view_count | 播放量 | 播放量 观看次数 YouTube |
| release_status | 未发表 | 发行 发布 未发表 |
| film_inspiration | 创作灵感且电影 | 创作灵感 电影 片名 |
| timeline_event | 音乐生涯、重要经历、有哪些变化、如何演变 | 音乐事业 年表 大事记 |

没有明确询问 composer、cast 或 director 字段的 eligible 原问题；不能因为提到歌曲或电影就推断这些意图。风格评价、资料查询、采访中的概念问题和歌词创作过程也未硬套到字段查询。未触发 case 仍纳入全局分母，逐 case 记录见 JSONL。

冻结源 SHA-256：Gold 1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e；corpus 8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a；projection 663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0；manifest 0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033。

## 全局结果

Macro 为 39 case 等权；micro 为 95 evidence unit。non-Gold admitted 是相对 baseline 新进 Top100 的 case–Parent 槽位，不是 distinct Parent 数。

| config | applied cases | Hybrid macro@100 | micro | recovered | lost | net | non-Gold admitted | Gold displaced | regression cases |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Baseline | 39 | 89.29% | 85/95 | 0 | 0 | 0 | 0 | 0 | none |
| Schema rewrite | 19 | 90.18% | 86/95 | 3 | 2 | +1 | 180 | 2 | rag-v0-036 |

## 按 schema 类别与历史 query type

| category | cases | baseline hits/units | rewrite hits/units | recovered | lost | new non-Gold |
|---|---:|---:|---:|---:|---:|---:|
| album_sales | 1 | 1/1 | 1/1 | 0 | 0 | 0 |
| arrangement_credit | 2 | 7/7 | 7/7 | 0 | 0 | 13 |
| award_credit | 1 | 3/3 | 3/3 | 0 | 0 | 3 |
| collaboration_credit | 1 | 2/2 | 2/2 | 0 | 0 | 11 |
| film_inspiration | 1 | 1/1 | 1/1 | 0 | 0 | 5 |
| lyric_credit | 4 | 6/9 | 9/9 | 3 | 0 | 45 |
| production_credit | 1 | 2/2 | 2/2 | 0 | 0 | 12 |
| release_status | 1 | 1/1 | 1/1 | 0 | 0 | 18 |
| timeline_event | 5 | 17/22 | 15/22 | 0 | 2 | 39 |
| tracklist_count | 1 | 1/1 | 1/1 | 0 | 0 | 20 |
| view_count | 1 | 1/1 | 1/1 | 0 | 0 | 14 |

Historical query type 是多标签，同一 case 可在多行出现，不能跨行求和。

| query type | applied cases | recovered | lost | non-Gold admitted |
|---|---:|---:|---:|---:|
| exploratory | 1 | 0 | 0 | 11 |
| fact | 13 | 3 | 0 | 134 |
| influence_style | 2 | 0 | 2 | 16 |
| relationship | 5 | 3 | 0 | 55 |
| timeline | 3 | 0 | 2 | 24 |

## 每个 rewrite 的 Gold Parent 轨迹

每行是一条 Gold evidence unit。Parent pre-rank 是融合后、selector 前的 Gold Parent 最佳排名；selected 是最终 Top100 名次。新增 leg 的无过滤 Dense/BM25 raw rank、Filter V2 后 rank、Gold Parent 的最早 fused Child rank，以及 candidate churn 均在 JSONL。多个 unit 共用一个 Parent 时排名重复，计分仍逐 unit。

| case / category | added diagnostic query | unit | Parent pre-rank baseline → rewrite | selected baseline → rewrite | result |
|---|---|---|---|---|---|
| rag-v0-001 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:1 | 84 → 137 | 42 → 64 | HIT |
| rag-v0-001 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:2 | 167 → 172 | 81 → 84 | HIT |
| rag-v0-001 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:3 | 17 → 7 | 11 → 7 | HIT |
| rag-v0-001 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:4 | 84 → 137 | 42 → 64 | HIT |
| rag-v0-001 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:5 | 84 → 137 | 42 → 64 | HIT |
| rag-v0-023 / collaboration_credit | 周杰伦 方文山 合作作品 作词 曲目表 | claim:1 | 32 → 31 | 12 → 12 | HIT |
| rag-v0-023 / collaboration_credit | 周杰伦 方文山 合作作品 作词 曲目表 | claim:2 | 5 → 11 | 5 → 5 | HIT |
| rag-v0-024 / lyric_credit | 周杰伦 曲目表 词作者 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-024 / lyric_credit | 周杰伦 曲目表 词作者 | claim:2 | 310 → 120 | — → 45 | HIT recovered |
| rag-v0-024 / lyric_credit | 周杰伦 曲目表 词作者 | claim:3 | 310 → 120 | — → 45 | HIT recovered |
| rag-v0-024 / lyric_credit | 周杰伦 曲目表 词作者 | claim:4 | 310 → 120 | — → 45 | HIT recovered |
| rag-v0-026 / arrangement_credit | 周杰伦 洪敬尧 曲目表 编曲者 | group:026-award | 7 → 9 | 7 → 9 | HIT |
| rag-v0-026 / arrangement_credit | 周杰伦 洪敬尧 曲目表 编曲者 | group:026-track-1 | 5 → 5 | 5 → 5 | HIT |
| rag-v0-026 / arrangement_credit | 周杰伦 洪敬尧 曲目表 编曲者 | group:026-track-2 | 5 → 5 | 5 → 5 | HIT |
| rag-v0-026 / arrangement_credit | 周杰伦 洪敬尧 曲目表 编曲者 | group:026-track-3 | 5 → 5 | 5 → 5 | HIT |
| rag-v0-026 / arrangement_credit | 周杰伦 洪敬尧 曲目表 编曲者 | group:026-track-4 | 5 → 5 | 5 → 5 | HIT |
| rag-v0-036 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-036 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:2 | 123 → 108 | 84 → 79 | HIT |
| rag-v0-036 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:3 | 123 → 108 | 84 → 79 | HIT |
| rag-v0-036 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:4 | 68 → 79 | 48 → — | MISS lost |
| rag-v0-036 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:5 | 11 → 11 | 11 → — | MISS lost |
| rag-v0-037 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:1 | 34 → 57 | 62 → 80 | HIT |
| rag-v0-037 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:2 | 31 → 35 | 60 → 66 | HIT |
| rag-v0-037 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:3 | 2 → 1 | 2 → 1 | HIT |
| rag-v0-039 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:1 | 47 → 55 | — → — | MISS |
| rag-v0-039 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:2 | 47 → 55 | — → — | MISS |
| rag-v0-040 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:1 | 2 → 2 | 2 → 2 | HIT |
| rag-v0-040 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:2 | 263 → 322 | — → — | MISS |
| rag-v0-040 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:3 | 129 → 127 | 78 → 77 | HIT |
| rag-v0-040 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:4 | 19 → 5 | 12 → 5 | HIT |
| rag-v0-040 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:5 | 151 → 178 | — → — | MISS |
| rag-v0-040 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:6 | 25 → 21 | 16 → 13 | HIT |
| rag-v0-040 / timeline_event | 周杰伦 音乐事业 年表 大事记 | claim:7 | 179 → 180 | — → — | MISS |
| rag-v0-049 / award_credit | 范特西 爱在西元前 威廉古堡 双截棍 得奖名单 创作奖项 | group:rag-v0-049-claim-1 | 1 → 3 | 1 → 3 | HIT |
| rag-v0-049 / award_credit | 范特西 爱在西元前 威廉古堡 双截棍 得奖名单 创作奖项 | group:rag-v0-049-claim-2 | 1 → 3 | 1 → 3 | HIT |
| rag-v0-049 / award_credit | 范特西 爱在西元前 威廉古堡 双截棍 得奖名单 创作奖项 | group:rag-v0-049-claim-3 | 1 → 3 | 1 → 3 | HIT |
| rag-v0-050 / tracklist_count | 杰威尔 黑胶 曲目表 收录曲数 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-051 / production_credit | 周杰伦 黑胶 母带 刻片 制作人员 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-051 / production_credit | 周杰伦 黑胶 母带 刻片 制作人员 | claim:2 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-052 / view_count | Mojito 播放量 观看次数 YouTube | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-053 / release_status | 周杰伦 发行 发布 未发表 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-055 / lyric_credit | 魔杰座 花海 曲目表 词作者 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-056 / film_inspiration | 喬克叔叔 创作灵感 电影 片名 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-057 / arrangement_credit | 魔術先生 曲目表 编曲者 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-057 / arrangement_credit | 魔術先生 曲目表 编曲者 | claim:2 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-058 / lyric_credit | 流浪詩人 曲目表 词作者 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-058 / lyric_credit | 流浪詩人 曲目表 词作者 | claim:2 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-059 / lyric_credit | 蘭亭序 曲目表 词作者 | claim:1 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-059 / lyric_credit | 蘭亭序 曲目表 词作者 | claim:2 | 1 → 1 | 1 → 1 | HIT |
| rag-v0-063 / album_sales | 最伟大的作品 专辑 销售 榜单 排名 | claim:1 | 6 → 8 | 3 → 4 | HIT |

## Recovered / lost 明细

Recovered: rag-v0-024/claim:2, rag-v0-024/claim:3, rag-v0-024/claim:4。

Lost: rag-v0-036/claim:4, rag-v0-036/claim:5。

## 解释与建议

19 个触发 case 中，16 个在 baseline 已经命中各自全部 Gold unit，因此它们主要用于观察回退与噪声，不能证明新 rewrite 提高了召回。原本有未命中 unit 的只有 024、039、040：024 恢复 3 个 unit，但三者共享一个 Gold Parent；039 的 2 个和 040 的 3 个仍未命中。未触发的 20 个 case 是 002、003、004、005、006、007、008、031、034、042、043、044、045、054、060、061、062、064、065、066；其问题表面没有本实验可安全映射的字段意图，未因主题相关而强行扩展。

024 的新增 leg 将共享 Gold Parent 排到无过滤 BM25 #19、Dense #280；Filter V2 后 BM25 #16，融合 Parent preselection 从 #310 升至 #120，最终进入 Top100 的 #45。此前单 case rewrite ablation 也恢复这 3 个 unit；本轮精确 Dense/selection rank 与前次略有不同，因为新 query 的 encoder batch 组成不同。历史 baseline 在 39/39 case 均通过与 Filter V2 已归档 Parent ID 顺序的逐 case 断言；跨报告只比较命中结论，不把细微 rank 差异解释成策略差异。

036 的年表同义词 rewrite 使 claim:4 与 claim:5 的 Gold Parent 仍处于融合候选 #79、#11，却都未通过固定 selector；该 case 原本两条均命中。039 的 Gold Parent 从融合 #47 变为 #55，仍未选中。040 原本未命中的 claim:2/5/7 仍未命中。因此 timeline_event 并未改善缺口，还产生两条回退。其余类别全部没有新恢复；lyric_credit 的另外 3 个 case 在 baseline 已全命中，不能据此推断其召回增益可泛化。

**结论：当前观测到的增量只发生在 024，尚不能证明 schema-aware rewrite 跨 case 泛化。** Historical query type 中 fact/relationship 的正增量均来自同一个 024；timeline/influence_style 有回退，其他 type 没有新增命中。11 类统一 mapping 在 19 个 case 上恢复 3、丢失 2，micro 85/95→86/95，macro 89.29%→90.18%，并引入 180 个 non-Gold Parent 槽位；这不是低噪声、零回退的统一规则。

**Planner V2 schema layer 目前不宜按统一映射上线。** 若继续研究，只建议把“明确询问作词署名”的窄触发规则作为离线候选，在含有未命中作词问题的独立样本上验证；尤其需要多个不同 Gold Parent，而非只重复验证 024 的同一 Parent。当前推荐上线范围为零，production Planner 保持不变。

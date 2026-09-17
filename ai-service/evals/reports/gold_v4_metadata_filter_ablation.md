# Gold V4 Metadata Filter Coverage Ablation

## 结论与实验边界

本实验只改变 metadata filter application policy。Gold、frozen corpus、index manifest/projection、Query Planner 已生成的全部 query legs、Qwen embedding revision、Dense/BM25 搜索参数、weighted RRF、每 leg 600 Child 诊断预算、Top100 Parent 预算、document/source cap 均保持不变。没有修改 production 配置、Reranker 或 Context Builder。A/B/C 都使用同一现存 Qdrant collection 的只读查询；A 的 39-case Parent Top100 排序必须逐 case/逐 channel 与历史归档完全一致才发布结果。

**方法限制：** `NO_FILTER_DIAGNOSTIC` 是无过滤诊断参照，不是推荐生产策略，也不是数学上必然单调的 upper bound：取消过滤会改变 rank 和候选竞争。C 将 A 的 exact-filter Child 排在每 leg 前面，再从 B 的同 leg 原始排序中加入符合窄规则的 fallback Child，仍截断在每 leg 600；filter confidence 只记录，不更改 RRF 权重。由于候选竞争，C 也可能失去既有 Gold。

- Gold SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；语料 SHA-256：`8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。
- Projection SHA-256：`663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`；index manifest SHA-256：`0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。
- Retrieval config SHA-256：`b60e4f0f4c0a565db0e02ba7da8e3946216b6087e046993c432e1945f5e652c3`；Query Planner config SHA-256：`afcb85b4649fb0cdc8d117f480b85bc4ddfebbbcecb8437f6c655698e1c7db77`；历史检索档案 SHA-256：`dc83c9f6fa13b42dfb0205866d672c473bf21baed00e46d931f1a9dfb7e10736`。
- Eligible cases：39；positive evidence units：95。macro 为 39 case 等权平均；micro raw hit 分母为 95。

## A / B / C 指标

| Config | Dense@100 | BM25@100 | Hybrid@100 | micro Hybrid hits | recovered | lost | net gain |
|---|---:|---:|---:|---:|---:|---:|---:|
| BASELINE | 79.84% | 77.14% | 83.30% | 80/95 | 0 | 0 | +0 |
| NO_FILTER_DIAGNOSTIC | 88.13% | 75.69% | 85.95% | 77/95 | 5 | 8 | -3 |
| SOFT_FALLBACK_FILTER | 85.82% | 82.27% | 87.58% | 83/95 | 5 | 2 | +3 |

每一配置的 Dense/BM25 micro hits 和逐 case/逐 evidence unit 三通道结果均在同名 JSONL。

相对 A，B 的 Hybrid macro **+2.65 个百分点**，但 micro 净少 3 条（恢复 5、丢失 8）；C 的 Hybrid macro **+4.27 个百分点**，micro 净增 3 条（恢复 5、丢失 2）。C 比 B 高 **1.62 个百分点**。这说明“取消过滤”并非单调上界：新候选会挤走原有 Gold。

045 是 disputed_scope_case。仅作敏感性分析，暂不把它用于判断策略成功时，38 个其余 eligible case 的 A/B/C Hybrid macro 分别为 **85.49% / 85.58% / 89.88%**；B 相对 A 仅 **+0.09 个百分点**。正式 39-case 指标仍以上表为准。

## Top100 candidate churn 与噪声

计数单位是 **case–Parent 槽位**；同一 Parent 在不同 case 中各计一次。Gold Parent precision proxy = Top100 中属于该 case Gold acceptable Parent 集的 Parent 槽位 / 所有 Top100 Parent 槽位。这只是检索候选噪声代理，不是最终答案精度。

| Config | retained | newly admitted | displaced | Gold displaced Parents | new non-Gold Parents | Top100 slots | Gold Parent proxy precision |
|---|---:|---:|---:|---:|---:|---:|---:|
| BASELINE | 2996 | 0 | 0 | 0 | 0 | 2996 | 2.04% |
| NO_FILTER_DIAGNOSTIC | 2299 | 1601 | 697 | 7 | 1596 | 3900 | 1.51% |
| SOFT_FALLBACK_FILTER | 2875 | 220 | 121 | 2 | 216 | 3095 | 2.04% |

C 的 Top100 中有 **220** 个 fallback Parent 槽位，其中 **216** 个不是该 case 的 Gold acceptable Parent；完整 fallback Parent、触发规则和 filter confidence 见 JSONL。
跨 39 case 求和，C 的 preselection exact-filter pool 有 **10,679** 个 case–Parent 槽位，符合窄规则的 fallback pool 有 **1,287** 个；最终 Top100 包含 **2,875 exact + 220 fallback** 槽位，fallback 占 **7.11%**。220 个 fallback 中，4 个是 Gold Parent 槽位，216 个不在该 case 的 Gold Parent 集。
与 A 比，C 新增 220 个 Parent 槽位、排挤 121 个，其中 **2 个是原有 Gold Parent**；Gold Parent proxy precision 的未四舍五入值由 **2.0360%** 略降到 **2.0355%**。因此 C 的召回收益伴随明显候选噪声，不能据召回增量直接推荐上线。

## 各 filter type 的贡献

blocked Gold 按 Gold span 所在 Child 是否被该类型的 exact 条件排除统计；一个 unit 可以被多个类型阻断，因此各行不必相加为 95。new candidate noise 为 C Top100 中触发该 fallback 类型的非 Gold Parent 槽位，也可能跨类型重复。

| Filter type | blocked Gold units | soft recovered Gold units | new candidate noise |
|---|---:|---:|---:|
| entity | 3 | 2 | 51 |
| content_type | 4 | 2 | 0 |
| temporal | 1 | 0 | 145 |
| language | 1 | 1 | 20 |

按 C 的恢复量，entity 和 content_type 各贡献 **2 个 evidence unit**，language 贡献 **1 个**；temporal **0 个**，却带入 **145 个非 Gold 候选槽位**，是本次噪声最大的规则。006 的 2 个 entity unit 实际共享 1 个 Parent；这张表的单位始终是 evidence unit。content_type 的 4 个 blocked unit 包含 045 的范围争议；它未被 C 放行。

## 保守 fallback 规则

1. Entity：仅当 exact pool 的不同 Parent 少于 12（沿用现有 exploratory coverage 阈值）且目标是 song 时，允许 album/artist 相关 Child；Child 正文必须显式出现该歌曲标题。
2. Content type：interview 精确类型优先；只兼容含采访语境和直接回答标记的 commentary。news 只兼容 `source_type=media_article` 的 artist_biography。review 不兼容 chart_event，因此 045 不作为放宽规则依据。
3. Temporal：已有时间元数据且与范围不相交的 Child 拒绝；完全缺失 year/区间/提及年份的 Child 可作为低 confidence fallback。
4. Language：`en` 精确类型优先；`und` 只有正文至少 8 个英文词且拉丁字母占字母数 ≥50% 才兼容。
5. Fallback 只改变候选准入与 exact-first 排序；RRF 权重、cap 和 budget 不变。

## 重点 case

### 006

Query：歌曲《七里香》的故事

Hybrid case recall A/B/C：0.00% / 100.00% / 100.00%。 Exact pool=6 Parent；soft fallback pool=52 Parent；soft Top100 fallback=52 Parent。

- claim:1 · 索尼资料称团队讨论后用首波主打《七里香》作为第五张专辑名称：A/B/C = **MISS / HIT / HIT**；exact blocked by entity。
- claim:2 · 索尼资料称《七里香》MV 在日本乡间取景：A/B/C = **MISS / HIT / HIT**；exact blocked by entity。

### 042

Query：周杰伦的英文资料

Hybrid case recall A/B/C：50.00% / 0.00% / 100.00%。 Exact pool=52 Parent；soft fallback pool=21 Parent；soft Top100 fallback=21 Parent。

- claim:1 · 英文专辑列表资料示例：A/B/C = **MISS / MISS / HIT**；exact blocked by language。
- claim:2 · 英文正文资料示例：A/B/C = **HIT / MISS / HIT**；exact blocked by none。

042 的无过滤原始 Dense/BM25 Child rank 约为 **373/399**，B 的 Hybrid Top100 仍 MISS，且挤掉另一条原有英文资料。C 只准入具有明显英文正文的 `und` 点，并把 exact 点优先保留；目标 Child 的 C leg rank 为 **82–90**、融合 Child rank **79**、distinct Parent rank **53**，所以 C 在 Top100 命中两条证据（其中一条为本轮恢复），而 B 未恢复该条。语言过滤确是必要障碍，但无条件取消过滤不足以恢复。

### 043

Query：周杰伦的采访资料

Hybrid case recall A/B/C：50.00% / 50.00% / 100.00%。 Exact pool=83 Parent；soft fallback pool=1 Parent；soft Top100 fallback=1 Parent。

- claim:1 · 人物报道转述周杰伦在 CNN 采访中希望听他歌曲的孩子更孝顺父母：A/B/C = **MISS / HIT / HIT**；exact blocked by content_type。
- claim:2 · 周杰伦在直接问答中表示看电影会刺激创作灵感：A/B/C = **HIT / MISS / HIT**；exact blocked by none。

### 044

Query：周杰伦的新闻资料，不要现场版

Hybrid case recall A/B/C：66.67% / 66.67% / 100.00%。 Exact pool=18 Parent；soft fallback pool=2 Parent；soft Top100 fallback=1 Parent。

- claim:1 · 杰威尔官方新闻列出周杰伦挑选公开展示的五首 DEMO：A/B/C = **HIT / HIT / HIT**；exact blocked by content_type。
- claim:2 · 人物报道记载周杰伦的新专辑发行间隔逐渐变长：A/B/C = **MISS / MISS / HIT**；exact blocked by content_type。
- claim:3 · 早期 DEMO 完成度较高：A/B/C = **HIT / HIT / HIT**；exact blocked by none。

### 045 · disputed_scope_case

Query：《晴天》的乐评资料，不含翻唱版

Hybrid case recall A/B/C：0.00% / 100.00% / 0.00%。 Exact pool=0 Parent；soft fallback pool=0 Parent；soft Top100 fallback=0 Parent。

- claim:1 · 《晴天》在 2003 年 HitFM 年度百首单曲中名列亚军：A/B/C = **MISS / HIT / MISS**；exact blocked by content_type。

045 的 Gold positive unit 是 chart ranking 背景，不是乐评文本。即便 B 恢复，也只作为争议范围诊断；不能算作 C 通用策略的成功依据。

### 065

Query：周杰伦在2004年访谈中怎样描述动作电影与快歌创作的关系？他列出了每首快歌对应的具体电影吗？

Hybrid case recall A/B/C：0.00% / 100.00% / 0.00%。 Exact pool=163 Parent；soft fallback pool=305 Parent；soft Top100 fallback=49 Parent。

- claim:1 · 周杰伦自述很多快歌受到动作片启发：A/B/C = **MISS / HIT / MISS**；exact blocked by temporal。

065 的 Gold Child 符合 C 的 `temporal_unknown` 准入，但有 **163** 个 exact Parent 优先；其 C 原始 leg rank 为 **248–249**，融合 Child rank **263**、distinct Parent rank **177**，仍未进入 Top100。C 在此 case 的 Top100 放入 49 个 fallback Parent，**全部非 Gold**；这说明仅把缺失年份视作低置信 fallback 并不能解决该 miss。

## 恢复、损失与因果判断

- B 恢复：006 `claim:1/2`、043 `claim:1`、045 `claim:1`、065 `claim:1`。B 损失：036 `claim:2/3/4/5`、037 `claim:1/2`、042 `claim:2`、043 `claim:2`。045 的恢复单列为范围争议。
- C 恢复：006 `claim:1/2`、042 `claim:1`、043 `claim:1`、044 `claim:2`。C 损失：037 `claim:1/2`；该 case 的 temporal-unknown fallback 新增 **54** 个 Top100 Parent，挤出了两个原有 Gold Parent。039 的 document-cap miss、024/040 的宽泛 query miss 均未因 filter policy 改变而恢复。
- 结论：metadata hard filter **确实系统性排除部分高排名相关点**，但它不是全部 Top100 ceiling 的单一主因。C 在 15 个原 dual-miss 中恢复 5 个，且损失 2 个原命中；B 的无过滤效果更弱。C 仍为 **87.58%**，未达到 92%。

## 逐 case 指标与恢复/损失

| Case | A Hybrid | B Hybrid | C Hybrid | B recovered/lost | C recovered/lost | C Top100 fallback |
|---|---:|---:|---:|---:|---:|---:|
| rag-v0-001 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-002 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-003 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-004 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-005 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-006 | 0.00% | 100.00% | 100.00% | 2/0 | 2/0 | 52 |
| rag-v0-007 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-008 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-023 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-024 | 25.00% | 25.00% | 25.00% | 0/0 | 0/0 | 0 |
| rag-v0-026 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-031 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-034 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-036 | 100.00% | 20.00% | 100.00% | 0/4 | 0/0 | 0 |
| rag-v0-037 | 100.00% | 33.33% | 33.33% | 0/2 | 0/2 | 54 |
| rag-v0-039 | 0.00% | 0.00% | 0.00% | 0/0 | 0/0 | 18 |
| rag-v0-040 | 57.14% | 57.14% | 57.14% | 0/0 | 0/0 | 0 |
| rag-v0-042 | 50.00% | 0.00% | 100.00% | 0/1 | 1/0 | 21 |
| rag-v0-043 | 50.00% | 50.00% | 100.00% | 1/1 | 1/0 | 1 |
| rag-v0-044 | 66.67% | 66.67% | 100.00% | 0/0 | 1/0 | 1 |
| rag-v0-045 | 0.00% | 100.00% | 0.00% | 1/0 | 0/0 | 0 |
| rag-v0-049 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-050 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-051 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-052 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-053 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-054 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-055 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-056 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-057 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-058 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-059 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-060 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-061 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-062 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-063 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 24 |
| rag-v0-064 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |
| rag-v0-065 | 0.00% | 100.00% | 0.00% | 1/0 | 0/0 | 49 |
| rag-v0-066 | 100.00% | 100.00% | 100.00% | 0/0 | 0/0 | 0 |

## 解释与限制

- 本轮只有 filter application policy 变化；B 绝不作为生产建议，C 也只是实验版，未提交或启用正式策略。
- 逐 unit recovered/lost、具体 Parent churn、fallback 理由、case-level proxy precision 和全部三个 channel 排序见 JSONL。
- 如果 soft recall 增加但 Top100 非 Gold Parent 增多，应把噪声和 displaced Gold 同时纳入人工判断。
- 实验完成后停止，不执行 document-cap、Planner、RRF、Reranker 或 Context Builder 优化。

# WorldLine Gold Dataset V1 真实数据准备计划

> 本计划只定义第一批真实数据的选择、采集、审核和冻结流程。本阶段不创建 `worldline_gold_v1.jsonl` 或 Source Ledger 数据，不修改 WorldLine、Semantic Expansion、Genre Executor、Evaluation Infrastructure、RAG、数据库或任何业务代码。

## 1. 数据选择策略

### 1.1 第一批目标

第一批冻结 10 个经过审核的 Artist–Genre case，建议构成为：

| 类型 | 数量 | 用途 |
|---|---:|---|
| Accepted | 8 | 评测真实 Artist → Genre 关系、Evidence 与 Identity |
| Rejected | 2 | 验证无证据和身份歧义时不会进入可执行关系 |
| 合计 | 10 | Real Gold V1 第一批基线 |

Accepted case 的区域最低覆盖：

| 区域 | 最低数量 | 建议数量 |
|---|---:|---:|
| 华语 | 2 | 3 |
| 欧美 | 2 | 3 |
| 日本 | 1 | 1 |
| 韩国 | 1 | 1 |

如果某个候选无法同时满足证据和身份门槛，应替换候选或将其转入 rejected，不为了凑齐区域数量降低审核标准。

### 1.2 关系范围

每个 case 只评测：

```text
artist → genre
relation_type = associated_with
```

当前 Registry V2 只允许以下目标：

| Genre ID | Canonical Name | 可用于 Semantic Gold | 可用于 Genre Executor Gold |
|---|---|---:|---:|
| `genre:hip_hop` | Hip-Hop | 是 | 是 |
| `genre:jazz` | Jazz | 是 | 否 |
| `genre:jazz_hip_hop` | Jazz Hip-Hop | 是 | 否 |
| `genre:r_and_b` | R&B | 是 | 否 |
| `genre:c_pop` | C-pop | 是 | 否 |

不接受 K-pop、J-pop、pop、rock、electronic、alternative 等未注册 Genre。发现这类来源时，记录在候选排除清单，不能写入正式 Gold JSONL。

### 1.3 候选池与替换策略

先准备 14 个待审候选，再从中冻结 10 个。下表只是 source discovery 队列，不是 Gold 标签，也不表示关系已经成立：

| 区域 | Artist 候选 | 待核验 Genre 假设 | 优先原因 |
|---|---|---|---|
| 华语 | 周杰伦 | C-pop | 当前 Knowledge V2 已有 `has_genre` 候选和来源血缘 |
| 华语 | 蔡依林 | C-pop | 名称稳定，预计容易核验 catalog identity |
| 华语 | 邓紫棋 / G.E.M. | C-pop | 有明确中英文 aliases，适合验证跨语言 identity |
| 华语 | 陈奕迅 | C-pop | catalog identity 通常明确 |
| 华语备用 | MC HotDog | Hip-Hop | 用于补充可执行 Hip-Hop case |
| 欧美 | Kendrick Lamar | Hip-Hop | 权威机构和编辑来源较多 |
| 欧美 | Nas | Hip-Hop | Artist–Genre 关系通常有稳定编辑来源 |
| 欧美 | Beyoncé | R&B | 官方及机构身份来源较稳定 |
| 欧美备用 | Alicia Keys | R&B | 用于替换证据或 catalog mapping 不合格候选 |
| 欧美备用 | Common | Hip-Hop | 用于补充 Hip-Hop executor 覆盖 |
| 日本 | Nujabes | Jazz Hip-Hop | 适合验证复合 Genre，但必须找到直接来源 |
| 日本 | Hikaru Utada | R&B | 适合验证本地名、罗马字与 catalog identity |
| 韩国 | Epik High | Hip-Hop | 目标 Genre 已注册，适合日韩区域覆盖 |
| 韩国备用 | Dynamic Duo | Hip-Hop | 用于替换证据不足或身份无法唯一解析的候选 |

所有 Genre 都是待审核假设。审核人只能依据冻结来源决定 accepted/rejected，不能根据上表、模型常识或 WorldLine 输出决定标签。

### 1.4 现有知识库的使用边界

当前 Knowledge V2 中可直接作为第一批种子的 Artist 级 `has_genre` 很少，主要集中在 `artist:jay_chou`。使用方式：

1. 从 release manifest 定位原始 document、parent、child 和 claim。
2. 重新核对原文是否直接支持 Artist 本身与 Registry Genre 的关系。
3. 核对 `evidence_span` 是否是冻结 source text 的逐字子串。
4. 将现有 claim 当作候选，不因 verification status 自动批准为 Gold。
5. 其他 Artist 使用 Evaluation fixture-only 来源，不写入正式 Knowledge Base。

### 1.5 Accepted 与 Rejected 分布

建议第一批包含：

- 至少 3 个 `genre:hip_hop` accepted case，保证后续 Genre → Artist Real Gold 能形成分组。
- 至少 1 个 `genre:r_and_b` accepted case。
- 至少 1 个 `genre:c_pop` accepted case。
- `genre:jazz_hip_hop` 只有在来源明确使用该风格名称或受控 alias 时才接受。
- 1 个 `no_direct_evidence` rejected case。
- 1 个 `artist_identity_ambiguous` 或 `catalog_mapping_ambiguous` rejected case。

Rejected 表示系统应拒绝扩图，不表示音乐关系在现实中为假。

## 2. Case 审核流程

### 2.1 完整流程

```text
建立候选池
  ↓
查找并冻结真实来源
  ↓
Relation Reviewer 核对 Artist → Genre
  ↓
Identity Reviewer 核对 knowledge → catalog
  ↓
运行 Schema / Span / Hash / Duplicate Validator
  ↓
冲突项 Adjudication
  ↓
Owner 批准
  ↓
冻结 10 cases 与 Source Ledger SHA256
```

任何 case 在 owner 批准前保持 `review.status=pending`，不进入正式指标。

### 2.2 Accepted Gate

Accepted case 必须同时满足：

1. 来源真实、可访问，或能由当前 Knowledge release manifest 复原。
2. `exact_span` 直接说明该 Artist 与目标 Genre 相关。
3. Evidence 的主体是 Artist，不是单首歌曲、单张专辑或合作对象。
4. Genre 可以一一映射到 Registry V2 canonical ID。
5. knowledge identity 与 catalog identity 都为 `resolved`。
6. catalog mapping 唯一，`ambiguity_status=unambiguous`。
7. case 内 catalog node ID 与 `provider:artist:entity_id` 完全一致。
8. Relation Reviewer 和 Identity Reviewer 都已签名并记录时间。
9. 自动校验全部通过。

### 2.3 Relation 审核问题

Relation Reviewer 对每条证据回答：

- 原文是否明确指向当前 Artist？
- 原文是在描述 Artist 的音乐类型，还是只描述某一作品？
- Genre 原词或 alias 是否能无歧义映射到受控 canonical name？
- 原文是否包含否定、比较、历史背景或“受影响”等会改变关系含义的限定？
- 截取 exact span 后是否仍保留判断所需语义？
- 多个来源是否相互独立，而不是转载同一原稿？

“融合某元素”“受到某音乐影响”“某首歌属于某 Genre”不能自动标为 Artist `associated_with`。

### 2.4 Rejected Gate

正式 Gold JSONL 可以保存以下 rejected case：

| Reason | 条件 | Evidence |
|---|---|---|
| `no_direct_evidence` | 搜索到的来源没有直接 Artist–Genre 陈述 | 可为空，或保存不构成支持的审核上下文 |
| `relation_not_explicit` | 来源只描述作品或影响关系 | 保存原文并在 notes 说明边界 |
| `artist_identity_ambiguous` | knowledge Artist 无法唯一对应 catalog Artist | 可以保留 relation evidence，但 catalog 不得伪造 |
| `catalog_mapping_missing` | 关系来源成立，但目标 provider 无法找到可核验 Artist ID | catalog reference 为空 |
| `catalog_mapping_ambiguous` | 多个 provider ID 均可能匹配 | 记录候选 ID 和冲突原因，不选择其中一个 |

### 2.5 Unsupported Genre 的处理

当前 `GoldCase` Schema 会在加载阶段拒绝未注册 Genre，因此 `unsupported_genre` 不能进入正式 `worldline_gold_v1.jsonl`。处理方式：

1. 在准备期维护 `candidate_exclusion_log`。
2. 记录 Artist、原始 Genre、来源、排除原因和审核人。
3. 原因使用 `genre_not_registered`。
4. 不创建伪造 canonical ID，不把未注册 Genre 强行映射为最接近的 Registry Genre。
5. 如果未来 Registry 正式扩展，应在新 Dataset 版本重新审核，而不是修改 V1。

这个排除清单属于审核工作底稿，不进入 Real Gold 指标。

### 2.6 双人审核与冲突处理

- Relation Reviewer 只判断来源是否支持关系。
- Identity Reviewer 只判断实体和 catalog mapping。
- 两者结论冲突时，case 进入 adjudication，保持 pending。
- Adjudicator 必须写明采用或拒绝哪个判断及理由。
- 由项目负责人最终确认 10 条冻结记录。
- AI 可以整理候选和计算 offset/hash，不能替代最终审核签名。

## 3. Source 记录方式

### 3.1 来源优先级

按以下顺序选择来源：

1. 当前项目 Knowledge release 中可由 manifest 和 hash 验证的原始文档。
2. Artist、唱片公司、发行方、奖项机构、音乐节等官方页面。
3. 有编辑责任的音乐机构、音乐百科或专业媒体 Artist profile。
4. 公开知识数据库用于 identity 和 external ID 交叉核验。
5. 通用百科只作为候选发现或辅助证据；作为唯一 relation evidence 时必须有直接原句并经过加强审核。

不使用：

- 搜索结果摘要。
- 模型回答。
- 无作者或无出处聚合页。
- 社交媒体短句作为唯一来源。
- 无法定位原文的转载。

### 3.2 Source Ledger 记录

每个来源在 `worldline_gold_v1_sources.jsonl` 中只记录一次：

```text
source_id
fixture_source_id
title
publisher
url 或 local provenance
language
source_type
bounded content
content_hash
```

建议 ID 规则：

```text
source:<publisher-slug>:<document-slug>
fixture:worldline-gold-v1:<document-slug>
```

同一 URL 或相同 content hash 不重复计算为多个独立来源。

### 3.3 Evidence Snapshot

每个 case 中的 evidence 必须与 Source Ledger 一致：

1. 复制包含判断所需上下文的有限 `evidence_text`。
2. 从原文逐字截取 `exact_span`，不做简繁转换或 Unicode 改写。
3. 以 Python 字符索引计算 `span_start`、`span_end`。
4. 验证 `evidence_text[span_start:span_end] == exact_span`。
5. 对完整 bounded content 的 UTF-8 bytes 计算 SHA256。
6. case 的 `content_sha256` 与 ledger `content_hash` 必须一致。
7. 记录访问日期放在 review notes；当前 Schema 没有独立 `accessed_at` 字段，不修改 Infrastructure。

只冻结足以支撑判断的上下文，不复制整篇文章。

### 3.4 Knowledge Release 来源

项目内部来源还需在 `provenance` 中保存：

- release version。
- release manifest SHA256。
- document ID。
- parent ID。
- child ID。
- claim ID。
- 原 source URL。

这些值写入 Source Ledger 的 provenance 字符串和 review notes，不向生产知识库回写任何内容。

### 3.5 来源一致性检查

冻结前运行现有 Gold Loader，必须通过：

- Source ID 和 fixture source ID 唯一。
- Source content hash 正确。
- Evidence source/title/publisher/URL/language/type 与 ledger 一致。
- Evidence text 与 ledger content 一致。
- Exact span 与 offset 一致。
- Identity evidence source ID 存在于 ledger。
- Relation composite key 无重复。

## 4. Identity 审核方式

### 4.1 Knowledge Identity

为每个 Artist 确认：

- `canonical_name`。
- `knowledge_entity_id=artist:<slug>`。
- 经核验的 aliases。
- `registry_scope`。

已有生产 Entity Registry 的 Artist 使用 `production_registry`。其他真实 Artist 使用 `fixture_only_registry`，只供 Evaluation adapter 构造隔离 registry，不代表生产系统已经支持该 Artist。

### 4.2 Catalog Mapping

当前 Gold Infrastructure 期望：

```text
catalog_reference.provider
catalog_reference.entity_id
catalog_reference.entity_type = artist
catalog_reference.name
expected_catalog_node_id = <provider>:artist:<entity_id>
```

审核步骤：

1. 在 provider 中打开 Artist 页面，而不是只采用搜索结果第一项。
2. 核对页面显示名、别名、地区、代表作品或专辑。
3. 用至少两个区分字段确认是同一 Artist。
4. 检查个人、组合、同名艺人和 tribute/cover 页面混淆。
5. 保存 catalog 页面作为 identity evidence source。
6. 确认 `expected_catalog_node_id` 与 catalog reference 可机械拼接一致。

### 4.3 多语言与 Alias

- 华语 Artist 保存简体、繁体、英文名中实际使用且能确认的 aliases。
- 日本 Artist 保存日文名与官方罗马字，不自行翻译名字。
- 韩国 Artist 保存韩文名与官方英文/罗马字。
- 组合名和成员名不得互作 alias。
- Stage name 和 legal name 只有在权威来源明确关联时才能同时保存。

### 4.4 Identity 状态

| 情况 | knowledge_status | catalog_status | ambiguity_status | 能否 accepted |
|---|---|---|---|---:|
| 唯一匹配 | resolved | resolved | unambiguous | 是 |
| knowledge 名称不明确 | ambiguous | unresolved/ambiguous | name_collision | 否 |
| 找不到 catalog Artist | resolved | unresolved | unambiguous | 否 |
| 多个 catalog ID 都可能匹配 | resolved | ambiguous | provider_conflict | 否 |

无法唯一解析时不选择“最可能”的 ID，也不创建临时 catalog ID。

### 4.5 审核记录

每个 case 必须填写：

- `review.status`。
- `review.method=source_and_identity_review`。
- `relation_reviewer`。
- `identity_reviewer`。
- `reviewed_at`。
- `notes`。

Notes 至少说明：

- Relation 来源为何足以或不足以支持关系。
- Catalog identity 用哪些字段确认。
- Alias、同名或跨语言问题如何处理。
- rejected case 的具体阻断原因。

## 5. 第一批 10 Cases 的执行方案

### 5.1 准备产物

下一轮经确认后才创建：

```text
ai-service/evals/datasets/worldline_gold_v1.jsonl
ai-service/evals/fixtures/worldline_gold_v1_sources.jsonl
ai-service/evals/reports/worldline_gold_v1_candidate_review.md
ai-service/evals/reports/worldline_gold_v1_candidate_exclusions.jsonl
```

其中：

- Dataset 只包含最终 approved review 结果。
- Source Ledger 保存去重、冻结的来源上下文。
- Candidate review 是给审核人阅读的工作包。
- Exclusions 记录 unsupported genre、弱来源和不适合当前范围的候选，不进入指标。

### 5.2 执行批次

#### Batch A：现有 Knowledge 种子

- 审核周杰伦候选的 source provenance、exact span、Genre mapping 和 catalog identity。
- 只在原文明确支持 C-pop 时接受。
- 当前 claim review 状态不能代替 Gold owner approval。

#### Batch B：外部来源候选

- 分区域采集华语、欧美、日韩 fixture-only 来源。
- 每个候选先完成 relation review，再完成 identity review。
- 每个区域至少保留一个备用候选。

#### Batch C：负样例

- 从实际审核失败中选择一个 `no_direct_evidence` case。
- 从实际 catalog 核验失败中选择一个 ambiguous/missing identity case。
- 不人为编造错误事实作为负样例。

#### Batch D：冻结与回放

- 运行 Gold Loader validation。
- 运行 `--suite real-gold`。
- 记录 Dataset、Source Ledger、Registry 和 Git commit hash。
- 真实指标与 Synthetic Baseline 分开输出。

### 5.3 第一批验收标准

- 正好 10 个经过审核的 case。
- 建议 8 accepted、2 rejected；若实际审核结果不同，报告真实分布。
- 华语、欧美、日本、韩国都有 accepted 覆盖。
- 每个 accepted case 至少 1 条真实且直接的 evidence。
- 每个 accepted identity 都为 resolved + unambiguous。
- Accepted target genre 全部来自 Registry V2。
- 至少 3 个 executable Hip-Hop accepted case，便于形成 Genre → Artist 评测组。
- 100% exact span、offset、hash 和 ledger 引用通过校验。
- 0 个 pending case 进入正式 Dataset。
- 0 个 duplicate relation。
- 0 个未经人工审核的模型生成标签。

## 6. 后续扩展到 30 Cases

### 6.1 扩展节奏

采用两次增量，每次增加 10 条：

| 版本批次 | 累计数量 | 重点 |
|---|---:|---|
| Batch 1 | 10 | 验证真实数据流程、Schema 与审核成本 |
| Batch 2 | 20 | 扩充区域、Genre 和多语言 identity 覆盖 |
| Batch 3 | 30 | 增加困难 identity、负样例和来源多样性 |

每个批次使用新的候选审核记录和 freeze manifest；已经批准的 case 不原地静默改写。

### 6.2 30 Cases 建议分布

| 维度 | 建议目标 |
|---|---:|
| 华语 | 8–10 |
| 欧美 | 8–10 |
| 日本 | 4–5 |
| 韩国 | 4–5 |
| Accepted | 24–26 |
| Rejected | 4–6 |
| Hip-Hop accepted | 至少 8 |
| R&B accepted | 至少 4 |
| C-pop accepted | 至少 4 |
| Jazz / Jazz Hip-Hop accepted | 有可靠来源时逐步增加 |

### 6.3 扩展质量控制

- 每增加 10 条先审计来源类型分布，避免全部依赖同一网站。
- 控制单一 Artist、单一地区和单一 Genre 的占比。
- 为多来源 relation 记录独立来源数，不把转载当作交叉验证。
- 监控 production registry coverage 与 fixture-only coverage。
- 监控 catalog ambiguity、无证据拒绝和 unsupported genre exclusion 数量。
- 每批重新运行 Synthetic Baseline，确认 Gold 数据接入没有影响合同基线。
- Metrics 只在同一 Dataset freeze hash 内比较。

## 7. 本阶段停止点

本轮只完成真实数据准备计划，不生成候选 JSONL、Source Ledger 或任何 accepted Gold case。等待确认后，再按本计划进行来源采集、双人审核和第一批 10 条数据冻结。

# WorldLine Gold Dataset V1 实施计划

> 本文只设计真实来源审核的 WorldLine Gold Dataset。本阶段不生成 Gold 数据，不修改 WorldLine Graph、Semantic Expansion、Genre Executor、EntityBridge、RAG Pipeline、Retrieval、Embedding、Reranker、数据库结构或任何业务能力。

## 1. Dataset 目标

### 1.1 目标

建立 `worldline_gold_v1.jsonl`，以真实来源、可定位原文和明确身份映射评测以下能力：

1. `artist → genre / associated_with` Semantic Expansion。
2. 当前可执行 Genre 的 `genre → artist` Exploration。
3. Evidence Grounding 与 Citation 可追溯性。
4. knowledge artist → catalog artist 身份映射。
5. Real Gold 下的 relation precision / recall 和 artist precision / recall。

Gold V1 用于补充当前 `synthetic_contract_fixture`：

- Synthetic Baseline 继续验证代码合同、错误码、原子性和边界行为。
- Real Gold Baseline 验证真实音乐关系、真实证据和真实身份。
- 两套数据单独执行、单独出指标、单独报告，不合并分子或分母。

### 1.2 Dataset Grain

Gold V1 的一行表示一个 **Artist–Genre 关系假设**，主键为 `case_id`，业务唯一键为：

```text
(artist.knowledge_entity_id, semantic_relation.target_genre.canonical_id, relation_type)
```

同一关系的多个来源放入同一 case 的 `evidence[]`，不会为每条来源重复建立 relation case。Genre → Artist 评测由 approved 的 Artist → Genre case 按 Genre 反向编组，不另造事实标签。

### 1.3 第一版规模

建议先建立 30–36 个候选 Artist，经证据和身份审核后冻结 **24 个 approved Artist**：

| 区域 | 目标 approved Artist | 选择原则 |
|---|---:|---|
| 华语音乐 | 8 | 优先已有 Entity Registry 与知识发布中的 Artist |
| 欧美音乐 | 8 | 优先具有稳定官方/机构来源和明确 catalog identity 的 Artist |
| 日韩音乐 | 8 | 日本 4、韩国 4；优先具有稳定多语言身份和明确来源的 Artist |

最终只要求 20–30 个高质量 approved Artist。某一区域达不到证据门槛时，减少总量并明确覆盖缺口，不用弱来源凑数。

建议关系分布：

- 每个 approved Artist 至少 1 条 accepted relation。
- 目标 24–32 条 accepted relation。
- 另设 6–10 条 rejected case，覆盖 evidence、identity、genre registry 和 ambiguity 边界。
- Accepted target genre 只能来自当前受控 Registry：Hip-Hop、Jazz、Jazz Hip-Hop、R&B、C-pop。
- Genre Executor 正向 Gold 只使用当前 `executable=true` 的 `genre:hip_hop`；其他 Genre 只进入 Semantic Expansion 或 Action Gate 负样例。

### 1.4 当前可用数据盘点

基于当前项目冻结资产：

- Knowledge V2 release 有 1,947 个 chunk、79 条 claim，并声明 claim evidence 已验证。
- `has_genre` 共 8 条，其中 Artist 级 4 条，均属于同一个 Artist。
- Artist 级 `has_genre` 分布在 3 个 source document。
- 现有三版 Entity Registry 合并后约有 17 个 Artist，覆盖集中在华语音乐。
- 当前 WorldLine Genre Registry 只有 Hip-Hop、Jazz、Jazz Hip-Hop、R&B、C-pop。
- 当前冻结 claim 中的部分 genre 不在 WorldLine Genre Registry，不能作为 accepted WorldLine Gold target。
- Catalog reference 不是 Entity Registry 的静态字段，必须在 Gold 审核时单独核对并冻结 provider/entity ID。

结论：现有知识库可以提供首批华语候选和来源血缘，但无法单独满足欧美、日韩与 20–50 Artist 的范围。新增来源必须保存为 Evaluation fixture，不进入正式 RAG 或数据库。

## 2. Schema 设计

### 2.1 文件与版本

建议新增：

```text
ai-service/evals/datasets/worldline_gold_v1.jsonl
ai-service/evals/fixtures/worldline_gold_v1_sources.jsonl
ai-service/evals/config/worldline_gold_v1.json
```

Dataset 使用：

```text
schema_version = worldline-gold-v1
dataset_version = v1
dataset_kind = real_gold_fixture
```

当前 `WorldLineEvalCase` 把 schema 与 dataset version 固定为 V0，并且 runner 直接构建 synthetic fixture。V1 实施时应新增 Evaluation-only Gold schema 和 adapter，避免把真实来源字段塞入 V0 的自由字典后失去校验。

### 2.2 Case Schema

下面是示意结构，字段值必须在数据生成阶段由真实审核结果填充，不能使用示例占位值进入正式 Dataset：

```json
{
  "schema_version": "worldline-gold-v1",
  "dataset_version": "v1",
  "dataset_kind": "real_gold_fixture",
  "case_id": "wl-gold-v1-artist-genre-001",
  "region": "cjk_chinese",
  "tags": ["accepted", "artist_to_genre", "c_pop"],
  "artist": {
    "canonical_name": "<reviewed canonical name>",
    "knowledge_entity_id": "artist:<reviewed_slug>",
    "aliases": ["<reviewed alias>"],
    "registry_scope": "production_registry",
    "catalog_reference": {
      "provider": "netease",
      "entity_id": "<verified positive numeric id>",
      "entity_type": "artist",
      "name": "<provider display name>"
    }
  },
  "semantic_relation": {
    "source_entity_id": "artist:<reviewed_slug>",
    "target_genre": {
      "canonical_id": "genre:c_pop",
      "canonical_name": "C-pop",
      "registry_version": "v2"
    },
    "relation_type": "associated_with"
  },
  "evidence": [
    {
      "source_id": "<stable source id>",
      "source_title": "<source title>",
      "source_url": "<source URL or frozen local provenance>",
      "publisher": "<publisher>",
      "source_type": "official_or_editorial",
      "published_at": null,
      "accessed_at": "<ISO-8601 UTC>",
      "language": "zh",
      "exact_span": "<verbatim supporting span>",
      "evidence_text": "<bounded context containing exact_span>",
      "span_start": 0,
      "span_end": 0,
      "content_sha256": "sha256:<fixture content hash>",
      "fixture_source_id": "<source ledger id>"
    }
  ],
  "expected": {
    "outcome": "accepted",
    "reason": "direct_source_support",
    "runtime_relation": "associated_with",
    "runtime_rejection_reason": null
  },
  "identity": {
    "knowledge_status": "resolved",
    "catalog_status": "resolved",
    "ambiguity_status": "unambiguous",
    "expected_catalog_node_id": "netease:artist:<verified id>",
    "identity_evidence_source_ids": ["<identity source id>"]
  },
  "review": {
    "status": "approved",
    "method": "source_and_identity_review",
    "relation_reviewer": "<reviewer>",
    "identity_reviewer": "<reviewer>",
    "adjudicator": "<reviewer or null>",
    "reviewed_at": "<ISO-8601 UTC>",
    "notes": ""
  }
}
```

### 2.3 Required Fields

#### Artist

- `canonical_name`：审核后的统一名称。
- `knowledge_entity_id`：必须符合 `artist:<slug>`。
- `aliases`：只保存确认属于同一 Artist 的名称。
- `registry_scope`：`production_registry` 或 `fixture_only_registry`。
- `catalog_reference`：provider、entity ID、artist type、provider display name。

`fixture_only_registry` 用于欧美/日韩等尚未进入生产 Entity Registry 的真实 Artist。它只允许 Evaluation 构造隔离的 EntityRegistry，不表示当前生产链已能解析该 Artist。报告必须额外输出 production registry coverage。

#### Semantic Relation

- source 必须等于 Artist knowledge ID。
- relation 只允许 `associated_with`。
- target genre 必须来自冻结的 WorldLine Genre Registry 版本。
- Accepted case 不允许自由文本 Genre 或模型创建的 ID。

#### Evidence

- `source_id` 和 `fixture_source_id` 可稳定定位来源记录。
- `exact_span` 必须逐字存在于 `evidence_text`。
- `span_start/span_end` 必须精确切出 exact span。
- `content_sha256` 必须匹配冻结 fixture。
- `source_url` 或本地 release provenance 至少存在一个。
- 只保存支持判断所需的有限上下文，不复制整篇受版权保护的页面。

#### Expected

- `outcome`：`accepted` 或 `rejected`。
- `reason`：Gold 审核原因。
- `runtime_rejection_reason`：预期生产 Validator/EntityBridge 错误原因，可为空。

Gold 审核原因和运行时错误码分开保存。例如 `relation_not_explicit` 是审核判断，运行时可能表现为 `candidate_count=0`。

#### Identity

- `knowledge_status`：resolved/unresolved/ambiguous。
- `catalog_status`：resolved/unresolved/ambiguous。
- `ambiguity_status`：unambiguous/name_collision/provider_conflict。
- Accepted executable relation 必须 resolved + unambiguous。

### 2.4 Rejected Case Schema

Rejected case 仍保存 candidate relation 和审核过程，但 `evidence` 可以为空。必须记录拒绝原因：

- `no_direct_evidence`
- `relation_not_explicit`
- `exact_span_missing`
- `source_integrity_failed`
- `source_not_authoritative`
- `genre_not_registered`
- `artist_identity_ambiguous`
- `catalog_mapping_missing`
- `catalog_mapping_ambiguous`
- `duplicate_case`

“没有找到证据”不代表音乐关系为假。此类 case 只表示系统必须拒绝无证据扩图，不能用于事实层面的负样本 Precision 解释。

### 2.5 Source Ledger

`worldline_gold_v1_sources.jsonl` 保存去重后的来源 fixture：

- source ID、title、publisher、URL。
- access/publish time。
- language、region。
- bounded evidence context。
- content SHA256。
- 本地 Knowledge release 时保存 release ID、document ID、parent ID、child ID 和 manifest SHA256。
- 外部 fixture 保存 capture method 与 source class。

Dataset 只通过 `fixture_source_id` 引用 ledger，加载器同时检查 case 内 evidence 快照和 ledger 完全一致，防止来源被静默改写。

## 3. 审核规则

### 3.1 Accepted Relation Gate

每条 accepted relation 必须同时满足：

1. **真实来源**：来源可访问或可由项目冻结 release manifest 验证。
2. **直接支持**：exact span 明确把该 Artist 与该 Genre 关联，不能依赖模型常识。
3. **精确定位**：exact span 是 evidence text 的原样子串，offset 与 hash 正确。
4. **实体明确**：Artist canonical name、aliases、knowledge ID 和 catalog mapping 指向同一实体。
5. **Genre 受控**：target genre 存在于冻结 Registry，alias 映射经过人工确认。
6. **关系方向正确**：Gold 保存 Artist → Genre；Genre → Artist 只作为相同事实的执行视图。
7. **审核完成**：review status 为 approved；pending 不进入正式指标。

任何一项失败都不能标为 accepted。

### 3.2 Source Authority

来源优先级：

1. 已冻结项目 Knowledge release 中具有验证链路的原始文档。
2. Artist、唱片公司、发行方、音乐节、奖项机构等官方页面。
3. 具有编辑责任的音乐百科、音乐媒体或机构资料页。
4. 公开知识数据库用于身份和外部 ID 交叉核对。
5. 通用百科可作为候选发现或辅助证据；若用于关系 Gold，应有明确原句，且建议增加第二个独立来源。

社交媒体片段、搜索摘要、模型回答、无作者聚合页和无法定位原文的二手转述不能作为唯一 accepted evidence。

一条权威且直接的来源可以通过；普通二手来源建议至少两个相互独立的来源。多个转载同一原稿只算一个来源。

### 3.3 Evidence Review

审核者逐项检查：

- Artist 名称是否出现在或由页面上下文唯一确定。
- Genre 是否直接出现，或是否通过受控 alias 一一映射。
- 句子是否描述 Artist 本身，而不是某一首歌曲、专辑、合作对象或影响来源。
- “融合某元素”“受某风格影响”不能自动等同于 Artist `associated_with`，需按审核 rubric 决定。
- exact span 是否被截断到失去限定语或否定词。
- evidence text 是否保留判断所需上下文。
- 来源 hash、offset 和 URL 是否一致。

### 3.4 Identity Review

Identity 审核与 Relation 审核分开：

1. 用 canonical name 与 aliases 核对 knowledge identity。
2. 用 Artist 页面信息核对 catalog provider ID，而不是只按搜索结果排名选择。
3. 同名 Artist 必须核对地区、作品、别名或其他区分字段。
4. 多个 catalog ID 都可能匹配时标记 ambiguous，不选一个“最像的”。
5. Catalog mapping 无法确认时 relation 可以保留为来源候选，但不能进入 executable accepted case。
6. fixture-only knowledge ID 不写入生产 Registry。

### 3.5 Review Workflow

```text
Candidate discovery
  → source capture and hashing
  → relation review
  → identity/catalog review
  → automated schema/provenance checks
  → conflict adjudication
  → owner approval
  → freeze dataset SHA256
```

- Candidate discovery 不读取 WorldLine 本次生成结果，避免从预测反推 Gold。
- AI 可以准备候选和审核包，但正式 case 在 owner 确认前保持 `pending`。
- Relation reviewer 与 Identity reviewer 可以是同一负责人，但两个决策字段都必须显式填写。
- 来源冲突或同名歧义进入 adjudication，不进入正式分母。
- 冻结后只允许新版本追加或纠错，不原地静默改写 V1。

## 4. Case 选择策略

### 4.1 阶段一：现有知识库候选

从 Knowledge V2 release 只读抽取：

- subject 为 `artist:*`。
- predicate 为 `has_genre`。
- verification status 为 supported。
- evidence span 可在冻结 parent/child 中逐字定位。
- object genre 存在于当前 WorldLine Genre Registry。

当前盘点显示这一步只能产生很少量华语候选，因此它是高置信度种子，不是完整 Gold V1。

现有 Entity Registry 中其他 Artist 可进入候选池，但没有直接 genre evidence 时不能仅凭已注册身份成为 accepted relation。

### 4.2 阶段二：区域补齐

对欧美和日韩 Artist 使用 fixture-only 来源采集：

- 先按来源稳定性和 catalog identity 可验证性筛选 Artist。
- 再找直接 Artist–Genre 陈述，不从预设 Genre 倒推需要的证据。
- 如果只能找到作品级 Genre，不提升为 Artist 级关系。
- 对日韩 Artist 同时保存本地文字、拉丁转写和常用英文别名。
- 对欧美同名或团体/个人重名重点增加 identity evidence。

候选池建议为每个区域 10–12 个，预计审核后保留约 8 个。不要预先承诺某个具体 Artist 一定进入 Gold。

### 4.3 Genre 分层

目标是覆盖当前受控词表，而不是平均凑数：

- Hip-Hop：同时支持 Semantic Expansion 和 Genre Executor Real Gold。
- Jazz、Jazz Hip-Hop、R&B、C-pop：支持 Semantic Expansion Real Gold。
- Disabled Genre 的 branch 继续作为 Action Gate 负样例。
- 某 Genre 若没有足够权威的 Artist 级来源，报告覆盖缺口，不降低证据标准。

### 4.4 正反样例

正样例：

- 来源直接支持、identity resolved、genre registered。
- exact span 与 provenance 完整。

拒绝样例：

- 真实页面提到 Genre，但主体是 Song/Album 而不是 Artist。
- 页面只出现模糊“受影响”或相邻文本，不能直接支持关系。
- Artist 同名导致 catalog mapping ambiguous。
- Genre 名称不能映射受控 Registry。
- 来源 URL 存在但 exact span 无法在冻结内容定位。

Rejected case 应来自真实审核中出现的边界，不为达到数量人工制造错误事实。

### 4.5 防止数据泄漏

- Gold 创建程序不调用 WorldLine runner 获取 expected 值。
- Gold 文件不保存 `actual`、trace 或模型输出。
- 审核包先冻结 expected 和 evidence，再运行 Agent。
- Dataset SHA256、source ledger SHA256、Git commit 和 Registry version 写入 run metadata。
- 同一 source span 不跨 train/tuning/eval 用途重复使用；当前 V1 只作为 Eval Gold，不用于 Prompt 或阈值调优。

## 5. Evaluation 接入方案

### 5.1 当前 Framework 缺口

当前 Evaluation V0：

- `schema_version` 和 `dataset_version` 固定为 V0。
- Label provenance 尚无 `real_gold_fixture`。
- Runner 默认使用 synthetic fixture builder。
- Run metadata 没有 dataset kind。
- Metrics 可以复用，但当前按一个 results list 聚合，直接拼接会混合分母。

V1 开发只修改 `ai-service/evals/` 和测试，不修改业务代码。

### 5.2 推荐接入结构

新增 Evaluation-only 组件：

```text
WorldLineGoldCaseV1 loader
  → source ledger integrity validator
  → real-gold fixture adapter
  → existing SemanticExpansion / GenreExecutor boundaries
  → WorldLineEvalResult
  → existing deterministic metrics
```

建议文件：

| 文件 | 作用 |
|---|---|
| `evals/worldline_gold_v1_models.py` | 强类型 Gold schema、review 和 source ledger models |
| `evals/worldline_gold_v1_loader.py` | JSONL、hash、referential integrity、exact span 校验 |
| `evals/worldline_gold_v1_adapter.py` | 将 approved Gold 转为隔离的 MusicKnowledgeDocument、EntityRegistry 和 catalog candidates |
| `evals/config/worldline_gold_v1.json` | Real Gold 输入与独立报告路径 |
| `evals/datasets/worldline_gold_v1.jsonl` | 真实审核 Gold |
| `evals/fixtures/worldline_gold_v1_sources.jsonl` | 冻结来源 ledger |

不要把 Gold schema 退化为现有 `input/fixtures/expected` 自由字典；否则身份与 provenance 缺失无法在加载阶段阻断。

### 5.3 双模式执行

CLI 建议支持：

```powershell
python run_worldline_eval.py --suite synthetic
python run_worldline_eval.py --suite real-gold
python run_worldline_eval.py --suite all
```

`all` 只负责连续执行两个 suite，输出结构必须为：

```json
{
  "synthetic_baseline": {"metrics": {}},
  "real_gold_baseline": {"metrics": {}}
}
```

禁止先合并 results 再调用 Metrics。报告可以并排展示，但不生成混合“总体 Precision”。

建议独立输出：

```text
evals/reports/worldline_eval_v0_*
evals/reports/worldline_gold_v1_results.jsonl
evals/reports/worldline_gold_v1_metrics.json
evals/reports/worldline_gold_v1_failures.jsonl
evals/reports/worldline_gold_v1_report.md
```

### 5.4 Real Gold Replay

#### Semantic Expansion

Adapter 将 approved case 确定性转换为：

- `ResolvedEntity`。
- `SearchPlan` 中冻结的 knowledge/catalog reference。
- 仅包含审核过 supported claim 的 `MusicKnowledgeDocument`。
- 受控 Genre Registry。

然后复用现有 Candidate → Validator → Graph Projection。这个 adapter 只服务 Evaluation，不写正式 Knowledge Base。

#### Genre Exploration

- 只选 `genre:hip_hop` accepted Gold case。
- 按 Genre 聚合 Gold Artist 集合。
- 使用审核 evidence 构造 executor input document。
- catalog candidate 只来自 case 中已审核的 catalog reference。
- 计算 Genre Expansion Success、Artist Precision 和 Recall。

其他 Genre 的 executor 指标为 N/A，不把 disabled 状态记为失败。

#### Identity

Real Gold 同时输出：

- `catalog_identity_accuracy`：对审核 catalog mapping 的准确率。
- `production_registry_coverage`：Gold Artist 当前是否已存在生产 Entity Registry。

fixture-only Artist 可用于隔离评测 EntityBridge 算法，但报告必须明确其当前不代表生产可执行覆盖。

### 5.5 Metrics

复用当前指标，Real Gold 单独计算：

- Semantic Relation Precision。
- Semantic Relation Recall。
- Grounded Relation Precision。
- Evidence Provenance Validity。
- Catalog Identity Accuracy。
- Genre Expansion Success Rate。
- Genre Artist Precision / Recall。

补充 Dataset 质量统计，不与模型质量混合：

- approved/pending/rejected case count。
- unique Artist count 和区域覆盖。
- source class 分布。
- single-source / multi-source relation 数量。
- production registry coverage。
- catalog ambiguous/unresolved rate。
- exact span integrity rate。

### 5.6 报告格式

同一 Markdown 报告中分成两个独立章节：

```text
Synthetic Baseline
  - contract and failure behavior

Real Gold Baseline
  - real relation and identity quality
  - regional/source coverage
  - pending and excluded cases
```

标题、表格、JSON 路径和 Failure Audit 都必须带 `dataset_kind`，避免被下游误聚合。

## 6. 自动验证与验收

### 6.1 Dataset Schema Validation

- 必填字段非空。
- ID、URL、时间和枚举格式正确。
- accepted case 至少 1 条 evidence。
- accepted Artist catalog/knowledge status 均为 resolved。
- target genre 存在于冻结 Registry。
- relation type 只能为 associated_with。

### 6.2 Evidence Exact Span Validation

- `evidence_text[span_start:span_end] == exact_span`。
- exact span 在 evidence text 中只出现一次；多次出现时必须增加 disambiguating offset/context。
- fixture content SHA256 匹配。
- source ledger 与 case evidence 快照一致。
- 本地 Knowledge source 的 document/parent/child ID 可在冻结 release 中解析。

不对 exact span 做 Unicode 归一化后再比较；Gold 必须保存来源原文。归一化只用于 alias duplicate 检查。

### 6.3 Duplicate Detection

- `case_id` 全局唯一。
- accepted composite relation key 唯一。
- source ID 唯一且相同 ID payload 完全一致。
- aliases 经项目现有 normalization 后无重复或跨 Artist 冲突。
- 相同 URL/内容 hash 的转载来源不重复计为独立 corroboration。

### 6.4 Missing Identity Detection

- knowledge ID 不存在或格式非法时拒绝加载。
- accepted case 缺 catalog reference 时拒绝加载。
- catalog provider/type/ID 与 expected node ID 不一致时拒绝加载。
- ambiguous identity 不能标为 accepted。
- fixture-only registry 必须显式标记，不能伪装成 production registry。

### 6.5 Synthetic / Gold 分离测试

- Synthetic runner 只读取 V0 case。
- Real Gold runner 只读取 V1 approved case。
- `--suite all` 生成两个独立 Metrics 对象。
- 修改 Synthetic case 不改变 Real Gold 分母。
- pending Gold 不进入正式指标，但在 coverage report 中计数。
- output path 和 dataset hash 不互相覆盖。

### 6.6 建议测试文件

```text
evals/tests/test_worldline_gold_v1_schema.py
evals/tests/test_worldline_gold_v1_evidence.py
evals/tests/test_worldline_gold_v1_identity.py
evals/tests/test_worldline_gold_v1_duplicates.py
evals/tests/test_worldline_gold_v1_runner.py
evals/tests/test_worldline_eval_suite_isolation.py
```

## 7. 实施步骤

确认本计划后，按以下顺序生成真实数据：

1. 新增 Gold V1 typed schema、source ledger schema 和 validator。
2. 从 Knowledge V2 release 生成华语候选审核包，不直接批准。
3. 建立欧美、日韩候选清单并只读采集 fixture-only 来源。
4. 完成 relation evidence review 和 catalog identity review。
5. 对冲突、同名和弱来源 case 做 adjudication。
6. 由 owner 确认 approved case，冻结 Dataset 与 Source Ledger SHA256。
7. 实现 Evaluation-only adapter 和双 suite runner。
8. 运行 Synthetic Baseline，确认 V0 不变。
9. 运行 Real Gold Baseline，单独输出 Metrics 和 Failure Audit。
10. 运行全部 Evaluation 与 WorldLine 回归。

## 8. 验收标准

Gold V1 冻结前必须满足：

- 20–30 个 approved、identity 明确的真实 Artist。
- 华语、欧美、日韩均有覆盖；缺口有明确说明。
- 100% accepted relation 有真实来源和可定位 exact span。
- 100% accepted relation target genre 来自受控 Registry。
- 100% accepted executable case catalog identity resolved 且 unambiguous。
- 100% fixture/source hash 校验通过。
- 0 个重复 case ID 或重复 relation composite key。
- 0 个 pending case 进入正式 Metrics。
- Synthetic 与 Real Gold 指标完全隔离。
- 生产 WorldLine、RAG、数据库和业务配置零修改。

## 9. 风险与控制

| 风险 | 影响 | 控制措施 |
|---|---|---|
| 现有 Knowledge 只覆盖少量 Artist–Genre | 无法满足区域与数量 | 使用外部 fixture-only 来源，单独记录 provenance |
| 把 Song/Album Genre 提升为 Artist Genre | 产生错误 Gold | 审核主体必须是 Artist，作品级关系直接拒绝 |
| 同名 Artist catalog 映射错误 | Identity 指标失真 | 单独 Identity review；歧义不强行 resolved |
| 搜索摘要或模型常识进入 Gold | Grounding 失真 | 只接受可定位原文；Candidate discovery 与 Agent 输出隔离 |
| 二手来源互相转载 | 伪多来源确认 | URL、publisher、content hash 去重，追踪共同原稿 |
| Fixture-only Artist 被误认为生产可执行 | 夸大成熟度 | 输出 production registry coverage 并标记 registry scope |
| Synthetic 与 Real Gold 混算 | 指标不可解释 | 分 config、分 result、分 metric、分报告章节 |
| 版权内容过量复制 | 不必要的来源风险 | 只保存支持判断所需的有限上下文和 exact span |

本阶段到此停止。等待确认后再生成候选、来源 fixture、审核包和正式 `worldline_gold_v1.jsonl`。

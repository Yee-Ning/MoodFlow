# WorldLine V2.4B Scene Relation 实施报告

日期：2026-09-25

## 修改内容

- 在现有 WorldLine `build_graph` 节点中，于 Era 投影之后接入 Scene Candidate → Validator → Graph Projection；只有明确询问音乐 Scene／场景／社群等意图时才运行。
- 增加 `scene_relations` 到原有 `WorldLineResult` 和会话状态；旧 checkpoint 缺少该字段时默认空列表。后续 song／artist／album 与现有 genre 探索保留 Scene graph、source 和 DTO。
- 扩充受控 Scene claim／graph relation 枚举；没有修改检索、ContextPack、Embedding、Reranker、LLM JSON repair、数据库或现有图的容量约束。
- 前端复用 V2.3C 图谱和证据面板，区分 Scene 节点，显示 Scene 名称、可验证地点、时间精度、置信度、冲突及原文来源。Scene 节点仍不可执行。
- 新增 Scene 专项测试、子图和 checkpoint 会话集成测试，以及前端图／证据面板断言。

主要文件：`ai-service/app/agent/foundation/subgraphs/worldline/scene_expansion.py`、`models.py`、`graph.py`、`session.py`、`state.py`、`builder.py`、`entity_resolver.py`、受控 `ClaimPredicate`、相关测试，以及 `soul-music-room/src` 的最小 Scene 展示接入。

## 架构与数据流

```text
明确 Scene 查询 → 现有 Entity Resolution / Query Understanding / SearchPlan
  → 原有 Music API + RAG retrieval / ContextPack
  → 原有 WorldLine graph + semantic / Era projection
  → supported_claims → Scene Candidate → Evidence / Entity / Scene Validator
  → Scene Graph Projection → 原有 graph/source merge
  → 原有 Narrative / Branch structured-output 链路
  → WorldLineResult.scene_relations + graph + sources/citations
  → 原有 session checkpoint → 前端图／证据面板
```

Scene 层不调用额外 LLM；候选只能来自检索结果里已验证的 `supported_claims`。artist↔artist 的 Scene association 可以由**同一 Scene 下两条分别验证的 artist membership**合成，边同时引用两条证据；仅同城市、年代或风格绝不触发该合成。若同一艺人对涉及多个共同 Scene，保留各自的 Scene membership，不把它们压成一个含糊的 artist↔artist 边。

## SceneRelation schema 与 relation types

`SceneRelation` 包含 `source_entity`、`target_entity` 或 `scene`、受控 `relation_type`、`scene_name`、可空 `location`、可空 `start_year/end_year`、`temporal_precision`（exact/approximate/unknown）、`confidence`、逐条 `evidence` 和 `metadata`。受控关系仅有：

| relation_type | 投影方向与证据要求 |
| --- | --- |
| `belongs_to_scene` | artist/song/album → scene；需明确成员／属于 Scene 表述 |
| `representative_of_scene` | scene → artist/song/album；需明确代表性表述 |
| `scene_association` | artist → artist；需明确具名 Scene 的直接 claim，或两条独立验证的共同 Scene membership |
| `style_scene_association` | genre/style 节点 → scene；需明确流派／风格与具名 Scene 的关系 |
| `emerged_from_scene` | artist/song/album → scene；需明确从 Scene 兴起／发源的表述 |

Scene ID 从受支持的具名 Scene 确定性生成；非 ASCII 名称带内容哈希以避免不同地域中文名映射到同一 ID。Scene 名称／别名尚无独立 registry，因此不同写法不会自动合并。

## Evidence 与 validation 规则

- 候选必须具有 `document_id → parent/chunk_id → supported claim_id → exact_span`；span 必须确实出现在返回文档，ContextPack sufficiency 为 `sufficient`，claim 字段与候选一致。
- span 必须同时出现具名 Scene、对应实体名称和明确的 scene/community/movement／场景／社群语义；只有地点、风格、年代、共同提及或否定句均拒绝。关系还需匹配对应的成员、代表、风格、来源或共同关联措辞。
- 需要现有图中可核对的实体 ID 和合法端点类型。置信度门槛为 0.85；无法确认则不扩图。
- `location` 只接受已在 `supported_claims` 中结构化提供、且原文 span 同时出现的地点；现有索引投影通常不含此扩展字段，因此生产路径多数返回 null，不从 Scene 名称猜地点。时间必须与 claim 和 span 一致，约略时间标记 approximate。
- 多来源年份或地点冲突时汇总值为 null，降低 confidence，并在 `metadata` 保留各条原始断言及 evidence ID。图节点、边、每边 evidence 或 source 达到原有上限时跳过新增关系并记录 trace，基础图保持可用。
- 每条 Scene edge 的 `evidence_ids` 必须对应 `WorldLineResult.sources`；DTO 也验证端点、图边及来源一致。前端只展示后端返回的 span 和来源。

## 测试结果与核心指标

| 验证 | 结果 |
| --- | --- |
| Scene 专项单元及边界测试（最终代码） | 22 passed |
| Scene、实体解析与 WorldLine 子图定向测试（最终代码） | 48 passed |
| WorldLine 相关子图、session、API、Era／genre 回归（本阶段） | 87 passed；后续仅新增 Scene 边界测试，由上述 48 项重新验证 |
| 后端完整测试 | 992 passed、14 skipped、206 subtests passed；后续新增的 Scene DTO 断言由上述 22 项验证 |
| Ruff（修改范围） | 通过 |
| `npm run test:worldline` | flow / view 通过 |
| `npm run build` | 通过；既有大 bundle 警告仍在 |
| `npm run lint` | 通过 |
| 浏览器窄屏视觉检查 | V2.4B WorldLine 入口正常显示；Scene 图和证据面板由前端 SSR 视图测试验证 |

自动化样例覆盖明确 Scene 关系、artist/song/album → scene、scene → representative、style → scene、artist↔artist、跨地域／时期、年份和地点冲突、约略时间、弱证据／否定、非 Scene 查询、容量限制及 checkpoint 恢复。测试断言所有生成边都有可映射 source；被拒绝样例不扩图。**当前没有审核过的 Scene 专项真实 Gold 分母，不能将合成测试通过率当作真实 relation precision、evidence coverage 或 hallucination rate。**

## 已知问题与 V2.4B Feature Acceptance

- 现有已建索引可能没有 Scene 专用 `supported_claims`；没有足够证据时按设计返回原图，不会为了覆盖率生成 Scene。新增受控谓词可供现有结构化 claim 管线在后续知识资料审核／索引更新时使用，本阶段没有重跑或调整 RAG。
- `location` 是保守的可空字段；现有 claim 投影不提供独立地点属性时不会从地名外观推断。Scene 别名归一和真实 Gold 质量测量尚未覆盖。
- Scene 节点仅展示证据，不提供 Scene branch executor。Docker／Production E2E 按本轮要求不处理。
- **Feature Acceptance 判断：受控 schema、证据门槛、图投影、API／session／前端契约与自动化回归通过后，可验收 V2.4B 的代码闭环；真实世界 precision 仍需 Scene Gold 和线上证据覆盖评估。**

## V2.4C Influence Relation 接口边界

后续可复用 `WorldLineGraph`、`EvidenceSource`、ContextPack、`supported_claims`、现有 merge/checkpoint、`WorldLineResult` 扩展字段与前端证据面板。Influence 应有独立受控 DTO、方向和因果证据验证；**不能**把共同 Scene、地域、风格或时代关联自动升级为影响关系。V2.4B 没有实现 Influence 谓词、投影、executor、API 动作或前端交互。

# WorldLine V2.4A Era Relation 实施报告

日期：2026-09-25

## 修改内容

- 在原有 WorldLine `build_graph` 节点中接入独立的 Era Candidate → Validator → Graph Projection 流程；只在明确询问时代、时期、年代或职业阶段且 SearchPlan 为 timeline 时执行。普通请求仍走原链路。
- 增加受控 Era relation DTO，并将 `era_relations` 加入现有 `WorldLineResult`。旧 checkpoint 缺少该字段时按空列表恢复；会话后续探索保留原有 Era graph、sources 和 DTO。
- 从现有 ContextPack 文档的 `supported_claims` 提取候选；验证文档唯一性、ContextPack 充分性、原文 span、claim 与时间字段、实体类型和置信度。未验证的 `graph_edges` 元数据无法直接生成 Era 边。
- 前端在既有图上区分 Era 节点，在证据面板显示后端返回的时间精度、置信度、冲突及原文证据。Era 节点保持不可执行；没有开放新的 branch executor。
- 新增 Era 专项单元与子图／会话集成测试，以及前端 SSR 视图断言。

主要文件：`ai-service/app/agent/foundation/subgraphs/worldline/{era_expansion,models,graph,builder,session,state,entity_resolver}.py`、`ai-service/app/rag/knowledge/claims/models.py`、`ai-service/app/rag/query_understanding/analyzer.py`、相关测试，以及 `soul-music-room/src/{types,components/worldline,pages}` 中的最小展示接入。

## 架构与数据流

```text
用户 Era 查询 → 原有实体解析和 Query Understanding / SearchPlan
  → 原有 Music API 与 RAG（Qdrant 检索、ContextPack）
  → 原有 catalog graph 与 artist→genre semantic projection
  → supported_claims → Era Candidate → Evidence/Entity/Time Validator
  → Era Graph Projection → 原有 graph/source merge
  → 原有 Narrative / Branch structured-output 链路
  → WorldLineResult.era_relations + graph + sources/citations
  → 原有 session checkpoint 与前端图／证据面板
```

Era 层不调用额外 LLM，不直接从模型常识生成关系。已有结构化生成及其校验／修复路径继续负责 narrative 和 branch。Era 的 source ID 关联 claim、retrieved document、chunk 和 exact span；Graph edge 的 `evidence_ids` 指向这些 source。`released` 证据只投影为相应年代的**发行活动**，`debuted` 只投影为 **Early career**；两者均不能推断“代表艺人”或“巅峰期”。

## Schema 与受控 relation types

`EraRelation` 包含 `source_entity`、`target_entity` 或 `era`、`relation_type`、可空 `start_year/end_year`、`temporal_precision`（exact/approximate/unknown）、`confidence`、逐条 `evidence` 和 `metadata`。受控类型仅有：

| relation_type | 允许的含义 |
| --- | --- |
| `belongs_to_era` | song/artist/album 与有证据的时代或年代活动 |
| `representative_of_era` | Era → 代表 song/artist/album，要求明确的“代表”证据 |
| `temporal_association` | 两个已有实体的同期关联，要求明确时间与关联证据 |
| `career_phase` | artist → Early/Breakthrough/Peak/Late 阶段，要求相应阶段证据 |
| `style_era_association` | 已有 genre/style 节点与时代的发展关联 |

多来源时间不一致时，关系的汇总年份设为 null、precision 设为 unknown、降低 confidence；各来源的原始年份和 span 保留在 `evidence` 与 `temporal_assertions`。约略年份保留 approximate 标记。图、边和来源容量达到既有上限时跳过新增 Era 投影并记录 trace，不丢失基础图。

## 数据层与 migration

**无 migration。** 会话复用现有 PostgreSQL LangGraph checkpointer；新增 DTO 作为现有结果／checkpoint 状态的一部分持久化。没有新建表、知识图谱或 pgvector 结构。知识检索仍使用原有 RAG/Qdrant 路径，未修改 retrieval、reranker、embedding 或参数。

## 测试结果与核心指标

| 验证 | 结果 |
| --- | --- |
| Era、实体解析、WorldLine 子图／session／API／generation 定向测试 | 68 passed |
| 后端完整回归（本阶段早期版本） | 965 passed、14 skipped、206 subtests passed；之后的容量和解析小修由上述 68 项重新验证 |
| Ruff（改动范围） | 通过 |
| `npm run test:worldline` | flow / view 通过 |
| `npm run build` | 通过；仍有既有大 bundle 提示 |
| `npm run lint` | 通过 |
| 浏览器响应式检查 | 本地 Vite 的窄屏 WorldLine 入口正常显示；Era 关系渲染由前端 SSR 测试覆盖 |

在合成代表性用例中，明确年份、跨年代阶段、约略年份、年份冲突、缺证据、原文不支持、否定表述、类型约束和普通查询回归均通过。所有测试中生成的 Era edge 均有可映射的后端 source（断言覆盖）；无证据／否定用例没有扩图。**真实世界 relation precision、evidence coverage 与 unsupported/hallucination rate 尚无 Era 专项 Gold 分母，不能把上述测试通过率当作生产质量指标。** 当前系统不具备可报告的真实 Era precision 数值。

## 已知问题与 V2.4A 验收

- 现有知识库的 `supported_claims` 主要是既有关系；Era 专用谓词的真实覆盖尚未经过资料审核。现有已验证的 `released`／`debuted` 可以保守投影，但缺少可靠证据时结果为空。不能为覆盖率填补无证据关系。
- 没有连通的生产 PostgreSQL + Qdrant + Music API 环境 E2E；checkpoint 连续性由会话集成测试验证，服务重启后的生产数据库恢复仍需上线环境复核。
- Era 节点可展示证据，但不可点击执行 Era → entity 探索。已有 narrative 可能描述 Era graph，但没有针对 Era 的专门文案质量 Gold。
- 当前无 Era 专项真实 Gold，故**功能与契约层通过 V2.4A 验证；真实 relation precision、evidence coverage 和生产重启恢复尚不能宣称完成验收**。建议先建立审核过的 Era 正反样例，再评估是否开放更多生成或探索能力。

## V2.4B Scene Relation 接口边界

V2.4B 可复用现有的 `WorldLineGraph`、`EvidenceSource`、`supported_claims`、ContextPack、merge、session checkpoint 与前端证据面板协议；应定义独立的受控 Scene relation DTO、证据候选／验证／投影模块，并在现有 `build_graph` 的扩展入口按明确 Scene 查询接入。Scene 的实体解析、证据门槛和动作执行需单独设计和评测。V2.4A **没有**新增 Scene 谓词、节点执行器、API 或前端动作，也没有实现 Influence Relation。

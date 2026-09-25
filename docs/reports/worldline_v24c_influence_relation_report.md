# WorldLine V2.4C Influence Relation 实施报告

## 修改内容

- 在现有 WorldLine 节点构图阶段加入 Influence Candidate → Evidence / Entity / Direction Validator → Graph Projection；没有新增 LangGraph 节点、检索链、图系统或数据库表。
- 新增受控 InfluenceRelation DTO 与 `WorldLineResult.influence_relations`；历史 checkpoint 缺少该字段时按 `[]` 解析。会话扩展与 Genre turn 保留旧关系、图边和来源。
- 阻止目录事实和未验证的 `graph_edges` 元数据绕过 Influence validator 写入影响边。
- 前端现有 SVG 图谱用独立线条和箭头显示 Influence；证据面板显示影响方向、scope、confidence、claim 原文及 document/chunk/claim provenance。不新增 Influence branch executor。

## 架构与数据流

```text
用户明确询问 Influence
  → 现有 Query Understanding / SearchPlan / Retrieval / ContextPack
  → supported_claims
  → InfluenceCandidateExtractor
  → InfluenceValidator（文档、chunk、claim、exact span、实体、方向、否定、弱推测）
  → InfluenceGraphProjector（现有 WorldLineGraph + EvidenceSource + merge）
  → WorldLineResult.influence_relations
  → 现有 narrative / branch / API / checkpoint / evidence panel
```

普通 WorldLine、Era、Scene 查询不触发 Influence 投影。候选仅来自 `supported_claims`；无有效证据或方向不确定时返回空列表并保留基础图。候选实体必须同时能由已有 SearchPlan / graph 身份映射到现有节点。

## Schema、关系类型与方向

`InfluenceRelation` 包含 `source_entity`、`target_entity`、受控 `relation_type`、固定 `direction=influencer_to_influenced`、可空 `influence_scope`、`confidence`、逐条 `evidence` 和 `metadata`。

受控关系类型为 `influenced`、`inspired`、`shaped_style`、`cited_as_influence`。`influenced_by` / `inspired_by` 仅作为 supported claim 的输入方向词，输出统一为 influencer → influenced。历史 `WorldLineRelation.INFLUENCED_BY` 保留枚举兼容，但新扩展不会投影这种反向边。

Scope 受控于 sound、style、songwriting、production、career。证据记录 `source_id`、`claim_id`、`exact_span`、confidence、证据强度与 support/contradiction。来源元数据保留 document ID、parent ID、child chunk ID、claim ID、source chunk ID、ContextPack 版本和检索版本。

## Evidence、方向与冲突规则

- 必须有充分 ContextPack、存在且唯一的检索文档、文档内可定位的 exact span、与 supported claim 完全一致的 subject/object/predicate/source chunk、达到阈值的 claim confidence，且正则方向校验明确。
- 被动语态、`inspired by`、`cited X as an influence` 等按原文解析到统一方向；反向或双向不明直接拒绝。
- 同 Scene、Era、Genre、地域、相似风格、合作、共同出现、时间先后不会生成 Influence。否定关系拒绝；`may/possibly/可能` 等推测语气降低候选 confidence 并拒绝投影。
- 同一关系的不同 scope 或相反归因保留逐条 provenance，汇总 scope 置空并降低 confidence。双向来源各自保留为带证据的关系，标记 direction conflict 并降低 confidence，不合并成虚构的单一结论。
- Graph edge/source 容量达到现有限制时跳过本轮 Influence 关系，不破坏基础图。合并与 checkpoint 写入仍使用现有原子提交路径。

## 修改文件

- 后端：`ai-service/app/agent/foundation/subgraphs/worldline/` 下的 `influence_expansion.py`、`models.py`、`builder.py`、`graph.py`、`state.py`、`session.py`、`entity_resolver.py`；`ai-service/app/rag/knowledge/claims/models.py` 仅补受控 claim predicate，不改检索、重排或 embedding 流程。
- 前端：`soul-music-room/src/types/worldline.ts`、`src/components/worldline/WorldLineGraph.tsx`、`WorldLineEvidencePanel.tsx`、`src/pages/WorldLinePage.tsx`、`WorldLinePage.css`。
- 测试：`ai-service/tests/test_worldline_influence_expansion.py`、`test_worldline_graph.py`、`test_worldline_entity_resolution.py`、`soul-music-room/scripts/test-worldline-view.mjs`。
- Migration：无。数据库 schema 未改动。

## 测试与核心指标

| 检查 | 结果 |
| --- | --- |
| Influence 定向、证据、否定、弱推测、冲突、容量与身份契约 | 自动化 fixture 全部通过 |
| WorldLine 运行时结果、非 Influence 路由、checkpoint 恢复与继续探索 | 自动化集成测试通过 |
| Era / Scene 定向回归 | `pytest` 相关五组测试：98 passed |
| 后端完整回归 | `OPENBLAS_NUM_THREADS=1 python -m pytest tests -q`：1024 passed、14 skipped、206 subtests passed |
| Python 静态检查 | `ruff check` 本阶段改动文件：通过 |
| 前端 WorldLine flow / view、TypeScript 构建、ESLint | `npm run test:worldline`、`npm run build`、`npm run lint`：通过 |
| 本地响应式页面外壳 | 1440 px 和 390 px 浏览器检查；390 px 无横向溢出 |

定向正确率、关系 precision、evidence coverage 和 unsupported suppression 目前只有确定性测试 fixture 断言，**没有真实 Influence Gold Dataset 的统计估计**；不能把这些通过率解释为生产质量指标。新增投影边的 evidence ID 均在测试中验证可解析到后端 `sources`。

## 已知问题与验收结论

- 方向验证刻意保守，未覆盖所有自然语言表达；未命中时跳过扩图，可能降低召回。
- Influence 只连接当前图中已有且身份可映射的 artist/song/album/genre 节点；不凭证据自由创造新实体。Style 暂复用已有 genre 类型，没有独立 style 节点。
- 本轮没有真实 Influence Gold 或生产 API/LLM 服务，因此不能给出真实 relation precision、hallucination rate 或完整带数据的响应式视觉验收；Docker / Production E2E 按本阶段范围未运行。
- 前端 build 保留现有大 chunk 提示；本阶段未调整打包结构。
- V2.4C 的功能级验收已满足：统一方向、证据追溯、保守拒绝、session 保留、前端最小展示及 Era/Scene 回归。生产质量验收需下一阶段专项评测。

## 后续 Semantic Relations Evaluation 接口

评测输入应复用现有 `supported_claims` / ContextPack、WorldLine 查询和 checkpoint；输出采集 `InfluenceExpansionTrace`、`influence_relations`、graph edge、`sources` 与 session 恢复结果。重点评测 direction accuracy、relation precision、evidence traceability/coverage、否定与弱证据拒绝、scope/attribution 冲突、capacity skip、非 Influence 查询隔离和长期 checkpoint 连续性。评测数据应包含明确正反例及人工核定的原文 span，不以模型生成结果反推 Gold。

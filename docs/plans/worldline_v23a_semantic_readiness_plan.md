# WorldLine V2.3A Semantic Readiness 实施计划

## 1. 目标与范围

V2.3A 为后续 Genre Executor 补齐两类基础设施：统一语义实体身份和受控 genre 词表，同时为现有 artist → genre Semantic Expansion 增加可观测 trace。

本阶段保持现有执行链：

```text
artist + SearchPlan + supported_claims
  ↓
EntityBridge / GenreVocabularyRegistry
  ↓
Candidate
  ↓
Validator
  ↓
Graph Projection
```

不实现 genre → artist，不开放 genre Action，不新增 LangGraph Node，不修改 RAG、Retrieval、Embedding、Reranker、数据库、WorldLineGraph schema、checkpoint schema 或 session 流程。

## 2. 当前接入点

当前 `SemanticExpansionService.expand_artist_genres()` 在 WorldLine `build_graph` 节点内执行，位置是目录图构建之后、V2.1 prior graph merge 之前。V2.3A 不改变这个位置。

接入方式：

1. Candidate 提取前，使用 `EntityBridge` 从 SearchPlan 建立当前目录 artist 的 catalog ID 与 knowledge ID 映射。
2. `supported_claims.object_entity_id` 必须由 `GenreVocabularyRegistry` 精确解析，才能形成 genre candidate。
3. Validator 继续独立核验证据、实体、relation 和 confidence。
4. Projection 将 `WorldLineEntityReference` 写入 node metadata；不增加 WorldLineNode 字段。
5. Service 完成后生成 `SemanticExpansionTrace` 并写结构化日志；trace 不进入 graph、checkpoint 或公开 API response。

## 3. 数据结构

### 3.1 WorldLineEntityReference

```text
WorldLineEntityReference
  node_id: str
  node_type: WorldLineNodeType
  canonical_name: str
  knowledge_entity_id: str
  catalog_reference: CatalogReference | None
  aliases: list[str]
```

校验规则：

- artist 必须有 `artist:<slug>` knowledge ID 和 `netease:artist:<id>` catalog reference；`node_id` 使用 catalog ID。
- genre 必须有 `genre:<slug>` knowledge ID，`catalog_reference=None`，`node_id` 使用 knowledge ID。
- aliases 去重且不包含空值。
- 无法唯一解析的 artist 返回 unresolved/ambiguous result，不构造可执行 reference。

### 3.2 CatalogResolutionResult

```text
CatalogResolutionResult
  status: resolved | unresolved | ambiguous
  reference: WorldLineEntityReference | None
  candidate_knowledge_ids: list[str]
  reason: str
```

`EntityBridge` 提供：

- `reference_for_catalog_artist()`：从当前 `ResolvedEntity + SearchPlan` 构造双 ID artist reference。
- `resolve_artist()`：从 knowledge ID 或 alias，经现有 EntityRegistry 和 SearchPlan 查找唯一 catalog artist。
- `resolve_genre()`：从 canonical ID/name/alias 构造无 catalog reference 的 genre reference。

### 3.3 GenreVocabularyEntry

```text
GenreVocabularyEntry
  canonical_id: str
  canonical_name: str
  aliases: list[str]
  executable: bool
  expandable: bool
```

Registry document 包含 `registry_version`。V2.3A 所有 genre 均为 `executable=false / expandable=false`，防止基础设施提前开放 Genre Executor。

第一版包含：Hip-Hop、Jazz、Jazz Hip-Hop、R&B、C-pop。

### 3.4 SemanticExpansionTrace

```text
SemanticExpansionTrace
  candidate_count: int
  accepted_count: int
  rejected_count: int
  rejection_reason: dict[str, int]
  source_artist: str
  target_genre: list[str]
  evidence_ids: list[str]
  registry_version: str
  validator_version: str
```

Trace 由现有 candidate/decision/projection 结果确定性构造。accepted 和 rejected 均进入统计；结构化日志事件为 `worldline.semantic_expansion`。

## 4. 文件修改范围

### 新增

| 文件 | 用途 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/entity_bridge.py` | Identity reference、resolution result 和 EntityBridge |
| `ai-service/app/agent/foundation/subgraphs/worldline/genre_registry.py` | 版本化 genre registry 模型、加载与 alias resolution |
| `ai-service/app/agent/foundation/subgraphs/worldline/registry/genres_v1.yaml` | 第一版受控 genre 配置 |
| `ai-service/tests/test_worldline_entity_bridge.py` | artist 双 ID、genre、alias、unresolved/ambiguous 测试 |
| `ai-service/tests/test_worldline_genre_registry.py` | 注册、alias、未注册拒绝、版本及不可执行测试 |

### 修改

| 文件 | 修改内容 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/semantic_expansion.py` | 用 EntityBridge/Genre Registry 替换直接 ID 拼接；增加 identity metadata 和 SemanticExpansionTrace |
| `ai-service/tests/test_worldline_semantic_expansion.py` | trace、identity metadata、registered/unregistered genre 和原有链路回归 |
| `ai-service/tests/test_worldline_graph.py` | 验证完整子图仍输出原有 graph/narrative/citation/session contract |

不修改 `state.py`、`session.py`、API schema、checkpoint、RAG 目录或前端。

## 5. 测试方案

### Entity Bridge

- `artist:<slug>` + SearchPlan catalog reference → `netease:artist:<id>`。
- catalog artist → 同时保留 knowledge/catalog 双 ID。
- artist alias 经现有 EntityRegistry 唯一解析。
- genre ID/name/alias → `genre:<slug>` 且 catalog reference 为空。
- 未映射 artist、未注册 genre、歧义 alias 返回非 resolved，不产生 reference。

### Genre Registry

- 五个首批 genre 均可按 ID 和 alias 查找。
- alias 规范化、ID 唯一、alias 不冲突。
- 未注册 genre 返回 None。
- registry version 固定且所有 entry 在 V2.3A 不可执行、不可扩展。

### Semantic Trace

- 一次同时包含 accepted 和 rejected candidate 的执行，统计数量正确。
- rejection reason 与 Validator 结果一致。
- accepted evidence IDs、source artist、target genre、registry/validator version 正确。
- trace 不改变 graph 节点、边或 public WorldLineResult schema。

### 回归

- artist → genre 的 Candidate → Validator → Projection 继续工作。
- graph merge、narrative、citation、session、API 测试全部通过。
- genre 继续不属于可执行 branch target。
- 运行 WorldLine 定向测试、完整 `tests`、Ruff、compileall 和 diff check。

## 6. 实施顺序

1. 实现并测试版本化 GenreVocabularyRegistry。
2. 实现并测试 WorldLineEntityReference、CatalogResolutionResult 和 EntityBridge。
3. 将 bridge/registry 接入 Semantic Expansion，投影 identity metadata。
4. 增加 SemanticExpansionTrace 与结构化日志。
5. 运行定向和全量回归，生成实施报告。

## 7. 验收标准

- artist reference 同时保留 knowledge ID 与 catalog ID。
- genre reference 不伪造 catalog ID。
- alias 只能解析到受控、唯一实体。
- 未注册或歧义实体不会进入可执行图。
- 现有 artist → genre 关系和 citation 行为不回退。
- accepted/rejected 均有 trace，拒绝原因可统计。
- genre Action 仍被禁用，没有任何 genre → artist 执行路径。

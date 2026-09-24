# WorldLine V2.2 Semantic Expansion 第一阶段实施报告

## 1. 实施结果

WorldLine V2.2 第一阶段已完成。当前 artist WorldLine 在现有检索返回充分 ContextPack 且 `supported_claims` 包含可验证 `has_genre` 关系时，可以经过以下固定链路生成语义图：

```text
Resolved artist + SearchPlan + MusicKnowledgeDocument
  ↓
RelationCandidateExtractor
  ↓
RelationValidator
  ↓
GraphProjector
  ↓
artist --associated_with--> genre
```

无证据、证据片段无法在本轮文档中逐字定位、artist 身份无法与 SearchPlan 唯一对应、genre 未在现有 EntityRegistry 注册、ContextPack 不充分或置信度低于阈值时，不创建 genre 节点和关系边。

本次没有修改 RAG、Retrieval、Embedding、Reranker、Query Understanding、数据库、checkpoint schema、Prompt 或前端 contract，也没有新增 LangGraph Node。

## 2. 修改文件

### 2.1 业务代码

| 文件 | 修改内容 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/models.py` | 新增 `WorldLineRelation.ASSOCIATED_WITH` |
| `ai-service/app/agent/foundation/subgraphs/worldline/semantic_expansion.py` | 新增 Semantic Candidate、Validator、去重、精确证据来源构造、Graph Projection 和编排服务 |
| `ai-service/app/agent/foundation/subgraphs/worldline/graph.py` | 在目录图构建完成后、V2.1 prior graph merge 之前调用 Semantic Expansion；子图节点和边拓扑未改变 |
| `ai-service/app/agent/foundation/subgraphs/worldline/builder.py` | 阻止旧 `metadata.graph_edges` 路径直接注入 `associated_with`，确保该关系必须经过 Validator |

### 2.2 测试代码

| 文件 | 覆盖内容 |
|---|---|
| `ai-service/tests/test_worldline_semantic_expansion.py` | 正确关系、无证据、ContextPack 不充分、证据片段缺失、低置信度、未知 genre、重复关系、来源合并和 citation |
| `ai-service/tests/test_worldline_models.py` | `associated_with` 的合法 WorldLineGraph contract |
| `ai-service/tests/test_worldline_builder.py` | legacy structured metadata 不能绕过 Validator 写入 `associated_with` |
| `ai-service/tests/test_worldline_graph.py` | 从 retrieve 到 graph、narrative、sources、citations 的完整子图集成 |
| `ai-service/tests/test_worldline_merge.py` | semantic edge 按三元组去重并合并 evidence IDs，旧图不被修改 |
| `ai-service/tests/test_worldline_generation.py` | genre node 不成为 session 模式的可执行探索目标 |

## 3. 已实现功能

### 3.1 Candidate Extraction

- 只处理 `ResolvedEntity.entity_type == artist`。
- 从当前 `SearchPlan.entities` 建立 `netease:artist:<id>` 与 `artist:<slug>` 的唯一身份映射。
- 只读取本轮 `MusicKnowledgeDocument.metadata.supported_claims`。
- 只提取 `predicate == has_genre`、subject 与 focus artist 一致、object 为 `genre:<slug>` 的声明。
- genre 名称只取现有 EntityRegistry 的 canonical name；不从正文、statement 或 slug 猜测名称。
- 不调用 LLM，不解析自由文本创建关系。

### 3.2 Relation Validation

Validator 会重新核验 candidate 与原始本轮文档：

- `sufficiency_status == sufficient`；
- document、parent、child、source chunk 和 claim ID 一致；
- evidence span 在 document content 中连续逐字存在；
- supported claim 仍在该 document metadata 中，且 subject、predicate、object、span 和置信度全部一致；
- source 为本轮 artist，target 为注册表中的 genre；
- 有效置信度为 extraction/verification confidence 的较小值，阈值为 `0.85`；
- relation 固定为 `associated_with`。

相同关系使用 `(source, target, associated_with)` 去重；独立证据按 claim、source chunk 和 span hash 去重合并。单边超过 8 个有效 evidence 时显式返回容量错误，不静默截断。

### 3.3 Graph Projection

- source node 保留目录 ID `netease:artist:<id>`。
- target node 使用注册表 ID `genre:<slug>` 和 canonical name。
- edge 使用 `WorldLineRelation.ASSOCIATED_WITH`。
- 每条独立关系证据生成稳定 ID：`knowledge:rel:<24 hex>`。
- `EvidenceSource.excerpt` 保存准确 evidence span。
- metadata 保存 document、parent、child、claim、context/retrieval version、relation key、validator version 和 confidence。
- Semantic delta 通过现有 `merge_worldline_graph` 合入本轮目录图，再按原 V2.1 流程合入 prior graph。
- node type 冲突、来源冲突、证据缺失和图容量超限继续由现有 merge/graph 校验拒绝。

### 3.4 Narrative、Citation 和 Session

- Narrative 仍使用现有生成器和 Prompt；它会收到已经包含 genre relation 和精确来源的最终 graph/sources。
- semantic source 可以通过 `[knowledge:rel:*]` 进入现有 inline citation 校验及 `WorldLineResult.citations`。
- 投影后的 graph 和 sources 继续进入现有 session state 与 Persistent Checkpointer；本阶段未改变 checkpoint 数据结构。
- genre 保持只读图节点，不进入 session 的可执行 branch target，不存在 Genre Executor。

## 4. 测试结果

### 4.1 定向 WorldLine 回归

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_worldline_semantic_expansion.py `
  tests/test_worldline_builder.py `
  tests/test_worldline_merge.py `
  tests/test_worldline_graph.py `
  tests/test_worldline_grounding.py `
  tests/test_worldline_generation.py `
  tests/test_worldline_session.py `
  tests/test_worldline_api.py
```

结果：`55 passed`。

### 4.2 完整测试

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
```

结果：`917 passed, 13 skipped, 206 subtests passed`。

### 4.3 静态检查与编译

- 对本阶段所有修改的 Python 文件运行 Ruff：`All checks passed`。
- 对 `app` 和相关测试运行 `compileall`：通过。
- `git diff --check HEAD~3..HEAD`：通过。

未安装新依赖。最初使用系统 Python 调用 pytest 时发现系统环境没有 pytest；随后使用项目已存在的 `ai-service/.venv` 完成全部验证。

## 5. 当前限制

- 只支持 `artist → genre / associated_with`。
- 只消费现有 `supported_claims`；普通正文即使提到风格，也不会生成关系。
- 只投影 EntityRegistry 已注册的 genre。当前未注册的 `Jazz Hip-Hop` 等目标不会生成节点。
- 不支持 `object_value` 形式的自由文本 genre。
- 不支持 song/album → genre。
- 不支持 influence、era、scene、similar artist 或其他语义关系。
- genre 节点不能继续探索，不生成可执行 genre action。
- 不调整 RAG 参数，因此在线能否产生关系取决于现有 Retrieval/ContextPack 是否返回包含有效 supported claim 的充分证据。
- 本阶段没有新增 PostgreSQL 重启测试，因为 checkpoint 结构和 session 写入路径未改变；现有 session/API/full-suite 回归均通过。

## 6. Git 提交

- `067ddbe feat(worldline): add semantic association relation`
- `63c33ba feat(worldline): implement semantic expansion core`
- `e80d473 feat(worldline): integrate semantic graph projection`
- `4d1396a fix(worldline): recompute semantic confidence in validator`

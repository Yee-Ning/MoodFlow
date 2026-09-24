# WorldLine V2.3A Semantic Readiness 实施报告

## 1. 实施结论

WorldLine V2.3A 已完成。本阶段为既有 artist → genre 证据链补齐了受控语义身份、版本化 Genre Vocabulary 和语义扩展 trace，同时保持现有执行链不变：

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

本次没有实现 Genre Executor、genre → artist、influence、era、scene 或 similar artist；没有修改 RAG Pipeline、Retrieval、Embedding、Reranker、WorldLineGraph schema、checkpoint schema、session flow 或数据库。

## 2. 已实现功能

### 2.1 Semantic Entity Identity Bridge

新增 `WorldLineEntityReference`，统一表达 WorldLine 节点的知识身份与目录身份：

- artist 使用目录 ID 作为 `node_id`，例如 `netease:artist:42`。
- artist 同时保留 knowledge ID，例如 `artist:test_artist`。
- artist 的 `catalog_reference` 沿用 SearchPlan 已解析的 provider、entity ID、type 和 name。
- genre 使用 knowledge ID 作为 `node_id`，例如 `genre:jazz_hip_hop`。
- genre 的 `catalog_reference` 固定为空，不伪造目录 ID。
- artist 和 genre 均保留 canonical name 与 aliases。

新增 `EntityBridge` 和 `CatalogResolutionResult`：

- `reference_for_catalog_artist()` 从现有 `ResolvedEntity + SearchPlan` 恢复双 ID artist reference。
- `resolve_artist()` 支持 knowledge ID 或 alias，并要求唯一的 catalog 映射。
- `resolve_genre()` 只接受受控 Genre Registry 中的 ID、名称或 alias。
- unresolved 或 ambiguous 结果不生成 `WorldLineEntityReference`，因此不会成为新的可执行身份。

投影时，reference 写入已有 `WorldLineNode.metadata.entity_reference`。没有新增或修改 `WorldLineGraph` 字段。

### 2.2 Versioned Genre Vocabulary Registry

新增代码配置形式的 `genres_v1.yaml`，版本为 `v1`，包含：

- Hip-Hop
- Jazz
- Jazz Hip-Hop
- R&B
- C-pop

Registry 校验 canonical ID、canonical name、alias 唯一性、跨实体 alias 冲突以及 executable/expandable 约束。ID 只能来自该受控配置，Semantic Expansion 不接受模型自由生成的 genre ID。

V2.3A 中全部 genre 均为：

```text
executable = false
expandable = false
```

因此当前 graph 可以保存 genre 节点，但不会把 genre 暴露为可执行探索目标。

### 2.3 Semantic Expansion Trace

新增内部 `SemanticExpansionTrace`：

- `candidate_count`
- `accepted_count`
- `rejected_count`
- `rejection_reason`
- `source_artist`
- `target_genre`
- `evidence_ids`
- `registry_version`
- `validator_version`

Trace 由 Candidate、Validator decision 和 Graph Projection 的确定性结果生成。accepted 和 rejected candidate 均进入统计，拒绝原因按 reason 聚合计数；accepted relation 对应的 evidence IDs 也会记录。

Trace 保存在内部 `SemanticExpansionResult` 并以 `worldline.semantic_expansion` INFO 日志输出。它没有进入 `WorldLineGraph`、session checkpoint 或公开 API response，因此不改变现有前后端 contract。

### 2.4 既有 artist → genre 链路保持

现有链路仍然：

- 只读取已有 `supported_claims`。
- 只接受 `has_genre`。
- 只投影 `artist → genre / associated_with`。
- Validator 继续验证 evidence document、parent/chunk、exact span、claim 一致性、confidence 和实体身份。
- Graph Projection 继续生成带 `evidence_ids` 的 edge 和可追踪 `EvidenceSource`。
- Narrative 可以继续引用 semantic source，citation 校验保持不变。

## 3. 修改文件

### 新增

| 文件 | 内容 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/entity_bridge.py` | WorldLineEntityReference、CatalogResolutionResult、EntityBridge |
| `ai-service/app/agent/foundation/subgraphs/worldline/genre_registry.py` | GenreVocabularyEntry、版本化 registry 加载和解析 |
| `ai-service/app/agent/foundation/subgraphs/worldline/registry/genres_v1.yaml` | V2.3A 受控 genre 词表 |
| `ai-service/tests/test_worldline_entity_bridge.py` | 双 ID、alias、genre identity 和 unresolved 测试 |
| `ai-service/tests/test_worldline_genre_registry.py` | registry、alias、未注册 genre 和不可执行状态测试 |
| `docs/plans/worldline_v23a_semantic_readiness_plan.md` | 开发前实施计划 |
| `docs/reports/worldline_v23a_semantic_readiness_report.md` | 本实施报告 |

### 修改

| 文件 | 内容 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/semantic_expansion.py` | Bridge/Registry 接入、node identity metadata、SemanticExpansionTrace 和结构化日志 |
| `ai-service/tests/test_worldline_semantic_expansion.py` | identity、受控 genre、trace 及既有链路回归 |

## 4. 测试结果

### 定向测试

| 命令/范围 | 结果 |
|---|---|
| `pytest tests/test_worldline_genre_registry.py -q` | 4 passed |
| `pytest tests/test_worldline_entity_bridge.py -q` | 4 passed |
| `pytest tests/test_worldline_semantic_expansion.py -q` | 10 passed |
| Bridge + Registry + Semantic + Graph + Session + Grounding | 38 passed |
| 全部 `test_worldline_*.py` | 90 passed, 2 skipped |

覆盖内容包括：

- artist knowledge ID → catalog ID。
- catalog artist 保留 knowledge/catalog 双 ID。
- artist 和 genre alias resolution。
- 无 catalog 映射的 artist 拒绝。
- 已注册 genre 接受、未注册 genre 拒绝。
- accepted/rejected trace 和 rejection reason。
- artist → genre graph projection、edge evidence、source/citation。
- relation/evidence 去重。
- graph、narrative、citation、session 回归。

### 全量与静态验证

| 命令/范围 | 结果 |
|---|---|
| `pytest tests -q` | 927 passed, 13 skipped, 206 subtests passed |
| Ruff（全部本次 Python 修改文件） | passed |
| `python -m compileall -q app/agent/foundation/subgraphs/worldline` | passed |
| 四个本次提交的 `git show --check` | passed |

### 验证限制

- 直接执行仓库级 `pytest -q` 时，`evals/tests/test_judges.py` 在收集阶段因当前 `.venv` 未安装可选依赖 `deepeval` 而中止。本阶段禁止新增依赖，因此没有安装该包；正式应用测试目录 `tests/` 已全部执行完成。
- 两个真实 PostgreSQL 重启验收测试保持跳过，原因是未设置 `RUN_WORLDLINE_POSTGRES_RESTART=1`。测试自身明确要求该开关和真实 PostgreSQL 环境。本次没有修改 checkpoint 或 session flow，其他 session 回归均通过。

## 5. Git 提交

本次按逻辑变更分为以下提交：

- `c4f1d18 docs: plan WorldLine V2.3A semantic readiness`
- `7bf8fd8 feat: add controlled WorldLine genre registry`
- `d4b7e91 feat: bridge WorldLine knowledge and catalog identities`
- `21173d2 feat: add semantic identity projection and trace`
- `3b79608 fix: normalize WorldLine identity aliases`

未暂存或提交工作区中与本任务无关的既有修改和数据目录。

## 6. 当前限制

1. 语义关系仍只支持 `artist → genre / associated_with`。
2. Genre Registry 当前只有五个受控类型。
3. genre 节点可展示和引用，但 `executable=false`、`expandable=false`，没有 Genre Executor。
4. artist 必须在 SearchPlan 中具有唯一的 knowledge/catalog 映射，否则 Semantic Expansion 不产生 candidate。
5. genre 仍没有 catalog reference，这是有意保留的知识实体身份，不会生成伪目录 ID。
6. Trace 当前是内部结果和结构化日志，没有持久化到 checkpoint，也没有加入 API response。
7. 未注册 genre claim 在 Candidate 边界被过滤，不进入 Graph Projection。

## 7. 下一阶段 Genre Executor 接入方案

V2.3B 建议以现有 readiness 层为唯一身份入口，最小接入顺序如下：

1. **定义受控执行资格**：仅对明确开放的 registry entry 设置 `executable=true` 和 `expandable=true`，仍不允许 LLM 创建 genre ID。
2. **生成服务端 Action**：从 graph 中已验证、可执行的 genre node 生成 branch/action；客户端仍只能提交服务端 branch ID。
3. **实现单一 Genre Executor**：输入 `WorldLineEntityReference`，以 `knowledge_entity_id` 执行 genre → artist 查询；保持 catalog reference 为空，直到目标 artist 经 EntityBridge 获得唯一 catalog identity。
4. **证据门控目标 artist**：只有具有 evidence 且能唯一映射到 catalog artist 的结果才进入 executable graph。
5. **复用现有 merge/session/checkpoint**：增量 graph 通过现有 merge 提交，失败时不污染旧 session。
6. **扩展 trace**：记录 genre executor 的 candidate yield、identity mapping failure、evidence rejection 和最终 graph yield。
7. **增加真实 PostgreSQL 连续探索验收**：创建 session、探索 artist → genre、重启服务、恢复 session、执行 genre → artist、确认 graph 和 evidence history 不丢失。

V2.3B 开始前应先选定首批真正可执行的 genre，并为这些 genre 准备可验证的 genre → artist evidence contract。当前 V2.3A 不包含任何上述执行逻辑。

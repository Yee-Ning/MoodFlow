# WorldLine V2.3B Genre Executor 实施报告

## 1. 实施结论

WorldLine V2.3B 已完成。现有 WorldLine session 现在可以在证据门控下执行以下连续探索闭环：

```text
catalog artist
  ↓ 已验证 artist has_genre genre supported_claim
knowledge genre
  ↓ 服务端生成的 genre branch/action
Genre Executor
  ↓ 已验证 artist has_genre current_genre supported_claim
EntityBridge 唯一 catalog identity
  ↓
new catalog artist
  ↓
graph merge + narrative/branches + persistent checkpoint
```

本阶段只开放 `genre:hip_hop` 作为首个可执行 genre。Genre 继续作为没有伪造 catalog ID 的 knowledge entity；新 artist 只有同时通过 supported claim 验证和 EntityBridge 唯一目录映射后才会进入 graph。

没有修改 RAG Pipeline、Retrieval、Embedding、Reranker、WorldLineGraph schema、checkpoint schema、数据库或公开 API schema；没有实现 influence、era、scene、similar artist 或 song/album → genre。

## 2. 已实现功能

### 2.1 受控 Genre 执行资格

新增版本化 `genres_v2.yaml`：

- `genre:hip_hop` 设置为 `executable=true`、`expandable=true`。
- Jazz、Jazz Hip-Hop、R&B、C-pop 仍不可执行、不可扩展。
- Session action 必须同时匹配当前 registry 版本、genre node identity metadata 和入边 evidence。
- 旧 registry 版本生成的 checkpoint 不会被自动提升为可执行 genre。

### 2.2 Genre Action / Branch

新增 `GenreActionPolicy`，只为满足全部条件的 genre node生成 action：

1. genre 已注册且同时 executable、expandable。
2. node 的 `WorldLineEntityReference` 与 registry canonical identity 完全一致。
3. graph 中存在 `artist → genre / associated_with` 入边。
4. 入边的每个 `evidence_id` 都能解析到当前 session 的有效 `EvidenceSource`。
5. genre 未访问且 session 未超过深度限制。

Branch ID 继续由服务端生成。客户端只能向既有 `POST /api/ai/worldline/explore` 提交当前 session 返回的 `branch_id`，不能提交任意 genre/node ID。

### 2.3 Genre Executor

新增 evidence-first `GenreExecutor`：

- 以受控 genre reference 和触发该 genre 的 artist/evidence 为输入。
- 通过现有 Query Understanding 服务构造 genre SearchPlan。
- 通过现有 `MusicKnowledgeRetriever` 边界取得 ContextPack documents，不更改检索实现或参数。
- 只读取 document metadata 中既有的 `supported_claims`。
- 只接受 `subject=artist:*`、`predicate=has_genre`、`object=current genre` 的 claim。
- 验证 parent/child、source chunk、exact evidence span、sufficiency、claim 一致性及 confidence。
- 忽略已经在 graph 中的 artist，防止循环重复扩图。
- 使用现有 Music API artist search 获取目录候选，再由 EntityBridge 完成唯一 knowledge artist → catalog artist 映射。
- 每次最多投影 3 个通过全部验证的 artist。
- 生成 `genre → catalog artist / associated_with` edge，并保存 relation source 与 `evidence_ids`。

Executor 记录 candidate、evidence accepted、catalog resolved、projected 和 rejection reason trace。外部检索、目录服务和 Graph Projection 异常均转换为稳定的领域错误。

### 2.4 EntityBridge 扩展

`EntityBridge.resolve_catalog_artist_candidates()` 新增以下规则：

- knowledge artist 必须来自现有 Knowledge Entity Registry。
- catalog candidate 必须是合法的正整数 `netease:artist:<id>`。
- canonical name 或 alias 必须精确归一化匹配。
- 只有唯一 catalog identity 才返回可执行 `WorldLineEntityReference`。
- 未解析或多 catalog ID 冲突时拒绝投影，不污染 graph。

Accepted artist 同时保存 knowledge ID 和 catalog reference，延续 V2.3A 双身份模型。

### 2.5 Session、Graph Merge 与 Checkpoint

现有 `WorldLineSessionService.explore()` 增加按 node type 分派：

- song、artist、album 继续进入原有 `_run_turn`。
- genre 经 `GenreActionPolicy` 二次校验后进入 Genre Executor。

Genre 执行成功后：

1. 先在内存中完成 delta graph 和 sources。
2. 复用 `merge_worldline_graph()` 完成 node/edge/source 去重、evidence union 和引用完整性校验。
3. 生成具有 inline source citation 的确定性 narrative。
4. 生成后续 artist/genre branches 与 `available_actions`。
5. 更新 current node、visited nodes、exploration depth、evidence history 和 revision。
6. 仅在所有校验成功后写入现有 Persistent Checkpointer。

检索失败、Music API 失败、无 grounded artist、投影失败、graph merge 失败或 checkpoint 写入失败时，旧 session snapshot 保持不变。

### 2.6 PostgreSQL 跨进程恢复

真实 PostgreSQL 验收 fixture 增加 genre create/explore/verify 三阶段：

```text
process A: create artist session and persist genre action
process B: restart, load checkpoint, execute genre branch, persist merged graph
process C: restart again, recover and verify graph/evidence/visited/revision
```

该流程使用生产路径 Persistent Checkpointer，没有使用 InMemorySaver 替代正式恢复验证。

## 3. 修改文件

### 新增

| 文件 | 内容 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/genre_actions.py` | Genre action 资格校验和 branch 注入 |
| `ai-service/app/agent/foundation/subgraphs/worldline/genre_executor.py` | Genre SearchPlan、Candidate、Validator、EntityBridge resolution、Graph Projection 和 trace |
| `ai-service/app/agent/foundation/subgraphs/worldline/registry/genres_v2.yaml` | V2.3B 受控执行 registry |
| `ai-service/tests/test_worldline_genre_actions.py` | action evidence/identity/version/visited/depth 门控测试 |
| `ai-service/tests/test_worldline_genre_executor.py` | executor 正常、拒绝、异常和投影测试 |
| `ai-service/tests/test_worldline_genre_session.py` | session 连续探索、merge、citation 与失败原子性测试 |
| `docs/plans/worldline_v23b_genre_executor_plan.md` | 实施前计划 |
| `docs/reports/worldline_v23b_genre_executor_report.md` | 本实施报告 |

### 修改

| 文件 | 内容 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/entity_bridge.py` | knowledge artist → 唯一 catalog artist candidate 映射 |
| `ai-service/app/agent/foundation/subgraphs/worldline/genre_registry.py` | 默认加载 v2 registry |
| `ai-service/app/agent/foundation/subgraphs/worldline/session.py` | genre action、执行分派、增量 merge、narrative/branches 和原子 checkpoint 提交 |
| `ai-service/tests/fixtures/worldline_postgres_restart_worker.py` | 跨进程 genre session worker |
| `ai-service/tests/test_worldline_entity_bridge.py` | candidate mapping 与歧义拒绝测试 |
| `ai-service/tests/test_worldline_genre_registry.py` | v2 执行开关测试 |
| `ai-service/tests/test_worldline_postgres_restart.py` | 真实 PostgreSQL genre restart 验收 |
| `ai-service/tests/test_worldline_semantic_expansion.py` | v2 registry 下 artist → genre 回归 |

## 4. API 与前端 Contract

公开 API 路径和 Schema 未改变：

- `POST /api/ai/worldline/sessions` 创建 session。
- `POST /api/ai/worldline/explore` 继续接受 `session_id + branch_id`。
- Response 继续返回完整 `WorldLineResult`、`session_id`、`current_node`、`available_actions` 和 `exploration_depth`。

新增行为是：满足证据门控的 genre branch 会以 `explore_node` action 返回，现有前端 branch 点击调用无需提交新的字段。执行后返回合并后的完整 graph，而不是单独的增量 graph。

## 5. 测试结果

### 定向与 WorldLine 回归

| 命令/范围 | 结果 |
|---|---|
| Genre Executor + Genre Session 定向测试 | 10 passed |
| 全部 `test_worldline*.py` | 106 passed, 3 skipped |
| 真实 PostgreSQL restart suite（`RUN_WORLDLINE_POSTGRES_RESTART=1`） | 3 passed |

覆盖内容包括：

- 可执行 genre branch 生成与服务端 branch ID 校验。
- 非注册、非当前版本、无 evidence、已访问和超过深度的 genre 不生成 enabled action。
- 正确 `artist has_genre genre` claim 生成新 catalog artist。
- 无 evidence、错误 genre、insufficient ContextPack 和已有 artist 被拒绝。
- catalog artist unresolved/ambiguous/identity collision 被拒绝。
- node/edge/source 去重和 evidence merge。
- narrative inline citation 与 grounding validation。
- 检索、Music API、Graph Projection 和 checkpoint 失败时 session 不变。
- 创建 session → PostgreSQL checkpoint → 进程重启 → genre explore → 再次重启 → graph/evidence/visited/revision 完整恢复。

### 全量与静态验证

| 命令/范围 | 结果 |
|---|---|
| `pytest tests -q` | 943 passed, 14 skipped, 206 subtests passed |
| Ruff（全部 V2.3B Python 修改文件） | passed |
| `python -m compileall -q app/agent/foundation/subgraphs/worldline tests/fixtures/worldline_postgres_restart_worker.py` | passed |

### 验证范围说明

- `ai-service/tests/` 全部通过，包含 API、session、semantic expansion 与既有 song/artist/album 回归。
- 真实 PostgreSQL restart 测试通过，不再依赖仅内存模拟作为持久化验收依据。
- 本阶段没有前端文件变更，因此没有新增 UI 构建或视觉验收。
- `evals/` 不属于本次执行的 `tests/` 全量命令；该目录依赖可选评测环境。本阶段没有安装依赖或修改评测系统。

## 6. Git 提交

本次按逻辑完整改动拆分为：

- `2f49d03 docs: plan WorldLine V2.3B genre executor`
- `3a64e0d feat: enable evidence-ready genre identities`
- `86bae7c feat: add evidence-first WorldLine genre executor`
- `af7f125 feat: execute genre branches in WorldLine sessions`
- `98d01a0 test: cover genre exploration across checkpoint restarts`
- `f76a669 test: harden genre planning and catalog identity`
- `29366bf test: verify genre session failure atomicity`
- `5273057 fix: normalize genre projection failures`

工作区中与本任务无关的既有修改、报告移动和数据目录均未暂存或提交。

## 7. 当前限制

1. 只有 `genre:hip_hop` 可执行；其余 registry genre 仍为展示节点。
2. 只支持 `artist → genre → artist`，且关系统一为 `associated_with`。
3. Genre Executor 只消费已有 `supported_claims`，不会调用 LLM 自由生成 artist。
4. 新 artist 必须在 Knowledge Entity Registry 中存在，并能由现有 Music API 候选唯一映射到 catalog identity；严格门控可能降低扩展 yield。
5. 单次 genre 执行最多加入 3 个 artist，防止 graph 无界扩张。
6. Genre narrative 当前按 accepted evidence 确定性生成，并使用现有 citation validator；没有新增 narrative prompt。
7. Genre Executor trace 记录在结构化日志和内部 result 中，没有加入公开 API 或 checkpoint schema。
8. 旧 registry/checkpoint 中的 genre 不会自动获得 V2.3B 执行资格，需要由当前版本链路重新产生合格 identity/evidence metadata。

## 8. 后续建议

在继续增加关系类型前，建议先积累真实 `genre → artist` supported claim 的接受率、catalog resolution 成功率和拒绝原因数据，并据此确认 registry 中下一批可安全开放的 genre。后续若进入新阶段，应继续沿用 Candidate → Validator → EntityBridge → Graph Projection → atomic checkpoint 的执行边界。

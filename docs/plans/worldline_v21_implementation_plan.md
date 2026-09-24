# WorldLine Agent V2.1 实施计划

状态：已确认并实施。本文只规划 song / artist / album 的连续探索。依据为实施前的 WorldLine 子图、API、checkpoint、前端调用和测试代码；实际结果见实施报告。

## 1. 当前问题

1. 独立接口 `POST /api/ai/worldline` 每次创建新 `WorldLineState`，现有子图以 `checkpointer=False` 编译，`generate_branches` 后结束；没有可恢复的累计图。[service.py](../../ai-service/app/agent/foundation/subgraphs/worldline/service.py#L28)、[graph.py](../../ai-service/app/agent/foundation/subgraphs/worldline/graph.py#L625)
2. 当前 branch ID 是 `branch:1..3`，每轮重复；分支虽指向已有图节点，却没有服务端执行入口，也不能直接当作跨轮有效的 action ID。[generation.py](../../ai-service/app/agent/foundation/subgraphs/worldline/generation.py#L362)
3. `WorldLineGraph` 限 15 节点/20 边，`WorldLineResult` 限 30 sources 且恰好三条 branches；V2.1 必须明确累计图容量和“少于三条可探索分支”的行为。[models.py](../../ai-service/app/agent/foundation/subgraphs/worldline/models.py#L170)
4. 当前目录实体只覆盖 song/artist/album。genre/era 等类型仅有图枚举或可选测试注入，不能在本阶段当作可执行目标。[music_service.py](../../ai-service/app/agent/foundation/subgraphs/worldline/music_service.py#L57)、[builder.py](../../ai-service/app/agent/foundation/subgraphs/worldline/builder.py#L131)
5. 项目已有 PostgreSQL LangGraph checkpointer，应用启动时调用初始化函数；现有 WorldLine 私有状态未接入。示例配置仍写 `CHECKPOINT_BACKEND=memory`，因此 V2.1 生产入口需要显式校验持久化后端，不能默默退回进程内存。前端 `/worldline` 仍是静态页。[checkpointing.py](../../ai-service/app/agent/checkpointing.py#L227)、[main.py](../../ai-service/app/main.py#L20)、[App.tsx](../../soul-music-room/src/App.tsx#L70)

## 2. 修改方案与真实执行链

```text
创建会话 POST /api/ai/worldline/sessions {query}
  → 原 WorldLine 子图的 V2.1 模式：实体解析 → SearchPlan → Music API
  → 原可选 RAG → 原 Graph Builder → 叙事 → 可执行分支候选
  → 校验 V2.1 结果 → 生成 session_id/revision=1/visited={root}
  → 将结构化 WorldLineSessionState 写入现有 checkpoint
  → 返回完整结果 + 会话字段

探索 POST /api/ai/worldline/explore {session_id,branch_id}
  → 从 checkpoint 读取会话 → 在当前 available_actions 中查 branch_id
  → 校验目标类型、真实目录 ID、visited、深度和容量
  → 注入可信目标实体，复用原子图的查询理解/事实/检索/构图节点
  → 在叙事生成前合并增量图与旧图、sources
  → 复用叙事/分支生成及结构化校验，得到新的完整 V2.1 结果
  → 所有校验成功后一次提交新快照，revision+1
  → 返回保留原 root 的累计图及新一轮 actions
```

会话持久化使用**现有 PostgreSQL** `create_runtime_checkpointer()` 和单例的会话 `StateGraph`，以 `worldline:<session_id>` 为独立 thread key；checkpoint 内容使用 JSON 兼容字典，读取时再作 Pydantic 校验。生产路径无注入 saver 时要求 `checkpoint_backend()=="postgres"` 且 PostgreSQL saver 已由应用 lifespan 初始化；否则 V2.1 会话端点 fail closed，绝不创建正式 MemorySaver 会话。测试可显式注入现有 `InMemorySaver`，但不增加第二套存储实现。计算下一轮图和响应期间不写会话；成功后才提交完整新快照。不新增数据库或表。旧 `POST /api/ai/worldline` 和 Foundation 单轮路径保持原样。

探索目标**不按名称重新猜测**：从服务端保存的当前 action 查对应 graph node，仅接受 `netease:song|artist|album:<正整数>`；根据该 canonical ID 复用 Music API 详情并校验返回实体身份。Query Understanding、SearchPlan 和现有可选 RAG 照常对该目标运行，不改底层实现。增量图的 root 必须等于目标 ID；合并后最终图 root 仍为首次实体。叙事输入额外给出原 root 与本轮 focus，事实来自本轮目标；引用仍从合并后的 sources 中校验。

V2.1 的分支候选由现有生成器的**可选探索模式**生成：只引用当前图节点，数量 0–3，优先未访问的 song/artist/album；图中若已有 genre/era 等节点，可作为禁用建议展示，但不能探索。复用原 LLM runtime、结构化输出与目标校验；没有候选时跳过 LLM、返回空分支并结束当前探索。旧单轮模式仍恰好生成三条，不改旧 `WorldLineResult` 约束。V2.1 为每轮分支分配与 session/revision 绑定的不重复 action ID，持久化映射；客户端只能提交该 ID，旧轮 ID 不得指向新轮节点。

探索深度初始为 0，V2.1 上限拟定为 3 次成功扩展。成功探索追加目标至 `visited_nodes`；重复提交、已访问目标或过期 action 直接拒绝，不调用 Music API/LLM。外部 API、LLM、merge 或 checkpoint 写入失败时，不返回部分新图，原快照保持可恢复；若本轮完成但没有新增节点/边，将它视为**已完成的空结果**，以 `no_new_connections` 标识并把目标记为已访问，避免反复消耗调用。会话的同一 session 操作必须串行；实现时要验证当前部署的 checkpoint 后端与并发策略，不能仅靠不同进程互不共享的锁宣称并发安全。

## 3. 预计修改文件

| 文件 | 改动范围 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/session.py`（新） | `WorldLineSessionState`、checkpoint 持久化/恢复、创建与探索服务、revision 和错误映射。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/merge.py`（新） | 纯函数合并累计图、sources 和证据 ID，先验证后返回新对象。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/models.py` | 新增 V2.1 API/会话/action/响应模型；保留旧 `WorldLineResult`。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/state.py`、`graph.py` | 加可选目标注入与旧图输入，在现有节点链中合并；旧路径不变。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/generation.py` | V2.1 可选候选集与 0–3 分支校验、原 root/本轮 focus 叙事上下文。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/service.py`、`ai-service/app/api/worldline.py` | 两个新端点的编排与 HTTP 状态；保留原接口。 |
| `soul-music-room/src/services/worldline.ts`、`soul-music-room/src/types/worldline.ts`、`soul-music-room/src/store/worldlineStore.ts`（新） | 保存 session ID 和完整结果，以 sessionStorage 保留页面导航状态，调用创建/探索 API。 |
| `soul-music-room/src/pages/WorldLinePage.tsx`（新）、`soul-music-room/src/App.tsx` | 最小可用页面：输入、节点列表、叙事和 branch 按钮；本阶段不做复杂图谱布局。 |
| `ai-service/tests/test_worldline_session.py`、`test_worldline_merge.py`（新），现有 WorldLine API/graph/generation 测试；前端相关测试 | 覆盖正常与失败链路、旧契约兼容；单元测试可显式注入 InMemorySaver。 |
| `ai-service/tests/fixtures/worldline_postgres_restart_worker.py`、WorldLine PostgreSQL restart smoke 测试（新） | 两个独立进程复用真实 PostgreSQL checkpoint：创建并写入，模拟服务重启后恢复并继续探索；复用现有 [postgres_restart_worker.py](../../ai-service/tests/fixtures/postgres_restart_worker.py) 的进程测试模式。 |

实现时只修改必要文件；不动 `app/rag/retrieval`、embedding、reranker、Query Understanding 核心实现、索引和数据库结构。

## 4. 数据结构与合并规则

`WorldLineSessionState` 服务端结构化保存：

| 字段 | 内容 |
|---|---|
| `session_id` | 随机 UUID，不由客户端指定。 |
| `root_entity` | 首轮 `ResolvedEntity`，后续不变。 |
| `current_graph` | 累计 `WorldLineGraph`，root 不变。 |
| `current_node` | 本轮 focus 节点 ID，必须存在于图中。 |
| `visited_nodes` | 唯一节点 ID 集合的稳定序列，首轮含 root。 |
| `available_branches` | 本轮服务器确认的 V2.1 `WorldLineBranch` 列表。 |
| `exploration_depth` | 成功扩展次数，上限 3。 |
| `evidence_history` | 按 ID 去重的 `EvidenceSource` 结构对象，保留历史引用。 |
| `revision` | 从 1 单调增加，防止过期 action 混用。 |

内部再保存最近一次完整 V2.1 响应及 `created_at/updated_at`，以便读回和审计；不以叙事纯文本代替图状态。Session ID 使用高熵随机值，服务端只相信 checkpoint 中的图、branch 与证据。前端缓存只用于显示，不作为后端事实来源。

`merge_graph(old, increment)` 先验证：增量 root 在旧图中且等于选中目标；node ID 去重，旧节点保留，同 ID 不同 type 报错；edge 按 `(source,target,relation)` 去重，同键合并唯一 `evidence_ids`；sources 按 ID 去重，同 ID 不同来源内容报错；所有 edge 端点与证据 ID 必须可在合并结果中解析。先构建候选结果并运行模型校验，之后才写 checkpoint。旧图/旧 sources 优先，容量不足时明确报 `capacity_reached` 并保持旧状态，不静默删除历史。沿用当前 15 节点/20 边/30 sources 约束；达到上限后不能继续扩展，这是 V2.1 的明确限制。

## 5. API 设计与前端最小闭环

| 接口 | 输入 | 成功输出 |
|---|---|---|
| 保留 `POST /api/ai/worldline` | `{query}` | 旧六字段 `WorldLineResult`，行为不变。 |
| 新增 `POST /api/ai/worldline/sessions` | `{query}` | 原六字段 `root_entity/graph/narrative/branches/sources/citations`，加 `session_id/current_node/available_actions/exploration_depth/revision`。V2.1 `branches` 为 0–3 条。 |
| 新增 `POST /api/ai/worldline/explore` | `{session_id,branch_id}` | 同一 V2.1 结构；`root_entity` 与 graph root 不变，图和 evidence 累计。 |

`available_actions[]` 含 `id`（即 Explore 的 `branch_id`）、`target_node_id`、`action_type="explore_node"`、`enabled`、`disabled_reason?`。每个 ID 只对创建它的 session/revision 有效。仅服务端当前分支能生成 action；不能提交任意 node ID。genre/era/style/influence 若出现在图或 branch 中，action 禁用；强行提交时返回 `unsupported_target_type` 和“当前版本暂不支持该类型扩展”。无可探索节点时返回空 actions。

输入校验失败 422；未知 session/branch 404；重复、过期或深度/容量冲突 409；外部音乐 API 失败 503；LLM/结构化结果失败 502。失败响应带稳定的业务 `code`；任何失败都不能污染已提交 session。前端仅保存完整 V2.1 响应及 session ID，点击 branch 按钮提交 action ID 并替换为最新响应；显示禁用原因和错误，不实现自由节点 ID 调用或复杂图形布局。

## 6. 测试方案与交付顺序

1. **状态/merge**：单元测试显式注入 InMemorySaver，覆盖创建、恢复、revision、两 session 隔离；纯函数测试节点与边去重、证据并集、类型冲突、容量/来源冲突时旧状态不变。另测生产入口在非 PostgreSQL 配置或 saver 未初始化时 fail closed。
2. **API/执行**：以固定 MusicService、Retriever、Generator 替身创建 Feather session，再选择 Nujabes action；断言旧 Feather/Nujabes 与新目录节点共存、root 不变、visited/depth/revision 更新，随后可继续选下一未访问目标。
3. **失败路径**：未知 session、无效/过期/跨 session branch、重复探索、genre branch、音乐 API 失败、LLM 失败、checkpoint 写失败；核对拒绝或失败后 snapshot 的图、证据、visited、revision 全不变。另测“无新关系”为已完成的空结果：旧图保持、目标记为已访问、revision 增加。
4. **兼容/前端**：运行既有 WorldLine 单轮与 Foundation 测试，确认旧六字段及恰好三分支不变；前端测试保存完整响应和 branch 点击参数，并检查创建、再次探索、错误与禁用交互。
5. **PostgreSQL 重启验收（必过）**：复用真实生产 session 代码、`CHECKPOINT_BACKEND=postgres`、既有 checkpoint 表及测试 PostgreSQL。进程 A 创建 WorldLine session 并确认 checkpoint 已写入；结束进程模拟服务重启。进程 B 重新初始化 saver，仅凭 `session_id` 读回结构化状态，执行保存的 branch，断言初始 root、旧节点/边、sources 均保留且 revision/depth/visited 更新。再以进程 C 读回扩展后的图。Music API 与 LLM 可以用固定替身，**checkpoint 路径必须是真实 PostgreSQL**，不能用 InMemorySaver 冒充。
6. **完整验证**：相关 `pytest`、必要的后端全套测试与静态检查、前端测试/构建/lint、关键交互及响应式检查。在线网易云/Qdrant/LLM 不可用时记录限制；若没有可用的测试 PostgreSQL，必须明确报告重启验收未通过，**不得宣称 V2.1 完成**。每个经验证的逻辑改动按 `AGENTS.md` 单独 commit。

完成编码与验证后再生成 `docs/reports/worldline_v21_implementation_report.md`，记录实际修改文件、API、测试结果、限制和下一阶段建议。该报告不会提前声称实现完成。

## 本阶段范围边界

本阶段交付“创建 → 写入 PostgreSQL checkpoint → 服务重启恢复 → 选择 song/artist/album branch → 扩展并合并 → 再保存 → 再恢复”的闭环。Jazz Hip-Hop 等 genre 分支只返回不支持，不引入新知识抽取、RAG 调优、数据库或高级音乐关系。Foundation 聊天自然语言续问与复杂图谱 UI 不作为 V2.1 验收条件；后续可以复用本阶段会话服务接入。

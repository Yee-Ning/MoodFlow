# WorldLine Agent V2 开发计划

状态：待确认；本文件是实施方案，不表示 V2 已实现。制定依据是当前工作树代码与 [WorldLine 现状审计](../audits/worldline_subgraph_current_state_audit.md)。

## 1. 当前架构问题

- 现有 WorldLine 是八节点单轮子图。`generate_branches` 后直接 `END`；独立 `POST /api/ai/worldline` 每次创建全新的私有状态，子图以 `checkpointer=False` 编译。[graph.py](../../ai-service/app/agent/foundation/subgraphs/worldline/graph.py#L625)、[service.py](../../ai-service/app/agent/foundation/subgraphs/worldline/service.py#L28)
- `branches` 是恰好三条、指向当前图中节点的建议，没有执行入口。`WorldLineResult` 不包含会话 ID、当前节点或可执行动作。[models.py](../../ai-service/app/agent/foundation/subgraphs/worldline/models.py#L206)、[api/worldline.py](../../ai-service/app/api/worldline.py#L15)
- Foundation 父图已有 checkpoint，但 WorldLine adapter 每次将 `worldline_graph` 等私有字段清空；聊天历史可辅助指代解析，不能恢复或合并上一轮图。[graph_factory.py](../../ai-service/app/agent/foundation/graph_factory.py#L16)、[adapters.py](../../ai-service/app/agent/foundation/adapters.py#L120)
- 当前图有 15 节点、20 边，结果最多 30 个 sources，不能无上限累加。目录只可解析 song/artist/album；genre/era/creator 虽在图枚举中，当前生产数据路径没有稳定的结构化关系产出。固定三分支生成在小图上还可能失败。[models.py](../../ai-service/app/agent/foundation/subgraphs/worldline/models.py#L14)、[builder.py](../../ai-service/app/agent/foundation/subgraphs/worldline/builder.py#L21)、[retrieval.py](../../ai-service/app/agent/foundation/subgraphs/worldline/retrieval.py#L209)
- 前端 `/worldline` 为静态页；聊天 service 可解析 Foundation 的 `worldline_result`，store 却只保留文本和推荐。[App.tsx](../../soul-music-room/src/App.tsx#L70)、[aiChatStore.ts](../../soul-music-room/src/store/aiChatStore.ts#L145)

## 2. V2 目标架构

```text
创建：POST /api/ai/worldline/sessions {query}
  → 复用 WorldLine 子图的 V2 模式 → 校验 V2 结果
  → 建立结构化 WorldLineSessionState → 现有 runtime checkpointer
  → 返回原六字段 + session_id/current_node/available_actions

探索：POST /api/ai/worldline/explore {session_id, branch_id}
  → 读取 checkpoint → 从该会话的 available_branches 查 branch
  → 校验目标、已访问、深度与图容量
  → 对目标复用 Query Understanding、Music API、现有可选 RAG
  → 复用 Graph Builder 产增量图 → 确定性 merge
  → 对合并图生成 narrative/branches → 校验来源与结果
  → 整轮成功后才提交新会话快照 → 返回完整累计图

Foundation 聊天：thread_id → 父图 checkpoint 中的 worldline_session_id
  → 后续“继续探索 X”只匹配该会话已呈现的可执行动作
  → 调用同一探索服务；不匹配或歧义则请求澄清
```

使用现有 `create_runtime_checkpointer()`，为 WorldLine 会话维护独立、稳定的图/服务实例；以 `worldline:<session_id>` 作为隔离后的 checkpoint thread key。checkpoint 中保存 JSON 兼容的结构化状态，并在读取时用 Pydantic 校验，不新增数据库表，也不改 RAG、Embedding、Retrieval、Reranker 或 Query Understanding 核心逻辑。`memory` 后端只保证同一服务进程存活期间连续探索；跨重启恢复需要已有 PostgreSQL checkpoint 部署。Foundation 的父图 checkpoint 只保存会话 ID 关联，不把整个累计图复制到聊天消息中。

单轮子图继续负责事实、检索、生成与校验。探索模式只增加**可信目标注入、旧图合并、原根保留和已访问目标约束**，不另写一套 RAG 或图生成器。所有输入 branch ID 必须在服务端保存的该会话分支中查得；客户端不得提交图、目标实体或证据覆盖服务端状态。会话的 `branches` 可以是 0–3 条：无足够候选时自然结束探索；旧 `WorldLineResult` 的固定三条约束及旧 API 不变，V2 使用独立响应模型保留同名六字段。

## 3. 修改文件列表（预计）

| 文件 | 计划改动 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/session.py`（新） | `WorldLineSessionState`、会话创建/读取/探索编排及 checkpoint 边界。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/merge.py`（新） | 纯函数合并节点、边、sources；去重、容量与证据引用校验。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/models.py` | 新增 V2 请求、动作、响应模型；保留原 `WorldLineResult` 六字段、固定三分支和旧接口契约；V2 同名六字段中的 `branches` 允许 0–3 条。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/state.py`、`graph.py` | 给现有子图添加可选探索输入；复用原链路，在构图后合并旧图，输出时保留初始 root。旧单轮调用不变。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/generation.py` | 叙事加入原根/当前目标的可选上下文；分支候选受可执行目标约束。必要时让 V2 在不足三条新动作时正常结束，而不伪造分支。 |
| `ai-service/app/agent/foundation/subgraphs/worldline/service.py`、`api/worldline.py` | 新增创建会话、执行分支及错误映射；保留原 `POST /api/ai/worldline`。 |
| `ai-service/app/agent/foundation/state/main.py`、`adapters.py`、`streaming.py`、路由规则（如需） | 以最小改动把 WorldLine session ID 关联到既有 Foundation thread/checkpoint；后续请求不能用默认 `None` 覆盖已保存的 ID，并须将明确的继续探索请求路由到 WorldLine。 |
| `soul-music-room/src/services/worldline.ts`、`types/worldline.ts`、`pages/WorldLinePage.tsx`（新）及 `App.tsx` | 类型化 API、图/叙事/引用/动作展示，节点与分支点击。复用现有页面样式与组件。 |
| `ai-service/tests/test_worldline_session.py`、`test_worldline_merge.py`、`test_worldline_api.py`、`test_foundation_e2e_acceptance.py`；前端对应测试 | 创建、扩展、去重、持久化、拒绝路径和交互契约。 |

最终文件范围以实现时的实际依赖为准；每一处改动须对应明确调用链和测试。

## 4. 数据结构设计

`WorldLineSessionState` 是服务端保存的结构化快照，字段如下：

| 字段 | 类型/含义 | 约束 |
|---|---|---|
| `session_id` | 不可猜测的 UUID 字符串 | 创建时生成；作为 checkpoint 查找键，不信任客户端提供的其他状态。 |
| `root_entity` | 原 `ResolvedEntity` | 全程不变。 |
| `current_graph` | 原 `WorldLineGraph` | 根 ID 始终等于 `root_entity.entity_id`；保留旧节点和边。 |
| `current_node` | 当前已探索节点 ID | 必须存在于图中。 |
| `visited_nodes` | 唯一节点 ID 列表 | 初始含根；成功探索后追加目标。 |
| `available_branches` | 当前一轮 `WorldLineBranch` 列表 | 作为服务端 branch ID → target 映射；只在新一轮成功后替换。 |
| `exploration_depth` | 已完成扩展次数 | 初始 0；建议 V2 上限 3，配置为 WorldLine 专用常量。 |
| `evidence_history` | 去重的 `EvidenceSource` 列表及必要引用信息 | 保留累计来源；不存只有文本的“记忆”。 |
| `created_at` / `updated_at` | UTC 时间戳 | 创建与成功扩展时更新。 |

此外会话持久化最近一次完整 V2 响应（用于重试、页面刷新及幂等响应）和单调 `revision`（并发/重复请求检测）。这些是内部字段，不要求前端回传。checkpoint 存字典投影，再由模型重建，避免扩充现有 checkpoint serializer 的类型白名单。会话 ID 是访问能力凭据；实现时应沿用现有用户/会话身份，绑定已有登录身份或浏览器会话，拒绝越权读取，并为匿名场景明确只在同一受控会话内使用。若部署多进程，单纯进程内锁不能保证同一 session 串行写入，需用 checkpoint 版本校验或现有 PostgreSQL 的跨进程互斥；未解决前不能宣称并发探索安全。

**合并规则**：以 node ID 去重，冲突时保留旧节点，禁止同 ID 不同类型；edge 键为 `(source,target,relation)`，重复边保留旧内容并合并不重复的 evidence ID；所有 edge 端点必须存在。sources 按 ID 去重，同 ID 内容冲突报错，且所有被边/叙事引用的来源均须保留。先验算容量和完整性，再构造新的 `WorldLineGraph` / `WorldLineResult`；失败不写入新会话快照。旧图优先，新增内容只在剩余 15 节点、20 边、30 来源额度内加入，不删除历史以腾空间。容量耗尽时给出明确的 `capacity_reached`，停止新扩展。

**访问规则**：创建时 root 已访问；探索已访问节点返回 `already_visited`，深度达到上限返回 `depth_limit_reached`；目标与服务端 branch 不一致返回 `invalid_branch`。一次有效探索若没有新增可证实节点或关系，可标记该目标已尝试并返回明确的无新增内容状态，避免重复消耗模型调用；下游调用失败则不增加深度、不标记已访问。`available_actions` 才是前端可点击动作的权威列表，可携带 `enabled` 与不可执行原因；原 `branches` 仍保留为内容建议。

**音乐实体边界**：首个可执行闭环先覆盖现有 Music API 能加载详情的 song/artist/album 节点。genre/era/creator 只有在该节点确实存在于当前图、具备可核验来源、且 WorldLine 专用证据投影能生成增量关系时才标记可执行；否则展示为暂不可扩展，不凭名称从目录或模型记忆造事实。`Nujabes → Jazz Hip-Hop` 应作为目标验收场景，但现有生产链目前不能稳定提供 Jazz Hip-Hop 图节点，因而需要一个**仅属于 WorldLine 的、证据约束的风格关系投影**，在首轮与扩展时都可使用；它从现有检索文档的明确证据片段提议受限节点/关系，经结构化校验及来源 ID 校验后交给现有 builder，不改底层检索。无有效知识证据时该场景应明确返回不可扩展，不能假装成功。

## 5. API 与前端契约

- **兼容旧接口**：`POST /api/ai/worldline {query}` 继续返回原六字段 `WorldLineResult`，供现有调用方使用。
- **新建会话**：`POST /api/ai/worldline/sessions {query}`。返回原六字段 `root_entity, graph, narrative, branches, sources, citations`，另加 `session_id, current_node, available_actions`、`exploration_depth`。V2 `branches` 可为空；返回 `revision` 用于防止重复提交。
- **探索分支**：`POST /api/ai/worldline/explore {session_id, branch_id, expected_revision?}`。成功返回同一 V2 结构，`graph` 是累计图，`root_entity` 是首次实体，`current_node` 是本轮目标。
- **节点点击**：前端只允许点击 `available_actions` 对应的节点；直接点节点时选取该节点对应 `branch_id` 调用同一 Explore API，不另开无校验的 node ID 接口。
- **错误语义**：输入无效 422；会话或 branch 不存在 404；已访问、过期 branch/revision 或并发冲突 409；达到深度/容量返回可识别的业务状态；外部音乐服务失败沿用 503，生成失败 502。失败响应不返回部分更新的图。
- **前端**：新增 WorldLine 页面消费 V2 服务；显示累计图、当前节点、已访问节点、叙事、分支及 sources/citations，分别处理 loading、澄清、错误、上限和禁用动作。Foundation 聊天结果的 `metadata.worldline_result` 继续可用，聊天 store 后续也须保留结构化结果；前端不得靠解析文字恢复图。

## 6. 开发步骤与验证门槛

1. **会话与容量基础**：实现 State/merge 纯函数、checkpoint 包装和会话创建 API；用注入式 saver 测创建、读取、重启语义（memory/postgres 边界）及旧接口兼容。
2. **目录实体分支执行**：branch ID 验证、目标 ID 精确解析、复用 Query Understanding/Music API/现有 RAG、增量图合并、叙事和分支再生成；覆盖旧图保留、边证据合并、已访问、深度、容量、失败不写状态。
3. **风格分支的有证据扩展**：在 WorldLine 边界加入窄范围结构化知识关系投影，用于首轮生成 genre 节点和后续增量关系，严校来源和节点/边；以固定 Nujabes/Jazz Hip-Hop 证据 fixture 测试。真实环境无相关证据时验证安全降级。此步不调检索参数、不改索引与 reranker。
4. **Foundation 连续性**：父图保存 `worldline_session_id`，将“继续探索 X”在当前会话可用动作中匹配，复用同一探索服务；不匹配/歧义请求澄清。验证两轮同 thread 继承图，另一 thread 不串图。
5. **前端闭环**：替换 `/worldline` 占位页，接创建/探索 API、图与来源展示、节点/分支点击；运行前端测试、构建、静态检查，并检查关键交互与响应式布局。
6. **完整验证与交付**：运行 WorldLine 单元/API/Foundation 集成测试，相关后端测试与全套必要测试、构建、静态检查；按项目规则逐个逻辑改动提交独立 commit。在线 Qdrant/网易云/LLM 与 PostgreSQL 验证依环境可用性进行，未验证项明确记录。

**验收条件**：同一会话两次探索后仍含首轮所有已保存的节点、边及根；第二轮可增加新的、带有效来源的关系；重复点击不再生成；深度/容量和失败路径不污染状态；页面可点击并展示最新累计图；旧单轮 API 和现有测试保持可用。对风格场景，只有在证据足以创建真实 genre 节点与关系时才算成功。

## 待确认的实施边界

建议按上述步骤分批开发。最关键的范围决定是 **Jazz Hip-Hop 等 genre 分支是否纳入本轮 V2 的实际可执行验收**：纳入则需要第 3 步的 WorldLine 专用证据关系投影；若仅先交付目录实体闭环，genre 分支会明确标记为不可执行，不能宣称已满足该示例。确认计划后再修改代码。

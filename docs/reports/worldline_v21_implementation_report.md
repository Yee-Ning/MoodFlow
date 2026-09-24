# WorldLine Agent V2.1 实施报告

## 1. 实施结论

本轮完成了 song / artist / album 的连续探索核心闭环：创建 WorldLine → 写入现有 PostgreSQL LangGraph checkpoint → 从服务端生成的 branch 选择目标 → 对目标重新执行原 WorldLine 查询链 → 合并累计图和来源 → 更新叙事及分支 → 保存下一版会话。旧的 `POST /api/ai/worldline` 单轮行为保留。

真实 PostgreSQL 验收使用三个独立进程：A 创建并保存；B 在“服务重启”后仅凭 session ID 恢复、探索并写入；C 再次恢复，验证旧图、来源与新节点均存在。另以两个独立进程同时提交同一 branch，验证只有一个进程成功提交。

## 2. 已实现功能

- **结构化 SessionState**：保存 `session_id`、原始根实体、累计图、当前节点、已访问节点、当前分支、探索深度、历史 evidence、revision、最近完整结果及时间戳；加载时重新校验 Pydantic 模型。会话本体存入现有 LangGraph checkpointer，未新增数据库或正式 MemorySaver 存储。
- **两条新 API**：`POST /api/ai/worldline/sessions {query}` 和 `POST /api/ai/worldline/explore {session_id, branch_id}`。响应保留 `root_entity/graph/narrative/branches/sources/citations`，补充 `session_id/current_node/available_actions/exploration_depth/revision/outcome`。旧 API 不变。
- **可信探索目标**：服务端从当前 checkpoint 中查 branch ID，校验目标是图中同名、同类型的 `netease:song|artist|album:<正整数>` 节点，再使用原 Music API 事实加载节点校验身份。客户端不能通过任意 node ID 探索；旧轮未使用的 branch ID 在新 revision 中失效，已执行 branch ID 保留在消费历史中并返回重复探索冲突。Genre 等类型明确拒绝。
- **沿用原执行链**：V2.1 模式复用原子图的 Query Understanding/SearchPlan、Music API、可选 RAG、Graph Builder、Narrative 与 Branch 生成。增量图在叙事前合并，最终根始终是首轮实体。分支结构化输出在探索模式允许 0–3 个未访问的目录节点；旧模式仍要求恰好 3 个。
- **图与证据合并**：保留旧节点，同 ID 不同类型报错；边按 `(source,target,relation)` 去重并合并 evidence ID；来源按 ID 去重并校验同 ID 内容一致。严格检查边端点、证据引用和原模型容量限制。计算、校验通过后才把新快照提交给 checkpoint。无新增节点或边时返回 `outcome=no_new_connections` 并标记目标已访问。
- **深度与并发**：最多成功扩展 3 次。同一 session 先在进程内串行；正式 PostgreSQL 路径再使用事务级 advisory lock 防止多个进程同时提交同一 branch。生产入口只接受已初始化的 PostgreSQL checkpointer，其他配置返回 `persistent_checkpoint_required`。
- **最小前端闭环**：`/worldline` 显示输入、累计节点、叙事、branch、来源数量和引用；节点与 branch 按服务端 `available_actions` 发起探索。完整响应存入 `sessionStorage` 供页面导航恢复；服务端 checkpoint 仍是唯一事实来源。

## 3. 修改文件

| 范围 | 文件 |
|---|---|
| 状态、合并、运行链 | `ai-service/app/agent/foundation/subgraphs/worldline/{session,merge,models,state,graph,generation}.py` |
| API 与生命周期 | `ai-service/app/api/worldline.py`、`ai-service/app/main.py` |
| 后端测试 | `ai-service/tests/{test_worldline_session,test_worldline_merge,test_worldline_postgres_restart,test_worldline_api}.py`、`ai-service/tests/fixtures/worldline_postgres_restart_worker.py` |
| 前端 | `soul-music-room/src/{types/worldline.ts,services/worldline.ts,store/worldlineStore.ts,pages/WorldLinePage.tsx,pages/WorldLinePage.css,App.tsx}`、`soul-music-room/scripts/test-worldline-flow.mjs`、`soul-music-room/package.json` |
| 文档 | `docs/plans/worldline_v21_implementation_plan.md`、本报告 |

未修改 RAG、Embedding、Retrieval、Reranker、Query Understanding 核心实现、数据库结构或依赖。

## 4. API 行为与错误

新会话从 revision 1、depth 0 开始。探索成功后 revision 和 depth 各加 1，`visited_nodes` 增加目标。当前 action ID 以每轮生成的随机值标识，服务端保存精确映射。未知 session/branch 返回 404；重复目标、容量或深度冲突返回 409；不支持的目标类型返回 422；Music API 或 checkpoint 不可用返回 503；LLM 或结果校验失败返回 502。失败时不提交新 session 快照。

## 5. 验证结果

| 验证 | 结果 |
|---|---|
| WorldLine 相关后端测试 | `37 passed`（session、merge、原 graph、generation、API） |
| 应用测试目录 | `905 passed, 13 skipped, 206 subtests passed`；在重复 branch 错误码完善前运行，随后相关测试与 PostgreSQL 测试再次通过 |
| 真实 PostgreSQL 重启与跨进程抢 branch | `2 passed`，使用现有数据库 checkpoint 表；未创建新表 |
| Python 静态检查 | `ruff check` 通过 |
| 前端 | `npm run build`、`npm run lint`、`npm run test:worldline` 通过 |
| 响应式视觉 | 本地浏览器检查了默认、桌面和 390px 手机断点的空状态页面；输入和创建按钮可见 |

执行全量 `python -m pytest -q` 时，评测目录 `evals/tests/test_judges.py` 在收集阶段因现有环境缺少 `deepeval` 中断；本轮没有安装依赖。随后对应用测试目录执行了完整测试。前端自动化验证使用模拟 API 响应；真实在线 Music API、RAG 后端与 LLM 的端到端业务请求未在本轮环境中执行。

## 6. 当前限制与下一阶段建议

- V2.1 只执行 song / artist / album 节点；genre、era、style、influence 等高级探索仍不支持。后续可单独设计这些类型的可信数据来源与分支执行规则。
- 继续沿用 `WorldLineGraph` 的 15 节点、20 边、30 sources 上限及深度 3；达到容量后会显式失败并保留旧会话。后续应设计分页或图摘要，避免简单提高容量导致上下文膨胀。
- 前端目前是节点列表和分支操作，不是复杂图谱画布。后续可在不改变 API 的前提下增加节点和边可视化、来源跳转及恢复会话入口。
- 当前 browser 视觉检查只覆盖空状态；完整有数据页面已由前端模拟交互测试验证数据流，但未做真实浏览器视觉验收。后续结合可用的 Music API 与 LLM 测试环境完成端到端 UI 验收。

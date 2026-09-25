# WorldLine V2.3C Frontend Interaction & Evidence UI 实施报告

## Executive Summary

WorldLine V2.3C 已完成。现有 song / artist / album / genre WorldLine 现在通过前端提供完整的 Graph Visualization、服务端 Action 驱动探索、Edge Evidence Panel 和 PostgreSQL checkpoint 恢复 UX。

本阶段没有增加新的 WorldLine 推理能力，没有修改 Semantic Expansion、Genre Executor、EntityBridge、RAG、Retrieval、Embedding、Reranker、数据库 schema、relation type 或 genre executable 配置。

## 1. 已实现功能

### 1.1 Graph Visualization

- 使用 React 原生 SVG 绘制后端返回的 nodes 与 edges，没有新增图谱依赖。
- 支持 song、artist、album、genre 的独立视觉样式。
- node key 保留后端 `node_id`；edge key 保留 `(source, target, relation)`。
- 明确区分 current node、visited node、actionable node 和 disabled node。
- disabled genre 继续展示，但不会触发 explore。
- relation label 直接来自后端；前端不推断、不合并、不生成关系。

### 1.2 Interactive Exploration

- node 与 branch 只查找 `available_actions` 中对应的服务端 action。
- 只有 `enabled=true` 的 action 可以提交。
- explore 请求只发送服务端生成的 `branch_id`，客户端不能构造任意 node exploration。
- pending branch 与全局 loading 会阻止重复点击。
- 成功后以服务端返回的完整 `WorldLineSessionResponse` 原子替换页面状态。
- 失败时保留旧 graph、revision、current node、visited nodes 与 evidence，只展示错误信息。

### 1.3 Evidence Panel

- 点击 edge 后显示 source node、target node、relation 和 evidence IDs。
- `edge.evidence_ids` 精确映射 `WorldLineResult.sources`。
- 显示后端提供的 source title、publisher/source、URL、excerpt、metadata。
- 使用 `WorldLineResult.citations` 标记当前 narrative 实际引用的来源。
- source 或 excerpt 缺失时展示 evidence unavailable，不生成或补写证据。

### 1.4 Session Recovery UX

- 新增只读 `GET /api/ai/worldline/sessions/{session_id}`。
- endpoint 调用现有 `WorldLineSessionService`，从现有 LangGraph persistent checkpointer 读取最新结构化 session。
- response 新增 `visited_nodes`，并校验 current/visited nodes 全部属于 graph。
- 浏览器只保存 `session_id`，不再把 graph/result 作为恢复状态真源。
- 页面刷新后调用 recovery API，恢复 graph、current node、visited nodes、depth、revision 与 actions。
- 404 `session_not_found` 清除失效 id 并提示重新创建；临时失败保留 id，可重试恢复。
- recovery 为只读操作，不提交 checkpoint，不改变 revision。

## 2. API 变化

### 新增

```http
GET /api/ai/worldline/sessions/{session_id}
```

成功返回完整 `WorldLineSessionResponse`。WorldLine session 仍由生产路径的 PostgreSQL LangGraph checkpointer 提供。

### Response 补充

```json
{
  "visited_nodes": ["netease:song:1", "netease:artist:2"]
}
```

现有 create/explore endpoint 和 request contract 保持不变。

## 3. UI 状态覆盖

- 创建 loading
- checkpoint recovery loading
- empty graph
- no available branch
- disabled branch / genre reason
- explore failure and old graph preservation
- invalid / expired session
- evidence unavailable
- duplicate click suppression
- no new connections

## 4. 修改文件

### 后端

| 文件 | 修改 |
|---|---|
| `ai-service/app/agent/foundation/subgraphs/worldline/models.py` | Response 增加 visited nodes 与一致性校验 |
| `ai-service/app/agent/foundation/subgraphs/worldline/session.py` | 映射 visited nodes；增加只读 recover |
| `ai-service/app/api/worldline.py` | 增加 session recovery GET API |
| `ai-service/tests/test_worldline_api.py` | recovery success/error contract 测试 |
| `ai-service/tests/test_worldline_session.py` | visited nodes 与 recovery 不写 checkpoint 测试 |

### 前端

| 文件 | 修改 |
|---|---|
| `soul-music-room/src/types/worldline.ts` | 对齐完整 session/source/node contract |
| `soul-music-room/src/services/worldline.ts` | recovery GET 与稳定 API error code |
| `soul-music-room/src/store/worldlineStore.ts` | server-truth recovery、session-id-only persistence、atomic failure UX |
| `soul-music-room/src/components/worldline/WorldLineGraph.tsx` | SVG graph visualization 与 server action interaction |
| `soul-music-room/src/components/worldline/WorldLineEvidencePanel.tsx` | evidence/citation panel |
| `soul-music-room/src/components/worldline/worldlineViewModel.ts` | deterministic layout、edge identity、evidence mapping |
| `soul-music-room/src/pages/WorldLinePage.tsx` | 完整 workspace 与状态 UX |
| `soul-music-room/src/pages/WorldLinePage.css` | graph/evidence/responsive 样式 |
| `soul-music-room/scripts/test-worldline-flow.mjs` | action、duplicate、failure、recovery 测试 |
| `soul-music-room/scripts/test-worldline-view.mjs` | graph 与 evidence render 测试 |
| `soul-music-room/package.json` | WorldLine test script 汇总 |

## 5. 测试结果

| 验证 | 结果 |
|---|---:|
| 全部 `test_worldline*.py` | 107 passed、3 skipped |
| 后端 Ruff | passed |
| `npm run test:worldline` | passed |
| `npm run test:ai-layout` | passed |
| `npm run test:ai-stream` | passed |
| `npm run test:ai-trigger` | passed |
| `npm run lint` | passed |
| `npm run build` | passed |

WorldLine 前端测试覆盖：

1. 初始 graph node/edge identity 渲染。
2. branch click 使用服务端 branch id，并用完整 response 扩图。
3. disabled genre 可见且不可执行。
4. edge evidence IDs 映射 source、excerpt 和 citation。
5. concurrent duplicate click 只产生一个请求。
6. explore failure 保留旧 graph object 与 revision。
7. refresh/recovery 恢复 current、visited、depth 与 actions。
8. expired session 清理浏览器 session id。

## 6. 视觉验证

使用本地 mock API 和浏览器实际运行页面完成：

- 桌面视口：双栏 graph/evidence workspace、session status、narrative 和 branch panel 正常。
- 窄屏视口：graph 与 evidence panel 转为单栏，fixed player 上方保留页面滚动空间。
- Edge 选择后正确显示后端 exact excerpt、source metadata 与 narrative citation 标记。
- disabled genre 分支保持可见并明确显示不可执行原因。
- 浏览器刷新后通过 recovery GET 恢复同一 `session_id`。

## 7. 当前限制

- SVG 使用确定性环形布局，适合当前最多 15 个节点；本阶段没有加入拖拽、缩放或自动布局引擎。
- 前端只展示后端已有 evidence；Music API source 没有 excerpt 时会明确显示不可用。
- 三个 PostgreSQL restart acceptance 测试需要设置 `RUN_WORLDLINE_POSTGRES_RESTART=1`，当前环境未启用，因此被跳过。生产 recovery endpoint 与 session create/explore 使用同一个 PostgreSQL checkpointer factory，未增加替代存储。
- Vite build 保留项目既有的大 chunk warning；构建成功，本阶段没有引入新依赖。
- 未增加 influence、era、scene 或新的 executable genre。

## 8. 结论

V2.3C 已把现有 WorldLine 后端能力形成可用的产品交互闭环：

```text
Create / Recover Session
        ↓
Render Full Graph
        ↓
Select Server-issued Action
        ↓
Explore and Replace Full Result
        ↓
Inspect Edge Evidence
        ↓
Refresh and Recover from Checkpoint
```

本阶段到此停止，不进入后续语义关系扩展。

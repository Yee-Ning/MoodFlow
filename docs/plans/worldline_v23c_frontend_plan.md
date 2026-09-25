# WorldLine V2.3C Frontend Interaction & Evidence UI 实施计划

## 1. 当前实现与问题

### 技术栈

- 前端位于 `soul-music-room`，使用 React 19、TypeScript、Vite、React Router 和 Zustand。
- 项目没有 React Flow、Cytoscape 或 D3 图谱依赖；现有图标库为 `lucide-react`。
- `/worldline` 已有最小页面，当前以两个列表展示 graph nodes 与 branches。

### 现有调用链

```text
WorldLinePage
  -> Zustand worldlineStore
  -> POST /api/ai/worldline/sessions { query }
  -> WorldLineSessionResponse

Node / branch click
  -> server-issued available_actions[].id
  -> POST /api/ai/worldline/explore { session_id, branch_id }
  -> full WorldLineSessionResponse replaces the displayed result
```

后端响应已包含 `root_entity`、`graph`、`narrative`、`branches`、`sources`、`citations`、`session_id`、`current_node`、`available_actions`、`exploration_depth`、`revision` 和 `outcome`。Graph edge 保存后端 node id、relation 和 `evidence_ids`；EvidenceSource 已包含 title、source、URL、excerpt 与 metadata。

### 当前缺口

1. 节点与关系只以列表/计数展示，没有真实 graph visualization。
2. edge 不可选择，无法查看 evidence/citation。
3. `sessionStorage` 保存完整结果并直接恢复，浏览器副本事实上参与了状态恢复。
4. 公开响应没有 `visited_nodes`，前端无法可靠区分已访问节点。
5. 后端没有只读 session recovery endpoint；现有 create/explore API 无法在刷新时从 PostgreSQL checkpoint 恢复完整响应。
6. 页面只提供统一 loading/error，缺少 recovery、empty graph、expired session、evidence unavailable 等明确状态。

## 2. V2.3C 目标架构

```text
Create session / Recover session / Explore branch
                    |
                    v
       PostgreSQL LangGraph checkpoint
                    |
                    v
       WorldLineSessionResponse (server truth)
          | graph + visited/current state
          | available_actions / branches
          | sources / citations
          v
  WorldLinePage
    + SVG Graph Visualization
    + Server-action Interaction
    + Evidence Panel
    + Session Recovery UX
```

浏览器只持久化 `session_id`。页面刷新后必须调用 recovery API 重新读取 checkpoint，不能从浏览器缓存恢复 graph、visited nodes、depth 或 actions。

## 3. 修改文件

### 后端 API contract（不修改推理链）

- `ai-service/app/agent/foundation/subgraphs/worldline/models.py`
  - 在 `WorldLineSessionResponse` 暴露 checkpoint 已有的 `visited_nodes`。
- `ai-service/app/agent/foundation/subgraphs/worldline/session.py`
  - `_response` 映射 `visited_nodes`。
  - 新增只读 `recover(session_id)`，读取 checkpoint 并返回完整 response，不生成 graph、不修改 session。
- `ai-service/app/api/worldline.py`
  - 新增 `GET /api/ai/worldline/sessions/{session_id}`。
- `ai-service/tests/test_worldline_api.py`
  - 验证 recovery success、404/503 错误映射与完整响应。
- 现有 session 测试
  - 验证 visited nodes 与 recovery 不写 checkpoint。

### 前端

- `soul-music-room/src/types/worldline.ts`
  - 对齐完整 node metadata、source excerpt/metadata、visited nodes 与 API error contract。
- `soul-music-room/src/services/worldline.ts`
  - 增加 recovery GET 和稳定错误码解析。
- `soul-music-room/src/store/worldlineStore.ts`
  - 只持久化 session id。
  - 增加 recovery、pending branch、session status、clear session。
  - create/explore 成功后原子替换完整 response；失败保留当前 graph。
- `soul-music-room/src/components/worldline/WorldLineGraph.tsx`
  - 使用原生 SVG 绘制后端 nodes/edges，不推断或生成关系。
  - 标识 current、visited、可执行、disabled genre。
  - 节点探索只调用匹配的服务端 action id；edge 选择只打开 evidence panel。
- `soul-music-room/src/components/worldline/WorldLineEvidencePanel.tsx`
  - 按 edge.evidence_ids 映射 response.sources/citations。
  - 展示 source/target/relation、excerpt、title、URL、source metadata；缺失时显示 unavailable。
- `soul-music-room/src/pages/WorldLinePage.tsx` 与 CSS
  - 整合 graph、narrative、actions、evidence、recovery 与所有空/错状态。
- `soul-music-room/scripts/test-worldline-flow.mjs`
  - 扩充 store/API/duplicate click/failure/recovery 测试。
- 新增前端 WorldLine view test script
  - 用 React server rendering 与纯映射函数验证 graph、disabled genre、edge evidence。
- `soul-music-room/package.json`
  - 增加相应测试脚本；不新增依赖。

## 4. 数据与交互规则

### Graph

- node key 始终使用后端 `node.id`。
- edge key 使用后端 `(source, target, relation)`；不在前端合并或改写 relation。
- 支持 song、artist、album、genre 的独立视觉类型；未知现有类型使用通用样式但保留原 type。
- `current_node` 使用强标识；`visited_nodes` 使用已访问标识。
- genre 没有 enabled action 时仍展示，且不可提交探索。

### Explore

- UI 只接受 `available_actions` 中 `enabled=true` 的 action。
- 请求体只使用该 action 的服务端 `id` 作为 `branch_id`。
- pending 时阻止再次提交；成功后用完整 response 替换 result。
- 请求失败只更新错误状态，不清除或局部修改当前 graph。

### Evidence

- 选择 edge 后，以 `edge.evidence_ids` 精确查找 `sources`。
- `citations` 仅用于标记 evidence 是否被当前 narrative 引用。
- excerpt/exact span 只展示后端 `EvidenceSource.excerpt`；缺失时显示“暂无可展示证据”，不生成补充文本。

### Recovery

- sessionStorage 仅保存 session id。
- 页面首次挂载调用 `GET /api/ai/worldline/sessions/{session_id}`。
- 404 表示 invalid/expired，清除 session id 并展示明确状态。
- 503 等临时失败保留 session id，允许用户重试。

## 5. UI 状态

- Initial loading / session creation loading
- Session recovery loading
- Empty graph
- No available branch
- Explore failure with previous graph preserved
- Invalid or expired session
- Evidence unavailable
- Disabled genre/action reason
- Duplicate click suppression

## 6. 测试方案

1. 初始 response 的 node/edge 和 identity 正确渲染。
2. enabled branch 点击只提交 server branch id，返回后完整 graph 扩展。
3. disabled genre node/branch 可见但不可执行。
4. edge evidence_ids 正确映射 source、excerpt 和 citation 状态。
5. 并发 duplicate click 只产生一个 explore request。
6. explore failure 后保留旧 result/revision/graph。
7. 刷新只从 session id 调 recovery API，完整恢复 current、visited、depth 和 actions。
8. recovery 404 清理失效 session；临时失败允许重试。
9. 后端 API/session 定向回归通过。
10. 前端 WorldLine tests、ESLint、TypeScript/Vite build 与既有回归通过。

## 7. 边界

- 不修改 WorldLine Graph、checkpoint schema、Semantic Expansion、Genre Executor、EntityBridge 或 RAG。
- 不开放任何新 genre，不加入 influence、era、scene 等关系。
- 不在前端推理 relation、evidence 或 executable 状态。
- 本阶段唯一新增 API 是只读 session recovery；原因是现有 create/explore API 无法在刷新后从 checkpoint 读取完整 session。

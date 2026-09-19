# Foundation 主图与 Subgraph V1 E2E 接线验收

验收日期：2026-09-19（Asia/Shanghai）
范围：Foundation MainGraph、CurrentSong、ToolCalling、GeneralChat、WorldLine，以及聊天 SSE / Foundation JSON 返回协议。
本轮未修改 Gold 数据、RAG 检索或重排参数、embedding/reranker 模型；未开展 Memory、压缩、限流、安全或部署工作。

## 1. Foundation 主图验收结果

**代码接线通过；真实环境仍有 WorldLine 外部依赖阻塞。** `AI_AGENT_GRAPH_MODE` 的代码默认值和 `.env.example` 已设为 `foundation`，本地配置解析结果也是 `foundation`。原有 `legacy` 模式仍可通过环境变量显式选择。真实 Hybrid Router 的离线脚本成功加载现有模型工件，覆盖规则路由、模型路由、多意图和澄清。独立启动的当前代码实例（`127.0.0.1:8002`）完成了 4 条路由的实际 HTTP 派发：CurrentSong 和 GeneralChat 返回成功，ToolCalling 返回正常动作或缺少歌曲的 `needs_input`，WorldLine 进入子图后因实体搜索失败返回结构化 `failed`。

MainGraph 按 `RouteDecision.tasks` 的依赖顺序派发，子图完成后返回 `aggregate_results`，最终写回当前轮答复。`development_routes` 也改为顺序执行，前置结果可以传入后续子图。同一轮前置 ToolCalling 的实体引用现在可交给后续 ToolCalling；跨轮历史引用仍受现有 5 组上限约束。主图是唯一 checkpoint 边界，四个子图显式关闭继承式 checkpoint，避免 WorldLine 检索文档在子图内部序列化失败。子图私有状态不会进入 `MainState`。

`FAILED` 会停止后续任务，`NEEDS_INPUT` 返回用户可读的补充请求；路由不确定进入 `clarify`，现在也能正常发出 SSE `final`。模型/子图抛出的未恢复异常由 API 转为通用错误，不把内部异常正文传给客户端。

## 2. 各 Subgraph 状态

| Subgraph | 状态 | 正常 case | 边界 case | 失败或 fallback case | 尚缺真实验收 |
| --- | --- | --- | --- | --- | --- |
| CurrentSong | **已完成 V1 接线** | HTTP→Router→歌曲上下文→分析→主图→JSON 成功；真实本机歌曲分析成功 | 无当前歌曲返回 `needs_input`；同线程切换歌曲 ID 后重新读取新歌曲上下文 | 上下文提供者超时返回不泄漏内部信息的 `failed` | 多轮真实播放器切歌后的浏览器联动；RAG 目前仅保留 `rag_context` 接口，`rag_used=false` |
| ToolCalling | **部分完成** | 受控 E2E 中真实执行测试工具并回填结果；本机 SSE 暂停请求产生 1 个动作 | 无当前歌曲的暂停请求返回 `needs_input`，不产生动作；非法参数不执行工具 | 超时返回 `failed`；同参数重复调用不再次执行；批量重复调用可修正后重试 | 真实服务端搜索/歌单写入、浏览器播放器动作完成确认 |
| GeneralChat | **已完成 V1 接线** | HTTP→Router→无工具模型→主图→JSON 成功；本机真实 LLM 对话成功 | 同线程第二轮能读取前轮历史；前置任务摘要沿适配器传入 | 模型返回非法工具调用时 JSON API 以 502 失败关闭 | 更广泛的真实多轮语义质量验收，不属于本轮接线门禁 |
| WorldLine | **部分完成** | 受控 E2E 跑通 Query Understanding/Plan→候选检索→Rerank→Context→生成→来源/引用→主图结果 | 未找到实体返回 `needs_input` | 检索异常降级到音乐目录事实并成功答复；真实本机实体搜索失败返回 `failed`，原因 `entity_search_failed` | Spring 的实体搜索上游返回 502；Qdrant 未启动，真实冻结 V3 索引检索及重排未验证 |

WorldLine 的受控链路使用注入的候选池和重排器，实际运行项目当前的检索适配、Context 选择、图构建和引用返回代码。它证明接线与数据契约，**不代表真实 Qdrant、Qwen Reranker 或冻结语料的质量验收**。现有 V3 `index_build_manifest.json` 可成功绑定，Manifest 标记 ready，记录 1,270 个点；运行配置仍为 `AI_RAG_RETRIEVAL_MODE=empty`、`AI_RAG_RERANK_MODE=off`，且未配置在线 Manifest。本轮未更改这些开关或参数。

## 3. 统一输出协议与 API

内部四个子图继续只向主图返回 `TaskResult`。公开的 Foundation 响应统一为以下形状；多意图请求包含多个 `results`，澄清请求的 `results` 为空：

```json
{
  "type": "chat",
  "status": "success",
  "message": "最终答复",
  "search_query": null,
  "results": [
    {
      "task_id": "...",
      "status": "success",
      "subgraph": "worldline",
      "answer": "...",
      "citations": [],
      "sources": [],
      "tool_calls": [],
      "metadata": {}
    }
  ]
}
```

`POST /api/ai/foundation` 返回普通 JSON；Foundation 模式下 `POST /api/ai/chat` 的 SSE `final` 使用相同对象。API 级测试验证两者内容一致。旧的 `type/message/search_query` 字段保留，前端 `aiChat` 解析器现在能读取 `status/results`；播放器 `action` 仍作为独立 SSE 事件发送。WorldLine 的来源和引用经 `WorldLineResult` 校验后再写入统一结果；Pydantic 负责 JSON 序列化，非法结构会失败关闭。Foundation 答复不再受旧聊天模型 2000 字输出约束，长 WorldLine 结果不会仅因长度触发流式错误。

`POST /api/ai/worldline` 保留专用 `WorldLineResult` JSON 契约，供直接使用图数据的调用方使用。现有 `/worldline` 页面仍是静态入口；聊天前端虽能解析结构化结果，尚未把图、分支和引用渲染成 WorldLine 页面。

## 4. 已修复问题

1. `development_routes` 并行执行有依赖的任务，导致前置任务结果无法稳定传递；现按任务顺序执行。
2. 同轮后续 ToolCalling 无法读取先前 ToolCalling 的实体引用；现优先取本轮前置任务，再取历史会话结果。
3. 四个子图继承主图 checkpoint 时，WorldLine 的检索文档导致序列化失败；现只在主图 checkpoint。
4. 澄清分支没有 `final_response` 节点，原 SSE runner 会误判无最终答复；现可返回 `needs_input` 的 `final`。
5. Foundation SSE 只给文本、长答复会撞旧 2000 字限制；现提供统一结构及等价 JSON 入口，前端解析器同步兼容。
6. ToolCalling 仅靠提示词避免重复工具调用；现执行前确定性拒绝同参数重复请求，并覆盖批量重复与重试。

## 5. 验证记录

| 命令或验证 | 结果 |
| --- | --- |
| `ai-service/.venv/Scripts/python.exe -m pytest -q tests` | **801 passed，2 skipped，198 subtests passed** |
| `ai-service/.venv-eval/Scripts/python.exe -m pytest -q evals/tests` | **4 passed** |
| `ai-service/.venv/Scripts/python.exe -m ruff check`（本轮修改的 Python 文件） | **通过** |
| `soul-music-room: npm run test:ai-stream` | **通过**，含 Foundation 结构化 `final` 解析 |
| `soul-music-room: npm run build` | **通过**，有既存大 chunk 提示 |
| `soul-music-room: npm run lint` | **通过** |
| `scripts/intent/test_hybrid_router.py --device cpu` | **通过**，使用现有模型工件和离线 LLM fixture |
| 本机独立 Uvicorn `8002` 的 `/health` | **200** |
| 本机 `8002` 的 `/api/ai/foundation` | ToolCalling 无歌曲 `needs_input`；GeneralChat、CurrentSong 成功；WorldLine `failed/entity_search_failed` |
| 本机 `8002` 的 `/api/ai/chat` | ToolCalling 成功，SSE 含 1 个 `action`、`final`、`done` |
| V3 Manifest 绑定校验 | **通过**，指向现有 V3 collection |

`tests/test_foundation_e2e_acceptance.py` 提供每个子图至少一个正常、边界和失败/fallback 的 API 级用例；另有冻结 RAG 组件接线和前置任务摘要传递用例。测试使用可控音乐服务、工具、候选池和生成器，避免把网络故障或生成质量误当作代码接线结果。前端本轮只修改协议解析，不修改可见 UI，因此未进行新的响应式视觉验收。

## 6. 真实阻塞项与冻结建议

- **WorldLine 实体搜索：** Spring Boot 端口 `8080` 可连接，但 `GET /api/music/entities/search?keyword=晴天&type=song&limit=5` 返回 **502**；因此真实 WorldLine 在 `resolve_entity` 停止，后续真实 RAG 与生成阶段无法验收。
- **Qdrant：** 本机 `127.0.0.1:6333` 无监听。冻结 V3 索引 Manifest 的文件绑定成功，真实集合查询、重排耗时和最终证据质量未验证。
- **播放器执行：** 真实 SSE 发出了客户端动作，前端模拟测试验证解析和白名单执行器；本轮未进行真实浏览器播放器交互及服务端歌单写入验收。
- **运行实例：** 原有 `8001` 实例在测试时曾超时且随后健康检查不可达；为排除旧进程状态，真实 HTTP 验证使用独立启动的当前代码实例 `8002`。

**结论：Foundation 主图、CurrentSong、GeneralChat 和四子图统一协议可作为 V1 代码接口冻结；ToolCalling 与 WorldLine 目前不宜标记为真实端到端验收完成。** 完成实体搜索上游恢复、Qdrant 冻结索引在线验证和浏览器动作验收后，才能对整个 Subgraph V1 作无条件冻结。RAG 参数调优可留到下一阶段。

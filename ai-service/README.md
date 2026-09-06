# MoodFlow AI Service

FastAPI + LangGraph 音乐 Agent。当前聊天路由会先通过 Spring Boot 解析正在播放的歌曲上下文，再让绑定真实业务工具的 LangChain ChatModel 自主决定直接回答或调用工具。

## Setup

```powershell
cd D:\code\ProjectsAll\MoodFlow\ai-service
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

复制 `.env.example` 为 `.env` 并设置 `LLM_API_KEY`。默认示例使用 OpenAI-compatible Chat Completions 接口。

## Run

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

## Agent Graph

```text
START
  -> load_song_context
  -> agent
       -> tools -> agent  (最后一条 AIMessage 有 tool_calls)
       -> END             (没有 tool_calls)
```

`MusicAgentState.messages` 继续使用 LangGraph 的 `add_messages` reducer，`InMemorySaver` 按 `thread_id` 保留 `HumanMessage`、`AIMessage(tool_calls)`、`ToolMessage` 和最终 `AIMessage`。每次 HTTP 请求只调用一次 `graph.astream`，并保持 `stream_mode=["messages", "updates"]`。

工具调用最多执行 5 次。超过上限时，`agent` 返回友好终止消息。`ToolNode` 把校验、后端或网络异常转换为模型可理解的错误 `ToolMessage`，React 只接收节点名称与完成状态，不接收工具内部结果。

## Graph Foundation V1

`app/agent/foundation` 是独立的 MainGraph 基础设施，现已支持 Rule → Model → LLM → Merger 动态路由。它尚未替换上面的生产 Chat HTTP/SSE 图；业务子图仍为 Stub。

```text
START
  -> prepare_context
  -> development_route
       -> one or more Stub SubGraphs
  -> aggregate_results
  -> final_response
  -> END
```

上图为显式传入 `development_routes` 时的兼容开发模式。默认 `create_main_graph()` 改用动态路由；可传入 `intent_router` 注入测试或替代运行时。四个 Stub SubGraph（含 GeneralChat）通过 adapter 接收对应任务的证据文本及 `routing_intents`，只把统一的 `TaskResult` 返回给 `MainState.task_results`；各自的私有状态保留在子图本地。

MainGraph 是唯一的持久化边界，复用共享的开发环境 `InMemorySaver`。调用方仍须通过 `configurable.thread_id` 提供线程标识，State 中不保存该运行配置。

## Hybrid Router: Rule Layer

`app/agent/foundation/rule_routing` 提供 Hybrid Multi-Intent Router 的第一层高精度规则路由。它依次执行轻量标准化、注册规则匹配、优先级与重叠 span 冲突处理、有效文本覆盖率计算，并返回可部分解析的 `RouteDecision`。

Rule Router 可以在同一输入中保留多个不重叠的 `IntentItem`。未覆盖子句以 `unresolved_segments` 保留原文 start/end，并提供兼容的拼接 `unresolved_text`。规则层自身不调用模型；HybridIntentRouter 将片段交给后续层。

## Model Router Dataset Foundation

`app/agent/foundation/intent_dataset` 是未来监督微调 Model Router 的第一阶段数据基础设施。训练标签直接复用 `app.agent.foundation.routing.IntentType`；`UNKNOWN` 只作为运行时兜底，不进入 `label_mapping.json` 或训练样本。

```text
data/intent/
  taxonomy.json
  label_mapping.json
  raw/manual_seeds.jsonl
  raw/generated.jsonl
  processed/cleaned.jsonl
  processed/conflicts.jsonl
  processed/rejected.jsonl
  splits/manual_test.jsonl
  splits/train.jsonl
  splits/val.jsonl
  splits/test.jsonl
```

在 `ai-service` 目录中依次执行：

```powershell
python scripts/intent/generate_dataset.py --seed 42
python scripts/intent/clean_dataset.py
python scripts/intent/split_dataset.py --seed 42
python scripts/intent/validate_dataset.py
```

所有路径均可通过 CLI 参数覆盖。生成器只写 raw 数据，默认 adapter 做少量、可复现的表面形式扩展；也可通过 `--synthetic-input` 或 `--rule-bootstrap-input` 合并外部 adapter 的 raw JSONL，不会调用 MainGraph。清洗先隔离非法样本和标签冲突，再做 exact/normalized 去重。划分以 `provenance.seed_id` 为组执行 label-aware 分配，使同一 seed 的简单改写不会跨 split；seed 组被分到 test 时仅保留人工 Seed，并排除其 synthetic 变体，因此 synthetic 数据不会进入 test。存在 `splits/manual_test.jsonl` 时会保留该文件，并把人工样本固定纳入 `test.jsonl`。

数据集可以进入独立的监督训练阶段；该阶段不会接入 Rule Router、MainGraph 或 LLM Router，也不包含 RAG、ONNX 和在线模型服务。

## Supervised Model Router Training

`app/agent/foundation/intent_model` 使用 `BAAI/bge-small-zh-v1.5` 的 Encoder，加一层 Dropout + Linear Head。模型直接输出 4 个 logits，训练损失为 `BCEWithLogitsLoss`；Sigmoid 只用于评估和预测。默认采用 CLS pooling，并以 validation Macro F1 选择最佳 epoch。

在 `ai-service` 目录中执行：

```powershell
python scripts/intent/train_model.py --device auto --seed 42
python scripts/intent/evaluate_model.py --input data/intent/splits/test.jsonl
python scripts/intent/predict_intent.py --text "分析一下这首歌然后帮我收藏"
```

训练完成后，`artifacts/intent_model/` 包含：

```text
encoder/
classifier_head.pt
label_mapping.json
thresholds.json
training_config.json
metrics.json
error_analysis.jsonl
```

阈值仅使用 validation 概率调优；test 只进行一次最终评估。预测允许多个标签同时过阈值，也允许没有标签过阈值。空标签结果返回 `needs_llm=true`，但这里不会实现或调用 LLM Router。

## Model Router V1

`app/agent/foundation/model_routing` 把底层 PyTorch 推理封装成长期复用的 `IntentClassifierRuntime`，并由 `ModelRouter` 将预测转换为共享的 `IntentItem`。模型、Tokenizer、label mapping 和 per-label thresholds 只在 Router 初始化时加载一次；每次 `route()` 仅执行推理，不访问 artifact 文件。

```text
RuleRouter.unresolved_text
  -> ModelRouter.route(text)
  -> IntentClassifierRuntime.predict(text)
  -> IntentItem(source="model")[]
  -> ModelRouteResult
```

ModelRouter 可独立运行，也已通过 HybridIntentRouter 接入 Foundation MainGraph。可使用已有 V1 artifact 独立验证：

```powershell
python scripts/intent/test_model_router.py --device auto
```

## LLM Router、Intent Merger 与动态 MainGraph

默认流程：`prepare_context → intent_router → 按任务组顺序调用 SubGraph → aggregate_results → final_response`。意图无法确定时进入 `clarify → END`，不执行已识别的部分任务。SubGraph 返回失败时停止后续任务并给出失败提示；模型加载/LLM 网络/结构校验失败向调用方抛出异常，不伪装成澄清。

`HybridIntentRouter` 对每个 Rule 未解决片段调用长期复用的 ModelRouter；默认只有拒识或 `needs_llm=true` 才调用 LLMRouter。可在工厂配置 `boundary_margin` 升级接近已保存阈值的标签，默认 0（关闭），不改写 `thresholds.json`。模型前向通过 `asyncio.to_thread` 执行。

`LLMRouter` 复用现有 LLM settings/client，以独立 JSON Schema、无工具请求进行分类。taxonomy 从现有文件加载；输出必须引用当前片段的原文证据，局部 offset 通过代码转换成全局 offset。若精确引用仅出现一次，代码可纠正 LLM 字符计数错误；不存在或歧义引用拒绝。结构或证据无效只重试一次，网络错误不重试。缺少配置或 provider 不支持结构化响应时明确报错。LLM confidence 不与模型概率或规则分数直接竞争。

`IntentMerger` 锁定 Rule 结果；LLM 结果只替换其负责片段的 Model 候选；相同 Intent + sub_intent + span 去重，不同位置或不同工具动作保留。全局按原文位置排序，重新生成连续 order。`RouteDecisionBuilder` 计算完成状态及分类证据覆盖率（不是业务执行成功率），按 RouteKey 分组生成任务，并用 `intent_orders` 引用原始 Intent。

同一 RouteKey 当前只生成一个任务，组内保留全部 Intent；任务组按首次出现顺序执行。这不等同于完整依赖规划：例如 Tool → Song → Tool 会聚合为 Tool 组和 Song 组，未来真实业务执行器需处理跨组依赖。GeneralChat 与三个业务子图均为 Stub，当前输出仅验证路由和调度；不会实际暂停播放器、收藏歌曲或生成业务回答。

模型工件与 taxonomy 路径可通过 `create_hybrid_router(model_dir=..., taxonomy_path=..., device=...)` 配置，再传给 `create_main_graph(intent_router=...)`。应用启动时构造一次图和运行时，重复请求只提交 `create_main_state(text, routing_context=...)`；checkpointer 继续通过 `configurable.thread_id` 隔离会话。

```powershell
# 真实 V1 BGE + 离线 LLM 澄清 fixture，不消耗 API 额度
python scripts/intent/test_hybrid_router.py --device cpu

# 真实 provider 调用，使用现有 LLM_* 配置；可能产生费用
python scripts/intent/test_hybrid_router.py --device cpu --live-llm --text "当前电影可以吗"

python -m unittest discover -s tests
```

离线 fixture 只用于验证升级/澄清流程，不是 LLM 识别准确率测试。换 V2 模型只需更换 artifact 或注入遵守现有接口的 Runtime；替换 LLM provider 只需更换 LLMIntentRuntime。

## Tools

- `search_music(keyword, limit=10)`：`GET /api/music/search`
- `get_song_lyrics(song_id)`：`GET /api/songs/{song_id}/lyrics`
- `get_user_playlists()`：`GET /api/user/playlists`
- `add_song_to_playlist(playlist_id, song_id)`：`POST /api/playlists/{playlist_id}/songs/{song_id}`

所有工具均只通过 Python `MusicServiceClient` 调用 Spring Boot，不直接访问网易云或数据库。搜索结果使用 `song_id/name/artists/album`，歌单结果使用 `playlist_id/name/editable`，便于后续工具继续使用真实 ID。

需要登录的工具不接受 `user_id` 或 Cookie 参数。FastAPI 只从当前请求读取 `JSESSIONID`，通过 LangGraph `context_schema` 和 `ToolRuntime` 注入工具；Spring Boot 继续负责登录、歌单归属、参数和业务异常校验。

## Streaming API

```powershell
curl.exe -N -X POST http://127.0.0.1:8001/api/ai/chat `
  -H "Content-Type: application/json" `
  -H "Accept: text/event-stream" `
  -d '{"message":"搜索 Open Arms","thread_id":"demo-thread","current_song_id":null}'
```

SSE 事件为 `message`、`update`、`final`、`error` 和 `done`。工具链请求的 `update` 通常依次出现 `load_song_context`、`agent`、`tools`、`agent`。

## Test

测试使用脚本化模型和 MockTransport，不消耗模型额度，也不会执行真实写操作。

```powershell
python -m unittest discover -s tests -v
```

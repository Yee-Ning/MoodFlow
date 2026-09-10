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

`app/agent/foundation` 是独立的 MainGraph 基础设施，现已支持 Rule → Model → LLM → Merger 动态路由，以及 CurrentSong、ToolCalling、GeneralChat 三个实际 SubGraph。生产 Chat HTTP/SSE 默认继续使用旧图；设置 `AI_AGENT_GRAPH_MODE=foundation` 后可显式启用 Foundation 图进行联调。

```text
START
  -> prepare_context
  -> intent_router
       -> one or more SubGraphs
  -> aggregate_results
  -> final_response
  -> END
```

显式传入 `development_routes` 时仍可绕过分类，用于隔离开发。默认 `create_main_graph()` 使用动态路由；可传入 `intent_router` 注入测试或替代运行时。各 SubGraph 通过 adapter 接收对应任务的证据文本及 `routing_intents`，只把统一的 `TaskResult` 返回给 `MainState.task_results`；各自的私有状态保留在子图本地。

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

`HybridIntentRouter` 对每个 Rule 未解决片段调用长期复用的 ModelRouter。模型拒识、多标签命中，或片段包含否定、条件、并列和顺序表达时会升级给 LLMRouter，避免把“不要暂停”或“聊天并推荐歌曲”直接按单一模型标签派发。可通过 `escalate_multilabel`、`escalate_semantic_complexity` 调整这两类升级，并通过 `boundary_margin` 升级接近已保存阈值的标签；阈值文件不会被改写。模型前向通过 `asyncio.to_thread` 执行。

`LLMRouter` 复用现有 LLM settings/client，以独立 JSON Schema、无工具请求进行分类。taxonomy 从现有文件加载；输出必须引用当前片段的原文证据，局部 offset 通过代码转换成全局 offset。若精确引用仅出现一次，代码可纠正 LLM 字符计数错误；不存在或歧义引用拒绝。结构或证据无效只重试一次，网络错误不重试。缺少配置或 provider 不支持结构化响应时明确报错。LLM confidence 不与模型概率或规则分数直接竞争。

`IntentMerger` 锁定 Rule 结果；LLM 结果只替换其负责片段的 Model 候选；相同 Intent + sub_intent + span 去重，不同位置或不同工具动作保留。全局按原文位置排序，重新生成连续 order。`RouteDecisionBuilder` 计算完成状态及分类证据覆盖率（不是业务执行成功率），按 RouteKey 分组生成任务，并用 `intent_orders` 引用原始 Intent。

RouteTask 明确携带分配文本、Intent 引用和前置任务 ID；未完成的 RouteDecision 不能派发任务，澄清决策必须携带面向用户的问题。相邻且 RouteKey 相同的 Intent 合并为一个任务，路由类型被其他任务隔开的 Intent 保持独立，因此 `Tool → Chat → Tool` 会按三个任务和原文顺序执行。CurrentSong 已提供只读的当前歌曲分析，ToolCalling 已能执行服务端音乐工具并生成浏览器播放器动作，GeneralChat 已提供无工具的普通对话能力；WorldLine 已支持歌曲、歌手和专辑的 V1 事实闭环。

## Music RAG Source Ingestion V1

`app/rag/source_ingestion` 提供独立的离线数据源入口，当前只实现
`Source Discovery → Candidate Manifest → Human Approval → Fetch → Raw Snapshot`。
首批 50 个“周杰伦音乐知识 Corpus V1”候选位于
`data/source_ingestion/jay_chou_corpus_v1/sources_candidates.json`，当前已按人工指令
全部标记为 `approved`；只有 `approved` 来源才会进入 Fetch。

```powershell
python scripts/source_ingestion.py validate
python scripts/source_ingestion.py list --status pending
python scripts/source_ingestion.py review jc-v1-001 approved
python scripts/source_ingestion.py fetch --source-id jc-v1-001
```

HTML、PDF、JSON/API 与纯文本 Fetcher 只保存原始响应 bytes，不提取正文。
客户端检查每个跳转目标的 robots.txt，遵守 Crawl-delay，并以每个站点至少
1.1 秒的间隔串行请求。快照使用 UTC 时间与 SHA-256 命名，通过 exclusive-create
保证不覆盖；`data/raw/sources_manifest.jsonl` 以追加方式记录来源、哈希、HTTP
状态与 corpus 版本。V1 不包含 Parser、Markdown 转换、Cleaning、Deduplication、
Chunking、Entity Linking、Claim Extraction、Embedding 或 Vector DB。

## Music RAG Parse + Normalize V1

`app/rag/parse_normalize` 在只读 Raw Snapshot 之上提供独立的离线解析层：

```text
Raw Snapshot
  -> FileTypeDetector
  -> ParserRouter + ParserRegistry
  -> HTML / Markdown / TXT / JSON Adapter
  -> PDFInspector
       -> SIMPLE_TEXT                    -> PyMuPDF4LLM
       -> COMPLEX_LAYOUT / SCANNED       -> MinerU (lazy)
       -> UNKNOWN                        -> PyMuPDF4LLM
       -> PDF Quality Gate               -> Docling fallback (lazy)
  -> MarkdownNormalizer
  -> ParseQualityEvaluator / PDFParseQualityEvaluator
  -> NormalizedDocument + ParseReport
```

后续阶段只需依赖 `NormalizedDocument` 和 `DocumentBlock`，不需要感知具体
Parser。PDF block 在后端可提供时保存 `page_number` 和 `bbox`，document 另有
`tables`、`images`；因此后续证据可以追溯到原 PDF 页。

PDF 采用“主环境轻量 + 延迟加载 Adapter”：主依赖只有 PyMuPDF4LLM，且该
Adapter 始终传入 `use_ocr=False`、`force_ocr=False`。MinerU 和 Docling 仅在被
路由到时才调用；当前仓库不安装二者、不下载模型。缺少可选后端会写成
`parser_attempts[].status=unavailable`，不会让整个 manifest 批次崩溃。扫描件或
没有可用 native text 的路径才允许可选后端启用 OCR。

在 `ai-service` 目录执行：

```powershell
python run_parse.py --source-id jc-v1-001
python run_parse.py --source-id some-pdf --parser auto
python run_parse.py --source-id some-pdf --parser pymupdf
python run_parse.py --source-id some-pdf --parser mineru
python run_parse.py --source-id some-pdf --parser docling
python run_parse.py --all-approved
python run_parse.py --manifest data/raw/sources_manifest.jsonl
python run_parse.py --source-id jc-v1-001 --force
```

`--parser` 默认是 `auto`。`auto` 才执行完整级联；显式指定后端用于调试，只尝试
一次且仍经过 Quality Gate。把 PDF 后端参数用于非 PDF 来源会明确失败。

每个成功来源输出：

```text
data/normalized/{source_id}/
  document.md
  document.json
  parse_report.json
  images/
```

解析彻底失败仍会生成 `parse_report.json`，但不会留下伪成功的 document。
`pass/warning/fallback_used` 是 CLI 成功状态，`manual_review/failed` 返回非零。
质量报告包括 `chars_per_page`、空页/乱码/替换字符/重复行/可疑换行比例、标题、
段落、表格、图片和 parser error 数量。所有分类、评分、通过和回退阈值分别集中
在 `PDFInspectorConfig` 与 `PDFParseQualityConfig`，没有散落在业务代码中。

三种后端统一映射到相同模型，示意输出如下：

```markdown
# Music Production Notes

Fantasy is used here as a deterministic native-text fixture.
```

```json
{
  "parser_name": "docling",
  "selected_parser": "docling",
  "status": "fallback_used",
  "parser_attempts": [
    {"parser": "mineru", "status": "success", "ocr_used": true},
    {"parser": "docling", "status": "success", "ocr_used": true}
  ]
}
```

MinerU Adapter 优先读取 `content_list_v2.json`、`content_list.json`、
`middle.json` 等结构化结果，再把 Markdown 作为补充；缓存位于
`data/parsed_cache/mineru/{source_id}/{raw_hash_prefix}/`，不覆盖 Raw Snapshot。
Docling Adapter 同时读取 `export_to_markdown()` 和 `export_to_dict()`。将来启用
MinerU 时可把其 CLI 放到独立重型环境并确保 `mineru` 可执行文件在 PATH，或在
构造 `MinerUAdapter` 时注入自定义 runner；启用 Docling 时另行安装其 runtime。
这两个动作可能下载大量依赖/模型，当前版本不会自动执行。

`document.md` 是 UTF-8、LF 换行的规范 Markdown；`document.json` 同时保存
blocks、Parser metadata、候选来源 metadata 和原始 JSON structured data；
`parse_report.json` 保存文本长度、标题/段落/表格计数、空内容、乱码比例、重复行
比例、评分、状态和耗时。输入与输出对应关系示例：

- HTML 的 `<h1>标题</h1><p>正文</p>` 变为 heading/paragraph blocks 和
  `# 标题\n\n正文`；只移除 `script/style/noscript`。
- Markdown 的 front matter、标题、列表、引用、代码、表格和图片会解析成相应
  blocks，再统一空格、列表符号和空行，不改写正文。
- TXT 严格检测 UTF-8、UTF-16、GB18030、Big5 等常见编码，并仅对保守匹配的
  首段使用标题启发式；其余内容保留为 paragraph。
- JSON 保留完整 `metadata.parser_metadata.raw_json`；Wikidata 和 MusicBrainz
  另外保留 entity/external IDs、aliases、relations、release/artist/recording 等
  字段，未知 schema 使用有深度、字段、条目和输出长度上限的递归表示。

重复执行时，只有
`raw_content_hash + parser/cascade fingerprint + normalization_version` 全部匹配才会跳过；
`--force` 可强制重解析。写入采用同目录临时文件、`fsync` 和原子替换，不会修改
Raw Snapshot。测试夹具覆盖 2 个 HTML、1 个 Markdown、1 个 TXT、1 个
Wikidata JSON 和 1 个 MusicBrainz JSON；本地真实快照验证覆盖
`jc-v1-001`、`jc-v1-007`、`jc-v1-038`、`jc-v1-040`。

PDF V1 已预留 `parse_page(...)`、`reparse_failed_pages(...)` 和默认关闭的
`VLMPageParser`，但不实现混合页面合并，也不调用视觉模型。本阶段不包含
Cleaning、Deduplication、Chunking、Entity Linking、Claim Extraction、Embedding
或 Vector DB。

## WorldLine V1 SubGraph

`app/agent/foundation/subgraphs/worldline` 是独立的、无 FastAPI request 依赖的 LangGraph 子图：

```text
START
  -> resolve_entity
  -> load_entity_facts
  -> retrieve_music_knowledge
  -> build_worldline_graph
  -> generate_narrative
  -> generate_branches
  -> END
```

目录职责如下：

```text
worldline/
  models.py           # ResolvedEntity、Graph、Branch、Result 等严格 Schema
  state.py            # 仅在子图内存在的 WorldLineState
  entity_resolver.py  # 规则化 query/type 提取、真实候选匹配与消歧
  music_service.py    # 复用 MusicServiceClient 的事实适配层
  retrieval.py        # MusicKnowledgeRetriever 接口与空检索降级实现
  builder.py          # 只从有证据的事实构建有界图
  prompts.py          # Grounded Narrative / Branch 系统提示
  generation.py       # LLM JSON Schema 调用、重试与分支落地校验
  graph.py            # LangGraph 节点、边、错误降级与结构化日志
  service.py          # 独立 API 调用入口
```

实体解析先从用户文本提取实体名称与可选类型，再通过 Spring Boot 的 `/api/music/entities/search` 搜索网易云真实候选。只有规范化名称唯一精确匹配时才选择 canonical entity；同名歌曲/专辑或同名不同演唱者会返回 ambiguous，不把模型猜测当作事实。详情通过 `/api/music/entities/{type}/{id}` 加载；歌曲会尽力补充最多 5 首网易云相似作品，歌手和专辑详情会携带最多 8 个真实相关作品。相似作品加载失败不会丢失歌曲、歌手、专辑等基础事实。

当前 `MusicKnowledgeRetriever` 默认实现是 `EmptyMusicKnowledgeRetriever`，不是正式 RAG。空结果或 Retriever 异常时，图仍可由 Music API facts 构建，Narrative 会明确标注知识范围。未来接入 Qdrant + BM25 + Hybrid Search + Reranker 时，只需替换该接口实现；如果检索结果提供经过验证的 `metadata.graph_nodes/graph_edges`，builder 会将其纳入图并绑定 evidence，普通检索文本本身不会自动造边。

SubGraph 的候选、facts、retrieved docs、graph intermediate data 均保留在 `WorldLineState`。通过 MainGraph 执行时只返回统一 `TaskResult`，且成功载荷只有 `data.worldline_result`。显式“构建歌曲《七里香》的音乐世界线”等请求可由 Foundation Router 派发；生产 `/api/ai/chat` 仍需设置 `AI_AGENT_GRAPH_MODE=foundation` 才使用 Foundation MainGraph。

也可以使用独立 JSON 接口直接联调：

```powershell
curl.exe -X POST http://127.0.0.1:8001/api/ai/worldline `
  -H "Content-Type: application/json" `
  -d '{"query":"歌曲《晴天》的音乐世界线"}'
```

成功响应是 `WorldLineResult`，示例：

```json
{
  "root_entity": {
    "entity_id": "netease:song:185809",
    "entity_type": "song",
    "name": "晴天",
    "artist": "周杰伦",
    "album": "叶惠美",
    "source": "netease",
    "metadata": {}
  },
  "graph": {
    "root": "netease:song:185809",
    "nodes": [
      {
        "id": "netease:song:185809",
        "type": "song",
        "name": "晴天",
        "description": "世界线根实体",
        "metadata": {}
      }
    ],
    "edges": []
  },
  "narrative": "当前未检索到补充音乐知识，以下内容仅基于音乐目录事实。",
  "branches": [
    {
      "id": "branch-1",
      "title": "晴天作品线 1",
      "description": "沿当前真实歌曲实体继续探索。",
      "branch_type": "related_work",
      "target_entity": {"id": "netease:song:185809", "type": "song", "name": "晴天"}
    },
    {
      "id": "branch-2",
      "title": "晴天作品线 2",
      "description": "沿当前真实歌曲实体继续探索。",
      "branch_type": "related_work",
      "target_entity": {"id": "netease:song:185809", "type": "song", "name": "晴天"}
    },
    {
      "id": "branch-3",
      "title": "晴天作品线 3",
      "description": "沿当前真实歌曲实体继续探索。",
      "branch_type": "related_work",
      "target_entity": {"id": "netease:song:185809", "type": "song", "name": "晴天"}
    }
  ],
  "sources": []
}
```

无法找到实体返回 404，同名歧义返回 409，音乐服务不可用返回 503，LLM/结构生成失败返回 502。默认流程不会调用 Neo4j、GraphRAG、用户画像、推荐算法或递归 Planner。

## CurrentSong SubGraph

`app/agent/foundation/subgraphs/current_song` 专门回答当前歌曲的歌词、主题、情绪、叙事和听感问题。子图先解析本任务应分析的歌曲：优先采用本轮前置 ToolCalling 任务产生的 `play_song` 目标，否则使用只读播放器快照中的当前歌曲 ID；若前置任务是上一首或下一首，则等待浏览器实际完成切歌，避免分析旧歌曲。

解析到歌曲后，子图通过 Spring Boot 的歌曲上下文接口加载可信元数据和歌词，再把路由分配文本、播放器快照及最近 12 条对话历史交给无工具模型。模型不能发起播放、收藏、搜索等操作，也不能把歌词内容当作指令；缺少当前歌曲时返回 `NEEDS_INPUT`，上下文服务失败时返回不泄露内部异常的 `FAILED` 结果。成功结果只向 MainGraph 暴露必要歌曲元数据和分析正文，不保存歌词全文；当前尚未接入 RAG，结果会明确记录 `rag_used=false`。

## GeneralChat SubGraph

`app/agent/foundation/subgraphs/general_chat` 使用现有基础聊天模型，但不绑定任何工具。子图只接收路由分配给它的文本片段、只读播放器快照、最近 12 条对话消息，以及本轮最多 8 个前置任务的受控摘要；工具参数、实体数据和其他 `TaskResult.data` 不会进入聊天模型。最终正文限制为 2000 个字符，并以统一 `TaskResult` 返回。

MainGraph 会把本轮聚合后的最终答复写回消息历史，因此同一 `thread_id` 的后续 Chat 请求可以读取用户与助手的成对历史。Chat V1 当前不接实时搜索和 RAG；涉及执行操作的请求仍应由 ToolCalling 处理。

## ToolCalling SubGraph

明确的暂停、继续、上一首、下一首、降低音量和收藏意图走确定性快速路径，不调用 LLM。搜索、歌词、歌单、播放指定歌曲和组合请求进入 `agent → tools → capture_tool_results → agent` 循环，最多调用 5 次工具。服务端工具继续通过 Spring Boot 执行；播放器操作只生成严格的 `ClientAction`，由 SSE 发送给浏览器。

浏览器动作包括暂停/继续、切歌、设置音量和静音、跳转进度、设置播放模式、播放搜索结果、设置喜欢状态和打开歌词。动作使用显式 set 语义与唯一 `action_id`，React 会校验参数、去重后再交给白名单执行器。子图会保存搜索歌曲和用户歌单的结构化引用，供同一会话后续的“刚才第几首”请求使用；Cookie、歌词正文和原始工具结果不会写入 MainState 的任务数据。

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
  -d '{"message":"暂停一下","thread_id":"demo-thread","current_song_id":185809,"player_context":{"has_current_song":true,"current_song_id":185809,"player_state":"playing","volume":0.72,"is_muted":false,"play_mode":"list-loop","current_time":30,"duration":240,"queue_size":1,"current_index":0,"current_song_liked":false}}'
```

SSE 事件为 `message`、`update`、`action`、`final`、`error` 和 `done`。旧图的工具链请求通常依次出现 `load_song_context`、`agent`、`tools`、`agent`；Foundation 模式会发送 MainGraph 节点更新，并在 ToolCalling 任务完成后先发送一个或多个 `action`，再发送 `final`。

## Test

测试使用脚本化模型和 MockTransport，不消耗模型额度，也不会执行真实写操作。

```powershell
python -m unittest discover -s tests -v
```

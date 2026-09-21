# Context Engineering V1 · Phase 1 实施报告

## 1. 原有 Context 架构问题

MainGraph 负责路由，四个 SubGraph 分别在调用处拼接提示。CurrentSong 和 GeneralChat 使用字符上限截取最近对话；ToolCalling 曾将完整工具消息传给模型；WorldLine 的 RAG Context Pack 虽已限制检索证据，但最终生成调用仍会直接放入全部 `sources`。这些边界缺少统一的单次调用 token 预算，也无法解释某条已检索证据为何没有进入最终输入。预路由已有独立的结构化分类 Prompt，本阶段保持它的轻量路径。

## 2. 新架构与修改文件

`ai-service/app/agent/foundation/context_engineering.py` 实现收集、策略筛选、确定性去重、来源和总量预算、按类型裁剪、消息组装及逐项轨迹。以下调用边界接入：

- `ai-service/app/agent/foundation/subgraphs/general_chat/assistant.py`
- `ai-service/app/agent/foundation/subgraphs/current_song/assistant.py`
- `ai-service/app/agent/foundation/subgraphs/tool_calling/assistant.py`
- `ai-service/app/agent/foundation/subgraphs/worldline/generation.py`
- `ai-service/app/agent/foundation/subgraphs/worldline/graph.py`

新增 `ai-service/tests/test_context_engineering.py`。原有系统提示正文、MainGraph 路由、RAG 检索与 Context Pack 构造、结构化输出校验均未重写。

## 3. 数据契约与执行顺序

`ContextItem` 保存 `id/type/source/content/priority/relevance/token_count/required/compressible/metadata/references`。`ContextPolicy` 保存节点、总输入窗口、预留输出、安全余量、来源预算、允许类型与优先级。`ContextPack` 保存最终项、实际估算 token、有效预算、删除和压缩项、组装后的消息及 `ContextTraceEntry`。这些契约沿用项目的 Pydantic 风格。

单次调用依次执行：Collector → Policy/Selector → Dedup → 已有字段投影与来源预算 → 总预算中先删除低优先级可选项 → 必要时抽取式压缩 → Assembler → LLM。用户问题、系统规则及结构化关键约束不会被自由摘要。必需项仍超预算时明确报错，不静默丢弃。历史中的本轮用户消息按既有 `select_recent_history` 边界剔除，内容级去重再处理剩余重复。

## 4. Token Budget

有效预算为 `total_input_budget - reserved_output_tokens - safety_margin`。CurrentSong/GeneralChat 默认分别为 `8192 - 2048 - 256 = 5888`；ToolCalling 为 `12288 - 2048 - 256 = 9984`；WorldLine 为 `12288 - 2500 - 256 = 9532`。来源预算按策略集中定义，例如 WorldLine RAG 为 5000、对话为 800，ToolCalling 工具结果为 4500。计数复用项目 `UnicodeHeuristicTokenCounter`，并计入组装文本及 AI 工具调用参数；这是离线近似值，真实模型 tokenizer 和工具 schema 的服务端开销仍需后续校准。

## 5. Compression 策略

普通对话优先完整保留最近内容，超预算先删除低价值轮次。CurrentSong 保留歌曲字段和 XML 信任边界标签，仅缩短歌词字段。Tool Result 先按已知业务 schema 投影：保留歌曲完整播放参数所需字段、歌单 ID、状态、错误与歌词，删除调试或无关字段。RAG Evidence 先按证据 ID/正文去重和来源预算筛选；必需证据压缩时保留精确证据片段、引用 ID 与来源关联。User Query、System 规则和结构化关键约束不做语义压缩。不调用额外 LLM 摘要。

## 6. 四个 SubGraph 的 ContextPolicy

| SubGraph | 高优先级 | 低优先级或排除 |
| --- | --- | --- |
| CurrentSong | 当前问题、歌曲资料 | 对话次要；RAG 排除 |
| ToolCalling | 当前任务、已有实体/参数、工具调用与结果 | 普通历史次要；RAG 排除 |
| GeneralChat | 当前问题、最近对话、前置任务摘要 | RAG 排除 |
| WorldLine | 当前问题、SearchPlan、结构化图/事实、RAG 证据 | 工具/历史次要 |

WorldLine 通过现有 Query Understanding 的 `SearchPlan` 和 RAG Context Pack 派生来源接入；没有另起意图判断。项目当前没有独立持久化的 `ToolPlan` 契约，工具选择仍使用现有 StructuredOutputService 与工具 schema。

## 7. RAG、Tool 与 Structured Output 接入

RAG 保持 Hybrid Retrieval → RRF/Selection → Reranker → RAG Context Pack。其 WorldLine 文档已包含 `context_pack_rank`、`evidence_span` 等元数据；生成器把 `EvidenceSource` 作为 `rag_evidence` 输入，并优先保留图边或目录事实引用的来源。进入模型的来源继续携带 `id/title/source/url/excerpt`，必要时保留精确证据片段。去重/预算删除的来源不再进入生成 payload。Grounding 与 citation 校验仍使用原有实现。

ToolCalling 保留 AI tool-call 与对应 ToolMessage 的配对、调用 ID 和可执行歌曲字段；原始 JSON 在调用前投影。已有 `prior_entities`、播放器状态和前置任务摘要仍通过原有 Prompt 契约提供。WorldLine 结构化生成继续用既有 schema 与重试机制；实际用户问题和已有 SearchPlan 传入默认生成器。

## 8. ContextTrace 示例

测试场景中两条 RAG 证据均已进入 Collector：`evidence:a` 记录 `collected → selected → final`；来源预算不足时，`evidence:b` 记录 `collected → selected → dropped`，原因 `rag_evidence_budget_exceeded`。未检索到时没有对应 evidence 的 `collected` 记录；日志另记录 `retrieved/final/dropped` 数量与 ID。工具结果投影记录 `compressed`、原因 `schema_projection`、投影前后 token 数。

## 9. Token Before / After

可重复的离线夹具：当前歌曲含原歌词和翻译歌词各 500 次双字短语，输入规则为 `rules`、问题为“分析这首歌”。按同一计数器及组装方式，裁剪前 **2089** token，裁剪后 **330** token，有效测试预算 **330**；`compressed_items = [current_song]`，问题原文和歌曲 XML 结束标签保留。这是测试夹具，不代表线上平均节省率。

## 10. 测试与回归

- `python -m pytest -q tests/test_context_engineering.py`：11 项通过。覆盖策略差异、required 保留与溢出、预算、低优先级删除、工具投影和配对、超大工具 JSON 保形压缩、孤立工具调用处理、去重、RAG 引用/精确片段、轨迹、WorldLine Provider 接入。
- 相关 WorldLine、CurrentSong、GeneralChat、ToolCalling 原有测试：通过。
- 全量 `python -m pytest -q tests`：831 passed、2 skipped、206 subtests passed。
- `python -m compileall -q app`：通过。
- 修改文件的 `ruff check`：通过。全量 `ruff check app tests` 仍报告 5 项既有未使用变量/import，位于未修改的 `routing.py`、PDF adapter 和 source ingestion 文件；本任务未改动这些无关模块。

## 11. 当前问题与验证限制

预算基于离线启发式计数，不包含具体供应商的完整 tool schema 与消息封装 token；若实际窗口较小，应根据生产模型 tokenizer 调整策略。ContextTrace 当前写入结构化对象并记录摘要日志，尚未持久化到请求级 tracing 后端。WorldLine 在不同来源拥有同一正文时会保留最先且必需的证据，具体质量仍取决于上游 RAG Context Pack。此次测试使用模拟模型；没有验证真实供应商响应质量或线上负载。

## 12. Phase 2 预留接口

Collector 接受外部 `messages` 与结构化来源；后续可由 Recent Window、Structured Notes、Immutable History、Checkpoint 等模块供给，但本阶段没有实现其存储和生命周期。Window Rollover、跨窗口恢复与 Three-Layer Memory 留在 Phase 2，不与单次调用的 `ContextPack` 混用。

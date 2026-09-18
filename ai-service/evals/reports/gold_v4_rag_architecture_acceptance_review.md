# Gold V4 RAG Architecture Acceptance Review

审计日期：2026-09-18。范围：当前工作区代码、冻结 Gold V4、冻结知识 release、索引 projection/manifest、已有离线 trace。性质：只读架构验收；未运行检索、消融、模型推理或在线服务，也未修改生产代码、配置、模型、索引及 Gold。

## 0. 验收口径与结论

本报告区分三种证据：**实现**＝代码和配置存在；**离线验证**＝冻结产物、历史 trace 或测试证明该路径工作；**在线启用**＝部署配置和真实请求证明正在使用。前两项不能自动推断第三项。本次未读取部署私有配置，也未做线上请求，因此线上实际启用状态为**未验证**。

| 维度 | 评分 | 判断依据 |
| --- | ---: | --- |
| Offline Pipeline | **8/10** | 从来源快照到知识 release、Parent/Child、Dense/BM25 索引和 manifest 的链路完整；有内容哈希与版本。缺少已验证的统一调度、自动增量发布与跨环境产物托管。 |
| Online Pipeline | **6/10** | Query Understanding、Hybrid、rerank、context、带引用生成均有代码并在 WorldLine 路径接线；默认聊天走 legacy、检索默认为 empty、rerank 默认为 off，未证明默认服务执行完整 RAG；WorldLine HTTP 入口返回 JSON。 |
| Evaluation System | **8/10** | 冻结 Gold、claim/evidence group、各阶段 trace、宏/微 Recall、失败审计和回放齐全；当前 50 题主要覆盖单一音乐域，早期 Gold 标签包含 AI 辅助审批，不能当作独立人工盲标或线上生成质量证明。 |
| Engineering | **6/10** | 模块与版本配置边界清楚，错误回退和单测较充分；尚未见贯穿请求到答案的生产 trace、依赖就绪探针、持久化会话记忆或跨环境 release/回滚闭环。 |

**验收决定：可以冻结当前经校验的知识 release、索引/Gold V4 资产及 RAG 核心模块接口，进入 Agent、Memory、Tool、MCP、Workflow、Frontend/API 与 Monitoring 等上层应用开发。不能据此宣称“默认在线 RAG 全链路已生产验收并可无条件冻结”。** 在面向业务流量启用完整 RAG 前，还需要确认实际部署模式、完整请求链路、产物交付与在线可观测性；这些是接线和运维验收，不是继续优化 Recall。

### 冻结基线核验

- 冻结 Gold V4 为 50 题：29 `fully_answerable`、10 `partially_answerable`、7 `insufficient_evidence`、4 `needs_input`；后四题为 046、047、048、068。39 个 full/partial 题拥有 95 个正向 evidence units。Gold SHA-256 为 `1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`，与[历史 shadow baseline](gold_v4_hybrid_shadow_baseline_20260917.json)一致。[Gold 数据](../datasets/rag_eval_gold_v4.frozen.jsonl)
- 知识 release `knowledge-v2-8608ffc2fa72e441` 的 manifest SHA-256 为 `2680c92d114db1574d3e46144e4aa17032c44876981055e6d7011fc5e0416059`；index projection 1270 行，SHA-256 为 `663b2435780a684e6bd9be619950e1601af1c0e8f6161352cbbd4bc0dec5a0a0`。索引 manifest 状态 `completed`、`ready_for_retrieval=true`，记录 677 Parent、1270 Child/point、0 失败，manifest SHA-256 为 `0e096a91abc08d55af5bba856d20bdbc8124180c272b917c2c4f498666eb1033`。以上本地文件哈希与历史 baseline 所载哈希一致。参见[知识 release](../../data/knowledge/v2/knowledge_release_manifest.json)、[索引 manifest](../../data/indexing/v1/knowledge-v2-8608ffc2fa72e441/v3/index_build_manifest.json)、[索引 projection](../../data/indexing/v1/knowledge-v2-8608ffc2fa72e441/v3/index_projection.jsonl)。这证明本地冻结文件一致，**不等于重新验证了实时 Qdrant collection 或部署实例**。
- 这些 release/index 数据文件当前在本地存在，但未作为 Git 跟踪文件纳入仓库；跨机器恢复取决于外部产物保存和交付流程。本次未验证该流程。[本地数据约定](../../data/raw/README.md)

## 1. Offline Pipeline 验收

| 环节 | 实现与证据 | 验收判断 |
| --- | --- | --- |
| Raw Data / Data Ingestion | 候选源有稳定 `source_id`、URL、publisher/author、类型、状态；抓取快照用 `source_id`＋时间＋内容哈希命名，排他创建且追加 provenance ledger；v2 有替换源 ID 与去重流程。[来源模型](../../app/rag/source_ingestion/models.py)、[快照](../../app/rag/source_ingestion/snapshot.py)、[v2 模型](../../app/rag/source_ingestion/v2_models.py) | **通过**来源可追溯、快照不可覆盖；版本是源快照/替代关系，不是已验证的自动 CDC。 |
| Cleaning | 规则版本、规则校验和、清洗审计、`needs_review`；有独立清洗命令。[清洗模型](../../app/rag/clean/models.py)、[清洗入口](../../run_clean.py) | **通过**。 |
| Metadata Extraction | enrichment 生成语义、时间、实体和来源元数据，并有质量/来源信息。[enrichment 模型](../../app/rag/enrichment/models.py)、[知识构建](../../run_knowledge.py) | **通过但有覆盖缺口**，详见下表。 |
| Document Parsing | HTML、Markdown、TXT、JSON 与 PDF 路由/级联；NormalizedDocument 保留原始哈希、parser 版本、结构块及解析质量。[解析目录](../../app/rag/parse_normalize/)、[文档模型](../../app/rag/parse_normalize/models.py) | **通过**已有类型；不能据此宣称任意 PDF/表格都无损解析。 |
| Chunking / Parent-Child | 明确 Parent/Child 模型、相邻 Child 链接、检索文本、表格处理；ID 由内容确定性生成，projection 校验 Child→Parent 与文档归属。[Chunk 模型](../../app/rag/chunk/models.py)、[ID](../../app/rag/chunk/ids.py)、[索引 projection](../../app/rag/indexing/projection.py) | **通过**。修改源文本会改变内容 ID，跨版本映射依赖 provenance/manifest。 |
| Embedding / Vector Index | Qwen3-Embedding-0.6B 固定 revision、1024 维、归一化余弦；Qdrant `dense` 向量和相似度搜索。[索引配置](../../app/rag/indexing/config/v1.yaml)、[Qdrant store](../../app/rag/indexing/store.py) | **通过**实现与冻结 manifest；未重新连接实时向量库。 |
| Sparse Index | 同一 Qdrant collection 内 `qdrant/bm25` 多语言稀疏向量；projection 将正文及 topics/keywords/entity aliases 组成 BM25 文本。[索引配置](../../app/rag/indexing/config/v1.yaml)、[projection](../../app/rag/indexing/projection.py) | **通过**BM25/关键词通道；不是独立的通用倒排搜索服务。 |
| Metadata / Scalar Index | `doc_id`、`parent_id`、`source_id`、类型、语言、年份/时间范围、entity、verification/knowledge status 建 payload index。[索引配置](../../app/rag/indexing/config/v1.yaml) | **通过**已列字段；未建 `title/author/category/tags` 的独立 payload index。 |
| Evaluation Dataset / Version | Gold V4、release manifest、index manifest、模型 revision、配置版本/哈希和 trace 哈希可互相绑定。[Gold](../datasets/rag_eval_gold_v4.frozen.jsonl)、[索引 manifest 模型](../../app/rag/indexing/models.py)、[baseline](gold_v4_hybrid_shadow_baseline_20260917.json) | **通过**本地冻结评测；产物登记/发布/回滚的跨环境闭环未验证。 |

构建采用多个显式 CLI：`run_parse_corpus.py` → `run_clean.py` → `run_enrich.py` → `run_chunk.py` → `run_knowledge.py` → `run_knowledge_release.py` → `run_index.py`。索引根据 `index_input_hash` 对变化点 upsert/skip，可选 stale prune；来源有 append-only 快照与替代源记录。**具备增量组件，不等于自动增量调度、原子切换和回滚已具备。** 相关实现见[索引服务](../../app/rag/indexing/service.py)与[release 验证](../../app/rag/indexing/release.py)。

| 版本对象 | 当前标识/固定方式 | 验收结果 |
| --- | --- | --- |
| Corpus | source snapshot `corpus_version`、知识 release ID 与 corpus SHA-256 | 有来源血缘和冻结 release。 |
| Index | `indexing_version`、schema/projection version、collection name、projection/输入哈希及 build manifest | 有完整构建声明和绑定；未验证跨环境切换。 |
| Embedding | 模型名、固定 revision、维度、归一化参数写入配置及 manifest | 可重复构建合同明确。 |
| Configuration | query、retrieval、rerank、index、context 均有版本化文件；历史 baseline 记录 config SHA-256 | 离线回放可核对；部署侧配置发行未验证。 |
| Evaluation | Gold V4 文件哈希、50-case trace 哈希、评测代码与报告 | 有冻结数据集和可回放 trace；与生产监控分开。 |

### Metadata 字段与使用方式

下表覆盖当前冻结 projection 的 **1270 个 Child payload**；“缺失”只指独立索引字段，不否认文本或上游源模型中出现相应信息。计数由本地 projection 枚举得到。

| 请求字段 | 当前状态 | Filtering / Routing / Ranking |
| --- | --- | --- |
| title | 上游来源/规范化文档有标题；Child payload 无独立 `title`（0/1270），只保留 breadcrumb、`source_name` 等 | 可作为展示/文本线索；无专用 title scalar filter 或 metadata boost。 |
| author | 上游 `CandidateSource`、`NormalizedDocument` 有 author；Child payload 无独立 `author`（0/1270） | 当前不能按索引 author 精确过滤。 |
| entity | `entity_ids` 非空 1236/1270，含类型/名称/aliases | Query Understanding 解析实体，在线 filter 按 entity ID 编译；BM25 文本含名称和 aliases。 |
| date | `date` 73/1270，`year` 185/1270，`source_published_at` 204/1270；另外 `min/max_mentioned_year` 628/1270 | 年份/范围可过滤；元数据缺失时精确时间筛选覆盖受限，不应把正文提及年份当成出版日期。 |
| type | `content_type` 和 `language` 均有字段；其中 content_type=`unknown` 230，language=`und` 419 | content type/language 可过滤，query type 参与路由与规划；未知值影响过滤覆盖。 |
| category | 无独立 `category`（0/1270）；`topics` 非空 1227/1270 | topics 进入 BM25 检索文本，但没有 category 专用过滤/路由合同。 |
| tags | 无独立 `tags`（0/1270）；`keywords` 非空 1122/1270 | keywords 进入 BM25 文本，非通用 tag filter。 |

当前显式 metadata filter 主要是 entity、temporal、language、content type；排序主要来自 Dense/BM25、RRF 和 reranker，未见通用 title/author/category/tags metadata 加权。见[filter 构造](../../app/rag/query_understanding/filters.py)、[filter 编译](../../app/rag/retrieval/filter_compiler.py)、[fusion](../../app/rag/retrieval/fusion.py)。后续如业务需要作者/分类检索，应先制定字段来源与语义，再作为新版本 schema；本次不修改。

## 2. Offline Evaluation 体系验收

**Gold 与证据单位。** 冻结 Gold V4 用 `answerability_level` 区分 full/partial/insufficient；支持 claim、supporting span、duplicate/equivalent/complementary evidence group。full/partial 的正向 evidence 被计分，insufficient 与 needs_input 不进入正向证据分母。group-aware 匹配使等价 Parent 不重复增加证据单位；complementary 需要组合。见[Gold 合同](../../app/rag/evaluation/models.py)、[group-aware](../../app/rag/evaluation/group_aware.py)、[V4 answerability](../../app/rag/evaluation/v4_answerability.py)。

**Trace 与指标。** Trace 合同保留 Query/Top40、Rerank Top12、最终上下文、引用 Parent、模型/配置版本、状态和耗时，并校验阶段间 Parent 关系。离线 CLI 可采集、验证、重放 trace，计算 Recall@K、宏/微 evidence recall、context/citation/latency 指标；生成阶段另有 citation-aware 评测与独立 DeepEval 环境。见[Trace 模型](../../app/rag/evaluation/models.py)、[评测服务](../../app/rag/evaluation/service.py)、[离线说明](../README.md)、[trace 采集](../../run_rag_trace_collection.py)、[生成入口](../../run_rag_answer_generation.py)。

| 故障段 | 可用定位证据 | 边界 |
| --- | --- | --- |
| Retrieval | 每 leg Dense/BM25 候选、融合/筛选与 Top40/Top100，Gold unit 是否命中 | 能定位召回/过滤/选择阶段；需要保留对应原始候选 trace。 |
| Reranker | Top40 输入、Top12 输出、分数、状态/回退 | 能区别候选未入池与已入池但降序。 |
| Context | 最终 context Parent、context pack/sufficiency 与引用 | 能区别 Top12 已有证据却未入上下文。 |
| Generation | 生成答案、citation ID、离线 Judge/失败审计 | 引用 ID/段落锚点校验不等于句子事实蕴含证明；真实在线答案质量仍需请求级验收。 |

**评测完整度：8/10。** 冻结资产和历史 trace 支持回归基线、离线 benchmark 和可解释的未来实验；[50-case shadow baseline](gold_v4_hybrid_shadow_baseline_20260917.json)记录 46 ready、4 needs_input，后续 retrieval/rerank/context 为 45 success、1 empty、4 not_run。它证明该离线采集路径，不证明默认聊天入口启用了同样链路。Gold 主要为周杰伦音乐知识，不能代表新知识库或所有业务意图；[评测说明](../README.md)明确早期 owner-approved Gold 为 AI 辅助复核，非独立人工盲标。上线门槛不应只看这一套离线指标。

## 3. Online Query Pipeline 验收

实际可接通路径：`/api/ai/worldline` 或 foundation graph 的 WorldLine 子图 → query/context/entity 解析 → SearchPlan → Dense + Qdrant BM25 → metadata filter → weighted RRF → Parent selection → 可选 rerank → Context Pack → grounded prompt/LLM → 带 sources/citations 的结构化结果。`/api/ai/chat` 是另一路 SSE 入口，由 graph mode 决定 legacy/foundation。见[API 装配](../../app/main.py)、[聊天入口](../../app/api/chat.py)、[WorldLine 入口](../../app/api/worldline.py)、[WorldLine graph](../../app/agent/foundation/subgraphs/worldline/graph.py)。

| 模块 | 代码验收 | 状态与限制 |
| --- | --- | --- |
| Query parsing / intent / context resolution | Query Understanding 有上下文消歧、实体解析、分析器、constraints、SearchPlan；foundation graph 有意图路由。[服务](../../app/rag/query_understanding/service.py)、[foundation graph](../../app/agent/foundation/main_graph.py) | **已实现**音乐域理解；无法唯一解析的 query 可 `needs_input`。 |
| Rewrite / expansion / planner | 原始 query、entity alias 词、少量语义 cue、对比多实体分解；单 leg `hybrid`，多 leg `multi_query_hybrid`，最多 3 variants。[expansion](../../app/rag/query_understanding/expansion.py)、[planner](../../app/rag/query_understanding/planner.py)、[配置](../../app/rag/query_understanding/config/v1.yaml) | **多查询/domain-aware 已有**；未见统一的 schema-aware 同义字段映射层。此前诊断 rewrite 不能算生产已接入。 |
| Dense / Sparse / Hybrid / filter / selector | 同一 Qdrant collection 检索 dense 与 BM25；按 leg 融合、weighted RRF、Child→Parent 聚合、去重、文档/来源 cap 与 overflow；有 entity/time/language/type filter。[检索服务](../../app/rag/retrieval/service.py)、[Qdrant](../../app/rag/retrieval/qdrant.py)、[融合](../../app/rag/retrieval/fusion.py)、[选择](../../app/rag/retrieval/selection.py)、[配置](../../app/rag/retrieval/config/v1.yaml) | **核心 Hybrid 能力具备**；这满足成熟 RAG 的检索功能基座，不能仅凭功能表宣称企业级生产运维已达标。 |
| Ranking | Qwen3-Reranker-0.6B 固定 revision；最多 40 候选、Top12，30 秒超时、限并发；分数须有限且在 0–1，排序有检索分/原 rank 作为稳定 tie-break，非法分数回退到检索顺序。输入是最佳 Child evidence＋title/breadcrumb，非完整 Parent。[rerank 配置](../../app/rag/reranking/config/v1.yaml)、[服务](../../app/rag/reranking/service.py)、[输入](../../app/rag/reranking/input_builder.py) | **已实现、可选接线**；运行模式 `off/shadow/on`，代码默认 `off`。 |
| Context engineering | 证据充足性 gate、按子问题/比较实体覆盖、近重复去重、压缩、选择与顺序；pack 最多 6 项、3000 tokens、单 source 3 项。[pack 服务](../../app/rag/context_packing/service.py)、[配置](../../app/rag/context_packing/config/v1.yaml) | **已实现**。WorldLine adapter 还声明 8 文档/6000 token 上限；使用 SearchPlan 时先走更严格的 Context Pack，不能把 8/6000 当实际 pack 上限。 |
| Prompt / generation / citation | WorldLine prompt 限制仅用提供来源，要求 `[source_id]`；结构化 LLM 生成与重试；结果含 sources/citations；验证引用 ID、正文锚点、知识摘录非空和实质段落引用。[prompt](../../app/agent/foundation/subgraphs/worldline/prompts.py)、[生成](../../app/agent/foundation/subgraphs/worldline/generation.py)、[grounding](../../app/agent/foundation/subgraphs/worldline/grounding.py) | **已实现结构/引用控制**；校验没有逐句验证证据是否语义支持全部事实，故不能承诺零幻觉。 |
| Streaming / evidence return | `/api/ai/chat` 发 SSE；`/api/ai/worldline` 返回带 sources/citations 的 JSON。[chat](../../app/api/chat.py)、[WorldLine API](../../app/api/worldline.py) | **两种接口能力分别存在**；未验证同一业务请求同时走完整 RAG、流式输出且逐段返回结构化证据。 |

**关键启用边界。** 代码和[示例环境](../../.env.example)默认 `AI_AGENT_GRAPH_MODE=legacy`、`AI_RAG_RETRIEVAL_MODE=empty`、`AI_RAG_RERANK_MODE=off`、空 index manifest；WorldLine retrieval runtime 对 `empty` 返回空检索器，`shadow` 只观察检索但不向答案交付文档，只有 `hybrid` 配合有效索引 manifest 才能提供检索证据。[运行模式实现](../../app/agent/foundation/subgraphs/worldline/retrieval.py)、[应用 settings](../../app/config/settings.py)。这是默认/示例配置的代码事实，**真实部署的私有环境变量未检查**。因此在线全链路只能判定“可配置接通且模块级测试通过”，不能判定“默认已启用并经真实业务流量验证”。

## 4. 工程化能力验收

| 项目 | 已有能力 | 缺口/验收边界 |
| --- | --- | --- |
| 配置管理 | Embedding/index、retrieval、query understanding、reranker、context 配置分文件版本；prompt 文本与部分 prompt version、运行环境模式分层；manifest 记录 release/模型/config 指纹。[索引配置](../../app/rag/indexing/config/v1.yaml)、[retrieval 配置](../../app/rag/retrieval/config/v1.yaml)、[WorldLine prompt](../../app/agent/foundation/subgraphs/worldline/prompts.py) | 未验证部署侧配置注册、审批、环境差异、回滚；不能从本地 YAML 推断生产设置。 |
| 日志与监控 | WorldLine graph 记录节点耗时，retrieval/rerank 记录状态、数量、部分警告与 latency；离线 trace 可关联 plan/index/Parent。[在线检索日志](../../app/agent/foundation/subgraphs/worldline/retrieval.py)、[WorldLine graph](../../app/agent/foundation/subgraphs/worldline/graph.py) | 未找到统一的生产 request/trace ID 跨 `query→retrieval→rerank→context→answer` 贯通，也未找到 OpenTelemetry/Prometheus 或持续质量/延迟仪表盘；不能把离线 trace 当在线监控。查询与上下文日志的保存、脱敏和保留策略未验证。 |
| 健康检查 / 失败处理 | `/health` 存在；retrieval/rerank 有空结果、失败状态、超时/回退路径。[main](../../app/main.py)、[rerank 服务](../../app/rag/reranking/service.py) | `/health` 仅返回 `ok`，未验证 Qdrant、模型、索引绑定和 LLM 就绪；真实依赖故障注入未做。 |
| 可扩展性 | Source parser/ingestion、索引 projection、retrieval service、rerank scorer、Context Pack、foundation subgraph 边界清楚；音乐业务工具与 Agent graph 已有。[Agent graph](../../app/agent/foundation/main_graph.py)、[source parser](../../app/rag/parse_normalize/registry.py) | 新知识库仍需新的 source/schema/entity/query profile 与 release/index；换 embedding/reranker 需对应配置、索引重建或合同兼容验证。当前 checkpoint 为进程内 `InMemorySaver`，非持久化 memory。[checkpoint](../../app/agent/checkpointing.py)；未发现通用 MCP 运行时/工具注册链路的验收证据。 |

按用户要求逐项看在线记录能力：**query** 有 SearchPlan/离线 trace，但生产日志持久化与脱敏未验证；**retrieval result** 有状态、候选数量、失败通道和耗时日志；**rerank result** 有状态、输入/输出数、模型和耗时日志；**context** 有 pack 结果、token/数量相关日志及离线 trace，生产完整上下文持久追踪未验证；**latency/error** 有节点耗时与部分异常日志，缺少已验证的全请求分布式关联、告警和持续监控。见[在线检索日志](../../app/agent/foundation/subgraphs/worldline/retrieval.py)、[WorldLine graph](../../app/agent/foundation/subgraphs/worldline/graph.py)、[离线 Trace](../../app/rag/evaluation/models.py)。

## 5. 完成项、未完成项与下一阶段

**已完成且可作为开发基线：** 来源 provenance 与清洗/解析/切块/知识构建；冻结 release 和双通道 Qdrant 索引；Parent-Child、metadata filter、RRF/selector、rerank、context pack；音乐域 Query Understanding；带结构化引用的 WorldLine 生成；Gold V4 与分阶段离线评测、历史 trace、相关单测。

**尚不能标“验收完成”：** 默认在线聊天入口上的 RAG 启用与真实端到端证据；流式回答中的统一结构化 evidence 合同；生产请求级链路追踪、监控和依赖就绪；跨环境 artifact 交付/回滚；持久化会话记忆；新知识库的通用接入合同；独立人工盲标及跨业务域评测代表性。Schema-aware rewrite 是实验结论，不是当前生产 Planner 能力。

**下一阶段只做上层应用与运维，不做本轮 RAG 指标调优：**

1. **Agent / Workflow：** 明确业务意图路由、WorldLine 与普通聊天的调用契约、失败/追问路径及工作流状态。
2. **Memory：** 为会话状态选择可持久化后端，定义作用域、过期与用户数据边界，替代仅进程内 checkpoint 的生产依赖。
3. **Tool Calling / MCP：** 统一工具注册、权限、超时、审计与错误返回合同；需要外部工具时再接 MCP，不把检索索引当工具注册中心。
4. **Frontend / API：** 明确同步 JSON 与 SSE 的产品场景，统一答案、引用、来源与错误状态的数据合同，并做一次真实请求级接线验收。
5. **Monitoring：** 增加跨阶段关联 ID、依赖就绪、耗时/错误/空结果仪表盘和告警；规定日志中 query/context 的脱敏与保留策略。

以上工作不要求更改本次冻结 Gold、索引、检索参数或评分规则。

## 6. 验证记录与限制

- 只读核对 Gold、知识 release、index projection 与 index manifest SHA-256；本地文件与历史 baseline 一致。枚举了 1270 条 index projection payload 的 metadata 字段覆盖。
- 在 `ai-service` 运行：`.\.venv\Scripts\python.exe -m pytest tests/test_source_ingestion.py tests/test_parse_normalize_corpus.py tests/test_rag_chunk.py tests/test_metadata_enrichment.py tests/test_rag_indexing.py tests/test_retrieval_service.py tests/test_reranking_service.py tests/test_rag_context_packing.py tests/test_rag_evaluation_service.py tests/test_rag_gold_v4_audit.py tests/test_worldline_retrieval_adapter.py tests/test_worldline_generation.py tests/test_worldline_graph.py tests/test_worldline_api.py -q`。结果：**165 passed、1 skipped、38 subtests passed**。
- 未执行线上服务、实时 Qdrant/LLM 请求、真实 SSE 浏览器交互、故障注入或跨环境部署检查；本报告关于“未见”的结论是对当前代码/冻结产物的静态审计，不是对未提供部署设施的不存在证明。
- 工作区已有 `gold_review_v0_blind_pack.md` 修改及若干 `data/` 未跟踪目录，均不属于本审计，未触碰。

**最终回答：当前 RAG 基础设施已具备可冻结的离线数据/索引/评测基线和模块化在线能力，可以进入上层应用开发；完整默认在线 RAG 与生产运维能力尚需单独验收，暂不应宣称全链路已经无条件冻结。**

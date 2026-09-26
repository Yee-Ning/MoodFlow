# Worldline Corpus / Source Context Preparation

检查时间：2026-09-26 22:33（Asia/Shanghai）。范围：真实 corpus/source metadata、独立 v4 release/index 发布和环境级 smoke。

本轮达到可进入 Online E2E + PostgreSQL Restart 验收的环境准备状态。正式 runtime 已指向新批准的 manifest，原有 release/index/collection 保留。达到四项目标后停止环境验证；未执行最终 Online E2E、PostgreSQL restart acceptance、Semantic Relations Evaluation、deferred eval debt 修复、Gold 新增、validator/scoring 修改或服务器部署。

## 1. 逐 document 审计与缺口

审计入口为原正式 `data/knowledge/worldline/v4/knowledge_enriched_chunks.jsonl` 及其引用的真实 raw response。共 56 documents、1,947 chunks（677 Parent、1,270 Child）、79 个原有 SUPPORTED claims、6,929 次已链接 entity occurrence。

| 字段 | 准备前 | 准备后 |
|---|---:|---:|
| source URL | 56/56 | 56/56，原 ID/URL 保留 |
| 明确 publisher | 45/56 | 56/56 |
| 明确 title | 53/56 | 56/56 |
| 原始 response SHA 与本地 response bytes 一致 | 56/56 | 56/56 |
| 原有 chunk section/locator | 56/56 | 保留；新增捕获另有 DOM locator/offset |
| 原文 document captures | 0 | 27 |
| 与原有 claim 精确绑定的 context packet | 0 | 40 |
| native packet 中 article_sentence | 0 | 39 |
| native interview_question / interview_answer / speaker_binding | 0 | 各 1，属于同一个完整访谈 packet |

逐文档的 URL、publisher/title 前后值、response SHA、locator、article subject context、Q/A、speaker binding、claim ID 与缺口见 [document readiness JSON](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/document-readiness-and-gaps.json)，便于阅读的 56 文档表和 39 条 claim 缺口见 [corpus document audit](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/corpus-document-readiness.md)。原文、身份补全证据及 offset 见 [document source metadata](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/document-source-metadata.json)。

保留的缺口：39/79 claims 没有可绑定的 RelationSourceContext，具体为 24 条没有唯一 verbatim HTML paragraph，11 条原证据属于 PDF/格式化材料、未提取 HTML paragraph context，4 条受现有每 document 8 captures 上限限制。缺口没有使用 Gold、审核说明或 LLM 填写。没有显式问答的文章不生成问句、答句或 speaker；没有 context 的 claim 保持缺失。

readiness 的含义是正式数据具备真实 article 与完整 interview packet 的输入能力、来源身份和 hash 完整、真实传输链验证通过。它不表示所有文档都具有四类角色，也不表示 79 条 claims 的 context 覆盖全部完成。上述逐条缺失是本轮要求允许登记并保留的缺失，不作为伪造字段补齐。

## 2. 来源身份补全

| 原缺口 document/source ID | 补全值 | 可审计依据 |
|---|---|---|
| jc-v2-009 | publisher：凤凰新媒体 | 原 raw HTML 的凤凰新媒体介绍链接及页面身份信息；提取链接原文，不用域名标签 |
| jc-v2-016、020、022、025、026、029、033 | publisher：维基媒体基金会 | 各自原 raw HTML 中指向 Wikimedia Foundation 的明确链接文字 |
| jc-v2-045 | publisher：UNIVERSITY OF MALAYA；title：JAY CHOU'S ZHONGGUO FENG: SONGS AND IDENTITY | 原 PDF 第 1 页封面机构与题名；题名只合并原文换行空白 |
| jc-v2-046 | publisher：Porcelain Publishing International Limited；title：中国风歌曲分析——以周杰伦的《霍元甲》为例 | 原 PDF DOI 与官方 DOI 页核对；publisher 从官方平台页面的明确身份取得 |
| jc-v2-048 | publisher：湖南日报报业集团；title：周杰伦：为妈妈和外婆而歌唱 | 原 PDF 第 1 页文章标题；家庭导报官方数字报页面明确列出报业集团 |

期刊 title 来自 [官方 DOI 页面](https://www.wsp-publishing.com/zh/article/doi/10.12184/wspcyycx2WSP2516-415516.20220608/)，与原 PDF DOI `10.12184/wspcyycx2WSP2516-415516.20220608` 一致；publisher 来自 [官方 publishing platform](https://www.wsp-publishing.com/en/home/)。该 publisher 记录的范围为“当前发布平台身份”，不据此断言 2022 年的公司归属。报业集团身份来自 [家庭导报官方数字报](https://epaper.voc.com.cn/jtdb/)。2010 年的文章 HTML 归档入口返回 403，因此标题直接使用已保存原 PDF；没有从文件名推导。

既有明确 publisher/title 保留。另有 8 个 HTML document 原有顶层 title 已存在，但 source_metadata.title 为空，现从各自 raw HTML title 元素补齐显式 source title；不改用于原 QA 的顶层标题。新增三份官方 identity response 的原始字节、URL、时间和 SHA 保存于 `ai-service/data/raw/worldline-source-context-v4-20260926/`。

每个补全字段记录 exact_span、start/end、extracted_text、extracted_text_sha256、response_sha256、原 URL 和 PDF page/DOM selector locator。没有用 breadcrumb、域名标签或文件名冒充来源身份。三份 PDF 封面/页首已渲染并人工视觉核对。

## 3. Source context capture 与 native claim binding

新数据目录为 `ai-service/data/source_context/worldline/20260926-v4/`。HTML 从已校验的 raw response 提取：BeautifulSoup 移除 script/style，使用 `get_text(separator='', strip=False)`。保留原文空白、段落缩进和标点，不做文本改写。offset 为 Unicode code point、半开区间；绝对 offset 指向该 raw response 的 decoded DOM text，packet segment offset 指向原文 capture content。

`source_context_captures` 保留整段 article 原文，或相邻的完整 interview question + answer + 原文 respondent label。`source_context_capture_provenance` 记录原始 response SHA、decoded DOM text SHA、绝对 offset、原 raw_path 和 locator。随后复用原 IndexDocumentProjector v4，只对既有 SUPPORTED claim 的 exact evidence span 做 native binding；不生成新 claim 或推测 subject/speaker。

source/chunk/claim IDs、Child/Parent 原文、table data、结构 offset、entity/claim 语义决策保持不变。metadata 更新使 enrichment_input_hash、knowledge_input_hash 改变，已用原 hash 函数重新计算，并保存 [旧/新 input hash lineage](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/input-hash-lineage.json)。

RelationSourceContext.document_id 按既有运行时契约绑定 Parent ID；真实 corpus doc_id 单独保留在 metadata、绑定清单与 smoke 记录中。没有将这两个 ID 混为同一个字段。完整 40 个 native packet 见 [native context bindings](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/native-context-bindings.json)。

## 4. 正式链路与完整传输验证

链路为原 raw/cleaned/normalized corpus → metadata overlay/enriched corpus → verbatim captures → native claim binding → 原 KnowledgeReleaseBuilder → IndexDocumentProjector v4 → 独立 Qdrant collection → Hybrid Retrieval → ContextPack → WorldLineContextAdapter → Worldline source_context binding。

对 56 documents 重新调用正常 ChunkEngine，1,947 chunks 的 ID、原文、table data、结构 offset 均与原版一致，新 document metadata 完整继承，见 [chunk metadata replay](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/chunk-metadata-replay.json)。这是实际 chunk engine 验证；metadata 准备复用原 enrichment/knowledge decisions，没有重新运行 LLM 提取。

Qdrant 的全部 1,270 个 live payload 与新 projection 逐项一致，dense/sparse vector 存在、dense dimension 正确、collection count 一致；release/projection/manifest fingerprints 均通过。40 个投影 packet 全部追溯到真实 raw response、原始 capture、绝对 DOM offset 和既有 native binding。

现有 audit 的 `children_with_capture_count=510` 是“Child 继承了具有 capture 的 document metadata”的数量；它不是已绑定 packet 数。真实 native binding 是 40 packets 分布于 27 个 Child，另有 1,243 个 Child 没有 bound context（大多数 Child 本就没有 SUPPORTED claim）。audit 的 document source_context_count 也包含继承复制，不能当作唯一 capture 数；本报告使用准备清单中的 27 unique captures。

实际 smoke 发现两处 context 装配问题，并仅在原装配器内修正：Child 跨多个 Parent paragraph 时重复加入已包含的 paragraph，导致 anchor 不唯一；长 Parent view 约 837 tokens 加完整访谈约 166 tokens 超过既有 900 tokens，导致 packet 整体被丢弃。现在为完整 packet 预留预算，优先压缩普通 Parent view，同时避免插入 Child 中已有的 paragraph。最大单项 900、总预算 3,000、排序、去重阈值、validator/scoring 均保持原值；仍装不下的 packet 整体省略并报告 warning，不截成 answer-only。另将 source_title 优先读取显式 source_metadata.title，原 QA 标题不变。

## 5. 独立 release/index 与 approval

| 项目 | 新正式发布 |
|---|---|
| release path | `ai-service/data/knowledge/worldline/source-context-20260926/v4/` |
| dataset_version / release ID | v4 / `knowledge-v2-aa5271a36860debb`（前缀沿用原 builder 约定） |
| release manifest SHA | `sha256:abb771839a93619039b8b8e5eaf5e776acfb5ee6355b01ba405d9fcc2002bd7e` |
| index path | `ai-service/data/indexing/worldline/source-context-20260926/v4/` |
| collection | `worldline_knowledge_v4_aa5271a36860debb_v1_v2_v4` |
| projection/indexing/schema | v4 / v1 / v2 |
| dense / sparse | Qwen3-Embedding-0.6B，pinned revision，1,024 cosine / Qdrant multilingual BM25 |
| projected / indexed / point count | 1,270 / 1,270 / 1,270 |
| limited_to / failed / pruned | null / 0 / 0 |
| index manifest SHA | `sha256:a74164c52346dd4050468d2e491dfe5993abec16cb70122d9df7d693769d2f17` |
| ready index approval | approved；1,270 live payloads/vectors 核验通过 |

[release manifest 副本](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/knowledge_release_manifest.json)、[index manifest 副本](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/index_build_manifest.json)、[approval 副本](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/ready_index_approval.json) 保留实际发布身份。两个 manifest 采用 Git `-text`，保持用于 SHA 校验的原始字节。

approval 范围仍为 `retrieval_build_integrity_only`，`semantic_source_context_readiness_approved=false`；本轮没有执行 Semantic Relations Evaluation，不能将此 sidecar 称为语义评分批准。corpus/source-context readiness 另由逐文档来源审计及真实 packet smoke 证明。

原知识 QA attestation 只用于原 release builder 的批准门槛，已有 79 claim / 100 entity QA samples 的 SHA 不变；没有把 QA rows、Eval Gold、review pack 或审核说明当作 source capture 或 production index data。

旧 `data/knowledge/worldline/v4/`、旧 `data/indexing/worldline/v4/` 与旧 collection `worldline_knowledge_v4_ed11e2baaf47553d_v1_v2_v4` 保留；新旧 collection 均 green、各 1,270 points。旧 manifest SHA 仍为 `sha256:c83a60216cbec622b8c80311b90dc1dfdf30eac9213f766b32dda8da6dec1ff2`，旧 knowledge 文件 SHA 与旧 manifest 一致。见 [rollback preservation](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/rollback-preservation.json)。需要 rollback 时可将 AI_RAG_INDEX_MANIFEST 恢复为 `data/indexing/worldline/v4/index_build_manifest.json` 并刷新 FastAPI；本轮没有执行 rollback。

## 6. 真实 smoke

详细证据：[real source context smoke](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/real-source-context-smoke.json)。两个案例均使用实际 dense + BM25 query，沿用现有 SUPPORTED 与 entity metadata filter；没有按 source/claim ID 强制命中或替换检索结果。

| 场景 | 真实来源与 native binding | 结果 |
|---|---|---|
| article subject context | 中国台湾网 jc-v1-020；claim `claim:603dddf836fa9457f64461ee`；Child `child-81de2143a7a24df54735db77` | 106 字符的完整原文段落进入 candidate、ContextPack、adapter；native source_context 接受；17 candidates、6 pack items |
| interview Q/A + speaker | 新浪娱乐 jc-v1-018；claim `claim:33051d48b804d35fe8b36525`；Child `child-354b33f7da14ee96414828ce` | 171 字符 packet 原样传输；完整主持人问句、方文山答句（包括后续《完美主义》句子）及原文 `方文山：` 全部保留；4 candidates、4 pack items |
| metadata/content 篡改 | 实际 packet 的内存副本 | publisher、source_url、content hash、claim ID 修改后原 runtime binding 拒绝 |
| raw response SHA/offset 篡改 | 实际来源 packet 副本及工程测试 | 原始 raw response SHA/绝对 offset 核验拒绝；没有修改生产 Qdrant 数据 |
| Child content/hash 篡改 | 实际投影 payload 的内存副本 | 原 retrieval hash guard 拒绝 |
| 缺失 context | 删除真实 packet 后，以及实际检索的无 context items | runtime 保持缺失，不自动补问句、speaker 或任何 source context |

这验证到 Worldline runtime 输入 binding；没有调用完整用户请求的生成链，也没有以此声称最终 graph 输出语义验收完成。

## 7. 当前配置与服务 readiness

正式私有 `.env` 已仅切换非敏感 manifest 路径；同步更新 `.env.worldline-online.example`。本机 FastAPI 刷新为新配置，health 正常；未重启 PostgreSQL。检查输出只记录配置存在性，没有密码/API Key。

```text
retrieval_mode=hybrid
ready_index_manifest=approved
index_manifest=data/indexing/worldline/source-context-20260926/v4/index_build_manifest.json
checkpoint_backend=postgres
rerank_mode=off
java_music_service=reachable
qdrant=reachable
postgres=reachable
model_provider=configured
llm_model=deepseek-flash
generation_model=deepseek-flash
```

| 服务 | 实际检查 | 状态 |
|---|---|---|
| PostgreSQL | 原数据库；checkpoint_blobs/checkpoint_migrations/checkpoint_writes/checkpoints；migration 9/9 | reachable、schema ready；真实同进程 checkpoint 写入、load、recover 一致 |
| Qdrant | 本机 6333；全量新 collection integrity；真实 hybrid query | reachable，HTTP 200；1,270 points |
| Java Music Service | 8080 `/api/music/entities/search?keyword=Jay%20Chou&type=artist&limit=1` | reachable，HTTP 200 |
| Provider | 既有 provider `/models`；两个配置模型在返回列表中 | configured、reachable，HTTP 200；本轮没有 generation 调用 |
| FastAPI | 8001 `/health` 与已有 session 路由 | reachable，HTTP 200；不存在的 session 返回 404 |
| Frontend / API base | 5173 页面及 `/api/ai/worldline/sessions/environment-readiness-missing` proxy | 页面 200；proxy 到 FastAPI 返回预期 404 |

必需配置无缺失：DATABASE_URL、PGPASSFILE、LLM_API_KEY、LLM_BASE_URL、LLM_MODEL、SPRING_BOOT_BASE_URL、AI_RAG_RETRIEVAL_MODE、AI_RAG_INDEX_MANIFEST 均已配置。QDRANT_API_KEY 未设置：当前复用的本地 Qdrant 不要求该可选认证项，实际读写/query 已通过。上述服务均已启动。

PostgreSQL 本轮使用带明确 engineering fixture 标签的结果复查 persistence；外部生成调用为 doubles，实际 PostgresSaver/session create/load/recover 使用原数据库，三个 relation DTO 类别各 1 条保存恢复一致。这是环境级同进程持久化检查，没有跨进程 restart acceptance。详见 [environment readiness](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/environment-readiness.json)。

## 8. 已运行命令、结果与交付范围

以下命令在 `ai-service` 目录使用既有 `.venv/Scripts/python.exe` 执行；复查环境后停止后续环境验证，仅整理已有证据。

```powershell
python -m scripts.worldline_source_context_preparation --output-root data/source_context/worldline/20260926-v4

python run_knowledge_release.py --finalize --source-root data/source_context/worldline/20260926-v4/working-knowledge --output-root data/knowledge/worldline/source-context-20260926/v4 --review-root data/knowledge/review-v2 --config app/rag/knowledge/config/v13.yaml --registry app/rag/knowledge/entity_linking/registry/v3.yaml --enriched-path data/source_context/worldline/20260926-v4/enriched_chunks.jsonl

python run_index.py --config app/rag/indexing/config/worldline_v4.yaml --knowledge-path data/knowledge/worldline/source-context-20260926/v4/knowledge_enriched_chunks.jsonl --release-manifest data/knowledge/worldline/source-context-20260926/v4/knowledge_release_manifest.json --output-root data/indexing/worldline/source-context-20260926/v4 --local-files-only

python -m scripts.worldline_source_context_smoke --manifest data/indexing/worldline/source-context-20260926/v4/index_build_manifest.json --preparation data/source_context/worldline/20260926-v4 --output ../docs/reports/worldline_corpus_source_context_preparation/real-source-context-smoke.json

python -m scripts.worldline_environment_readiness --output ../docs/reports/worldline_corpus_source_context_preparation/environment-readiness.json --approve-index --checkpoint-trace ../docs/reports/worldline_runtime_integration/runtime_smoke_trace.json

python -m pytest tests/test_worldline_source_context_preparation.py tests/test_worldline_environment_preparation.py tests/test_rag_indexing.py tests/test_worldline_runtime_integration.py tests/test_worldline_retrieval_adapter.py tests/test_rag_context_packing.py tests/test_retrieval_contracts.py tests/test_retrieval_service.py tests/test_worldline_api.py --junitxml=../docs/reports/worldline_corpus_source_context_preparation/engineering-tests.xml -q --tb=short

$env:QDRANT_INTEGRATION_URL='http://127.0.0.1:6333'
python -m pytest tests/test_rag_indexing.py -k real_qdrant --junitxml=../docs/reports/worldline_corpus_source_context_preparation/qdrant-live-tests.xml -q

python -m ruff check scripts/worldline_source_context_preparation.py scripts/worldline_source_context_smoke.py tests/test_worldline_source_context_preparation.py app/rag/context_packing/service.py app/rag/indexing/source_context.py
python -m compileall -q scripts/worldline_source_context_preparation.py scripts/worldline_source_context_smoke.py tests/test_worldline_source_context_preparation.py app/rag/context_packing/service.py app/rag/indexing/source_context.py
```

准备、原 builder release gate、全量索引、40 packet 原文追溯、真实 article/interview smoke 均通过。相关工程测试 **110 passed、1 skipped、0 failures/errors**；该 skipped 是需要显式 QDRANT_INTEGRATION_URL 的现有 opt-in test，已单独对真实 Qdrant 补跑 **1 passed、0 skipped**，临时 test collection 清理完成。Ruff、compileall、scoped Git diff check 均通过。见 [工程 JUnit](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/engineering-tests.xml)、[Qdrant JUnit](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/qdrant-live-tests.xml)、[validation summary](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/validation-summary.json)。

代码/非敏感配置/相关测试已独立提交：`d49edbb`（Prepare verbatim Worldline source contexts and preserve complete packets）。改动仅涉及 source metadata 准备和 smoke 脚本、source_title 传输、原 ContextPack 装配及对应测试。未改 evaluator、validator/scoring、前端 UI、数据库结构或依赖。未覆盖已有用户未提交的 Gold/reports 变更，也未 push。

新 raw authority、metadata overlay、enriched/knowledge release 和大体积 projection 保存在本机，Git 交付包含脚本、manifest 与审计证据，不重复提交原用户未跟踪 corpus。document source metadata 审计副本保留全部来源/捕获字段及 parser metadata hash，省略无关 parser node tree；完整 metadata 保留于本地 overlay 与正式 release。文件路径、大小和 SHA 见 [local artifact inventory](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_corpus_source_context_preparation/local-artifact-inventory.json)；这是当前本机运行环境的 readiness，不表示这些本地 corpus 文件已分发到其他机器。

## 9. 剩余限制与最终状态

当前环境 blocker：无。39 条 claim context 缺失、非问答文档的 Q/A/speaker 缺失均保持可见，不补写；超过既有预算的完整 packet 仍可能整体省略。Semantic Relations 的既有 deferred debt 没有触碰。当前 publisher 平台身份不作为历史归属证明。

最终用户 UI → runtime → PostgreSQL restart → recover 的 Online E2E、真实 provider generation 完整行为、跨进程 checkpoint 恢复和 Final Worldline Regression 均未执行，属于后续验收范围。没有前端 UI 改动，因此本轮没有新增前端构建/响应式视觉验收；服务可达和 metadata 传输通过不等同于这些完整验收完成。旧报纸 archive HTML 403 的限制已通过原 PDF/官方页面分别记录，没有假造网页原文。

```text
corpus_source_context_ready=true
ready_index_manifest=approved
environment_ready=true
can_run_online_e2e_postgres_restart=true
```

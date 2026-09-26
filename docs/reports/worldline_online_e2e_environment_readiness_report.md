# Worldline Online E2E Environment Readiness

日期：2026-09-26。阶段：Online E2E Environment Preparation。

**PostgreSQL 已修复；Java、Qdrant、PostgreSQL、Provider、FastAPI 和 Frontend 均可连接。正式配置已启用 hybrid + PostgreSQL，独立 v4 index 已完成并获构建完整性批准。真实 corpus 的来源上下文仍不齐，本轮最终判定 `environment_ready=false`、`can_run_online_e2e_postgres_restart=false`。**

本轮完成环境与数据准备、经用户明确批准的 PostgreSQL 故障恢复，以及环境级 smoke。没有执行用户完整请求 → UI → restart → recover，没有执行 Semantic Relations Eval、Final Worldline Regression、deferred eval debt 修复、Gold 新增、evaluator 修改或服务器部署。

机器证据：[readiness.json](worldline_online_e2e_environment/readiness.json)、[validation.json](worldline_online_e2e_environment/validation.json)、[数据库恢复记录](worldline_online_e2e_environment/postgres-recovery.json)。最终探测时间为 2026-09-26 21:22（Asia/Shanghai）。

## 1. PostgreSQL checkpoint readiness

`postgres_checkpoint_ready=true`。继续复用原 `moodflow-postgres` 容器、`moodflow_pgdata` 卷和 `moodflow_agent` 数据库。现有 `.env` 原本已经配置 PostgreSQL backend，但数据库当时未运行；本轮保持并明确配置 `CHECKPOINT_BACKEND=postgres`，没有增加第二套正式数据库。

- `WorldLineSessionService` 的正式 factory 强制 PostgreSQL，lifespan 通过 `initialize_runtime_checkpointer()` 初始化现有 `AsyncCompatiblePostgresSaver`；没有启用 memory fallback。
- `WorldLineSessionService` 使用独立 `worldline:<session_id>` thread 保存 JSON 兼容 snapshot，恢复时重新校验 `WorldLineSessionState`。
- `checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 四表齐全。官方 checkpoint migration 为 v0–v9，实际 MAX(v)=9，与当前安装的 saver 要求一致。
- `python -m scripts.migrate_agent_postgres` 成功：business migrations 和 LangGraph migrations 均已 current；已有 pgvector extension 和数据库 collation 检查通过。
- 真实 PostgreSQL smoke 已完成 create session → checkpoint 写入 → load → 新 service instance recover，保持在同一个 Python 进程。Era / Scene / Influence DTO 各 1 条，graph、evidence、来源上下文、support/contradiction 和 `attribution_conflict` flags 均随完整结果恢复，结果全量相等。
- smoke session ID：`b48a107bc82346ffba861018756e1d12`。保留该记录用于追溯。

持久化 smoke 的 turn 输入来自上一阶段明确标注的独立 engineering result，用于检查序列化与存储。PG saver 和读写均为真实服务；未将 engineering result 写入生产知识索引。最终用户在线生成和跨进程 restart acceptance 仍未执行。

### PostgreSQL 启动故障及恢复

启动报错为 `invalid checkpoint record` / `PANIC: could not locate a valid checkpoint record`。`pg_control` 指向 checkpoint LSN `0/4C0DD78`，对应 WAL 段 `000000010000000000000004`；现有卷只保留 `...005` 和 `...006`。控制文件 NextXID=1448，现存 WAL 最新可读 checkpoint 的 NextXID=1610，控制状态与 WAL 不一致。日志同时记录过数据目录 lock file invalid 和 immediate shutdown；更早的具体触发原因尚未证实。

恢复先保存完整物理快照，再仅在网络隔离的副本上重建 WAL、只读启动、导出，并初始化干净的恢复目录、逻辑还原。11 张 public 表的行数和排序后的行内容摘要全部一致；其中 checkpoints=93、checkpoint_blobs=99、checkpoint_writes=117。恢复副本和还原结果均通过 `pg_amcheck --heapallindexed --parent-check --rootdescend`。用户明确批准后才将恢复数据应用回原卷，随后启动原容器、验证原配置连接并运行迁移。临时隔离恢复容器已移除。

保留两份故障前数据：

- 外部物理备份：`ai-service/tmp/worldline-environment/postgres-recovery-20260926/moodflow_pgdata-before-repair.tar`，67,502,080 bytes，SHA256=`46d8fe8e32dd7d85fbb02c018773f486f071868ed56d09e792b9c1be3405cc79`。
- 原卷内旧目录：`moodflow_pgdata:/.pre-repair-20260926`。

私有备份及角色恢复材料保存在本地 ignored 路径，未进入报告或 Git。恢复保留了可读取的业务/checkpoint 数据；无法证明故障前未落盘事务没有丢失。WAL 重建后采取导出、重新初始化和还原流程遵循 [PostgreSQL 16 pg_resetwal 官方说明](https://www.postgresql.org/docs/16/app-pgresetwal.html)。数据库故障恢复中的进程启动/停止不作为 Worldline restart acceptance 证据。

## 2. Qdrant hybrid retrieval readiness

`qdrant_hybrid_retrieval_ready=true`。

| 项目 | 结果 |
| --- | --- |
| Qdrant | 本地 1.19.0，`http://127.0.0.1:6333`，复用 `ai-service_qdrant_storage` |
| 独立 collection | `worldline_knowledge_v4_ed11e2baaf47553d_v1_v2_v4` |
| IndexDocumentProjector | v4；indexing=v1，schema=v2 |
| Corpus | 56 documents / 677 parents / 1,270 children / 79 verified native claims |
| 全量 index build | projected=1,270，indexed=1,270，failed=0，point_count=1,270，pruned=0 |
| Dense | Qwen3-Embedding-0.6B，revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`，1024 dimensions，cosine；复用本机缓存及 CUDA |
| Lexical | Qdrant BM25，multilingual，named sparse vector `bm25`，IDF modifier |
| 真实 query | 正式 HybridRetrievalService：success，40 candidates，dense/lexical 无失败，warnings=[] |
| ContextPack | 6 个真实 items，metadata 和来源 hash/provenance 到达正式 adapter |

新增专用 `app/rag/indexing/config/worldline_v4.yaml`；保持 `prune_stale=false`，没有替换或清理旧 collection。生产数据入口为 `data/knowledge/v1/knowledge_enriched_chunks.jsonl` 及原有 enriched corpus，发布到 `data/knowledge/worldline/v4`。复用已存在的 knowledge QA attestation，通过原 KnowledgeReleaseBuilder 和索引 validator；没有将 Eval Gold、Semantic Relations review pack 或审核说明作为索引数据。

真实 payload 审计发现 32 个 Child/Parent 带原始首尾空白。旧读取逻辑先 strip 再验证原始 SHA，导致合法来源被误拒绝。本轮只修正原文读取以保留这些空白、SHA 和 offsets；ID 仍按原逻辑清理，篡改拒绝测试保留。最终全量 1,270/1,270 payload 可通过 retrieval contract 校验。[payload-validation.json](worldline_online_e2e_environment/payload-validation.json)

## 3. Approved ready index manifest

`ready_index_manifest=approved`，**批准范围为 retrieval build integrity**；source-context / semantic corpus readiness 没有获得批准。

- Knowledge dataset：`data/knowledge/worldline/v4/knowledge_enriched_chunks.jsonl`。
- Knowledge release：`knowledge-v2-ed11e2baaf47553d`，dataset_version=v4。release ID 前缀沿用既有 builder 的 `knowledge-v2` 命名，hash 和 v4 输出目录独立。
- Runtime manifest：`data/indexing/worldline/v4/index_build_manifest.json`，status=completed、ready_for_retrieval=true、limited_to=null、failed_count=0。
- Manifest SHA256：`sha256:c83a60216cbec622b8c80311b90dc1dfdf30eac9213f766b32dda8da6dec1ff2`。
- Approval sidecar：`data/indexing/worldline/v4/ready_index_approval.json`，status=approved、scope=`retrieval_build_integrity_only`。
- 校验包含：knowledge release 原审批及 artifact hashes、knowledge / release manifest / projection fingerprints、runtime embedding/BM25 参数匹配、全量 live Qdrant payload 与投影逐条相等、dense/sparse vectors 存在、dimension 和 exact point count。

原生 source/chunk/claim IDs 不变；child/parent content SHA、raw response body SHA、locator、source title、release ID、knowledge/index input hash 和 document metadata 随 payload 保存。`relation_source_contexts` 也被保存，当前真实 corpus 中该数组为空，没有生成补写值。

紧凑归档：[knowledge_release_manifest.json](worldline_online_e2e_environment/knowledge_release_manifest.json)、[index_build_manifest.json](worldline_online_e2e_environment/index_build_manifest.json)、[ready_index_approval.json](worldline_online_e2e_environment/ready_index_approval.json)、[release-artifact-inventory.json](worldline_online_e2e_environment/release-artifact-inventory.json)。约 296 MB knowledge corpus 和 218 MB projection 保留在本地 release 目录；Git 保存紧凑 manifest、SHA inventory 和验证记录，不纳入大体积生成数据。原用户 working corpus 未修改。

## 4. Corpus / source-context readiness

`corpus_source_context_ready=false`。下表区分实际来源字段与运行时可用的标签/回退值，不以回退值冒充完整的来源身份。

| 检查 | 结果 |
| --- | --- |
| Source URL | 56/56，来自现有 enrichment.source.source_url，包括原始 `original_url` 继承链 |
| 明确 publisher | 45/56；11 个 document 缺明确 publisher。source_name 标签均存在，部分是域名标签 |
| 原生 document title | 53/56；3 个 document 缺明确 title；payload 的 breadcrumb fallback 不计作来源 title |
| Raw response body SHA | 56/56，与现有 raw_path 文件字节 SHA 实际匹配 |
| Child / Parent content SHA | 全量保存并验证；1,270 个 live payload 与投影一致 |
| Article subject context | 0 个 article_sentence capture；native claim subject 不等同于已捕获的文章主体上下文 |
| Interview question / answer | 0 个结构化 capture |
| Speaker binding | 0 个结构化 capture |
| Native bound relation_source_contexts | 0；缺失保持缺失 |

逐 document 的缺口见 [readiness.json 的 corpus.documents](worldline_online_e2e_environment/readiness.json)。现有 79 条 claim 的 predicate 分布也记录在其中，不能从索引 build 成功推断三类关系场景的真实证据覆盖完整。

本轮补齐传输字段：IndexDocumentProjector v4 → Qdrant payload selector → RetrievalCandidate.metadata → ContextEvidence.document_metadata/source_provenance → WorldLineContextAdapter。真实 query 验证 document metadata 到达 ContextPack 和 Worldline document；独立工程测试验证具有 capture 时 article/interview packet 的完整传递，以及缺失、不合法 metadata、SHA 篡改的处理。生产 capture 当前为零，因此生产链上的真实 article/question/answer/speaker-binding packet 完整性仍受语料缺口限制。

缺口只登记，没有从 Gold、审核说明或 review pack 补写 article subject、说话人、问答或关系方向。

## 5. 服务 connectivity 与配置

| 服务 | 可连接 | 配置/启动状态 | 实际证据 |
| --- | --- | --- | --- |
| Java Music Service | true | 已启动；使用本机 JDK 21，默认 JDK 17 不满足项目要求 | 原 MusicServiceClient search_entities 返回周杰伦实体；Java search endpoint 200 |
| Qdrant | true | 已启动；1.19.0 + named dense/BM25 + payload indexes | 全量 payload/vector 读回，真实 hybrid query success |
| PostgreSQL | true | 修复后已启动；原库及 checkpoint migration 齐全 | 真实连接、create/save/load/recover 通过 |
| Model/provider | true | credentials 已存在，base URL 有效，两个模型均 configured | authenticated `/models` 200，配置模型在返回列表中 |
| FastAPI | true | `127.0.0.1:8001` 已启动，lifespan 成功 | `/health` 200，session-not-found 404，空 create query 422 |
| Frontend | true | `127.0.0.1:5173` 已启动；Vite `/api/ai` proxy 指向 8001 | 页面 200；代理真实 session endpoint 返回预期 404 |

Netease 上游本地 3000 也已启动，真实搜索返回数据。当前没有未启动的目标服务。Provider 校验限于认证、连接和模型配置，不进行用户请求生成验收。

Provider 原配置 `deepseek-v4-flash` 不在实际 `/models` 列表中；本轮将本地 `.env` 的 `LLM_MODEL` 和 `LLM_GENERATION_MODEL` 明确配置为实际可用的 `deepseek-flash`。Java 启动使用 `D:\code\JDKall\openjdk-21.0.1`，重新启动时也需要为该进程指定 JDK 21。

必需的 DATABASE_URL、PGPASSFILE、LLM credentials/base URL/model、Spring base URL 和 RAG manifest/mode 均完整。`QDRANT_API_KEY` 未设置；当前仅本地 loopback Qdrant 的实际匿名连接/query 已成功，它是可选项，不列为 blocker。Frontend 的 `VITE_AI_API_BASE_URL` 可不设置，当前相对 `/api/ai` 路径由 Vite proxy 正确转发。报告仅保存配置存在性和非敏感信息。

## 6. 当前 runtime config

```text
retrieval_mode=hybrid
ready_index_manifest=approved
ready_index_approval_scope=retrieval_build_integrity_only
index_manifest=data/indexing/worldline/v4/index_build_manifest.json
checkpoint_backend=postgres
rerank_mode=off
java_music_service=reachable
qdrant=reachable
postgres=reachable
model_provider=configured
model_provider_connectivity=reachable
llm_model=deepseek-flash
llm_generation_model=deepseek-flash
fastapi=reachable
frontend_api_proxy=reachable
corpus_source_context_ready=false
```

本地 `.env` 已应用以上非敏感配置；既有 database/provider credentials 保留。可复用的非敏感 overlay 保存于 `ai-service/.env.worldline-online.example`。现有 persistence/memory PostgreSQL 配置保持使用原数据库。

## 7. 验证命令、结果与影响范围

完整命令参数保存于 [validation.json](worldline_online_e2e_environment/validation.json)，Python 命令从 `ai-service` 执行，npm 命令从 `soul-music-room` 执行。

| 命令/验证 | 最终结果 |
| --- | --- |
| `python run_knowledge_release.py --finalize ...` | 独立 v4 release created，1,947 chunks，79 verified claims；原 QA hash 校验通过 |
| `python run_index.py --config app/rag/indexing/config/worldline_v4.yaml ... --local-files-only` | 全量 build completed，1,270 indexed，0 failures，ready_for_retrieval=true |
| `python -m scripts.migrate_agent_postgres` | business/checkpoint migrations current |
| `python -m scripts.worldline_environment_readiness ... --approve-index --checkpoint-trace ...` | 真实 PG、Qdrant、ContextPack、服务探测通过；readiness guard 因 corpus_source_context blocker 返回非零并明确输出 false |
| `python -m pytest`，相关 indexing/retrieval/context/session/API/runtime 工程测试 | 116 passed、2 opt-in skipped、0 failures/errors；[JUnit](worldline_online_e2e_environment/engineering-tests.xml) |
| 设置 `QDRANT_INTEGRATION_URL` 后 `pytest ... -k real_qdrant` | 上述两个 opt-in 场景补跑：2 passed、0 failures/errors；使用唯一临时 test collection，完成后清理；[JUnit](worldline_online_e2e_environment/qdrant-live-tests.xml) |
| `python -m ruff check` 本轮代码/测试 | PASS |
| `python -m compileall -q` 本轮相关目录/脚本/测试 | PASS |
| `npm run build` | PASS；保留现有 bundle >500 kB 提示 |
| `npm run test:worldline` | frontend flow/view contract PASS |
| 全量投影 retrieval contract 检查 | 1,270/1,270 PASS，0 invalid payload |
| PostgreSQL 恢复对比 / `pg_amcheck` | 11 public 表内容摘要一致，93 个既有 checkpoint 保留，0 errors |
| FastAPI/Frontend proxy HTTP smoke | 200 / 404 / 422 均符合预期；[fastapi-smoke.json](worldline_online_e2e_environment/fastapi-smoke.json) |

代码影响集中于 document metadata / raw-response SHA 传输、原始文本空白保留、专用 v4 index config 和环境 readiness 工具/测试。关系判断、评分、Gold 和 evaluator 未修改。没有 frontend UI 改动；本轮未做最终 UI 用户旅程或响应式视觉验收。

环境级验证已完成。因生产语料缺失 source-context captures 与部分来源身份字段，无法完成真实 article/interview/speaker packet 的生产数据 smoke，无法进入最终 Online E2E + PostgreSQL Restart。Provider 未执行生成测试；最终跨进程 restart acceptance、完整用户 UI 流程和 Final Worldline Regression 按本轮边界未执行。数据库恢复仍保留前述未落盘事务风险。

代码/config/tests commit：`6854cec`。报告和紧凑 release/readiness 证据另作独立 commit；没有 push。工作区原有未提交 corpus、审核报告和文档移动保持原样，未纳入本轮提交。

## 8. 剩余 blocker 与阶段结论

唯一剩余 blocker 类别为 `corpus_source_context`：

1. 从可审计的真实来源补齐 article context、完整 interview question/answer 与 speaker binding，并通过原生 source/chunk/claim 绑定流程发布；当前缺失只登记。
2. 对 11 个缺明确 publisher、3 个缺明确 title 的 document 补齐真实来源元数据。不得以域名、breadcrumb、Gold 或审核文字替代原来源证明。
3. 后续 source-context 完整 release 需要重建独立索引并重新验证完整 packet 的真实传递。当前 technical index approval 不代表该数据缺口已解决。

```text
postgres_checkpoint_ready=true
qdrant_hybrid_retrieval_ready=true
ready_index_manifest=approved
corpus_source_context_ready=false
environment_ready=false
can_run_online_e2e_postgres_restart=false
final_online_e2e_performed=false
postgres_restart_acceptance_performed=false
```

本轮停在环境准备边界，等待真实来源数据满足 readiness 后再进入最终 Online E2E + PostgreSQL Restart。

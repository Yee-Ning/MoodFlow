# Production Compose Runtime Smoke 诊断报告

日期：2026-09-28。环境为现有隔离 Compose project `moodflow-rehearsal-20260928`；六个长期服务均 healthy，migrate Exited (0)。本轮**没有可在授权范围内实施的部署集成修复**：失败发生在既有实体搜索/Worldline 分支业务路径，而冻结 Pixies session API 在同一 Compose 环境成功。未修改 Worldline V1、relation validator、Gold、corpus 或 Eval；未部署服务器。

## 两条同源请求的精确 trace

| Stage | 原请求：`探索 Nirvana 的音乐世界线。`，`POST /api/ai/chat` | 冻结查询：`乐队《Pixies》的年代、scene 和影响关系`，`POST /api/ai/chat` |
|---|---|---|
| Frontend → Nginx → FastAPI/SSE | HTTP 200，`text/event-stream`，首事件约 0.094 s，`final.status=failed` | HTTP 200，同源 SSE，首事件约 0.047 s，`final.status=failed` |
| Router / Worldline Subgraph | `subgraph=worldline`；`prepare_query_context` 已走通（由进入下一节点推断） | `subgraph=worldline`；诊断实测 query context 成功 |
| Java entity resolution | **第一个失败 stage**：`resolve_entity`；解析目标为 `Nirvana`，未指明实体类型。Java 无类型搜索按 song → artist → album 顺序，首次 Netease `/cloudsearch?type=1` 的歌曲条目缺少必需 `name`。Java 记录 `网易云 API 响应缺少有效字段: name`，同源 `/api/music/entities/search?keyword=Nirvana&limit=5` 返回 HTTP 502；既有 Python `MusicServiceClient` 将 HTTP 502 映射为 `MusicServiceError(code=http_502)`，Worldline 捕获后输出 `entity_search_failed`。 | 成功解析 `netease:artist:98542`；Java 经 Netease artist search/detail 正常。 |
| Qdrant Hybrid / ContextPack | **未进入** | 同一查询/同一非 session 代码路径的独立真实客户端诊断：`QdrantHybridSearchBackend` success、3 candidates、无失败通道；ContextPack 3 items。 |
| Era / Scene / Influence / graph | **未进入** | 同一诊断中三种扩展及 graph build 均执行成功；无替换 validator。 |
| narrative / branch provider | **未进入** | 聊天 SSE 的首个失败 stage 为 `branch_generation_failed`。同一代码路径的独立诊断进一步定位：narrative 的真实 `deepseek-flash` response completed、结构校验通过；branches 的真实 provider 两次 completed，但两次候选都触发 `SemanticValidationError(code=branch_type_target_mismatch)`；`StructuredOutputError(failure_type=retry_exhausted,last_failure_type=semantic_validation_error)` → `WorldLineGenerationError(worldline_branches_invalid)`。不把独立诊断冒充聊天原请求的逐字 provider 响应。 |
| PostgreSQL commit / final | MainGraph 对该失败聊天写入 7 条 checkpoint；没有 Worldline session commit，返回 failed。 | MainGraph 对失败聊天写入 14 条 checkpoint；没有 Worldline session commit，返回 failed。 |

原请求的唯一新 thread 为 `rehearsal-942b4be9296840a08f1a7f4a9f0b7694`；Pixies 聊天 thread 为 `rehearsal-5f01dde8d8c14c7fbdc7f7035ff86806`。原请求最后成功点是 query context，首个失败点是 Java entity resolution；后续 Qdrant、ContextPack、关系、generation 与 Worldline session commit 均未发生。没有把 SSE HTTP 200 当作业务成功。

辅助对照：同源 Java 搜索 `Nirvana` **不带** `type` 返回 HTTP 502；**指定** `type=artist` 返回 HTTP 200、5 个候选。这说明该失败由无类型查询先遍历异常的 song 结果触发，不是 Java/Netease URL 不可达。Pixies 诊断使用原应用构造器、真实 Java/Qdrant/LLM 客户端与现有校验器；仅在独立一次性 Python 进程中观察日志及异常链并原样重新抛出，没有 fixture、mock 或 memory saver。诊断输出中的 Qdrant 3 candidates、ContextPack 3 items、narrative provider completed 和分支校验异常构成逐阶段证据。

## 冻结验收入口的 control

Worldline V1 Freeze 的成功路径是 **`POST /api/ai/worldline/sessions`**，并非 `/api/ai/chat` 的无 session 分支。对完全相同的 Pixies 查询，在当前 Production Compose 的正式 Frontend 同源入口请求该 session API：HTTP 200，session `32f0f7c3b9e7483a9a74999fa100130d`，revision 1，graph 11 nodes / 10 edges，Scene 1、Influence 1、branches 3；PostgreSQL 中 `worldline:<session_id>` 有 3 条 checkpoint。真实响应及代码路径均使用真实 Java、Qdrant hybrid、provider 和 PostgreSQL。冻结验收记录的同一 Pixies session 路径也为 graph 11/10、Scene 1、Influence 1，provider model `deepseek-flash`。因此 **Pixies 的正式冻结入口在 Compose 中成功**，不能据聊天入口的分支失败推断 Compose 全面回归。聊天入口使用 `BranchDraftOutput`，严格要求恰好 3 个语义匹配的分支；session 入口使用 `ExplorationBranchDraftOutput`，允许 0–3 个。两种入口的既有业务约束不同，本轮未改写。

## 冻结配置与容器逐项对照

| 合约项 | Freeze / approved | Rehearsal 实测 | 判断 |
|---|---|---|---|
| Manifest path / SHA | `data/indexing/worldline/final-native-20260927/v4/index_build_manifest.json` / `445b7263221ae5864015752a6689e5d8303c21409407375a6e0b33ac831f50f3` | 同一路径在 `/app` 下、同一 SHA | 一致 |
| Release / collection | `knowledge-v2-daa965c10984eefa` / `worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4` | 同值；Qdrant green、41 points，冻结 projection 已逐 point/payload 校验 | 一致 |
| Index binding 版本 | schema v2 / projection v4 / registry v5 | 同值 | 一致 |
| Retrieval / checkpoint | hybrid / PostgreSQL | `AI_RAG_RETRIEVAL_MODE=hybrid`、`CHECKPOINT_BACKEND=postgres`；实际 retrieval 和 checkpoint 写入成功 | 一致 |
| Java / Netease | 真实 Java Music Service → Netease upstream | `http://java:8080` → `http://netease:3000`；Pixies artist search/detail、session 路径成功 | 容器 DNS/路径差异是预期传输差异 |
| LLM | DeepSeek / `deepseek-flash` | `https://api.deepseek.com`，`LLM_MODEL` 与 `LLM_GENERATION_MODEL` 均为 `deepseek-flash`；真实 generation completed | 一致 |
| HF / intent | pinned Qwen revision 与冻结 intent model | `/models` 只读离线缓存，模型 SHA 验证通过；taxonomy SHA `a60027b108e744d2baa457b6221572b9720d6f1ba377ef0350dbe014002bc63a`，模型文件可读 | 一致；CPU/local_files_only 为生产预期 |
| 工作目录 / mount / 权限 | approved release + index manifest | `/app`；manifest、release、intent model 可读；`deploy.runtime verify` 通过 | 一致 |
| 冻结代码 | baseline `7afc619` | 从该 baseline 到当前 HEAD，Worldline、retrieval、Java 业务源码无 diff | 一致 |

未发现可解释该失败的 manifest、release、索引、registry、模型版本、URL、挂载、权限或启动顺序偏差。Nirvana 无类型查询碰到上游数据形状异常；Pixies 聊天入口的严格分支语义校验因 provider 输出不匹配而拒绝。要让**原始聊天请求**成功，需决定并修改 Java malformed-hit 处理及/或 Worldline 查询类型判定、分支生成/校验策略；这些均属于本轮禁止自动修改的应用业务行为。**`worldline_runtime_regression=true` 仅标记所要求的聊天入口仍无法完成，并不否定冻结 Pixies session API 在 Compose 中通过。**

## 范围、验证与停止条件

本轮运行了两条正式同源 `/api/ai/chat` 请求、Pixies 正式同源 session API control、Nirvana Java 无类型/指定 artist 对照、真实客户端阶段诊断、PostgreSQL checkpoint 查询、冻结配置/文件哈希/权限检查。没有提交代码改动或更改 V1；没有执行 Semantic Relations Eval、Gold/corpus 扩充、server deployment。由于根因不在允许的 deployment/runtime integration 修复范围内，按停止条件**未执行“修复后 smoke”与“修复后 application restart / continuation”**。上一轮基础设施 `production_restart_pass=true` 仍有效；下方标志 `false` 专指本轮要求的**修复后**重启未验证，而非证实重启产生新回归。

```text
worldline_runtime_regression=true
production_worldline_smoke_pass=false
runtime_config_parity_pass=true
postgres_checkpoint_runtime_pass=true
production_restart_pass=false
runtime_smoke_pass=false
can_start_server_deployment=false
```

# Worldline Online E2E + PostgreSQL Restart Acceptance

本轮执行了真实前端用户旅程、真实 provider generation、真实 Qdrant hybrid retrieval，以及完全停止 Python 服务后的 PostgreSQL session recovery。**完整验收未通过，不能进入 Worldline V1 Freeze。** 重启恢复机制通过；恢复后 continuation 返回 `source_conflict`，没有生成 revision 3。另外，正式 release/index 没有可供 Scene / Influence 阶段消费的 native predicate，无法完成这两类非空关系的在线正例及持久化验收。

执行日期：2026-09-26，Asia/Shanghai。下文 trace 时间使用 UTC；23:03 / 23:06 / 23:11 本地时间分别对应 15:03 / 15:06 / 15:11 UTC。没有运行 Semantic Relations Eval、修改 evaluator/scoring/validator、新增 Gold、修 deferred eval debt、部署服务器或重构架构。

## 1. 验收边界与正式配置

| 项目 | 实际配置 / 证据 |
| --- | --- |
| Frontend | `http://127.0.0.1:5173/worldline`，现有页面与 Vite API proxy |
| FastAPI | `http://127.0.0.1:8001`，原 `app.main:app` / 原 lifespan |
| Java Music Service | 原 8080 服务；真实搜索及 entity facts 调用 |
| retrieval_mode | `hybrid` |
| retrieval backend / encoder | `QdrantHybridSearchBackend` / `QwenQueryEncoder` |
| approved manifest | `data/indexing/worldline/source-context-20260926/v4/index_build_manifest.json`，路径相对于 `ai-service/` |
| manifest SHA | `sha256:a74164c52346dd4050468d2e491dfe5993abec16cb70122d9df7d693769d2f17` |
| release | `knowledge-v2-aa5271a36860debb` |
| Qdrant collection | `worldline_knowledge_v4_aa5271a36860debb_v1_v2_v4`，正式 1,270 Child points |
| checkpoint_backend | `postgres`；实际 commit saver 为 `AsyncCompatiblePostgresSaver` |
| PostgreSQL | 复用 `moodflow-postgres` / `moodflow_pgdata` / `moodflow_agent`，本轮未停止 PostgreSQL |
| generation model | 请求模型与 provider 返回模型均为 `deepseek-flash` |

实际三次检索返回的 manifest 均与指定文件逐项一致，文件 SHA 与 `ready_index_approval.json` 相符。approval 仍是 `retrieval_build_integrity_only`，不代表语义正例覆盖或 Eval 通过。旧 release/collection/manifest 未被覆盖。

在线请求没有使用 engineering fixture、mock relation DTO、memory saver 或绕过 generation/validator。取证 runner 通过 `sys.setprofile` / 日志观察真实函数完成结果，运行原 ASGI app，不替换服务、返回值或 checkpointer。单元测试中的独立测试对象仅验证取证工具，不参与在线请求。主旅程重启前后及 provider 失败 trace 的 `observer_error_count=0`；最早的实体歧义 trace 使用初版 observer，没有该字段。

配置、HTTP headers、密码、DSN、API Key 不进入报告。checkpoint probe 使用现有环境连接并只执行 SELECT / `get_tuple`，没有调用 setup、迁移、创建测试 session 或写入 checkpoint。

## 2. 完整真实 E2E trace

主验收 session：`42c804ea5a774780a46539f6aaac0f6f`。

创建请求：

> 歌手《周杰伦》的早期出道与专辑发行时间线、音乐场景和创作影响。请用一段简短文字说明图谱，资料缺失如实说明。

| 步骤 | 真实操作 | HTTP / 结果 | 状态 |
| --- | --- | --- | --- |
| A | 页面提交 `POST /api/ai/worldline/sessions`，经 Vite proxy 进入 FastAPI | 200，Java root `netease:artist:6452`；provider narrative + branches 经现有校验成功 | revision 1，11 nodes / 10 edges，10 sources |
| B | 点击服务器返回的《屋顶》分支，`POST /api/ai/worldline/explore` | 200，branch `wlb:1:11d8ee79427c46399c5ada76dc2638b4`；加载 song `5257138` | revision 2，14 nodes / 13 edges，17 sources |
| C / D | 读取该真实 session 的 PostgreSQL checkpoint | 6 checkpoint rows / 6 blobs / 6 writes；保存完整状态摘要 | revision 2，原图谱 / evidence_history / last_result |
| E | 完全停止验收 FastAPI Python PID `16312` | 原 PID 不存在，8001 无监听 | 15:08:42.9998472 UTC |
| F | Python 停止期间直接读取原 PostgreSQL | 存储和状态 SHA 与 C 相同；PG start time 相同 | 没有重建或清空 PostgreSQL |
| G | 重新启动原 FastAPI app，Python PID `34620` | app lifespan 完成，8001 恢复监听 | 新进程 15:08:48.925780 UTC 开始追踪 |
| H | 原页面 reload，通过 sessionStorage 的同一 session ID 自动 GET recover | 200，恢复响应与重启前响应完整一致 | revision 2，14 nodes / 13 edges，17 sources |
| I | 点击真实吴宗宪分支，`POST /api/ai/worldline/explore` | **502，`source_conflict`**；branch `wlb:2:578c69e9dce74eada80f42ad6a508491` | 保留 revision 2；没有 revision 3，没有失败写入 |

成功 A/B 的服务端链路实际包含 prepare query → entity resolution → Query Understanding → Java facts → Qdrant hybrid → ContextPack / WorldLine adapter → Era / Scene / Influence expansion → validator / conflict aggregation → graph / Relation DTO → narrative generation / grounding → branches generation / validation → PostgreSQL `_commit` → API response → 页面显示。I 执行到 Java、hybrid、ContextPack、三个 relation 阶段及 build/merge；在 source identity 合并处失败，未执行 provider generation 或 commit。

验收前还有两次真实失败，保留证据，不计入成功：第一次 query 将原歌手和装饰性同名目录条目误判为 ambiguous，HTTP 502；修复通用 literal name 优先解析后，较长 query 的首次 provider 输出 `max_output_tokens` incomplete，重试虽 SDK completed，应用最终仍返回 `narrative_generation_failed`。随后使用上面的自然简短请求成功；没有提高 validator 宽容度、修改 prompt 模板或制作通过专用 DTO。

证据：[browser journey](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/browser-journey.json)、[重启前完整应用 trace](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/before-restart-trace.jsonl)、[重启后 trace](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/after-restart-trace.jsonl)、[初始歧义](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/initial-entity-ambiguity.json)、[初始 generation 失败](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/initial-provider-grounding-failure.json)。trace 包含 UTC timestamp、PID、实际输入 plan / pack、实际结果、真实 provider 响应及实际 committed state。

## 3. Era / Scene / Influence 与 conflict semantics

| 项目 | 创建 | 重启前 explore | recover 后 | 验收判断 |
| --- | --- | --- | --- | --- |
| Era relations | 2 | 2 | 相同的 2 | 真实正例通过 |
| Scene relations | 0 | 0 | 0 | 仅空数组契约；非空正例未验证 |
| Influence relations | 0 | 0 | 0 | direction / stance 非空正例未验证 |
| graph | 11 nodes / 10 edges | 14 nodes / 13 edges | 完整相同 | 恢复通过 |
| evidence sources | 10 | 17 | 完整相同 | 恢复通过 |
| citations | 7 | 6 | 完整相同 | 恢复通过 |

Era 真实图谱方向为 `netease:artist:6452 → era:netease_artist_6452_early_career` (`career_phase`) 及 `netease:artist:6452 → era:2000s` (`belongs_to_era`)；DTO 的 year=2000、precision=exact、confidence=0.98 / 0.96 与原 graph/source 同时保留。3 条 evidence 分别绑定 native claims：

| claim ID | evidence ID |
| --- | --- |
| `claim:217c9e0609104466d789e270` | `knowledge:era:2aae8efd0afc31eb2e84a72d` |
| `claim:9b6a36ea033c15bdbf0a8782` | `knowledge:era:05d36d32e12db7ef82f29652` |
| `claim:868e9742f0cc9053c1bfd602` | `knowledge:era:2c1d65985d2f04c096688449` |

trace 中 Era validator 的 accepted=2、evidence=3，记录了 `validation_normalization_then_conflict_aggregation`、各 claim 的 accepted/support 和 `conflict_flags=[]`；DTO `temporal_conflict=false`。本旅程未自然产生非空冲突证据，没有人为制造 conflict flag。空 flags 的保存/恢复得到验证；非空冲突分支及 Influence stance 不能标记在线通过。`source_conflict` 是 merge identity 错误，不是关系冲突语义正例。

从正式 knowledge release 审计出 **79 个 unique SUPPORTED claims**；Child projection 可索引 **78 个 unique claims**，其中 1 个 `has_genre` claim (`claim:e395f612ac8d9b15697a809b`) 位于 Parent chunk，并非新的丢失。release 全部 claim predicates 为 debuted / released / collaborated_with / includes_track / born_on / wrote_lyrics_for / has_genre / performed_at / won_award / chart_position / produced / composed_for / arranged / contains_music_element，**Scene 支持的五类 predicates 与 Influence 支持的六类 predicates 数量均为 0**。A 的 Scene / Influence 阶段确实 requested=true、candidate=0；不是阶段未运行。不能把 genre、collaboration、普通原文 mentions 或 LLM narrative 自动改成 Scene / Influence relation。

详见 [实际 claim inventory / Era DTO / source conflict](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/acceptance-evidence-summary.json)。本轮未为补正例修改 corpus、claim extractor、索引或 Gold。

## 4. PostgreSQL 跨进程恢复对比

thread ID：`worldline:42c804ea5a774780a46539f6aaac0f6f`。`_commit` 的真实 saver 为 `AsyncCompatiblePostgresSaver`，前后服务使用 postgres；没有 fallback 到 memory。

| 检查点 | revision | checkpoint / blob / write rows | 存储 / 状态变化 |
| --- | --- | --- | --- |
| 停止前 15:08:37.502493 | 2 | 6 / 6 / 6 | 基准 |
| Python 停止期间 15:08:46.312048 | 2 | 6 / 6 / 6 | 与基准完全相同 |
| 新 Python recover 后 15:09:36.875293 | 2 | 6 / 6 / 6 | 与基准完全相同 |
| continuation 失败后 15:16:27.610530 | 2 | 6 / 6 / 6 | 与基准完全相同，没有失败提交 |

四次原始快照共同的完整 session state SHA：`4f8ac7d3010b7cad0e7feb51a2cfd1575dd3d4b693e69357abf2c93b27ca1432`。

四次 checkpoint rows / blob byte SHA / write byte SHA 合并后的 storage SHA：`ff17193fdb70eebabe0778ff8786c62e43910f148f3624d3e74cd2ae184af7ad`。这是该 session 的数据库存储摘要，不是整个数据库文件 SHA。

PostgreSQL `pg_postmaster_start_time()` 始终为 `2026-09-26T13:19:39.325252+00:00`。存储摘要及启动时间证明 PostgreSQL 在 Python stop/restart 期间保持原实例及 session 数据；验收动作没有删除 volume、替换目录、修改表或启动另一套 PostgreSQL。

直接从 PostgreSQL 反序列化的 `current_graph` 与 API graph、`evidence_history` 与 API sources 相同；恢复前后整个 response 相同，包括 root/current node、2 visited nodes、depth=1、revision=2、available branches/actions、last_result、Era evidence/exact_span/provenance、relation direction 和空 conflict flags。消耗过的《屋顶》branch ID 保留；`created_at` / `updated_at`、完整 last_result / session state 保留。本服务没有独立名为 history 的字段；实际历史对应 `visited_nodes`、`consumed_branch_ids`、`evidence_history` 和 `last_result`。

`session_recovery_pass=true` / `postgres_restart_mechanics_pass=true`。但用户要求 I 成功完成一轮 continuation 且非空 Era / Scene / Influence relations 均保留，本轮没有达到；因此 **`postgres_restart_acceptance=false`**，不能仅凭 recover 成功将完整 acceptance 标记通过。

证据：[进程完全停止](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/process-stop-evidence.json)、[停止前 checkpoint](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/checkpoint-before-restart.json)、[停止期间 checkpoint](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/checkpoint-while-api-stopped.json)、[恢复后 checkpoint](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/checkpoint-after-recover.json)、[失败 continuation 后 checkpoint](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/checkpoint-after-failed-continuation.json)。

## 5. Qdrant / ContextPack / Source Context trace

| 请求 | plan ID | candidates | channels | failed_channels | retrieval 总耗时 |
| --- | --- | --- | --- | --- | --- |
| A 周杰伦创建 | `query-plan:2d9295a5744ecbb2bc73002a` | 40 | dense + lexical | `[]` | 16,577.48 ms |
| B 屋顶 explore | `query-plan:0494a24420a3b747737bae86` | 40 | dense + lexical | `[]` | 4,438.55 ms |
| I 吴宗宪 continuation | `query-plan:d86c2b37d845b8f7f20ba307` | 21 | dense + lexical | `[]` | 18,728.84 ms |

三次真实 Query Understanding plan 使用指定 collection/release 的 index profile，hybrid status=success。trace 记录了实际 dense / lexical ranks、raw scores、subquery IDs 与检索耗时；不是 `/collections` connectivity 代替 query。首轮 / 重启后 embedding 冷启动耗时较大，不能据此报告性能验收通过。

每次真实 WorldLine adapter 产生 6 个 ContextPack documents。B 的 `jc-v1-037` / `doc-jc-v1-037-5b9c65da243bc2089467` / `parent-17747bf81bf90411ed3b8a4f` / `child-886936e400404269f9e2c52a` 携带两个 article context packets，绑定 `claim:550e9bc3e3ee4ae4426ccdd5` 和 `claim:ae32871569e10a7fcffd3cf8`。来源为真实 Wikipedia《東風破》，整段原文保留，anchor [0:40]、article_sentence segment [0:120]、原 DOM locator `p[1]` / decoded text [751:871]。

两个 packets 从 index projection → 真实 retrieval candidate → ContextPack item → WorldLine adapter document 逐项相同；source/chunk/parent/claim ID、URL/publisher/title/locator、response SHA、Child/content SHA、knowledge/index input SHA、release ID 与 source provenance 一致。只读离线复核实际记录，并调用现有 `source_context` binding 与 `verify_raw_origin`，验证真实 raw response SHA 和绝对 DOM text offsets；没有另外生成 acceptance DTO。

实际返回的其余 items 在 candidate 没有 native bound context 时，ContextPack 和 adapter 也没有 context，没有自动补写。原 document metadata 中有 captures，并不等同于当前 Child/claim 有 bound packet。A 6 items 都无 packet；B 只有上述 1 item 有两个 packets，其余 5 missing；I 6 items 都无 packet。article metadata 完整进入 runtime 已验证；**本轮请求未选到完整 interview Q/A packet，不能声称本次真实旅程覆盖了 interview 正例**。上一准备阶段的独立 Q/A smoke 不能替代本轮关系正例。

证据 summary 保留三组完整 ContextPack identity/provenance、两个原生 packets 和相应 assertion 结果；完整实际 pack / adapter documents 位于重启前后 JSONL trace。

## 6. 真实 provider generation

记录到 9 次真实 Responses generation（initial failure 2 次、主旅程 A/B 共 7 次），全部请求 / 返回 model 为 `deepseek-flash`。这不是整个进程所有可能 provider 请求的总计；是观察到的 `_create_response` generation 次数。每次 response ID、UTC timestamp、SDK status 和结果保存于 trace/summary。

| 请求 / schema | attempt | latency ms | SDK status | 应用结果 |
| --- | --- | --- | --- | --- |
| 初始长 query / narrative | 1 | 9,164.87 | incomplete，max_output_tokens | 未通过 |
| 初始长 query / narrative | 2 | 4,624.64 | completed | 请求最终 narrative_generation_failed |
| A / narrative | 1 | 7,793.24 | completed | schema_validation_error，现有 retry |
| A / narrative | 2 | 5,219.22 | completed | structured output success |
| A / branches | 1 | 2,221.23 | completed | semantic_validation_error，现有 retry |
| A / branches | 2 | 2,141.04 | completed | structured output success |
| B / narrative | 1 | 6,033.54 | completed | semantic_validation_error，现有 retry |
| B / narrative | 2 | 6,237.38 | completed | structured output success |
| B / branches | 1 | 2,988.54 | completed | first_pass_success=true |

SDK completed 不等于应用校验通过。A/B 的成功以实际 structured output final_status=success、HTTP 200、PG commit 及前端显示为准。没有用 `/models` 代替生成。I 的 source merge 失败发生在 generation 之前，新进程没有记录 continuation generation；不能声明 restart 后 generation 正例通过。已有 provider retry / token cap 风险保留，不在本轮修改 validator 或 generation 策略。

## 7. API / Frontend 真实 smoke

页面实际执行 create → explore → reload/recover → continuation。三个成功 HTTP response 均由原 `WorldLineSessionResponse.model_validate` 再次验证 schema / graph/session 边界。原 UI 消费 `era_relations` / 空 `scene_relations` / 空 `influence_relations`、graph edges、sources、citations 与 revision；点击 Era edge 时 evidence panel 展示来源 URL / publisher、exact span、year、confidence、SHA/release/native parent ID。方向与服务端 edge 一致。

recover 后 UI 显示 root=周杰伦、depth=1、visited=2、revision=2、14 nodes / 13 edges，以及 checkpoint synced；I 失败后显示“世界线扩展无法合并”“当前图谱已保留，没有写入失败的探索结果”，保留前一图谱和 revision。没有改 UI、手工注入页面 state 或 fake fetch response。

`frontend_happy_path_contract_pass=true`，包括实际 Era 正例、空数组、会话恢复和错误保留状态。完整 `frontend_contract_pass=false`：真实 Scene / Influence 非空 relation、Influence direction / stance 与非空 conflict metadata 仍未覆盖。现有前端单元 flow/view tests 通过，不能替代这些在线正例。

截图：[创建页面](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/create-ui.png)、[Era evidence](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/era-evidence-ui.png)、[recover 页面](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/after-recover-ui.png)、[continuation 失败保留状态](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/continuation-ui.png)。这是当前本地 IAB viewport 的旅程 smoke，未进行新的 UI 构建或全面响应式视觉验收。

## 8. 剩余 blockers / regression

**B1：恢复后 continuation source identity 冲突。** 实际失败 evidence `knowledge:chunk:178d28bd38cac5d826711d47` 来自 `jc-v1-030`、Child `child-5f529ba523df4dc63765c6e2`、point `175bbbde-af7e-5638-a7a0-d1ccee5f7e99`。应用 `_source_identity` 已排除 fusion/rank/subquery 等 query observations，却没有排除 `matched_child_ids`。同一稳定 evidence 的 prior value 是：

```text
[child-5f529ba523df4dc63765c6e2,
 child-b606cac246881f7ca972e157,
 child-a16687b2432310c69c80af50]
```

此次 continuation 的 value 是前两项。对实际重启前 source 与此次增量 source 做 `_source_identity` 比较，除了该字段没有其他受保护 top-level / metadata 差异；不可变 source / chunk / excerpt / URL / hash / locator / provenance 一致。第三个 matched Child 是 query hit 集合变化，不是原文被改写。这是现有 merge runtime blocker；本轮没有修改 merge、弱化 source hash guard 或跳过错误来通过验收。失败前后 PG state 完整相同。

**B2：正式 native Scene / Influence predicate 正例为空。** 环境准备 true 证明数据链和连接具备运行条件，不保证正式 corpus 已包含所有 relation predicates。没有可验证 native claims 时，现有 expansion 正确返回空数组；必须在后续受限工作中从真实来源建立可审计 native binding 并重新走独立 release/index approval，不能使用 Gold/review pack、把普通 mentions 改名或 hardcode 案例。本轮不做这一数据/语义改动。

**保留风险：** provider 初始 token 截断与 grounding/schema/semantic retry 已发生；本轮只验证至少一次真实成功生成，不代表稳定性/SLA通过。非空 conflict flags、Influence stance、interview packet 在线命中及恢复后成功 generation 均没有正例证据。暂未触及的 merge capacity 边界未被计作已测试失败或通过。

## 9. 修改、验证与交付范围

| 文件 / 提交 | 影响 |
| --- | --- |
| `ai-service/app/agent/foundation/subgraphs/worldline/entity_resolver.py` | 先 NFKC / trim / casefold 保留标点做 faithful exact match，再使用既有宽松匹配；修复装饰性目录名字导致的假歧义，没有按具体歌手写特例 |
| `ai-service/tests/test_worldline_entity_resolution.py` | 4 个新增用例覆盖 literal、真实同名 ambiguity、fallback ambiguity/unresolved、alternate title；与代码提交 `41c927d` |
| `ai-service/scripts/worldline_online_acceptance_server.py` | 被动观测原 app / real generation / real retrieval / PG commit，不改变结果；已停止取证 runner |
| `ai-service/scripts/worldline_online_checkpoint_evidence.py` | 原 PostgreSQL 的 session read-only checkpoint / blob / write / snapshot 证据读取 |
| `ai-service/tests/test_worldline_online_acceptance_observer.py` | 7 个取证测试：字段保护、真实 return、async completion、非 target、日志失败隔离、dataclass relation arrays、canonical state hash；工具与测试提交 `41dd446` |
| 本报告与 `docs/reports/worldline_online_e2e_postgres_restart/` | 真正 HTTP / UI / process / provider / Qdrant / PostgreSQL 证据及不通过结论 |

从 `ai-service/` 运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_worldline_entity_resolution.py tests/test_worldline_api.py tests/test_worldline_session.py tests/test_worldline_generation.py tests/test_worldline_runtime_integration.py tests/test_worldline_retrieval_adapter.py tests/test_worldline_merge.py tests/test_worldline_grounding.py tests/test_worldline_source_context_preparation.py tests/test_worldline_online_acceptance_observer.py -q --junitxml=../docs/reports/worldline_online_e2e_postgres_restart/engineering-regression-tests.xml
.\.venv\Scripts\python.exe -m pytest tests/test_worldline_online_acceptance_observer.py -q --junitxml=../docs/reports/worldline_online_e2e_postgres_restart/evidence-observer-tests.xml
.\.venv\Scripts\python.exe -m ruff check app/agent/foundation/subgraphs/worldline/entity_resolver.py tests/test_worldline_entity_resolution.py scripts/worldline_online_acceptance_server.py scripts/worldline_online_checkpoint_evidence.py tests/test_worldline_online_acceptance_observer.py
.\.venv\Scripts\python.exe -m compileall -q app/agent/foundation/subgraphs/worldline/entity_resolver.py tests/test_worldline_entity_resolution.py scripts/worldline_online_acceptance_server.py scripts/worldline_online_checkpoint_evidence.py tests/test_worldline_online_acceptance_observer.py
```

相关工程回归 **93 passed / 0 failed / 0 skipped**；取证测试后续增加 dataclass/hash 用例后的最终单独运行 **7 passed**；ruff / compileall 通过。两个测试命令有重叠，不把它们相加声明独立 100 项。最初工具缺少 import 的 collection error 已修复并由最终运行覆盖，未跳过或弱化测试。

从 `soul-music-room/` 运行 `npm run test:worldline`：existing frontend flow / view 均 passed。测试包含既有单元 fixtures，仅作工程回归，不算真实 Online E2E 场景。本轮没有前端代码修改，不重新执行项目全量前端 build/lint；没有宣称全产品最终 regression 通过。

只读离线证据复核断言通过：完整 pre/recover response 相等、四次原始 PG state/storage/start time 相等、真实 manifests 与 approval SHA 匹配、真实 dual-channel query、native packet/provenance/raw offsets 一致、缺 context 保持 missing，以及 `source_conflict` 受保护差异定位。结果保存为 [acceptance evidence summary](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/acceptance-evidence-summary.json)。这组断言证明记录内容，不把在线失败改为成功。

七份原始 HTTP/PG 完整响应本机保留于 `ai-service/tmp/worldline-online-acceptance/original-http-and-pg-evidence/`。Git 证据副本仅将无关、重复的 `parser_metadata` 节点树替换成该值的 canonical SHA 指纹；其他运行时/source/claim/context/offset/hash 内容保留。原文件 SHA、大小、绝对路径和副本 SHA 位于 [original evidence inventory](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/original-evidence-inventory.json)。PG state/storage SHA 及完整 response 相等结论来自变换前原件；不能将副本重新计算的整个 state SHA 当成原件 SHA。JSONL trace 本身在捕获时就省略 parser tree/credentials。

最后已关闭临时 observer，恢复普通 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8001`（PID `29912`），health 正常，停止后未新增验收 query。见 [runtime restoration](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline_online_e2e_postgres_restart/runtime-restoration.json)。用户原有未提交 Eval 文件、报告移动/删除和本地 corpus 未覆盖、暂存或提交；没有 push。

## 10. 最终结论

```text
online_e2e_pass=false
postgres_restart_acceptance=false
session_recovery_pass=true
postgres_restart_mechanics_pass=true
retrieval_runtime_pass=true
provider_generation_pass=true
frontend_happy_path_contract_pass=true
frontend_contract_pass=false
worldline_v1_freeze_ready=false
```

完成本轮真实执行与证据报告；停在上述两个明确 blockers。当前不能进入 Worldline V1 Freeze / 文档归档阶段，不部署、不继续 Eval，也不将空 Scene / Influence 数组或失败 continuation 视为验收通过。

# Worldline V1 Freeze decision — blocked

2026-09-27。本轮只修 Runtime Entity Binding，并执行一次新 session 的真实前端、在线服务与跨进程恢复验收。V1 **未冻结**：Entity Binding 和 Influence 已通过，但正式 Scene candidate 被现有 validator 拒绝，不能把完整 Online E2E 标记通过。

## 修改与边界

修复 commit：`2aaa1ee`，`Bind verified native relation endpoints to runtime knowledge nodes`。
清理边界 commit：`f23c208`，保留已由现有 typed projector 验证并引用的 endpoint，避免产生悬空 Era 等关系。

- `ai-service/app/agent/foundation/subgraphs/worldline/runtime_entity_binding.py`：从当前 approved manifest 载入并校验 immutable projection、release gate 和 fingerprint；retrieved claim 必须与 release-gated SUPPORTED projection record 精确一致。核对 source/document/chunk/claim、URL、publisher、title、locator、raw response/content SHA、release/index provenance，以及存在时的 RelationSourceContext。
- `ai-service/app/agent/foundation/subgraphs/worldline/graph.py`：在 `_knowledge_entity_map` / candidate extraction 前受控 materialize registry endpoint，之后仍执行原有 Scene/Influence validator 和 projector。未被现有 typed validator/projector 的正式关系引用的临时 endpoint 不进入最终 session 图。
- `ai-service/tests/test_worldline_runtime_entity_binding.py`：25 项测试，覆盖缺失 endpoint 成功、Scene、unresolved/ambiguous/unsupported 拒绝、hash/URL/span/chunk/source/provenance/context/type 篡改拒绝、普通 mention 拒绝、容量、已有 identity 冲突、验证拒绝后清理、现有 typed projector 的节点引用保留，以及正式 build graph hook 的顺序。

新 endpoint 使用真实 registry canonical knowledge ID，保留类型、claim/evidence span、source provenance、registry version；没有制造 Java catalog ID。知识节点不可执行，前端也将其显示为当前不可探索。Scene descriptor 仍只由原 projector 在验证通过后生成，并保留 descriptor identity 与正式 evidence binding。

未修改 relation validator、merge、persistence、extraction、evaluator/scoring、Gold、corpus 或知识配置；未新增来源、release、collection；未部署。原有用户未提交修改保留且未提交。

## 真实链路及索引

入口：`http://127.0.0.1:5173/worldline`。新 session：`a209655236234f8e8894f69d9a96c062`。

前端提交“乐队《Pixies》的年代、scene 和影响关系” → Vite `/api/ai` proxy → FastAPI → PostgreSQL Worldline Session → Query Understanding → 真实 Java Music Service → Qdrant dense + lexical hybrid → ContextPack → WorldLineContextAdapter → approved native claim 对齐 → registry endpoint materialization → 原 Scene/Influence validator → graph/relation DTO → PostgreSQL commit → 正式 API response → 现有前端消费。

使用当前既有 approved manifest：`ai-service/data/indexing/worldline/final-native-20260927/v4/index_build_manifest.json`。

- manifest SHA：`445b7263221ae5864015752a6689e5d8303c21409407375a6e0b33ac831f50f3`，本轮未改变。
- release：`knowledge-v2-daa965c10984eefa`。
- collection：`worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4`。
- 真实 `QdrantHybridSearchBackend` 查询成功，3 个 candidate，channel evidence 同时有 `dense`、`lexical`。既有 Qwen encoder 未替换。两轮 song continuation 查询返回 empty，不补写知识 metadata；已提交的关系保留。
- 真实 source/chunk/claim IDs、SHA、article source context 进入 adapter。完整 trace 使用现有 passive observer，不替换任何服务或结果；observer error count 为 0。observer 会省略 parser tree 和敏感字段，索引信任校验使用实际运行中的完整 metadata。

详见 [retrieval_binding_evidence.json](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/retrieval_binding_evidence.json)、[重启前 trace](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/online_trace_before_restart.jsonl)、[重启后 trace](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/online_trace_after_restart.jsonl)。

## Runtime binding 与关系结果

`artist:nirvana` 不在初始 Java graph；它通过 selected registry v5 唯一身份与 immutable native claim 绑定进入 runtime graph。未将 Java 搜索中的歧义候选冒充唯一 catalog entity。

Influence 正式通过：`netease:artist:98542`（Pixies）→ `artist:nirvana`（Nirvana），direction=`influencer_to_influenced`，confidence=0.95。依据 ABC News native claim `claim:fe72b27603b613b82780f909`，child `child-97b7f05741b56b6909b02407`，原文 evidence/source/provenance 可追踪。UI 实际打开关系证据，显示 direction、claim、support stance、publisher、locator、SHA 和 release。

Scene 现已产生真实 candidate，source endpoint=`artist:nirvana`，claim=`claim:fd05f277d5d1fa9457b3b85a`，predicate=`emerged_from_scene`，child=`child-8b686e5334c7b896431e157f`。它已不再在 entity binding 前被丢弃；但 validator 最终拒绝理由为 `scene_not_in_evidence_span`。

具体原因：claim 的 object value 为 `Grunge-rock scene`，原文包含 `“Grunge-rock” scene`。`SceneRelationValidator._scene_is_supported` 要求 scene name 连续出现在 exact span 中，引号造成当前字串检查不匹配。**本轮没有删除引号、改写 evidence/claim、变更 validator 或绕过验证**。这是真实 Online Scene acceptance blocker。另一个 WSWS passive Influence candidate 仍被原 validator 以 `direction_missing` 拒绝；ABC direct Influence 已正式通过，不改变方向判断规则。

详见 [runtime_entity_binding_evidence.json](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/runtime_entity_binding_evidence.json)。

## PostgreSQL 跨进程恢复

| 阶段 | revision | graph nodes / edges | Scene / Influence | checkpoints |
|---|---:|---:|---:|---:|
| create | 1 | 10 / 9 | 0 / 1 | 首轮 commit |
| explore Where Is My Mind? | 2 | 11 / 10 | 0 / 1 | 6 |
| 完全停止 FastAPI Python | 2 | 11 / 10 | 0 / 1 | 6 |
| 新进程 recover | 2 | 11 / 10 | 0 / 1 | 6 |
| continuation Here Comes Your Man | 3 | 12 / 11 | 0 / 1 | 9 |

重启前实际 API Python PID=`44420`，重启后=`27824`；同时停止各自 venv launcher。停机证据确认 API Python 数为 0、8001 无监听。PostgreSQL `moodflow_agent` 始终运行，无数据库重启、恢复、迁移或手工写入。checkpoint 写入均来自正式 runtime 的 `AsyncCompatiblePostgresSaver`，未切 memory。

recover DTO 与重启前 DTO **完全相等**。revision 2 → 3 正常 commit；原 nodes metadata 精确保留、原 edges/evidence IDs 保留、sources/provenance 精确保留、Influence DTO/direction/stance/conflict metadata 精确保留，visited nodes、consumed branches、evidence history 均未丢失。恢复前后 `postgres_session_storage_sha256`、`session_state_sha256`、PostgreSQL start time 保持相同；只有 continuation 后 checkpoint 由 6 增至 9。

`postgres_restart_acceptance=true` 在本报告指对**实际已生成图与关系**的真实跨进程持久化恢复通过。Scene 从未生成，不能由这一标志推断 Scene 恢复或完整 Online E2E 已通过。Era 本轮也为空，本轮没有修改该层或声称它在线通过。

证据：[停机确认](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/api_fully_stopped.json)、[停机前 PostgreSQL](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/postgres_before_stop.json)、[停机中 PostgreSQL](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/postgres_process_stopped.json)、[recover 后 PostgreSQL](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/postgres_after_recover.json)、[continuation 后 PostgreSQL](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/postgres_after_continuation.json)。

## Provider 与前端

实际执行 6 次 provider generation，model=`deepseek-flash`，全部 `completed`，latency 分别为 8864.57、2931.94、4847.69、2186.11、7143.88、3410.54 ms，覆盖三轮 narrative/branches。未以 `/models` 可连通代替 generation；未输出 API Key。[provider_generation_evidence.json](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/provider_generation_evidence.json)

前端真实 create、explore、recover、continuation 均获得 HTTP 200，revision 1 → 2 → recover 2 → 3 可见。启动新进程尚未就绪时首次 reload 显示恢复未完成；使用现有“重试恢复”后成功，未重建 session。前端消费正式关系数组、graph edges、direction、stance、evidence/source/provenance；新 knowledge endpoint 未获得可执行 action。Scene 非空响应本轮未验证，因为实际 validator 拒绝该关系。

最终已关闭 agent 临时 tab；用户原 tab 保留。已停止 passive observer，恢复普通本地 uvicorn `app.main:app`，继续使用相同 approved hybrid manifest 和 PostgreSQL。

![实际 Influence 方向及证据](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/frontend_influence.png)

![重启 continuation 后 revision 3](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/frontend_revision3.png)

## 验证命令与结果

在 `ai-service`：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_worldline_runtime_entity_binding.py tests/test_worldline_scene_expansion.py tests/test_worldline_influence_expansion.py tests/test_worldline_runtime_integration.py tests/test_worldline_merge.py tests/test_worldline_session.py tests/test_worldline_api.py tests/test_worldline_influence_source_context.py tests/test_worldline_entity_bridge.py -q
# 最终 143 passed, 22 subtests passed；含全部 25 项 binding 测试。
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_worldline_runtime_entity_binding.py -q
# 单独运行时 24 passed；最后新增的 typed-projector 清理边界测试由上述完整相关回归验证。
.\.venv\Scripts\python.exe -X utf8 -m compileall -q app/agent/foundation/subgraphs/worldline/runtime_entity_binding.py app/agent/foundation/subgraphs/worldline/graph.py tests/test_worldline_runtime_entity_binding.py
# passed
.\.venv\Scripts\python.exe -X utf8 -m scripts.worldline_online_checkpoint_evidence --session-id a209655236234f8e8894f69d9a96c062 --output ../docs/reports/worldline-runtime-binding-20260927/postgres_before_stop.json
# 四个阶段分别执行 read-only evidence 命令；6/6/6/9 checkpoints，revision 2/2/2/3。
```

在 `soul-music-room`：`npm run test:worldline` → frontend flow 与 frontend view 均通过。前端无代码改动，未重复前端全量 build/lint/响应式视觉测试，不将这些检查声称为本轮已执行。

当前任务 staged diff 的 `git diff --cached --check` 通过。仓库全局 diff 检查发现用户既有 Gold 文件 trailing whitespace，按范围限制未处理且未提交。没有通过删除、弱化或跳过相关测试规避失败。

真实验收 JSON 逐项断言了 response schema、完整 recover DTO、nodes/edges/evidence、relation DTOs、sources/provenance、历史状态、PostgreSQL storage hashes、两组实际 PID、真实 hybrid channels、provider completion 与 Scene 拒绝理由。详见 [acceptance_evidence.json](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-runtime-binding-20260927/acceptance_evidence.json)。

验证限制：唯一一次真实在线与 restart trace 执行于 `2aaa1ee`。随后 `f23c208` 仅修清理边界；最终相关回归全部通过，并使用四份实际 HTTP response graph 断言最终 cleanup 不改变本次在线结果。没有以此离线断言冒充新的 Online E2E，也未重复运行完整 acceptance；普通 FastAPI 最终重启加载最新代码。

## 最终状态与停止条件

本轮 Runtime Entity Binding 已修复。尚未满足完整 Scene Online acceptance，未生成通过版 Freeze，不继续修 validator/Eval/extraction 或扩大 corpus。

```text
runtime_entity_binding_pass=true
scene_relation_online_pass=false
influence_relation_online_pass=true
postgres_restart_acceptance=true
frontend_contract_pass=true
online_e2e_pass=false
worldline_v1_freeze_ready=false
```

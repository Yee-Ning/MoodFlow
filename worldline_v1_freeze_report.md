# Worldline V1 Freeze Report

2026-09-27。**freeze_status=frozen**。最后一个 Scene 原文匹配 blocker 已修复；本轮约定的 Runtime Entity Binding、真实 Scene / Influence 在线关系与 PostgreSQL 跨进程恢复、continuation 验收全部通过。Worldline V1 按当前已验收基线冻结，立即停止模块开发。

## 冻结基线与范围

代码基线 commit：`7afc619`，`Match quoted Scene names while preserving evidence and rejection guards`。既有 Runtime Entity Binding 修复：`2aaa1ee`；保留已验证 typed edge endpoint 的边界修复：`f23c208`。

本轮只修改 `ai-service/app/agent/foundation/subgraphs/worldline/scene_expansion.py` 和对应测试。Scene 名称比较允许成对的直/弯双引号、NFKC 与空白规范化；仅在比较中处理，不修改原始 evidence、offset、SHA、claim、URL 或 provenance。保留词边界、否定、推测与来源校验，补齐 claim/chunk 严格一致性。没有 artist/case hardcode，没有从 narrative 或普通 mention 创建实体，没有绕过 validator。

受控 graph entity 仍来自 approved、SUPPORTED native claim 与唯一 registry binding；知识 endpoint 保留 canonical ID、entity type、knowledge ID、claim/evidence 和 provenance，不制造 Java catalog ID，保持不可执行。Scene descriptor 在 validator/projector 通过后生成。

未修改 extraction、corpus、知识配置、release、collection、merge/persistence、evaluator/scoring 或 Gold。未继续 Semantic Relations Eval、deferred eval debt、来源扩充、指标调优、架构重构或服务器部署。用户原有未提交修改保留。

## 当前正式数据与 runtime

- `retrieval_mode=hybrid`；`rerank_mode=off`；`checkpoint_backend=postgres`。
- manifest：`ai-service/data/indexing/worldline/final-native-20260927/v4/index_build_manifest.json`，既有 approval 有效，本轮未改变。
- manifest SHA256：`445b7263221ae5864015752a6689e5d8303c21409407375a6e0b33ac831f50f3`。
- release：`knowledge-v2-daa965c10984eefa`。
- collection：`worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4`。
- projection SHA256：`1552c23089f8e9051011bd32d70020cdea22b3354e77f5d8dc04d61b34e6c418`。
- 复用既有 PostgreSQL `moodflow_agent`，真实 saver 为 `AsyncCompatiblePostgresSaver`；没有 memory fallback。
- 旧 release / collection / manifest 保留；回滚索引入口为 `data/indexing/worldline/source-context-20260926/v4/index_build_manifest.json`。本轮没有执行回滚。
- 临时 passive observer 已退出，恢复普通 `uvicorn app.main:app`，`/health` 返回 `status=ok`。Java、Qdrant、PostgreSQL、Frontend 保持本地开发服务；未部署。

## 单次真实最终验收

真实入口：`http://127.0.0.1:5173/worldline`。session：`05d5e607f38c42e089582bff961b2fff`。

用户提交“乐队《Pixies》的年代、scene 和影响关系” → Frontend `/api/ai` → FastAPI → PostgreSQL session → Query Understanding → Java Music Service → Qdrant dense + lexical hybrid → ContextPack / WorldLineContextAdapter → approved claim identity 校验与 registry endpoint materialization → Scene / Influence candidate → validator → projector / graph merge → PostgreSQL checkpoint → 正式 API DTO → 现有前端。

初始真实 hybrid 检索使用 `QdrantHybridSearchBackend` / `QwenQueryEncoder`，返回 3 个 candidates，channel evidence 同时包含 dense 与 lexical。ContextPack、adapter、正式 relation evidence 的 source/chunk/claim ID、原文、SHA、source URL、publisher、title、locator、article source context 和 release/index provenance 一致。

- Scene：`artist:nirvana → scene:grunge_rock_scene`，`emerged_from_scene`；claim `claim:fd05f277d5d1fa9457b3b85a`。来源 World Socialist Web Site，文章 Assessing an American pop icon，`https://www.wsws.org/en/articles/2012/12/05/nirv-d05.html`。原文中的 `“Grunge-rock” scene` 未改写。source `wl-native-v2-001`，child `child-8b686e5334c7b896431e157f`。
- Influence：`artist:pixies → artist:nirvana`，`influenced`；claim `claim:fe72b27603b613b82780f909`；direction `influencer_to_influenced`、stance `support`。来源 ABC News，文章 Kurt Cobain's legacy is evident in the music from the year he died，`https://www.abc.net.au/news/2024-04-05/30-years-kurt-cobain-died-alternative-music-1994/103652402`。source `wl-native-v2-002`，child `child-97b7f05741b56b6909b02407`。

两条 claim 均与正式 projection 的 SUPPORTED claim 精确对齐；原生 claim 无独立 `grounding` 字段，未补写。已核对真实 source-context anchor / segments 的 offset 与原文、content SHA、raw response SHA、chunk SHA；完整字段见 [retrieval binding evidence](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-v1-freeze-20260927/retrieval_binding_evidence.json)。

| 步骤 | revision | graph nodes / edges | Scene / Influence | checkpoint 条数 |
| --- | --- | --- | --- | --- |
| Frontend create | 1 | 11 / 10 | 1 / 1 | 初次写入 |
| Frontend explore Where Is My Mind? | 2 | 12 / 11 | 1 / 1 | 6 |
| 完全停止 FastAPI Python，PG 不变 | — | PG 状态保留 | 1 / 1 | 6 |
| 新 FastAPI 进程 recover | 2 | 12 / 11 | 1 / 1 | 6 |
| Frontend continuation Here Comes Your Man | 3 | 13 / 12 | 1 / 1 | 9 |

FastAPI 实际 Python PID 从 `17716` 变为 `32992`，停机时 API Python 数量为 0、8001 无监听。PostgreSQL 自身 start time 不变；停机前、停机中、recover 后的 session storage SHA 和 session state SHA 完全一致。recover 响应与重启前 DTO **逐字段完全相同**。continuation 成功 HTTP 200 并提交 revision 3，保留全部 12 个旧节点、11 条旧边、7 个旧来源、两类 relation DTO、方向、stance、conflict metadata、history / visited / consumed 状态，增加新专辑节点和证据；没有 source_conflict。

song continuation 当前检索为 empty，缺失仍保持缺失；此前已验证并提交的图与知识关系保留，没有人工补写 context。真实前端消费 create、explore、recover、continuation 均 HTTP 200；Scene / Influence 证据面板可显示来源、exact span、方向、metadata 和引用。知识 endpoint 在 UI 保持当前不可探索，正式 Java catalog 节点可继续探索。

## Provider

真实 provider `deepseek-flash` 完成 6 次 generation：create narrative / branches、首次 explore narrative / branches、重启后 continuation narrative / branches，全部 `status=completed`。provider latency 分别为 6755.30、2435.07、4756.19、3019.74、8321.07、2532.94 ms。不是 `/models` connectivity 替代。未记录 API Key / 密码。

## 测试与检查

在 `ai-service`：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_worldline_scene_expansion.py tests/test_worldline_runtime_entity_binding.py tests/test_worldline_influence_source_context.py tests/test_worldline_runtime_integration.py tests/test_worldline_merge.py tests/test_worldline_session.py tests/test_worldline_api.py tests/test_worldline_influence_expansion.py tests/test_worldline_entity_bridge.py -q
```

结果：**160 passed，22 subtests passed**。覆盖新增 Scene typography、词边界、否定/推测拒绝、unsupported / chunk / evidence 拒绝，以及既有 binding、provenance、source conflict、graph capacity、API、session 与持久化相关测试。没有删除、跳过或弱化失败测试。

- 对修改的 Python 文件 `compileall -q` 通过；任务范围 `git diff --check` / staged diff 检查通过。
- 在 `soul-music-room` 执行 `npm run test:worldline`：flow / view contract 测试通过。
- 真实前端用户旅程与证据面板 smoke 通过，保存 DOM / 网络响应 / 截图。
- 保存的只读证据检查可复跑：在 `ai-service` 执行 `.\.venv\Scripts\python.exe -X utf8 ../docs/reports/worldline-v1-freeze-20260927/verify_acceptance.py`，全部 assertions 通过。此脚本只读验收文件，生成摘要，不创建 session 或修改数据库。

## 覆盖边界与保留限制

Freeze 基于用户最后约定的 Scene / Influence、Runtime Entity Binding、正式前端与跨进程 continuation 范围。此真实请求 Era 数组为 **0**，没有将它记为 Era 在线正例验收。没有新跑最终全面 regression 或 Semantic Relations Eval，没有宣称 corpus 全覆盖。

本轮没有真实冲突正例；conflict flags / direction / stance 的 DTO 与恢复保留已核对，真实冲突拒绝边界由相关单元测试覆盖。两条来源为 article context，不宣称本轮新增 interview Q/A / speaker 在线覆盖。Scene 的 location / time 仍缺失，未从原文推测补写。原生 claim 独立 grounding 字段缺失已如实保留，source-context offset 与 hash 链可审计。

没有前端源代码修改，未重跑全量 frontend build / lint / 响应式视觉验收；只完成现有 contract 测试与本次真实用户旅程。已知 deferred evaluation debt 留在冻结边界外。本次没有未解决的冻结范围 blocker。

## 归档与最终状态

[验收摘要](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-v1-freeze-20260927/acceptance_evidence.json)、[完整证据目录与 SHA](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-v1-freeze-20260927/evidence_manifest.json)、[provider generation](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-v1-freeze-20260927/provider_generation_evidence.json)。旧 blocked 决策原文保存在同目录 `previous_blocked_freeze_report.md`，由本次通过结果取代，历史不删除。

```text
runtime_entity_binding_pass=true
scene_relation_online_pass=true
influence_relation_online_pass=true
postgres_restart_acceptance=true
session_recovery_pass=true
retrieval_runtime_pass=true
provider_generation_pass=true
frontend_contract_pass=true
online_e2e_pass=true
worldline_v1_freeze_ready=true
freeze_status=frozen
```

即日起停止 Worldline V1 开发；不继续 Eval、不扩 corpus、不部署。后续如需修改，应作为明确的新版本需求重新开启。

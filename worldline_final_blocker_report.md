# Worldline 最终闭环结果：未达到 Freeze

日期：2026-09-27。本轮已完成上游抽取修复、正式 release/index 发布、真实前端请求和跨进程恢复；最终 Online E2E 未通过。没有运行 Semantic Relations Eval，没有增加 Gold、来源或部署。

## 唯一已证实的阻断根因

**component → input → expected → actual → root cause**

`build_worldline_graph → _knowledge_entity_map → Scene/InfluenceCandidateExtractor`
→ 新 approved 索引返回的 SUPPORTED native child claims：Nirvana `emerged_from_scene`、Pixies `influenced` Nirvana、Nirvana `influenced_by` Pixies
→ 从真实 claim 生成 Scene relation 和方向为 Pixies → Nirvana 的 Influence relation
→ 实际正式请求已经把三条 claim 及原文 context 送入 adapter，但两层 expansion 的 `requested=true, candidate_count=0, accepted_count=0, rejected_count=0`
→ **生产图谱没有为 corpus 中的 `artist:nirvana` 建立经过验证的 runtime 实体绑定。candidate extractor 只接受已存在于图谱中的 endpoint；native claim 在进入 validator 前即被 `return None` 丢弃。**

这次请求的真实根艺人是 Java 返回的 Pixies；初始图谱只有 Pixies 与八首歌曲。`_knowledge_entity_map` 实际只有 `artist:pixies`：Scene subject Nirvana 未绑定；正向 Influence 的 object Nirvana 未绑定；被动 Influence 的 subject Nirvana 未绑定。新索引、context packet 和正确 predicate 已到达运行时，不能再把这次失败归结为缺少 production positive。

定位：

- [builder.py](D:/code/ProjectsAll/MoodFlow/ai-service/app/agent/foundation/subgraphs/worldline/builder.py:32)：构图边界；第 161 行只读取可选 `graph_nodes`，当前正式投影没有提供这种节点。
- [era_expansion.py](D:/code/ProjectsAll/MoodFlow/ai-service/app/agent/foundation/subgraphs/worldline/era_expansion.py:360)：只将已有 graph node 与 catalog/reference 建立 knowledge ID 映射。
- [scene_expansion.py](D:/code/ProjectsAll/MoodFlow/ai-service/app/agent/foundation/subgraphs/worldline/scene_expansion.py:278)：未绑定 subject 时返回 None。
- [influence_expansion.py](D:/code/ProjectsAll/MoodFlow/ai-service/app/agent/foundation/subgraphs/worldline/influence_expansion.py:410)：任一 endpoint 未绑定时返回 None。
- [实际输入及映射证据](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-final-native-20260927/runtime_candidate_binding_evidence.json)。其中保存实际 SearchPlan、图节点、adapter claim、source context 和各 endpoint 的绑定结果；没有使用 fixture 图谱。

没有为通过验收硬编码 Nirvana catalog ID、注入 mock 节点、修改 relation validator，或把 provider 叙事中的影响描述冒充 Relation DTO。根据用户失败处理要求，本轮停止于此根因，不继续扩 corpus 或开启 Eval。

## 已完成的生产抽取和数据发布

保持现有 Claim/ClaimCandidate schema、predicate enum、正式 verification 和 KnowledgeReleaseBuilder gate。通用修复包含：原文局部唯一 canonical/alias 绑定，完整局部句子的 factuality 检查，较小 extraction batch，失败 batch 拆分重试，严格 structured parse，extraction 独立 4096 token budget，以及 structured triple 与原文实体角色的 admission 校验。被拒绝的 proposal 及真实 provider 输出保留于诊断/trace，不作为已验证 owned claim。

parent→child grounding 仅允许 SUPPORTED parent、唯一连续原文 child 映射、完整 source/document/parent/child lineage 和 SHA；保留语义参数与 verification，记录 derived_from 与偏移。不唯一、跨 child、hash 不一致和篡改保持失败；已有独立 child claim 不重复制造相同 native relation。

仅使用现有两篇真实来源。SourceIngestionService 获取的 immutable snapshots 在本轮重新 parse/clean/chunk/link/extract/verify；最终候选使用同一对原始 snapshot 独立重新运行抽取，没有拼接先前 run 的 claim。版本：knowledge config `v18`、claim extraction `v13`、prompt `v4`、entity linking `v4`、verification `v3`、registry `v5`。原 config v13 保留。

| 数据 | 实际结果 |
|---|---|
| chunk | 76：35 parent、41 child；76 success，0 partial，0 failed |
| owned claim | 21；SUPPORTED=21，其余 verification 状态=0 |
| release QA | 原有 gate 要求的 21 claim 和 100 entity-link sample 已审阅并 attested；QA sample 不作为索引输入 |
| real extraction trace | 123 次 provider 调用：57 extraction、36 admission、30 verification；model=deepseek-flash |
| Scene child | `claim:fd05f277d5d1fa9457b3b85a`，Nirvana `emerged_from_scene` “Grunge-rock scene”，SUPPORTED |
| Influence child | `claim:fe72b27603b613b82780f909`，Pixies `influenced` Nirvana，SUPPORTED |
| 另一个原文 Influence child | `claim:d025713850e37cf4ccc5283b`，Nirvana `influenced_by` Pixies，SUPPORTED；语义方向同为 Pixies → Nirvana |

两篇来源的 publisher/title 来自原网页 metadata；article sentence 原文、DOM offset、locator、URL、response/content SHA、source/chunk/claim/entity IDs 均保留。来源不具备 interview Q/A 或 speaker binding，保持缺失。

| 来源 | 原网页 | raw response SHA256 |
|---|---|---|
| WSWS，Assessing an American pop icon | [原文](https://www.wsws.org/en/articles/2012/12/05/nirv-d05.html) | `dda83b56f08bad394c250dfb419d917f58445d7cf8bb3180adb04ed7e137626c` |
| ABC News，Kurt Cobain's legacy is evident in the music from the year he died | [原文](https://www.abc.net.au/news/2024-04-05/30-years-kurt-cobain-died-alternative-music-1994/103652402) | `c24ba194455764abd4da78284a7b102f8e30e2156ea5b145197146b40c8c2f25` |

正式 release：`knowledge-v2-daa965c10984eefa`，路径 [release manifest](D:/code/ProjectsAll/MoodFlow/ai-service/data/knowledge/releases/worldline-final-native-release-20260927/v2/knowledge_release_manifest.json)。首次输出目录不符合既有 dataset_version=v数字的索引约定而被拒绝；随后通过同一正式 builder 生成合规 `/v2` release，没有修改 manifest 或放宽 gate。

正式 collection：`worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4`，41 point，全量 live payload 与 dense/BM25 vector、point count 已验证。没有手工写入 Qdrant。

[新 index manifest](D:/code/ProjectsAll/MoodFlow/ai-service/data/indexing/worldline/final-native-20260927/v4/index_build_manifest.json) SHA256：`445b7263221ae5864015752a6689e5d8303c21409407375a6e0b33ac831f50f3`。
[ready approval](D:/code/ProjectsAll/MoodFlow/ai-service/data/indexing/worldline/final-native-20260927/v4/ready_index_approval.json)：approved，范围为原有 retrieval build integrity；不冒充 semantic expansion 验收。

真实 hybrid 数据 smoke：Scene 查询有 6 candidate、4 ContextPack item；Influence 查询有 3 candidate、3 item。两类 native positive 均在 live payload、ContextPack、adapter 中可追踪，原始 raw response 与 DOM offset 通过核对。[数据 smoke 全记录](D:/code/ProjectsAll/MoodFlow/ai-service/data/indexing/worldline/final-native-20260927/v4/native_retrieval_smoke.json)。此数据运输成功不等于 expansion relation 成功。

旧 release `knowledge-v2-aa5271a36860debb`、collection `worldline_knowledge_v4_aa5271a36860debb_v1_v2_v4`、source-context-20260926 manifest 保留用于 rollback。

当前非敏感 runtime 配置：`retrieval_mode=hybrid`、`rerank_mode=off`、`checkpoint_backend=postgres`，manifest 指向 `data/indexing/worldline/final-native-20260927/v4/index_build_manifest.json`。私有 .env 未提交，报告不包含凭据。

## 本轮真实 Online / PostgreSQL trace

Frontend（5173）→ Vite API proxy → FastAPI（8001）→ Worldline Session → Query Understanding（索引绑定 registry v5）→ Java Music Service → live Qdrant hybrid → ContextPack/adapter → expansion → real provider → PostgreSQL →正式响应及 UI。

session：`bc8ee3a12b0843cab03485288d90bb88`。前端初始请求：“乐队《Pixies》的年代、scene 和影响关系”。使用服务端实际生成的分支探索 Where Is My Mind?；恢复后探索 Here Comes Your Man，没有构造 mock 分支。

| 阶段 | revision | graph nodes / edges | checkpoint |
|---|---:|---:|---|
| 前端 create，HTTP 200 | 1 | 9 / 8 | 正式 postgres commit |
| 前端 explore，HTTP 200 | 2 | 10 / 9 | 6 checkpoint |
| 完全停止旧 FastAPI，保持 PG | 2 | 存储未修改 | checkpoint/blob/write 摘要 SHA 不变 |
| 新进程 recover，同一 session | 2 | 10 / 9 | 整个 recover DTO 与重启前 explore DTO 相等 |
| 前端 continuation，HTTP 200 | 3 | 11 / 10 | 9 checkpoint；成功 commit |

停止进程：venv launcher PID 16612 与实际 FastAPI Python PID 30452 均停止，8001 listener 消失；新 launcher PID 41004、实际 Python PID 13388。PostgreSQL 未重启，`postgres_started_at` 保持不变。实际 saver 为 `AsyncCompatiblePostgresSaver`，没有回退 memory。

原 nodes、edge identity、edge evidence IDs 和 sources/provenance 在 continuation 后保留。其中 Here Comes Your Man → Pixies 原 edge 增加新的 Java evidence ID，属于正常证据追加。recover 时 graph、history/state、sources 均完整一致；continuation revision 2 → 3，无 source_conflict。

实际 Era / Scene / Influence relation 数组始终为 0。不能以空数组恢复成功证明非空 relation 的 direction/stance/conflict flags 跨进程恢复，因此 PostgreSQL **mechanics pass=true**，本轮要求的完整 restart acceptance 仍为 false。前端 create/explore/recover/continuation happy path 可消费且 console error=0；native relation 展示 contract 未通过，不能以 happy path 替代。

real provider generation：deepseek-flash，7 次实际 completed response，latency 约 1922–7315 ms；包括真实 narrative 和 branches generation。生成成功不代表 native relation 已进入图谱。provider structured retry/SLA 风险只登记，不继续调优。

证据：[汇总](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-final-native-20260927/acceptance_evidence.json)、[重启前 trace](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-final-native-20260927/online_trace_before_restart.jsonl)、[重启后 trace](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-final-native-20260927/online_trace_after_restart.jsonl)、[PG 重启前](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-final-native-20260927/postgres_before_stop.json)、[进程停止时](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-final-native-20260927/postgres_process_stopped.json)、[PG continuation 后](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-final-native-20260927/postgres_after_continuation.json)、[provider](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-final-native-20260927/provider_generation_evidence.json)、[前端请求](D:/code/ProjectsAll/MoodFlow/docs/reports/worldline-final-native-20260927/frontend_network_evidence.json)。被动 observer 仅记录真实调用，不替换服务、generator、validator 或 saver。

验收后已停止 observer，恢复普通 uvicorn app.main:app，保留新 approved manifest；无服务器部署。

## 修改与验证

已验证代码/data revision：`165709a5ded9659b76217645a940219832d90a08`。独立 commits：`04780e4`（局部抽取/安全 grounding）、`456f1fc`（query registry 与所选索引一致）、`1a8b0cd`（structured 实体角色 admission）、`3f0e441`（predicate 参数类型检查）、`165709a`（真实生产 release/index 及原始证据）。没有修改 merge/persistence、relation validator、evaluator/scoring 或用户原有未提交文件。

在 ai-service 使用 `.venv\Scripts\python.exe -X utf8`：

- `-m pytest tests/test_production_extraction_grounding.py tests/test_rag_knowledge.py tests/test_rag_indexing.py tests/test_llm_client.py tests/test_worldline_merge.py -q`：107 passed、1 skipped、25 subtests passed。skip 是既有可选 Qdrant integration URL 未设置；独立实际 live Qdrant 构建/查询已经执行。
- `-m pytest tests/test_worldline_session.py tests/test_worldline_runtime_integration.py tests/test_worldline_api.py tests/test_worldline_conflict_handling.py tests/test_worldline_source_context_preparation.py tests/test_worldline_influence_source_context.py tests/test_query_registry_binding.py tests/test_query_understanding_service.py tests/test_query_understanding_graph.py tests/test_query_understanding_contracts.py tests/test_query_understanding_index_profile.py -q`：133 passed。
- `-m compileall -q app/rag/knowledge/claims app/rag/knowledge/child_grounding.py app/rag/knowledge/verification/models.py app/rag/indexing/projection.py app/rag/query_understanding/service.py app/llm/client.py scripts/worldline_native_claim_preparation.py`：通过。
- 正式 `run_knowledge_release.py --prepare-review / --finalize`、`run_index.py --local-files-only`、全量 `verify_index(approve=True)`、真实两次 hybrid 数据查询、真实前端会话及 PG read-only checkpoint evidence：结果如上。
- `git diff --cached --check`：通过。raw/release/manifest staged bytes 与工作区完全一致，未因 Windows 换行转换破坏 SHA。

当前限制为实际 native relation expansion 未通过；非空 Era/Scene/Influence 的方向、stance、冲突元数据和重启恢复没有得到本轮成功证明。未继续历史 Eval debt，未生成 Freeze report，未部署。

## 最终 flags

```text
scene_native_positive_ready=true
influence_native_positive_ready=true
new_release_ready=true
retrieval_runtime_pass=true
provider_generation_pass=true
session_recovery_pass=true
postgres_restart_acceptance=false
frontend_contract_pass=false
online_e2e_pass=false
worldline_v1_freeze_ready=false
```

# Worldline Final Acceptance Report

日期：2026-09-27（Asia/Shanghai）。范围：B1 source identity merge、B2 最小 production native positive 数据、一次窄范围真实在线及跨进程验收。

**最终验收未通过，不能冻结 V1。** B1 的 query observation 误报已修复，真实 PostgreSQL 恢复后的 continuation 已成功提交 revision 3。B2 的正式抽取结果仍缺少 Scene / Influence native positive，尚未发布新 release / collection / approved manifest。

证据摘要：[acceptance_evidence_summary.json](worldline-final-acceptance/acceptance_evidence_summary.json)。摘要包含原始响应、只读 PostgreSQL 探针、运行 trace 和 corpus 产物的 SHA；完整响应及较大的原始 trace 保留在所列本地路径，未作为验收成功的替代物。

## 1. B1：source identity merge

修改 `ai-service/app/agent/foundation/subgraphs/worldline/merge.py`：将 `matched_child_ids` 与已有的 rank、fusion、channel、subquery、retrieval leg 等显式 observation 字段统一排除在稳定 source identity 比较之外。使用字段白名单；没有按字段前缀忽略 provenance。

source/chunk/claim/document/point ID、URL、exact span 及 offset、原始 response/content SHA、parent SHA、locator、release ID、knowledge/index input hash、绑定的 source context 均继续严格比较。只有同时存在不可变 index input hash 与原始 source content SHA 时，压缩查询视图的 `context_document_sha256` 才作为 observation；缺少这些绑定时仍严格校验。

回归覆盖同一 source 的 matched child 子集变化及 rank/fusion/subquery 变化可合并，旧对象不被修改；22 个受保护字段篡改子测试仍拒绝为 `source_conflict`。未索引 source 的 context hash 变化仍拒绝。修复已提交：`a3a0dd7`。

原 session `42c804ea5a774780a46539f6aaac0f6f` 在新 Python 进程恢复后，原分支不再报 `source_conflict`，但返回 HTTP 409 `capacity_reached`：原图 14 个节点，扩展超过既有 15 节点容量。该 session **没有**提交新 revision。未修改容量规则、未删除旧图节点。

另一个从正式前端创建的普通会话完成了跨进程 revision 2 → 3，详见第 3 节。`source_identity_merge_regression_pass=true`，`restart_continuation_commit_pass=true`；不能将原会话的容量拒绝描述为成功。

## 2. B2：正式生产数据闭环未满足

重新通过 `SourceIngestionService`、HTTP policy、不可变 raw snapshot / SHA ledger 获取以下三篇真实页面。没有读取或复制 Gold、review pack、审核说明来生成这些输入。

| Source ID | 来源、页面标题 | 正式处理结果 |
|---|---|---|
| wl-positive-001 | [新浪娱乐：周杰伦带头“清洁”音乐圈：请还音乐纯净空间](https://ent.sina.com.cn/y/2009-01-09/09252334563.shtml) | HTTP 200，raw SHA 校验通过；未生成 Scene / Influence native positive |
| wl-positive-002 | [凤凰网娱乐：温岚：周杰伦将光芒分给我](https://ent.ifeng.com/music/hk/200709/0912_1839_222698.shtml) | HTTP 200，raw SHA 校验通过；未生成 Scene / Influence native positive |
| wl-positive-003 | [新浪娱乐：周杰伦与台湾音乐界呼吁抵制买榜恶习(组图)](https://ent.sina.com.cn/y/2009-01-08/17582334028.shtml) | HTTP 200，raw SHA 校验通过；未生成 Scene / Influence native positive |

另一个实际页面候选 `wl-positive-004`：[Hypebeast 原文](https://hypebeast.cn/2020/11/how-jay-chou-influenced-chinese-pop-music)。正式 HTTP ingestion 返回 **405 Not Allowed**，没有创建 raw snapshot，也没有将搜索结果摘录冒充原始响应。

三篇成功输入已执行原正式链：

`raw snapshot → Parse/Normalize → Clean → Chunk → Enrich（rule-only metadata）→ Knowledge claim extraction / verification（真实 provider，config v13）`

隔离 staging 路径：`ai-service/data/{normalized,cleaned,chunked,enriched,knowledge}/worldline-native-positive-20260927/`。来源候选及 raw ledger 在 `ai-service/data/source_ingestion/worldline-native-positive-20260926/`。

产物共 3 document、31 Parent / 40 Child、71 chunk、69 个唯一 claim。验证结果：65 SUPPORTED、1 PARTIALLY_SUPPORTED、3 INSUFFICIENT_EVIDENCE。原生 predicate 分布：`other=43, performed_at=13, collaborated_with=6, signed_to=3, wrote_lyrics_for=1, released=1, chart_position=1, occurred_at=1`。Scene 与 Influence 均为 **0**。

这说明当前正式抽取产物不满足目标，不等同于断言原文中不存在任何相关陈述。没有将普通音乐圈提及、genre、collaboration 或带主观判断的引语人工改写成 positive predicate；未修改抽取结果、validator 或评分规则。

只读调用既有 release builder 的 source validation，明确被 `knowledge_release_claim_verification_incomplete` 拒绝。`pre_embedding_ready=true` 不等于 release / semantic readiness。未过滤未验证 claim 来规避 release gate。

因此新来源尚未进入 `knowledge release → IndexDocumentProjector v4 → 独立 Qdrant collection → ready approval`；不能声称 Scene / Influence production positive 已进入正式索引。尚无满足目标的 positive claim 可完成 native RelationSourceContext 绑定与在线关系验证。

保留当前已批准的旧 release/index：

- Manifest：`ai-service/data/indexing/worldline/source-context-20260926/v4/index_build_manifest.json`
- Manifest SHA：`sha256:a74164c52346dd4050468d2e491dfe5993abec16cb70122d9df7d693769d2f17`
- Release：`knowledge-v2-aa5271a36860debb`
- Collection：`worldline_knowledge_v4_aa5271a36860debb_v1_v2_v4`（1270 points）
- 旧 ready approval 的 scope 是 `retrieval_build_integrity_only`，不能据此认定新增 corpus 的关系语义验收通过。

旧 collection / manifest 未覆盖，runtime 未切换至 staging。

## 3. 真实前端、PostgreSQL restart 与 continuation

正式用户会话：`caa9b2997d874518b550cf3a156fe9d9`。全程使用 localhost Frontend / FastAPI、Java Music Service、真实 Qdrant hybrid、真实 provider、PostgreSQL saver。没有将 engineering fixture、memory saver 或 mock relation DTO 用作在线验收。

| 阶段 | HTTP | Revision | Nodes / edges | Sources | Era / Scene / Influence |
|---|---:|---:|---:|---:|---:|
| Frontend 创建周杰伦世界线 | 200 | 1 | 11 / 10 | 10 | 2 / 0 / 0 |
| Frontend explore《刀马旦》 | 200 | 2 | 13 / 12 | 17 | 2 / 0 / 0 |
| 完全停止 Python 后，同 session 前端 recover | 200 | 2 | 13 / 12 | 17 | 2 / 0 / 0 |
| Frontend continuation《骑士精神》 | 200 | 3 | 15 / 14 | 23 | 2 / 0 / 0 |

选择的 continuation 是模型通过正式响应返回的可用 branch，没有人工写入 branch / relation。Python 进程 PID 39976 完全停止，并确认 8001 无监听；新 Python PID 9064 启动同一 `app.main:app`，浏览器 reload 后由正式 recover API 恢复会话。被动 trace observer 不替换 service、generator、validator 或 checkpoint 返回值。

PostgreSQL 原实例与数据卷保持不变，server start time 一直为 `2026-09-26T13:19:39.325252+00:00`。revision 2 在停止前、Python 停止期间的 PostgreSQL storage SHA、session state SHA 以及 6 checkpoint / 6 blob / 6 write 完全一致。revision 3 提交后为 9 checkpoint / 9 blob / 9 write。实际 `_commit` 使用 `AsyncCompatiblePostgresSaver`，没有回退 memory。

恢复响应的 graph、sources、Era DTO 与重启前精确相等。续接后，旧节点原值、旧边方向及 evidence ID、全部旧 source 原始 metadata、Era DTO、visited nodes 和 consumed branch history 均保留。摘要中的 14 个 preservation 检查全部为 true。

**Scene / Influence 始终为空，不能用空数组相等证明这两类关系、direction / stance / conflict metadata 的跨进程保留已验收。** 未构造冲突 case 以填充覆盖。

前端已实际显示 revision 3、15 nodes / 14 edges，并打开 `belongs_to_era` 证据，显示原文、publisher、source/chunk ID、source SHA、locator 和 release provenance。截图：[revision-3-evidence.png](worldline-final-acceptance/revision-3-evidence.png)。

另一条《布拉格广场》explore 返回 HTTP 502 `narrative_generation_failed`，真实 provider 的语义校验码为 `unknown_citation`；当时仍为 revision 1，没有错误提交。保留该失败记录，随后正常选择另一条现有 branch 成功。未放宽引用校验以通过验收。

## 4. Retrieval / provider / frontend 证据

重启后的 retrieval trace：`QdrantHybridSearchBackend` / `QwenQueryEncoder`，绑定上述 approved manifest；真实 query 返回 success、40 candidates，进入 ContextPack 的 6 document 均带可追踪 source/chunk/claim IDs、原始 SHA、locator 与 release/index provenance。耗时 20297.68 ms。

创建请求的 ContextPack 包含 17 个真实 `article_sentence` capture；实际 metadata 重新经过 `DocumentContextCapture` hash / offset / exact span 校验，全部通过。无 capture 的 document 保持缺失；重启续接选中的 6 document 没有 capture，未自动生成。本次没有命中完整 interview Q/A，不能声称新增 Q/A smoke 已覆盖。

重启后的两次实际 provider generation：

| Model | Schema | 结果 | Latency |
|---|---|---|---:|
| deepseek-flash | worldline_narrative | completed，正式 continuation 响应成功 | 7534.71 ms |
| deepseek-flash | worldline_branches | completed，正式 branch 输出成功 | 2041.20 ms |

实际 UI 验证了 create / explore / recover / continuation、graph edges 与 Era evidence 消费。Scene / Influence 非空响应的完整 frontend contract 尚未完成。报告及摘要不包含 API Key、密码或 DSN。

验收结束后已退出被动 observer，恢复普通 `uvicorn app.main:app` 本地进程；继续保留 PostgreSQL 与旧 approved runtime 配置：`retrieval_mode=hybrid`、`checkpoint_backend=postgres`。

## 5. 修改、检查与剩余 blocker

产品代码只修改 `merge.py`，对应更新 `tests/test_worldline_merge.py`，commit `a3a0dd7`。此外生成本报告、紧凑证据摘要、截图及隔离 staging / raw ledger；没有修改 frontend、validator、evaluator / scoring、Gold / review pack、旧生产索引或部署服务器。仓库原有用户未提交修改未纳入任务 commit。

执行检查：

```text
ai-service:
.venv/Scripts/python.exe -m pytest tests/test_worldline_merge.py tests/test_worldline_session.py tests/test_worldline_runtime_integration.py tests/test_worldline_api.py tests/test_worldline_conflict_handling.py -q
68 passed, 22 subtests passed in 24.93s

.venv/Scripts/python.exe -m ruff check app/agent/foundation/subgraphs/worldline/merge.py tests/test_worldline_merge.py
All checks passed

soul-music-room:
npm run test:worldline
worldline frontend flow passed; worldline frontend view passed
```

没有重跑 Semantic Relations Evaluation 或 Final Worldline Regression；未重做 UI，未以 build 代替真实交互验收。

全仓 `git diff --check` 命中用户原有 `ai-service/evals/reports/gold_review_v0_blind_pack.md:26` 尾随空白。该文件未修改；仅对本任务暂存文件执行的 diff 检查通过。大型 raw / staging / 运行响应和 trace 为当前工作区本地证据，摘要提交其 SHA 与路径，没有将整个未提交 corpus 或用户已有报告一并提交。

剩余 blocker：B2 的 production Scene / Influence native positive 尚未生成、验证及发布。当前 staging 另有 4 条非 SUPPORTED claim，正式 release gate 拒绝发布；Hypebeast 候选无法经正式 HTTP ingestion 获取。需要可审计、能通过现有正式抽取和验证的最小真实来源后，才能继续新 release/index 与非空 Scene / Influence restart 验收。原 session 的容量拒绝和一次真实模型引用失败保留为本轮限制，未扩大范围修复。

最终六项 acceptance flag（完整覆盖标准，区分已通过的 mechanics / happy path）：

```text
online_e2e_pass=false
postgres_restart_acceptance=false
session_recovery_pass=true
retrieval_runtime_pass=true
provider_generation_pass=true
frontend_contract_pass=false
worldline_v1_freeze_ready=false
```

`postgres_restart_mechanics_pass=true`、`restart_continuation_commit_pass=true`、`frontend_happy_path_contract_pass=true`。由于 Scene / Influence 数据及非空响应覆盖缺失，未宣布最终验收通过，未进入 V1 Freeze。

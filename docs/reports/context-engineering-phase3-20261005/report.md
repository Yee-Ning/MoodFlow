# Context Engineering Phase 3 验收报告

日期：2026-10-05。分支：`codex/context-engineering-phase3`。基线：Phase 2 `7c286b35c3f2483394ecc015be0e65d8ed6313ef`。

## Gate 0 与确认

先读 Phase 1/2 报告，再核对代码；发现恢复边界与原要求不符后，只读停止，未先建分支或修改代码。West 随后明确选择 A：按实际字段、事件结构和 task_id 双语义统一契约，保留 checkpoint + 不可变 proposal 的恢复机制；Journal 独立恢复列为后续优化。

| 核对项 | 实际接口 | 本轮裁决 |
| --- | --- | --- |
| Phase 1 | commit `9508a7bd54f8def193354b48faf6e10168fcb9cb`；报告 2287 passed、12 skipped、288 subtests passed | 验收基线保留 |
| Phase 2 | 实现 `153fd06d7e6517a6b870c4404a58ed2a453f9938`，报告 HEAD `7c286b3`；2316 passed、12 skipped、288 subtests passed | 基于此 HEAD 创建分支 |
| ContextPack | items/total_tokens/budget/dropped_items/compressed_items/messages/trace/manifest/intent；manifest 为 tokens/budget/sources | 保留 L1；新增投影 schema 一致性校验 |
| call_manifest | 合并来源统计，含调用 ID、run/trace、agent/model、input/output/total、估算依据、输出上限、状态和预算事件 | 仍为观测投影，不能参与后续选择或成为第二份账本 |
| Source / policy | context_sources.py 的 SourceRequest/SourceRegistry；context_policy.json 的 lite/sources/profile_categories/budget | 不改注册、配额、选择或策略配置 |
| Envelope | 嵌套 request.target_agent/reason/context_summary；run_id/tool_call_id/source_agent/original_input/tasks/results/entities/evidence_refs | 不新增 mood/narration/key_ids 或 L3 快照，按真实字段写契约 |
| RogueHandoff | 服务端规范化 UTF-8 JSON + SHA-256，proposal 固定完整载荷摘要，接收方先验后执 | 原算法和所有篡改拒绝断言保留 |
| History | 外壳 event_id/task_id/session_id/type/timestamp/node/source/payload或storage_ref/references/metadata/request_id/event_key/event_seq；agent 位于 payload | 不虚构顶层 agent；新引用格式另设兼容接口 |
| 预算事件 | budget.exceeded 是观测流；持久化为 MODEL_USAGE phase=budget，tokens=0 | 保留真实事件类型与计费语义 |
| task_id | History 为长期 continuity_task_id；BudgetLedger 的参数 task_id 是原始 run_id | 文档明确两种作用域，查询严格校验，不改业务命名 |
| checkpoint / journal | proposal → checkpoint 保存 → committed；保存后回执缺失，用 checkpoint + 匹配不可变 proposal 写 reconciled | 按 West 确认保留，不改提交/恢复顺序；Journal 独立恢复为后续项 |

## 契约与代码

契约文档先于代码写入：`docs/contracts/context-pack.md`、`handoff-envelope.md`、`journal.md`。分别列明字段、来源与预算口径、写入所有者、生命周期、完整性锚点、恢复边界、幂等和下钻接口。

- ContextPack 增加 manifest 与 total_tokens/budget 的一致性检查；CallManifest 校验拆分总量、输出上限状态、run/trace 作用域和完整来源投影。没有更改 L1 选择或 Assembler 算法。
- 持久调用的 call_manifest 增加 usage_ref，指向已结算 MODEL_USAGE（长期 task_id + event_id）；顶层 task_id 继续表示 run_id，不改旧观测字段语义。
- 新转交后续阶段以 TransferJournalReference v2 持久化。JournalReader 校验 canonical proposal 的存在性、task/session/type/transfer_id、checkpoint namespace，再物化原 TransferJournalEvent v1 视图。旧 v1 仍可读取；同键跨版本重放返回内容等价的原不可变事件，不覆盖旧档案。
- 拒绝回执也引用 proposal，只保留该次 reason_code/retry_allowed。namespace 迁移的 proposed 用 v3 指针连接来源 session，校验 previous_session_id、同 task/conversation、事件类型和 transfer_id，不再复制旧 proposal 正文；迁移执行与 checkpoint 确认逻辑保持原样。
- 新 RUN_PROGRESS 的 run v2 删除 usage 累计副本；run_id 是 MODEL_USAGE 账本查询指针。checkpoint 继续保留实际执行用量，既有恢复累计不下降、reserve/settle 和预算策略保持原样。
- 会话投影新增 SESSION_PROJECTION 白名单归档；handoff 表只存 SessionProjectionPointer。读取恢复原投影版本、所有字段与 token_count，不改变 L1 输入。旧表内完整投影只读兼容，不改写。
- 新档案写入全部复用 HistoryWriter 的身份、白名单、私有文本、字节、引用与幂等门。没有新增直接 store.append_event 调用的应用路径，没有新增模型调用。
- 最外层请求结束丢弃旧 active_manifest；嵌套作用域仍恢复父调用观测。这修复了旧单次 Pack 观测在请求结束后被重新恢复的问题，不改变模型输入。

## 去重清单

以下裁决针对新写入；已有 immutable v1 事件不得追溯修改或删除，保留兼容读取。

| 字段 / 内容 | 原位置 | 最终归属 | 迁移方式 |
| --- | --- | --- | --- |
| candidate_summary | Envelope.request.context_summary；proposed/rejected/committed/reconciled/target_* 的 summary | 当前 Envelope 协议视图；长期仅 canonical proposed.summary | 新后续回执删 summary，改 summary_ref；拒绝只存本次 reason_code/retry_allowed；内存读取物化 |
| payload_digest | proposed 与每个后续回执的 summary | canonical proposed.summary.payload_digest | 后续只引用 proposal；不从接收载荷造摘要 |
| namespace 迁移 proposal.summary | 源 session 与目标 session 的 proposed 正文 | 最初 immutable proposal | 新目标 proposed 写 v3 指针；读取按 session lineage 下钻 |
| completed_result_refs | proposed.summary 与 committed/reconciled.result_refs | canonical proposed.summary.completed_result_refs | commit 回执删 result_refs；目标观察保留本事件的 result_refs |
| summary 中的 run/hop/agent/route 字段 | proposal 与后续阶段的整个 summary | canonical proposal | 后续回执只保留事件关联 transfer_id/phase 和 summary_ref |
| RUN_PROGRESS.run.usage | MODEL_USAGE 账本、checkpoint、公开进度 | MODEL_USAGE 是持久计费用量；checkpoint 是执行提交见证 | 新进度删除 usage，仅 run_id 查账；不重复累计 snapshot |
| HandoffProjection 的 summary/goal/constraints/preferences/decisions/entities 等文档 | handoff 表完整文档；消费方的物化视图 | SESSION_PROJECTION 中唯一有界文档 | 新 handoff 表只存事件指针；读取物化，不另落一份文档 |
| Envelope L3 累计 | 之前未携带；可能的新快照与账本重叠 | MODEL_USAGE / BudgetLedger 查询 | 明确不增加快照，用现有 run_id 查询 |
| input/output/total/model | MODEL_USAGE、call_manifest、handoff.trace、checkpoint owner 累计 | 每次调用事实归 MODEL_USAGE；其他为统计/提交见证 | manifest/trace 不作为记账输入；账本每 call_id 结算替换预留一次 |
| budget.exceeded 的 stage/level | 观测 trace 与 MODEL_USAGE phase=budget | MODEL_USAGE 为恢复事实；trace 为观测 | 不新建第二种预算历史事件，不另计费用 |
| Pack.tokens/budget/source 数字、trace/dedup ID | Pack、入口 state manifest、调用日志 | 单次 Pack + 可观测投影 | 标明投影身份，不作为 Collect 来源；请求结束清理临时 manifest |
| completed_results.content / 原输入 / 实体 | 活跃 Envelope、私有 checkpoint、公开结果/用户消息 | 对应公开事实事件；Envelope 是临时协议视图，checkpoint 是当前恢复见证 | 不把完整 Envelope 再抄到 History；公开归档仅有界摘要/指针与完整载荷 digest |
| event.references、transfer_id、checkpoint_ref、task/run/session 标识 | 外壳索引、payload 关联、不同事件观察 | 引用或本事件的身份/见证 | 保留关联标识，不作为第二份业务正文或余额；未把 scope 字段混成一个 ID |

## 指针与生命周期

EventPointer = 长期 task_id + event_id + 可选非负 offset；offset 仅为位置提示，下钻返回完整事件。旧 Envelope/协议仍使用 HistoryPointer(kind,target_id) + 查询 task 参数，避免修改完整性摘要载荷字节。

JournalReader.resolve 验证存在性和所有权；transfer 进一步验证类型、session、canonical proposal 和 namespace。不存在、跨任务、非 proposal 或错版本诚实报错。时间查询要求带时区边界，按 event_seq 分页跨过过滤空页；不会因第一页不匹配而漏掉后续事件。所有读取只读，不调用模型、不写历史。

Pack 是单次调用视图；manifest 仅观测。Envelope 是转交视图，现有 checkpoint 保留执行见证。Journal 长期 append-only；checkpoint 可变工作状态，当前恢复需要两者配合。用户已确认本轮不实现 Journal 独立重建 checkpoint，也不反转现有保存/确认顺序。

## 验证

最终全量通过：**2332 passed、12 skipped、288 subtests passed，0 failures / 0 errors**，退出码 0。pytest 用时 804.72 秒（约 13 分 24 秒），统计插件用时 805.02 秒。185 个 warning 与 Phase 2 相同，未作为失败。相比 Phase 2 新增 16 个契约用例，未新增跳过。

分组验证：修正后的契约/预算/投影/历史/完整性/恢复 131 passed；PostgreSQL 故障与篡改 38 passed、2 个原有条件跳过；新增契约/预算/投影 47 passed；补齐拒绝回执后 160 passed；namespace 指针/实际迁移/迁移死亡恢复/篡改拒绝 21 passed。

完整套件后仅扩充 AST lint 的文件范围，加入 context_pointers.py 和 namespace migration 的新写入路径，未修改实现。扩充后的契约 lint、SDK 预算 lint 和 Phase 1 Source/入口测试再验证 **28 passed**。最终 17 个 Python 文件的 ruff check、Python compileall 通过；本任务 git diff --check 通过。未运行前端构建/视觉检查，因为没有前端改动。

首轮相关套件 256 passed、2 failed：旧进度测试期待 usage 副本；另一个用例发现最外层 request_scope 恢复旧 manifest。前者将同数值断言移到实际保存的 checkpoint，并增加无进度副本及同 run_id 断言；后者修复请求生命周期并新增嵌套/根作用域测试。完整性测试仅把读取适配为 JournalReader，摘要相等、SHA-256、指针锚点及先验后执断言全部保留。未删除或跳过测试。

新增 usage_ref 时的独立模块收集发现包初始化循环，已把 EventPointer 放入不依赖 long_horizon 初始化的 context_pointers.py；47 项和后续套件验证该导入路径。首次全量运行在代码复核发现拒绝回执/namespace proposal 仍有正文副本后主动中止，未将未完成结果计作验收；补齐指针、通过 160/21 项回归后重新运行固定代码版本的完整套件。

`contract-example.json` 为本地合成数据，展示原 Envelope、canonical proposal、v2 回执、无 usage 副本的进度、投影索引/归档和 call_manifest 的 settled 指针；未调用远程模型，不含真实用户文本或凭证。

| 验收项 | 验证证据 |
| --- | --- |
| Envelope 结构、缺必需字段、多余字段 | test_envelope_required_and_extra_fields_and_digest_shape；既有 test_handoff_contracts |
| Journal schema / scope | test_archive_and_progress_writer_reject_scope_mismatch_and_extra_fields；既有公开历史白名单/私有文本/字节/幂等测试 |
| 单一摘要与完整性锚点 | test_transfer_receipts_store_only_canonical_proposal_pointer；test_rejection_references_proposal_and_preserves_verdict_and_retry；原 payload integrity 全部断言 |
| namespace 指针及 lineage | test_migrated_proposal_references_original_summary_with_lineage_check；真实 PostgreSQL 显式迁移、迁移中死亡和源载荷篡改拒绝测试 |
| 投影唯一存储 / 不改变 L1 视图 | test_projection_index_contains_only_pointer_and_roundtrips_exact_view[v1/v2]；test_postgres_reference_archives_and_receipts_survive_reopen |
| 指针下钻与时间查询 | test_explicit_pointer_drilldown_and_time_query_cross_pages；跨任务、缺目标、错类型及错 run 测试 |
| 账本归属、旧 schema 重放 | test_progress_references_budget_without_changing_checkpoint_or_legacy；test_legacy_receipt_and_progress_replays_keep_original_immutable_content |
| 调用观测可下钻 | test_call_manifest_pointer_resolves_settlement_in_long_term_scope；manifest 缺字段/多字段/拆分/输出上限一致性测试 |
| Pack 生命周期 | test_outer_request_discards_stale_pack_but_nested_scope_restores_parent；Phase 2 manifest 测试原断言 |
| 预算旁路 / SDK 纪律 | 新 test_new_contract_paths_cannot_call_sdk_or_write_around_history_writer；原 Phase 1 Source AST、Phase 2 SDK accounted_call AST 与 schema/framing 预算测试 |

完整套件从 ai-service 目录执行，使用一次性 loopback PostgreSQL。可复现命令（URL 仅含测试凭证）：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -c "import os,sys,pytest;os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PERSISTENCE_BACKEND='sqlite',AI_CONTINUITY_STORE_MODE='sqlite',DATABASE_URL='postgresql://postgres:phase3-test-only@127.0.0.1:15467/moodflow_phase3_test',MEMORY_BACKEND='memory',CHECKPOINT_BACKEND='memory',JEV_ENABLED='false',DEEPEVAL_DISABLE_DOTENV='1',AI_RAG_RETRIEVAL_MODE='empty');sys.path.append('.venv-eval/Lib/site-packages');raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests','evals/tests','-q','--disable-warnings','--tb=no']))"
```

最终运行使用仅保留计数、失败/跳过 nodeid 和耗时的统计插件，写 verification-summary.json；不保存配置、错误上下文或原 prompt。测试期间不修改实现，避免父/子进程混用版本。

统计钩子的原始 passed=2620 包括 288 个 unittest subtest，已按实际 pytest 最终输出拆为 2332 + 288；JSON 保留原数和最终输出说明，不把 subtest 再算成新增测试。12 个跳过为既有 live JEV、开发数据库 smoke、Worldline opt-in、指定旧容器重启和真实 Qdrant 用例；本次新 PostgreSQL 存储、旧进程死亡恢复、迁移/篡改验证实际执行。没有因本任务删除、跳过或弱化原业务断言。

## 限制与提交

- 旧事件和旧 handoff 表内容不做追溯迁移；只读兼容，不能声称旧档案已经物理消除重复。
- Journal 独立恢复完整 checkpoint 未实现，按 West 确认为后续优化。本轮仍要求有效 checkpoint 与不可变 proposal 配合。
- 新投影归档接受既有 HistoryWriter 的 16 KiB 白名单门；读取保持原版本与内容，未调整投影 token 策略。
- 新 reader 兼容旧格式；旧版 reader 不能直接解析新 v2/v3 事件或投影指针索引。后续发布/回滚需要保留兼容 reader，本轮没有执行发布。
- 没有前端改动，不运行前端构建或视觉验证；未 push、部署、修改生产配置或数据库。
- 原有无关工作区文档/数据变更不纳入提交。
- PostgreSQL 验证使用一次性 `moodflow-context-phase3-test-20261005` 容器，仅监听 127.0.0.1:15467，使用现有 pgvector/pgvector:pg16 镜像；fixture 创建随机数据库并应用已有迁移。测试后已核对创建时的完整 ID 并移除该容器，也删除本轮临时统计驱动，没有清理其他容器或用户文件。

实现 commit：`56b1090ab62f373855a54d908235a11903aeb75b`（`feat: unify context pack and journal contracts`）。包含 17 个实现/测试 Python 文件及三份契约，共 20 个本任务文件；暂存前核对 status/diff，cached diff --check 通过。

本报告、最终脱敏统计和合成契约示例作为独立文档 commit，仍只包含本任务文件。没有 push、部署、改生产配置或改写已有 commit；工作区原有无关变更保留。

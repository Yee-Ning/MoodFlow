# Context Engineering V1 · Phase 2 Long-Horizon Context Continuity

## 1. 实际新增与复用模块

Phase 2 在 Phase 1 的单次调用 Context Engine 上增加持久化连续性，没有另建 Prompt 拼接器。

- 新增 `ai-service/app/agent/foundation/long_horizon/models.py`：History、Artifact、Handoff、Session 和 rollover 契约。
- 新增 `long_horizon/store.py`：标准库 SQLite 持久化；History、Artifact、Handoff 由数据库 trigger 保证不可更新、不可删除。
- 新增 `long_horizon/service.py`：事件追加、Artifact、Handoff 投影与预算、lineage、rollover、cold start、revalidation、drill-down 和 Phase 1 回注。
- 新增 `long_horizon/runtime.py`：生产运行时的持久化 service，数据库位于被忽略的 `ai-service/tmp/context_continuity.sqlite`。
- 扩展 `context_engineering.py`：增加 `handoff_projection/history_drilldown/artifact_projection/memory_source` 来源，仍由原 Selector、预算、压缩和 Assembler 处理；ContextTrace 增加 task/session/request 坐标。
- 扩展 Foundation state、adapter、四个 SubGraph 及 streaming runner：稳定 `thread_id` 作为长期 task ID，具体 `session_id` 作为 LangGraph 模型窗口。原有 RAG、Tool、Structured Output 和各系统 Prompt 保持原流程。

项目已有 LangGraph `InMemorySaver` 继续保存当前进程内的图 checkpoint。SQLite Continuity Store 保存跨 Session 的业务连续性；两者职责不同，且新 Session 不加载旧 checkpoint 的完整消息。

## 2. 数据契约

### HistoryEvent

字段为 `event_id/task_id/session_id/event_type/timestamp/node/source/payload|storage_ref/references/metadata/request_id`。支持 user/assistant message、route decision、structured output、tool call/result、retrieval、RAG evidence、artifact、checkpoint、handoff、revalidation、ContextTrace 和 error。事件只能追加；大型内容使用 `storage_ref`。

### ArtifactRecord

保存 `artifact_id/task_id/session_id/artifact_type/created_at/content_hash/payload/projection/metadata`。Handoff 和 Active Context 仅传 `artifact_ref + projection`，完整 payload 通过 `get_artifact(ref)` 精确读取。候选歌曲集测试保存 100 条大记录，但跨 Session 只携带数量与前三个 ID。

### HandoffProjection

严格 Pydantic schema 包含 task/session/handoff 身份、goal、summary、constraints、preferences、decisions、current stage、completed/pending/next actions、entities、key message refs、artifact refs、evidence refs、revalidation 字段与 last-known state。`constraint_refs` 和 `decision_refs` 将关键结论直接指回不可变事件。引用在持久化前验证目标存在且属于同一 task。

### SessionRecord

包含 `task_id/session_id/previous_session_id/parent_session_id/inherited_handoff_id/created_at/closed_at`。新 Session 必须显式继承一个已存在 Handoff；连续 rollover 会保持共同根 Session。

## 3. Rollover 条件与流程

`RolloverPolicy` 默认支持：

1. Active Context 达到 5200 token threshold；
2. 显式阶段 checkpoint；
3. 可配置的大型 Artifact 形成事件。

流程为：验证 draft 和全部 refs → 构建并验证预算化 Handoff → 追加 checkpoint → 持久化 Handoff → 追加 handoff event → 关闭旧 Session → 打开带 lineage 的新 Session。History 不裁剪或覆盖。运行时没有按固定轮数切换。

## 4. Handoff Budget

默认独立预算为 1200 个启发式 token。测试使用更严格的 300 token 预算：先缩短/删除 summary，再减少 key messages、completed steps、非关键 entity projection 和 preferences；goal、constraints、constraint refs、decisions、current stage、pending items 和 next actions不会静默删除。关键字段本身超过预算时直接失败。

连续 rollover 会从上一份 Handoff 的结构化字段与原始 refs 合并约束和决定，不使用 summary-of-summary 作为真相。3 次 rollover 测试确认第一 Session 的 constraint 及原始 event pointer 一直可解析。

## 5. Cold Start 与 Reality Revalidation

生产入口收到稳定 `thread_id` 后找到其 active Session，并以 `session_id` 作为 LangGraph checkpoint thread。Cold Start 只装载：Handoff、被引用的关键事件、Artifact projection、最新用户 query 和本次前端提供的播放器快照。

易变字段由 Handoff 的 `revalidate_fields` 指定。测试覆盖 `current_song_id/player_state/queue_size`：Session 1 的歌曲 ID 1 保留为 historical state，Session 2 收到歌曲 ID 2 和 paused 状态后写入独立 `reality_revalidation` 事件；Active Context 同时包含“当时值”和“当前值”。历史记录不会被当前快照覆盖。

## 6. Evidence Refs 与 History Drill-down

内部 API：

- `get_history_event(event_id)`
- `query_task_history(task_id, event_type/session_id/keyword/limit)`
- `get_artifact(ref)`
- `drill_down(task_id, pointers|filters)`

例如约束“不要 Live Version”的 `constraint_refs` 指向 Session 1 的 user-message event。Session 2 的 Handoff 即使因预算删除低价值 key-message 列表，仍能从 constraint/evidence ref 精确加载原文。下钻结果由 `build_active_context()` 作为 `history_drilldown` 再送入 Phase 1，因此仍会经过 selection、dedup、source budget、总预算和 compression。

## 7. Phase 1 与 Phase 2 的连接

Phase 2 只负责跨窗口存在：

`Immutable History → Handoff/Artifact refs → Session Lineage → Cold Start/Revalidation/Drill-down`

随后统一进入：

`ContextCollector → ContextPolicy → Dedup → Budget → Compression → ContextAssembler → ContextPack`

四个 SubGraph 默认 assistant 均接受 continuity source；WorldLine Provider 也将 continuity 与已有 SearchPlan/RAG Evidence 一起交给 Phase 1。Memory 只有空的 `memory_source` 接口，没有实现 consolidation 或三层 Memory。

## 8. 至少跨 3 Session 的 E2E Trace

测试 `test_three_rollovers_keep_original_constraint_and_pointer` 实际运行以下链路：

1. Session 1 追加 user message “永远不要 Live Version”。
2. 显式 checkpoint 生成 Handoff 1，constraint_ref 指向原始 event；关闭 Session 1，Session 2 继承 Handoff 1。
3. Session 2 追加 assistant stage event；Handoff 2 合并原始 constraint/ref 与当前 stage；关闭 Session 2，Session 3 继承 Handoff 2。
4. Session 3 再追加 stage event并 rollover；Session 4 指向 Session 3/Handoff 3，parent 仍为 Session 1。
5. 最终 Handoff 仍含最初 constraint，原始 Session 1 event 和 Handoff 1 均可读取。

生产入口集成测试进一步运行：Session 1 → handoff → Session 2 cold start → current song 1→2 revalidation → 新 `session_id` LangGraph window → 用户消息 →模型响应→助手消息追加。

Drill-down E2E 使用一条超长历史事件，回注后的 ContextPack 满足 `total_tokens <= budget`，并保存带 task/session 坐标的 ContextTrace event。

## 9. 测试与回归结果

- 新增 `tests/test_long_horizon_context.py`：覆盖约束跨 Session、精确 pointer、History 不变性、Artifact ref、歌曲重验、Phase 1 预算、lineage、Handoff 超预算优先级、3 次 rollover、trigger 和生产 runner 集成。
- Phase 2 与关键 Phase 1/四 SubGraph/API 定向回归：112 passed、4 subtests passed。
- 修改范围 Ruff：通过。
- `python -m compileall -q app`：通过。
- 全量 `python -m pytest -q tests`：841 passed、2 skipped、206 subtests passed。

## 10. 已知限制

- Token 仍使用项目既有的 Unicode 启发式计数器，需要按生产模型 tokenizer 校准。
- SQLite 适合当前单服务 V1；多进程高并发部署需要迁移到共享数据库并增加事务级 rollover 锁。当前单次 rollover 的各持久化步骤已按安全顺序执行，但不是一个跨全部步骤的单 SQL transaction。
- Runtime 自动记录 user/assistant、route 和结构化 task result；更细的原始 Tool/RAG 事件可通过统一 `record_event` 接口写入，但现有 SubGraph 输出没有公开所有内部 raw event，因此尚未自动持久化每一个内部步骤。
- LangGraph checkpoint 本身仍是进程内存储；跨 Session 恢复依赖新的 Handoff/History 闭环。进程崩溃时旧 Session 的图内瞬时 state 不恢复，但已经追加的 History/Artifact/Handoff 可恢复。
- 没有为 History 建语义向量索引；V1 按精确 ref、metadata/filter 和 keyword 查询。

## 11. Three-Layer Memory 下一阶段接口

Phase 1 已预留 `memory_source` Context 类型。未来 Memory 模块只能作为新的 source 输入 Collector，并继续受现有 Policy/Budget/Trace 管理。History 表示发生过什么，Handoff 表示任务做到哪里，Artifact 表示任务产生什么；本阶段没有实现 Working/Short-Term/Long-Term Memory 或 memory consolidation。

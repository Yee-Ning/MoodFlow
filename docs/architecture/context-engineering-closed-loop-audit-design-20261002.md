# 上下文工程闭环：代码审计与缺口设计

审计日期：2026-10-02。源码基线：`86e5c187d8ace0f7d4b3dd7c74b7f8cc4b660c23`。

本报告以本次用户定义的三个闭环为验收依据：单次调用、Session 切换、恢复。此前计划和架构文档用于理解背景；实现状态以当前源码和本次运行的既有测试为证据。本文仅交付审计和设计，没有实现下述新增接口、修改源码或测试、调整灰度配置。

文档放在 `docs/architecture/`：它讨论跨 Context、Continuity、Handoff 和 Checkpoint 的责任边界与协议，适合与现有 Handoff 架构文档一起维护。

## 1. 审计结论

**闭环已有主要基础设施，但尚未在生产调用路径上完整闭合。** `context_engineering.py` 覆盖 Collect、Select、Dedup、Token Budget、按需 Compress、Assemble 六步；独立的只追加 History、带预算的 Session 投影、Session Lineage、现实状态比对和历史下钻也都有实现。主要缺口是：生产入口没有自动换窗和按需下钻编排；Agent 转交摘要与 Session 进度投影没有接通；恢复没有结合现实变化与转交完整性做统一决策；部分模型调用没有直接使用预算后的最终输入。

“存在服务接口”和“生产请求完整接入”分别判定。已有能力不需要另建一套存储或图框架。

### 1.1 身份和存储层的含义

| 名称 | 当前含义 | 代码位置与关键对象 |
| --- | --- | --- |
| Conversation / thread | 用户会话入口标识；Handoff 执行使用 `handoff:v1:{thread_id}` 命名空间 | [streaming.py](../../ai-service/app/agent/foundation/streaming.py) `stream_foundation_assistant`、`invoke_foundation_assistant` |
| Continuity task | 同一 Conversation 内一个可跨多个 Session 的长期任务记录；不等同于一次请求的子任务 | [long_horizon/models.py](../../ai-service/app/agent/foundation/long_horizon/models.py) `TaskRecord`；[service.py](../../ai-service/app/agent/foundation/long_horizon/service.py) `start_task`、`open_conversation` |
| Continuity session | 上述长期任务的一扇上下文窗口；持有前序、父级和继承投影标识 | [models.py](../../ai-service/app/agent/foundation/long_horizon/models.py) `SessionRecord`（118 行） |
| RouteTask | 一次用户请求的顺序执行计划中的一个能力子任务 | [routing.py](../../ai-service/app/agent/foundation/routing.py) `RouteTask`；[handoff/state.py](../../ai-service/app/agent/handoff/state.py) `HandoffRun.pending_tasks` |
| HandoffRun | 一次请求的多 Agent 执行；保存原始输入、计划、结果、Agent、hops 和用量 | [handoff/state.py](../../ai-service/app/agent/handoff/state.py) `HandoffRun`（88 行） |
| LangGraph checkpoint | 图的执行快照及待执行节点；配置中的 `session_id` 映射到 checkpoint namespace | [checkpointing.py](../../ai-service/app/agent/checkpointing.py) `SessionScopedSaverMixin._storage_config` |
| Immutable History | 独立的事件账本及 Artifact、Session 投影；不是 `state.messages` | [models.py](../../ai-service/app/agent/foundation/long_horizon/models.py) `HistoryEvent`；[store.py](../../ai-service/app/agent/foundation/long_horizon/store.py) `SQLiteContinuityStore`；[postgres_store.py](../../ai-service/app/agent/foundation/long_horizon/postgres_store.py) `PostgresContinuityStore` |

`MainGraph.prepare_context` 当前主要做输入规范化与响应、路由字段初始化（[main_graph.py](../../ai-service/app/agent/foundation/main_graph.py) 80 行）；六步 Context 流程在各业务模型调用处执行。Handoff 图有自己的 `prepare`，每次新的输入会清空旧 run、生成新的 run_id 和 started_at（[handoff/graph.py](../../ai-service/app/agent/handoff/graph.py) 75 行）。因此 Session 谱系、Agent 转交链、图恢复三个概念不能互相替代。

### 1.2 闭环组件 × 状态 × 代码位置

| 闭环组件 | 状态 | 代码位置、关键函数 / 类及判定依据 |
| --- | --- | --- |
| Current Session 打开与注入 | 已实现 | [streaming.py](../../ai-service/app/agent/foundation/streaming.py) `_continuity_start`（75 行）、`_graph_config`（169 行）；[service.py](../../ai-service/app/agent/foundation/long_horizon/service.py) `open_conversation`、`open_task`。实际入口打开 active task/session，向图注入 continuity_context 和 session_id。 |
| Collect | 已实现 | [context_engineering.py](../../ai-service/app/agent/foundation/context_engineering.py) `ContextCollector.collect`（162 行）。采集系统指令、查询、消息、歌曲 / 业务状态、结构输出、Tool、RAG、Session 投影、历史 / Artifact 投影及长期记忆；保留成对 tool_call_id。 |
| Select | 已实现 | 同文件 `ContextSelector.build`（388 行）、`POLICIES`。按节点类型、必需项、优先级和相关性选择；`build_context_async` 可接入 JEV 选择器，之后仍执行确定性预算流程。 |
| Dedup | 已实现 | 同文件 `_dedup_keys`（278 行）、`ContextSelector.build`。归一化内容哈希及证据标识去重；必需项不因重复被丢弃。 |
| Token Budget：ContextPack | 已实现 | 同文件 `ContextPolicy.input_budget`（81 行）、`ContextSelector.build`。预留输出与安全余量，先限来源、再限总量；最终重新计算组装消息和 AI tool_calls 的估算 token；必需项超限报错。 |
| Compress if needed | 已实现 | 同文件 `_compress_song_xml`、`_compress_tool_json`、`_truncate`、`ContextSelector.build`。先做 Tool schema 投影、先丢低价值可选项，再抽取式压缩；保护引用与必需原文片段，不压缩系统指令、查询、结构输出和 Handoff 投影。 |
| Assemble | 已实现 | 同文件 `ContextAssembler.assemble`（349 行）。固定类型顺序、消息角色、Tool 配对和来源标签；返回 ContextPack.messages 与 trace。 |
| 预算输入实际用于所有模型调用 | 部分实现 | [GeneralChat](../../ai-service/app/agent/foundation/subgraphs/general_chat/assistant.py) `answer_general_chat`、[CurrentSong](../../ai-service/app/agent/foundation/subgraphs/current_song/assistant.py) `answer_current_song_task`、[ToolCalling](../../ai-service/app/agent/foundation/subgraphs/tool_calling/assistant.py) `answer_tool_calling_task` 和 [agents.py](../../ai-service/app/agent/handoff/agents.py) `choose_transfer` 等消费 pack.messages。WorldLine 的 [generation.py](../../ai-service/app/agent/foundation/subgraphs/worldline/generation.py) `ProviderWorldLineRuntime.generate` 计算 pack 后重组 payload、使用原 instructions 发请求，未整体采用 pack；[llm_routing/runtime.py](../../ai-service/app/agent/foundation/llm_routing/runtime.py) `ProviderLLMIntentRuntime.classify` 和 [analyzer.py](../../ai-service/app/rag/query_understanding/analyzer.py) `ExistingLLMQueryAnalysisRuntime.analyze` 直接构造结构化请求，没有经过这六步。 |
| 模型请求和 Run 额度核算 | 部分实现 | [llm/runtime.py](../../ai-service/app/llm/runtime.py) `RequestLedger`、`AccountedChatModel.ainvoke`、`accounted_call`；[llm/client.py](../../ai-service/app/llm/client.py) `_create_response`。Handoff scope 内计入完整请求 / Tool schema、输出和报告用量，但 scope 外不启用累计限额；ContextPack 仍使用 Unicode 启发式计数，并未计入全部 provider framing / schema。不是精确 tokenizer 保证。 |
| LLM → Tool / RAG → Structured Output | 已实现 | [structured_output/service.py](../../ai-service/app/agent/structured_output/service.py) `StructuredOutputService.validate/run`；[handoff/agents.py](../../ai-service/app/agent/handoff/agents.py) `execute_owned_task`；[explorer.py](../../ai-service/app/agent/handoff/explorer.py) `execute_exploration`。复用业务工具和 WorldLine 生成、校验边界；不意味着所有内部步骤都进入独立 History。 |
| Immutable History 模型与追加接口 | 已实现 | [models.py](../../ai-service/app/agent/foundation/long_horizon/models.py) `HistoryEvent`（34 行）、`HistoryPointer`、`ArtifactRecord`；[service.py](../../ai-service/app/agent/foundation/long_horizon/service.py) `record_event`（136 行）。payload / storage_ref 二选一；追加时校验 task/session 和跨任务引用。 |
| History / Artifact / Session 投影不可修改删除 | 已实现 | [store.py](../../ai-service/app/agent/foundation/long_horizon/store.py) 初始化触发器（111–122 行）；[001_agent_continuity.sql](../../ai-service/migrations/postgresql/001_agent_continuity.sql) `reject_agent_immutable_change` 及三个 `BEFORE UPDATE OR DELETE` 触发器（97 行起）。普通 UPDATE / DELETE 被数据库拒绝，不只依赖 frozen 模型。 |
| History 生产写入闭环及内容白名单 | 部分实现 | [streaming.py](../../ai-service/app/agent/foundation/streaming.py) `_record_result`（181 行）、两个请求入口：写 USER_MESSAGE、ROUTE_DECISION、STRUCTURED_OUTPUT、ASSISTANT_MESSAGE，大结果写 Artifact。TOOL_CALL / TOOL_RESULT / RETRIEVAL / RAG_EVIDENCE / ERROR 虽有枚举，未发现 app 中对应的独立事件写入调用。TaskResult 可能含来源 / 动作数据，但不能等同于逐步可追溯事件；无统一 scratchpad 排除与记录重放幂等门。 |
| Agent 转交摘要 → Immutable History | 缺失 | [handoff/tools.py](../../ai-service/app/agent/handoff/tools.py) `create_transfer_tools` / `execute_transfer` 只返回 Command 更新 run、messages、envelope；[messages.py](../../ai-service/app/agent/handoff/messages.py) `transfer_message_pair` 写内部消息，未调用 continuity.record_event。 [trace.py](../../ai-service/app/agent/handoff/trace.py) `TraceRecorder` 是日志 / 可选 sink，且不含摘要，不是摘要账本。 |
| Budgeted Handoff：Session 投影服务 | 已实现 | [models.py](../../ai-service/app/agent/foundation/long_horizon/models.py) `HandoffDraft/HandoffProjection`（84 / 106 行）；[service.py](../../ai-service/app/agent/foundation/long_horizon/service.py) `build_handoff`（201 行）、`RolloverPolicy`。已有 goal、constraints / refs、summary、decisions / refs、进度、待办和 Artifact refs；默认投影预算 1200，裁掉低价值字段，必需信息超预算报错。 |
| Session 投影：精确封装预算与 Agent 链维度 | 部分实现 | `build_handoff` 计数对象是 draft 字段，未计入随后新增的 handoff_id、task_id、session_id、created_at、token / dropped 元数据；`HandoffDraft` 无 last_agent、run_id、handoff_chain、转交阶段或 task_summary 聚合。 |
| Artifact / Refs：跨窗口传引用 | 部分实现 | [service.py](../../ai-service/app/agent/foundation/long_horizon/service.py) `create_artifact`、`build_handoff`、`build_active_context`；[streaming.py](../../ai-service/app/agent/foundation/streaming.py) `_record_result`。支持 ref 与摘要投影，但大结果阈值为序列化后 12000 字符；projection.content 未单独预算，cold_start 先取完整 ArtifactRecord。已有模式，缺少统一大小边界及按需载入契约。 |
| Session Lineage | 已实现 | `SessionRecord.previous_session_id / parent_session_id / inherited_handoff_id`；[service.py](../../ai-service/app/agent/foundation/long_horizon/service.py) `rollover`（240 行）；两种 store 的 `create_session` / `put_handoff` 验证引用；PostgreSQL 有同任务仅一个 active session 的唯一索引。 |
| 换窗事务与生产自动编排 | 部分实现 | PostgreSQL [postgres_store.py](../../ai-service/app/agent/foundation/long_horizon/postgres_store.py) `transaction`（52 行）支持原子 rollover；SQLite [store.py](../../ai-service/app/agent/foundation/long_horizon/store.py) `transaction`（157 行）仅 yield。`should_rollover`、`rollover` 已有，但 app 中没有生产入口调用，当前换窗依靠显式服务调用 / 测试。 |
| Reality Revalidation：历史 / 当前字段比对 | 部分实现 | [service.py](../../ai-service/app/agent/foundation/long_horizon/service.py) `cold_start`（272 行）比较 revalidate_fields 与 live_state，并写事件；生产 `_continuity_start` 传入 routing_context。未带权威来源、时间 / 版本 / unknown 状态，也没有据变化选择继续、重取或暂停的策略。无继承投影时直接返回，未核验中断 run。 |
| Reality Revalidation：转交完整性决策 | 缺失 | [handoff/state.py](../../ai-service/app/agent/handoff/state.py) `HandoffRun.validate_run` 已校验边、顺序、任务、计数与 owner；但没有融合 checkpoint、配对消息、历史事件和现实变化的 Recovery 决策层。 |
| History Drill-down：查询与引用解析 | 已实现 | [service.py](../../ai-service/app/agent/foundation/long_horizon/service.py) `drill_down`（306 行）、`query_task_history`、`build_active_context`（320 行）；两个 store `query_task_history`。支持 task、session、type、keyword 和指针；显式指针校验任务归属。 |
| 按需 Drill-down → 再进入六步 Context | 部分实现 | `_continuity_start` 会直接载入投影引用的 key_events / artifacts；`ContextCollector.collect` 将 key_events 注入 history_drilldown。但 app 生产入口未调用 drill_down / build_active_context；查询只按时间升序 LIMIT，缺少稳定游标、输入预算、变化触发计划；显式 pointers 分支不应用 limit。 |
| 同 Session 图执行恢复 | 部分实现 | [checkpointing.py](../../ai-service/app/agent/checkpointing.py) checkpoint saver；[test_handoff_network.py](../../ai-service/tests/test_handoff_network.py) `test_explorer_checkpoint_resume_keeps_two_hops_and_does_not_relay_twice` 使用 `ainvoke(None, config)` 证明继续 Explorer、不重复转交；[handoff_checkpoint_acceptance.py](../../ai-service/scripts/handoff_checkpoint_acceptance.py) 有 PostgreSQL 验证脚本。两个实际请求入口始终给新 initial_state，未提供这种恢复编排；跨 Session 恢复不能直接借用新 namespace。 |

### 1.3 “只增不改”及覆盖范围的明确回答

Immutable History **确实存在，普通数据库 UPDATE / DELETE 被阻止**。`TaskRecord`、`SessionRecord.closed_at` 的生命周期更新不属于事件账本改写。Pydantic frozen 不等于嵌套 dict 深度冻结；真正的持久化保证来自存储层触发器。这里的保证范围是正常应用写入与 SQL 更新 / 删除，不宣称抵抗数据库管理员关闭触发器、重建表等操作。

`BaseAgentState.messages` 使用 [add_messages reducer](../../ai-service/app/agent/foundation/state/base.py)，同 ID 消息可被替换，是运行态 / checkpoint 消息集合；[history.py](../../ai-service/app/agent/foundation/history.py) `select_recent_history` 是最近 12 条 / 12000 字符选择器。二者都不等于不可变事件账本。

转交消息已做一层推理隔离：`transfer_message_pair` 把 AIMessage.content 置空，保留调用与工具回执；`CompletedTaskSummary` 不复制任意 TaskResult.data；`task_view` 排除控制调用，现有测试也验证 private_scratchpad 不进入 HandoffEnvelope。**这不等于所有 Immutable History 写入已有统一排除保证**：`_record_result` 仍整体序列化 TaskResult，`record_event` 接收通用 payload / metadata，没有逐事件白名单。当前不存在把转交摘要纳入不可变历史的生产写入边界。

本次在 `ai-service/app` 搜索相关调用点：`should_rollover` 无服务外调用；`rollover` 无生产调用；`drill_down` 无生产调用；`build_active_context` 无服务外调用。已有测试显式调用它们，证明基础能力存在，不能证明自动闭环已经接通。

## 2. 缺口设计：复用现有模块，补上编排与契约

以下全部为**待实现设计**。保留已实现的六步处理、History 存储、Session Lineage、业务工具、Structured Output 和 Handoff 状态机；只扩展审计中“部分实现 / 缺失”的边界。不新增依赖，不改变三 Agent、四条边和业务能力归属。

### 2.1 模块划分与接口

下面路径为拟新增文件或现有文件的拟扩展点，当前不存在的文件不作代码证据。

| 模块 / 放置位置 | 接口（输入 → 输出） | 职责与接入点 |
| --- | --- | --- |
| `foundation/long_horizon/coordinator.py`：ContinuityCoordinator | `begin_call(identity, request_kind, live_snapshot, checkpoint_ref) → CallContinuityPlan`；`after_checkpoint(call, pack_stats, checkpoint_ref) → RolloverDecision` | 统一 JSON / SSE 入口；区分新请求、同 run 恢复、新 Session；调用现有 open / cold_start / rollover。仅在安全边界切窗。 |
| `foundation/long_horizon/history_writer.py`：HistoryWriter | `append_public_event(identity, event_key, event_kind, allowlisted_body, refs) → HistoryPointer` | 复用 record_event 与 store；统一白名单、大小限制、幂等和不可变纠正事件。记录不依赖 SSE 消费者是否仍连接。 |
| `handoff/continuity.py`：AgentTransferJournal / ProgressProjector | `observe_transfer(run_before, command_after, public_summary) → TransferReceipt`；`project_progress(session, event_cursor, projection_budget) → HandoffDraftV2` | 把服务端确认的转交语义写成事件；按已提交 run/hop 和已校验 TaskResult 聚合进度，不根据日志文案推断完成。接在 transfer Command 与 checkpoint 落盘确认边界。 |
| `foundation/long_horizon/revalidation.py`：RealityRevalidator | `revalidate(projection, live_snapshot, checkpoint_snapshot, refs) → RevalidationReport` | 来源 / 版本 / 时效核验、转交完整性、任务与动作状态核验；与 cold_start 比对合并，不再创建平行的 Session 系统。 |
| `foundation/long_horizon/recovery.py`：RecoveryPlanner | `decide(revalidation, checkpoint, journal, limits) → RecoveryPlan` | 决定继续目标 Agent、继续源 Agent、重新取事实、等待输入或结束；不自行发业务工具和新增授权。 |
| 现有 `service.py` / store：HistoryDrilldown 扩展 | `resolve_history(task_scope, cursor, pointer_set, filters, budget) → DrilldownPack` | 复用 drill_down，增加同任务授权、稳定分页、预算和惰性 Artifact 投影；只有明确需要时展开引用。 |
| 现有 `context_engineering.py` 与模型调用适配层 | `prepare_model_input(call_kind, sources, request_overhead, remaining_limits) → BudgetedModelInput` | 复用 Collector / Selector / Assembler；统一最终发送 payload 的预算核验，使 ContextPack 与 provider 请求一致。 |

协调器提供生命周期，不承接 Curator / Explorer 业务，不代替 MainGraph / HandoffGraph 执行。实际图仍负责路由与 checkpoint；HistoryWriter 记录已校验的公共事实。先把这些职责组织成少量可测试接口，后续是否拆成单独文件可随实现规模调整。

### 2.2 公共数据结构

| 数据结构 | 关键字段 | 不变量 |
| --- | --- | --- |
| `ContinuityIdentity` | conversation_id、continuity_task_id、session_id、request_id、graph_mode、run_id（可空）、route_task_id（可空） | 两种 task_id 显式区分；用户身份从服务端作用域取；foundation 与 handoff namespace 不混用。 |
| `CheckpointRef` | execution_thread_id、session_id、checkpoint_ns、checkpoint_id、graph_version、public_state_digest | 只引用快照；digest 基于许可的公共状态，不把完整 prompt / scratchpad 复制到历史。核验命名空间与源 Session。 |
| `AgentTransferSummaryV1`（本文语义上的 task_summary） | schema_version、transfer_id、run_id、hop_index、source_agent、target_agent、route_task_id、reason_code、candidate_summary、completed_result_refs、pending_task_refs、entity_refs、evidence_refs、summary_provenance | 服务端从原始计划、已校验结果和 accepted transition 组装。candidate_summary 兼容现有 context_summary 字符串，标记为非权威参考；无任意 data / scratchpad 字段。 |
| `TransferJournalEvent` | event_key、phase、transfer_id、summary_ref、checkpoint_ref、result_refs、timestamp、event_seq | phase 为 proposed / rejected / committed / target_started / target_finished / reconciled；只追加，每个阶段幂等。committed 要有实际持久化的执行快照佐证。 |
| `HandoffDraftV2 / HandoffProjectionV2` | 复用现有 goal、constraints / refs、summary、decisions / refs、completed / pending / next、artifact / evidence refs；增加 agent_progress、projection_version、source_event_cursor、source_checkpoint_ref、state_revision、counter_name | 新增字段使用 JSON 原始类型，明确 schema_version。新 reader 读取 V1 时标记转交维度 unknown，不能凭缺字段猜 owner；旧 reader 不直接读取 V2。 |
| `AgentProgress` | last_run_id、last_agent、run_status、last_route_task_id、recent_run_chain、accepted_hop_count、transfer_phase、pending_transfer_ref、run_state_ref、progress_refs、earlier_chain_refs | last_agent 来自最后已提交 run 状态；recent_run_chain 由 entry_agent + accepted hops 推导；摘要不成为另一个 owner 状态来源。 |
| `LiveSnapshot / StateObservation` | field、value、source、observed_at、revision、status（valid / changed / unknown）、scope | 客户端播放器状态、服务端认证、音乐服务事实、索引版本分别标来源；unknown 不等同于未变化，历史也不覆盖当前值。 |
| `RevalidationReport` | field_results、transfer_integrity、ref_integrity、checkpoint_integrity、budget_state、required_drilldown、invalidated_dependencies | 每个判断携带来源 / 原因和引用；真实可用性与历史宣称分开。 |
| `RecoveryPlan` | action、resume_checkpoint_ref、resume_agent、run_id、remaining_deadline、required_reads、blocked_reason、evidence_refs | action 为 resume_target / resume_source / refresh_facts / needs_input / stop；同 run 恢复沿用原 run / limits / usage / hops。 |
| `DrilldownPack` | typed_items、resolved_refs、missing_refs、next_cursor、tokens、budget、truncated、reason | 查询和指针展开都受 count / token / byte 上限；大对象默认返回投影，完整内容不直接塞回模型。 |

现有 store 只接受合法引用。新增 event_key 唯一性和 event_seq 排序信息属于拟议迁移：event_key 用请求身份、run、transfer_id、阶段 / 结果版本确定性生成；同 key 同内容返回原 receipt，同 key 不同内容拒绝并另写冲突事件，不 UPDATE 旧事件。event_seq 采用任务范围内可稳定排序的序号或等价游标，不用随机 UUID 推断因果顺序。

### 2.3 对接点一：Agent task_summary 聚合为 Session Summary Progress

当前 transfer tool 的实际参数是 `reason + context_summary`；这是有限字段的结构化工具调用，context_summary 本身仍是字符串。设计中的 task_summary 是上表的**服务端结构化投影**，不直接把模型摘要升级为事实或授权，也不要求立即重命名现有工具参数。

聚合输入限定为上一投影、该 Session 新增的已提交转交事件、已校验公共 TaskResult、用户约束 / 决策事件和 Artifact refs。

聚合规则：

1. 按 run 和 hop_index 对齐转交；accepted 的 hop 链与任务进度由 HandoffRun 校验结果提供。proposed / rejected 可留审计引用，不能算入 accepted_hop_count、handoff_chain 或完成步骤。
2. 同一个 RouteTask 的完成依据是成功的已校验 TaskResult；“交给 Explorer 调查”只表示待办所有权变化，不表示已经完成调查。失败 / needs_input 保留状态与未完成内容。
3. 同一事实 / 决策按事件引用或实体与版本去重；更正用新事件引用 supersedes / invalidates。旧 constraints / decisions 不无限机械拼接：用户明确撤销的约束保留历史引用，在当前投影标记失效，防止 `_inherit` 长期带入已撤销约束。
4. 将已完成工作、证据来源、当前步骤、剩余工作写入 summary / completed_steps / pending_items / next_actions；保留关键决策引用。模型摘要只能辅助表达，不能补造完成、实体、结果或新的授权。
5. Session 投影必须写明 **last_agent、last_run_id、run_status、recent_run_chain、transfer_phase**。摘要示例：“Curator 已完成目录查询；G→C→E 的转交已提交，Explorer 尚未开始；目录结果见 artifact ref，下一步核验目标实体后继续探索。”
6. 一个 Session 可包含多个 run；内联完整记录最近 run 的链（现有最多 5 个 accepted hops），早期 run 链放入不可变进度 Artifact 并传引用，保留范围与数量。不能把最后一个 run 的链称为整段 Session 的全部历史；进度 refs 可下钻恢复全部链。

`agent_progress` 是供新 Session 理解的投影；恢复执行的权威 owner 仍是经核验的 checkpoint / HandoffRun，而不是 summary 文本。

### 2.4 Budgeted Handoff 与 Artifact 边界

复用默认 `handoff_token_budget=1200`，但以**真正发送的完整投影封装**计数，记录 counter_name、token_count、budget 和裁剪原因。设计初始分配可按以下上限预留，后续依据实际测量配置；不宣称对应 provider 的精确 token。

| 投影部分 | 初始预留 | 保留策略 |
| --- | --- | --- |
| Goal / Constraints / 当前授权边界 | 300 | 目标、有效约束及关键引用必须保留，不截断改变语义。 |
| Summary Progress / 当前步骤与待办 | 300 | 已完成和未完成明确分开；详细结果改为 Artifact refs。 |
| Agent ownership / 最近转交链与恢复状态 | 200 | 当前 owner、链、阶段和关联 run / checkpoint refs 必须保留。 |
| Key Messages / Decisions 的引用 | 160 | 关键决策与约束依据先保留，其他可延迟下钻。 |
| Artifact / Evidence refs 与版本 | 120 | 传 ref、类型、hash / 版本及极小描述，不传候选列表、图全文或原始工具 JSON。 |
| 封装、标识与计数安全余量 | 120 | 最终计数必须覆盖封装，计数字段变化也要做最终复核。 |

上述配额可调配，但总预算不变；必需字段本身超限时不能默默删约束或链。优先把详细完成记录移入 Artifact，仍超限则返回 projection_over_budget，保持原 Session，走明确的输入精简 / 配置处理路径。

`ArtifactRecord` 保留不可变 payload、hash 和引用能力。增加按类型 projection 的允许字段及 token / 字节上限；缺少安全投影的内容只保留引用。引用解析先读 metadata / projection，确需完整内容时按 slice / field 有预算展开，并记录原因。完整对象可被业务工具按引用读取，但不会因 Session 切换自动进入 LLM。

ContextPack 输入预算、Session 投影预算、RequestLedger 单 run 累计限额分别核算。换窗减少上下文长度，不为正在恢复的 run 清零 token、retry、handoff_count 或 started_at。现有默认 agent / run 额度为 16000 / 32000，超时为 45 / 120 秒；沿用现有限制或显式配置，不借换窗规避限制。

### 2.5 对接点二：Reality Revalidation 与转交完整性恢复

恢复分两种情况：**恢复中断的同一 run**，以及**在新 Session 开始后续新请求**。后续新请求可以按当前输入重新路由；中断 run 只能从已核验 checkpoint 继续，不能重新传 initial_state 进入 prepare。对外恢复接口应由服务端身份范围约束，使用引用 / resume token 定位 run，不能接受客户端指定任意 Agent 或替换原计划。

Reality Revalidation 先检查来源、时间 / 版本、引用归属和完整性，再检查转交。覆盖至少这些现实维度：

| 现实维度 | 核验方法与失效影响 |
| --- | --- |
| 播放器 / 当前歌曲 / 队列 | 读取带观察时间的当前客户端状态和已有动作 ACK；有服务端可核验歌曲事实时补取。客户端快照标注来源，缺 ACK / 过期为 unknown，不把发出 action 当作已播放。代词任务若尚未绑定且当前歌曲改变，要重新取上下文；若原任务明确绑定歌曲 ID，保持原对象，仅重新核验可用性。 |
| 登录、用户作用域、写操作资格 | 从当前服务端认证上下文 / 音乐服务会话核验，不继承旧 cookie / token，不把摘要当作写权限；未知资格阻止相关写操作。 |
| 实体 / 引用 | 核验 catalog ID 与 graph ID 的类型、引用 task 归属、Artifact hash、资源仍可用。音乐图节点不能直接当可播放歌曲，复用现有 catalog 搜索边界。 |
| 检索事实与索引版本 | 对比来源 / release / index 版本和证据 hash；变化只使依赖该证据的未完成工作失效，按需重新检索，旧证据作为历史保留。拿不到版本时记 unknown。 |
| 转交 / 执行 | 联合检查 run、原计划、hops、current_agent、accepted_count、pending prefix、checkpoint 的 next / pending writes、转交调用与回执配对、journal receipts。 |
| 时间 / 预算 / 重试 | 从原 started_at、usage、retry 和 RunLimits 计算剩余额度；过期则结束旧 run。用户发起新的续办请求可产生新 run，但旧结果与链保留为历史，不能伪装成原 run 无损继续。 |

只有在 checkpoint 与 journal 中一致的已提交状态才算交接完成。参考决策规则如下，特别覆盖 Curator→Explorer 断线：

| 断线时的可靠证据 | 恢复决策 | 是否允许回退 / 重做 |
| --- | --- | --- |
| 尚未有 accepted 转交；source checkpoint 完整，源 Agent 的原任务仍有效 | resume_source；从 Curator 的可靠节点继续，重新提议时仍过现有 contract / guard / budget | 允许继续未提交的源状态；proposed 不算转交，无已完成动作时可重新提议。 |
| guard 已审核，但 Command / checkpoint 尚未确认 accepted hop；source checkpoint 未变化 | journal 记为未提交 / rejected 或经对账仍未提交；resume_source | guard 的 allowed 本身不构成 owner 转移；不直接跳 Explorer。若提交状态不确定先核对，不能猜“尚未提交”。 |
| checkpoint 已有 C→E hop、owner=Explorer、envelope 一致、AI / Tool 消息配对完整，next 指向 Explorer；现实与预算有效 | resume_target，使用源 Session 的 checkpoint 配置和空输入继续 Explorer；保持原 run_id、链和 usage | 不重发 transfer，不删除 accepted hop，不重新路由。 |
| 上述 checkpoint 完整，但 committed journal receipt 缺失 | 先幂等补写 reconciled / committed receipt，再 resume_target | 只补历史记录，不重复转交；来源是核验过的持久化快照。 |
| journal 声称 committed，但 checkpoint 仍是源状态或缺失 / 不匹配 | 先受限下钻并对账；不能定位可靠提交状态则 needs_input，记录 integrity_conflict | 不凭摘要跳目标，也不盲目回滚；已发生但无可恢复快照的外部动作不能重做。 |
| Explorer 已开始、尚无结果；可信 checkpoint 指向可恢复节点，所有已执行操作为只读且无未知写效果 | 从 checkpoint 的待执行节点继续，必要时重新取只读事实；结果写入幂等 | 只读工作可按现有重试规则重取；不回退至 Curator 重交。 |
| Explorer 已完成，TaskResult / result receipt 有效，下一节点未开始 | 恢复图的下一节点 / final_response；缺结果历史 receipt 时先补写 | 不重新探索；同一结果不能重复累加到 task_results。 |
| 当前事实变化，影响尚未完成任务；链本身一致 | refresh_facts，记录失效依赖，再由合法 owner 继续；变化导致目标歧义 / 原授权不足时 needs_input | 不改旧 History / 原计划；不能借变化自动执行新的业务意图。 |
| 已发写操作或客户端 action，但提交 / ACK 状态不明 | 优先只读查询 / 查询 ACK；无法确认则 needs_input；若已有 operation_id / action_id 幂等保证，可依该协议处理 | 禁止无条件重发播放、歌单变更等；“未收到结果”不等于“未执行”。 |
| 未配对控制消息、非法链、跨任务引用、run 超时 / 额度耗尽 | 完整性问题先下钻定位；可恢复性无法证明则 stop / needs_input；额度耗尽依现有停止规则结束 | 不删 hop，不清零计数，不制造新的授权或默认 GeneralChat 接管业务写操作。 |

转交完整性的最低校验集合：source 与前序 owner 一致；目标属于四条合法边且通向下一能力 owner；hop 对应同一 run、任务和进度；call_id / transfer_id 唯一；消息配对；envelope 的 active / pending 与 run 一致；current_agent 等于链尾；checkpoint 待执行节点与运行状态一致。已有 `HandoffRun.validate_run` 与 `parse_transfer_call` / `transfer_message_pair` 继续负责各自校验，新增层负责跨存储的核对。

所谓“回退”仅限继续**证据确认尚未提交**的源 checkpoint，不回写历史、不删除已接受链。断线本身不自动触发回退。结构一致但 reality unknown 的状态也不能被当作已核验有效。

### 2.6 History Drill-down 与恢复后的 Single-Call

下钻由明确原因触发：转交提交对账、原约束 / 决策语义不足、现实变化影响任务、引用缺失 / 版本不一致、需要大 Artifact 的指定片段。普通后续请求从预算后的 Session 投影和近期消息开始，无需全量读取所有历史。

优先级为：明确指针 → 相同 run / transfer / route_task 的邻近事件 → 同 Session 关键事件 → 同 continuity task 的相关事件。查询使用类型化字段和 event_seq 游标，不用全文 LIKE 的最早 N 条承担恢复决策。建议初始单次下钻上限 20 个事件、800 估算 token，并受当前 ContextPolicy / ledger 剩余预算再次约束；不足时返回 truncated、missing_refs 和 next_cursor。

数据权限校验覆盖用户 / 会话作用域、continuity task、session、run 与引用目标；即使直接调用 get_artifact，也通过统一 resolver 核验，不只依赖“先前 build_handoff 验证过”。跨 scope 引用不能进入模型。预算限制同时应用于搜索分支和显式 pointers 分支；拒绝无限 pointers 列表。复杂转交对账在确定性代码层完成，不能为了给模型讲清楚而暴露完整 checkpoint。

把核验后的当前事实、带来源的历史事实、changed / unknown 结果和允许的 drilldown / artifact 投影交给现有 Collector → Selector → Dedup → Budget → Compress → Assembler。当前现实字段优先；历史用于解释和追溯。将 revalidation 元数据真正纳入上下文来源契约，避免 `_continuity_start.changed_fields` 被 Collector 忽略；同时禁止把历史摘要以系统指令方式授予执行权限。

### 2.7 对接点三：Immutable History 写入边界

| 进入不可变历史 | 内容边界 / 写入时机 |
| --- | --- |
| 用户请求和用户明确决策 / 约束 | 用户输入事件、语义来源引用；按请求幂等记录，不记录认证信息。用户撤销 / 更正通过新事件表示。 |
| 已校验路由计划、公共 TaskResult、最终可见答复 | 只存各类型允许字段；大结果先写安全 Artifact，以 ref 表达；最终答复不等待 SSE 客户端连接才记录。 |
| 转交摘要 task_summary | 写入服务端组装的 AgentTransferSummaryV1；context_summary 作为限长的非权威参考字段；记录转交阶段、双方、任务、进度、引用。committed 与 proposed / rejected 区分，不能仅凭模型调用算交接完成。 |
| 工具调用 / 返回与动作状态 | 白名单工具名、必要公共参数、operation_id / action_id、status、结果引用；成功 / 失败 / ACK 分开。认证头、cookie 和原始 HTTP 请求不进。 |
| 检索与证据使用 | query / entity 的必要公共投影、索引 / release 版本、source / chunk IDs、证据 hash / refs 和采用结果；大段原文在受控 Artifact 中按需取。 |
| Session 投影、Lineage 关联、现实核验、恢复决定 | 投影引用、前后 Session、CheckpointRef、有效 / 变化 / unknown 和原因；不复制执行快照全文。 |
| Context Trace 与公共错误 | 选择 / 丢弃 / 压缩原因、token / budget、来源标识、公共错误码；不记录输入全文、异常对象 repr、私有 prompt 或秘密配置。 |

| 不进入不可变历史 | 处理原则 |
| --- | --- |
| Agent 中间推理、scratchpad、隐藏思考 / reasoning 文本 | 不落 Immutable History，也不通过 summary / metadata / Artifact 旁路保存。 |
| 未过滤 AIMessage.content、模型原始响应、完整隐藏 prompt、guard 原始评判文本 | 转交仅保留许可摘要 / 控制语义，guard 只留状态与必要分数 / 版本；面向用户的最终文本经过公共输出边界再记录。 |
| API Key、Token、cookie、密码、私钥、请求认证头 | writer 入口按类型排除，禁止通用 metadata / data 全量序列化旁路。 |
| 任意 TaskResult.data、raw tool JSON、完整 graph / 候选 / lyrics 默认副本 | 先按业务类型白名单与大小预算投影；业务确需保留的大内容才存允许的 Artifact payload，scratchpad 即使放在其中仍禁止。 |
| “工具调用成功所以一定已播放”等无证据推断 | 记录请求 / 回执事实，完成状态需要对应结果 / ACK，不虚构业务完成。 |

写入位置在图的语义事件 / checkpoint 确认边界和业务工具公共结果边界。SSE / JSON 只是传输适配层，不能成为账本唯一写入责任人；消费者断线后账本仍应记录已提交状态。通用 record_event 作为底层插入能力保留，生产调用通过 HistoryWriter 的类型化接口，防止字段白名单被绕过。

Checkpoint 可以保留恢复必需的控制调用、配对回执和结构化运行状态，属于独立的内部执行存储；现有 checkpoint 不作为不含私有数据的保证。后续应按最小恢复状态审查它，但不能把 checkpoint 导出内容混入 Immutable History 或公开 SSE。该区分不会通过“checkpoint 也是历史”扩大 scratchpad 的写入许可。

### 2.8 持久化、幂等和换窗安全边界

Agent 转交涉及图 checkpoint 与 Continuity 两套持久化调用，即便都使用 PostgreSQL，当前也**没有共享同一事务连接的保证**。设计采用可对账的追加 journal / receipt 协议：

1. 记录经过筛选的 proposed；guard 与现有纯状态转移产生候选 Command，尚不宣称已持久化转交。
2. 图保存含 accepted hop、owner、paired messages 和下一节点的快照；保存确认之后用确定性 event_key 写 committed receipt 和 summary ref。
3. 崩溃窗口内缺 receipt 时，以完整 checkpoint 对账并追加 reconciled 事件；不能依据一个提前写入的 accepted 日志直接执行目标 Agent。任何 proposed / receipt 和 checkpoint 冲突都按第 2.5 节处理。
4. receipt 补写、结果事件和最终答复事件可重复调用但只返回同一已提交事件；关闭 Session 前确认需要继承的事件都已持久化。并发同 conversation/session/run 使用短期执行租约或行锁和版本比较，防止两个请求同时推进 / rollover。

首轮自动换窗只在**请求完成 / 明确安全 checkpoint、无未确认写效果、转交记录已对账**的边界进行。中途断线先在原 Session 恢复；pending run 不被自动挪到新 checkpoint namespace。确需跨窗续办时先生成冻结的 run_state_ref 和 source_checkpoint_ref，做显式恢复迁移校验，不能把旧状态随意注入新 prepare。

Coordinator 从实际 ContextPack / 最终请求统计取得 active_context_tokens，复用 should_rollover 阈值；不得拿累计计费 token 代替当前窗口长度。指标应包括预算前候选规模和裁剪压力，避免只观察被强制压到预算内的 token 数而永不触发换窗。已达停止额度的 run 先按现有规则结束，再谈新的后续请求。

复用 PostgreSQL rollover 事务，将投影、关闭旧 Session、创建新 Session 和 lineage 关联原子提交，并以幂等 rollover key 防止重复切窗。SQLite 目前 transaction 是兼容占位：后续实现必须提供真实事务与 active-session 并发约束，或明确禁用该后端的自动换窗，不能把测试后端的多步写入当作原子切换。

### 2.9 Single-Call 剩余缺口的最小修补设计

不重写六步算法。将所有模型调用分为业务回答、控制转交、路由分类、检索查询分析和生成；每类复用合适的 ContextPolicy，短分类请求可以采用最小必需来源，而不是加入整段会话。

WorldLine provider 应直接消费已预算的模型输入，或通过适配器将 pack 按相同内容无损转换为实际 payload，再核验完整请求。不得计算 pack 后用原始大 payload 和原 instructions 绕过已组装输入。统一的最终输入预算要覆盖消息、instructions、Tool / 输出 schema、封装开销与输出预留；记录 heuristic / tokenizer 名称和实际 provider usage 的差异，仍用现有 RequestLedger 管理 Handoff 总额。

为 required Handoff 投影、结构输出和当前状态分别设置可解释上限。必需项超限继续明确失败 / 请求精简，不通过截断约束、丢工具配对或改变公共结构来塞入模型。

## 3. 与现有 Handoff 的冲突 / 集成注意点

| 当前代码 / 约定 | 冲突或风险 | 设计处理 |
| --- | --- | --- |
| [contracts.py](../../ai-service/app/agent/handoff/contracts.py) `TransferArguments` 只有 reason / context_summary，extra=forbid | 背景称 task_summary，不代表当前已有同名结构对象；直接新增模型工具字段会破坏严格契约 | 外部参数先兼容，服务端构造 AgentTransferSummary；需未来升级工具 schema 时显式版本化。 |
| [state.py](../../ai-service/app/agent/handoff/state.py) handoff_chain / handoff_count 从 hops 派生 | Session 投影若另存可修改 owner / count，可能与实际链矛盾 | 投影只读、来自校验过的状态；不把投影字段加成另一个 run reducer / 状态源。 |
| [models.py](../../ai-service/app/agent/foundation/long_horizon/models.py) `HistoryEventType.HANDOFF` 当前由 rollover 使用 | Agent 交接和 Session 交接混用同一个事件名会造成统计 / 恢复误判 | 增加明确 agent_transfer 与 session_handoff 语义 / 版本；旧 HANDOFF 按 node=rollover 兼容读取。 |
| `HandoffEnvelope` 限制 result summary / entities；[context.py](../../ai-service/app/agent/handoff/context.py) exploration_projection 显式不授予播放权限 | Session 汇总若复制 TaskResult.data / 图节点并当作歌曲 ID，扩大授权或丢业务边界 | 复用同一类型白名单、目录实体解析和当前用户授权；投影 / refs 不授予工具资格。 |
| [agents.py](../../ai-service/app/agent/handoff/agents.py) `task_view` 将 `continuity['handoff']` 包装为 window_handoff + completed_exploration | 单个 handoff 字典同时承载 Session 投影和同 run Explorer 结果，schema 和预算易混淆 | 后续拆成明确 session_projection / agent_progress / business_refs 来源，保留兼容读取适配，分别预算。 |
| [streaming.py](../../ai-service/app/agent/foundation/streaming.py) History 写入依赖流循环 / invoke 返回 | SSE 断连和 JSON 中途错误可能留下输入，缺提交记录 / 最终记录；重试可能产生重复事件 | 账本写入下移到语义边界并幂等，传输层不二次累计结果；记录失败 / 未确认状态。 |
| `_continuity_start` 返回 changed_fields，但 Collector 没有消费它 | 有现实比对事件，不代表 LLM 能收到“旧状态已变化” | 类型化 revalidation 来源进入 pack，当前 / 历史 / unknown 明确分开。 |
| `prepare` 每个新输入重置 run；SessionScopedSaver 将 session_id 作为 namespace | 用新 Session / 新 initial_state 恢复会启动新的执行，可能重复业务 / 转交 | 同 run 用原 namespace 与空输入恢复；跨窗必须显式 checkpoint 关联与迁移决策。 |
| [llm/runtime.py](../../ai-service/app/llm/runtime.py) RequestLedger 与 HandoffRun usage / deadline | 换窗把用量清零或刷新 started_at 会绕过已实现的保障 | 投影与 run 额度独立；恢复保留 usage、retries、started_at 和 limits。 |
| `build_handoff` 的预算仅计 draft；[generation.py](../../ai-service/app/agent/foundation/subgraphs/worldline/generation.py) pack 后重组请求 | 声称“有预算”但最终封装可能更大 | 计完整投影与最终 provider 输入；保留原必需项保护。 |
| frozen / extra=forbid 模型、严格 checkpoint serializer；handoff_run 使用 JSON primitives | 直接将新 Pydantic 类塞入旧 checkpoint 可能拒绝反序列化；旧 reader 拒绝 V2 多余字段 | 扩展数据继续使用 JSON primitives；引入 schema_version 和兼容 reader，避免无必要扩大反序列化白名单。 |
| PostgreSQL 原子 rollover，SQLite transaction 为 no-op；checkpoint saver 为另一持久化边界 | 把几个“写成功”当作跨边界原子提交会遗漏断线窗口 | 使用追加 journal 对账；SQLite 补事务或禁用自动换窗；PostgreSQL 也不能默认与 saver 共事务。 |
| [rollout.py](../../ai-service/app/agent/handoff/rollout.py) 按 thread 固定切流；两个图的执行 namespace 不同 | 切换模型图不自动带来 Session Lineage 或旧 checkpoint 迁移 | 保持会话固定 graph_mode；将图版本写入 refs。显式迁移需独立流程；本文不启用灰度、不改现有配置。 |

以上以集成约束为主，不要求改变业务网络拓扑。最核心的冲突是同名 Handoff 表达两种生命周期，以及运行态消息 / 账本 / Session 投影各有不同的一致性边界。

## 4. 后续实现验收设计（本次不新增测试）

| 验收路径 | 必须证明的结果 |
| --- | --- |
| 一般单次调用与长工具 / RAG 输入 | 六步 trace 完整；最终真正发送内容在预算内；工具配对和必需引用不丢。 |
| GeneralChat→Curator→Explorer→Curator 组合路径后换窗 | summary 仅聚合已完成结果；记录最后 owner、完整最近 run 链、未完成任务与结果引用；新 Session 可按引用核对。 |
| Session 投影必须信息超预算 | 明确失败且旧 Session 仍 active；约束、授权边界和转交链不静默删除。 |
| 同一 rollover 重放 / 并发执行 | 只有一个有效新 Session；无重复 committed / result 事件；没有部分关闭旧窗。 |
| C→E 提议前、guard 后、checkpoint 后、receipt 前、Explorer 结果后逐点断线 | 按第 2.5 节恢复；accepted transfer 不重复，未提交转交不凭空跳目标；结果不重复累加。 |
| 恢复时歌曲变化 / 索引版本变化 / 权限不明 | changed / unknown 进入最终 Context；只使相关依赖失效，写操作不被历史摘要授权。 |
| 缺历史 receipt、坏链、孤立 ToolMessage、跨任务 ref、missing checkpoint | 可证明缺记录时补写；无法证明时停止 / 请求输入；不删旧事件或伪造 owner。 |
| 历史 / Artifact 写入携带 scratchpad 或秘密字段 | writer 类型白名单拒绝 / 排除；不经 metadata / projection 旁路入库；正常公共摘要仍可追溯。 |
| 显式 pointers 与搜索下钻 | 都有 count / token / byte 限额和稳定游标；大 Artifact 只按需展开；超限不全量灌入模型。 |
| 恢复 run 已过 120 秒或额度耗尽 | 按原限制终止；换窗不重置额度；新的续办请求与旧 run 明确关联而非伪装无损恢复。 |
| JSON / SSE 输出兼容、客户端中断 | 公共响应协议与既有调用方兼容；账本记录不依赖 SSE 是否被继续消费。 |

## 5. 本次验证、限制与交付范围

本次运行已有测试，未新增、跳过或弱化测试。使用仓库此前已有的 `.dev/handoff-repair-20261001/offline.py` 进行隔离验证：memory checkpoint、临时 SQLite 测试、禁用长期记忆与真实 JEV 调用、占位 LLM 地址。进程显式覆盖上述运行参数；审计未打开或输出凭据文件，未修改 `.env` 或实际灰度开关。应用 Settings 仍有既有 env_file 加载机制，不能把参数覆盖宣称为阻止全部底层文件读取。

运行命令（从仓库根目录）：

`ai-service/.venv/Scripts/python.exe .dev/handoff-repair-20261001/offline.py -m pytest -q tests/test_context_engineering.py tests/test_long_horizon_context.py tests/test_foundation_streaming.py tests/test_handoff_contracts.py tests/test_handoff_network.py tests/test_handoff_checkpoint_acceptance.py`

结果：**183 passed，35.98 秒，退出码 0**。覆盖既有单次 Context 选择 / 超限 / 配对、SQLite 不可变保护、Session 投影 / 谱系 / 状态比对 / 下钻、流输出、严格 Handoff 契约、网络和同 checkpoint 的 Explorer 恢复。测试通过只证明当前被测基础行为，不证明本文拟议闭环已经实现。

交付前检查文档相对链接、代码符号 / 行号、Markdown 表格、本文范围的 `git diff --check` 与提交范围。只新增本 Markdown 文档；此前未提交文档改动、移动文件和数据目录保留。全仓库 `git diff --check` 检出原有 `ai-service/evals/reports/gold_review_v0_blind_pack.md:26` 的行尾空白，属于任务开始前已有修改，本次不处理、不提交；本文单文件检查单独记录。

限制：本次没有重跑真实 PostgreSQL 重启 / 断电恢复、真实 LLM / JEV / 音乐服务、索引版本变化及客户端动作 ACK 联调；因此 PostgreSQL 触发器 / 事务依据源码，完整恢复策略依据设计，不宣称已通过真实故障注入。未做前端构建 / 视觉验收或全库构建：本次无源码 / UI 改动，交付是架构文档。没有环境失败阻止本次文档交付；上述实时验证属于未执行项，需要在后续实现验收中完成。

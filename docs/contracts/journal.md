# Journal / Immutable History 契约

版本：既有 HistoryEvent 外壳；新增引用回执 v2、迁移 proposal 指针 v3 和进度用量引用 v2。所有新公开写入仍经过 HistoryWriter 白名单。

## 身份与外壳

| 字段 | 约束 / 含义 |
| --- | --- |
| event_id | 事件标识；HistoryWriter 为 evt:SHA-256 |
| timestamp | 写入时间；Writer 使用 UTC |
| task_id | 长期 continuity_task_id，跨会话档案作用域 |
| session_id | 会话 / checkpoint namespace |
| event_type | HistoryEventType 枚举 |
| node / source | 写入来源；公开 Writer 为 public_history / typed_writer:v1 |
| payload / storage_ref | 恰好一个：白名单 DTO / 外部存储引用 |
| references | 经存在性和 task 所有权校验的指针；从 payload 索引产生，不是内容副本 |
| metadata | 既有元数据；公开 Writer 不接受任意模型状态 |
| request_id | 可选传输请求 ID；重试不改变首条回执 |
| event_key | 幂等键；公开 Writer 必须生成 |
| event_seq | store 分配的正整数游标；按 task 排序 |

agent 位于对应 payload（如 PublicUsage.agent、summary.source_agent/target_agent），不新增外壳 agent。预算接口的 task_id 实际是原始 run_id；History.task_id 是长期任务。scope 必须分别传递和校验，禁止混用。

## payload 白名单

| event_type | DTO / 关键字段 |
| --- | --- |
| USER_MESSAGE / ASSISTANT_MESSAGE | PublicText：text / artifact_ref |
| USER_DECISION | PublicDecision：decision_id、kind、effect、text、supersedes |
| ROUTE_DECISION | PublicRoute：run_id、tasks、needs_clarification |
| STRUCTURED_OUTPUT | PublicTaskResult：run_id、route_task_id、route_key、status、content |
| AGENT_TRANSFER | 旧 v1 事件、新 v1 proposal、v2 引用回执（含拒绝）或 v3 迁移 proposal 指针；见 Envelope 契约 |
| RUN_PROGRESS | run 身份/链/状态/任务/limits、checkpoint_ref、result_refs、transfer_refs；新 v2 不保存 run.usage，以 run_id 查 MODEL_USAGE |
| MODEL_USAGE | PublicUsage：run_id、agent、call_id、phase、tokens、reported、model_calls/retries/reported_tokens/estimated_tokens、input/output、model、billing_agent、estimated、max_output_tokens、budget_level/stage |
| TOOL_CALL / TOOL_RESULT | PublicTool：run_id、operation_id、name、公开参数、status、effect、result/baseline/reconciliation_ref、confirmation_source、错误代码 |
| CLIENT_ACTION / ACTION_ACK | PublicAction / PublicActionAck：动作 ID、参数摘要 / 状态及 action_ref |
| CHECKPOINT | PublicCheckpoint：checkpoint_id、run_id、fence、revision |
| REALITY_REVALIDATION | PublicRevalidation：run_id、字段状态、完整性、action、reason、receipt_gaps |
| CONTEXT_TRACE | PublicContextTrace：selected/candidate_tokens、budget、dropped_count；仅统计 |
| HISTORY_ARCHIVE | PublicArchive：first/last_cursor、count、event_refs |
| NAMESPACE_MIGRATION | PublicMigration：run_id、source/target_session/checkpoint、phase |
| SESSION_HANDOFF | PublicSessionHandoff：handoff_id、target_session、reason |
| SESSION_PROJECTION | SessionProjectionArchive：有界 HandoffProjection v1/v2 文档；handoff 表只保存 SessionProjectionPointer |
| ERROR | PublicHistoryError：code、related_key_digest |

旧 HANDOFF/rollover 按 SESSION_HANDOFF 读取；其他 legacy 枚举不因此获得新公开写权限。PublicUsage 老事件允许缺拆分和模型；新实际调用记录二者。若二者存在，input+output 必须等于 tokens。

`budget.exceeded` 是观测流名称，不是新增 HistoryEventType。持久事件为 MODEL_USAGE，phase=budget、tokens=0、budget_level/stage；查询账本忽略它的费用，但恢复停拉状态读取它。阈值及截断→摘要→停拉→降级策略不变。

## append-only、顺序和幂等

HistoryWriter 校验身份、DTO 白名单、私有文本、16 KiB、指针，然后 append_event_once。store 的不可变事件禁止覆盖/删除。相同键同内容返回原事件；同键异内容失败并写幂等 conflict 事件。幂等键：public:SHA-256([continuity_task_id, session_id, logical_event_key])，event_id 使用相同摘要。

调用先写 usage:{call_id}:reserved，再 dispatch，随后 settled；同 call_id 的 settled 替换预留一次。崩溃没有 settled 时预留继续计费。账本是 MODEL_USAGE 的查询投影，不是第二份余额事实。旧 baseline 和未知模型兼容及“累计不下降”纪律保留。跨 namespace 的事件键和原 run_id 两种作用域保持现状。

转交 proposal 先于 checkpoint；committed/reconciled 与结果观察在保存后写，保持既有故障恢复纪律。Journal 不是当前实现完整执行状态的唯一恢复源；完整恢复还依赖 checkpoint 及不可变 proposal 校验。详见 Envelope 契约。禁止从未经校验的 checkpoint 创造 proposal/digest/usage。

## 指针和查询

旧内部 HistoryPointer 为 kind=event|artifact、target_id；task 所有权在查询参数校验，保持业务 Envelope 摘要字节不变。新增对外 EventPointer 为 task_id（长期任务）+event_id+可选 offset（非负整数）；offset 是定位提示，返回完整事件，不改写/裁切事实。适配后复用同一存在性、所有权门。不存在、跨任务或不是预期类型时抛明确错误，不编造内容。

既有 query_task_history 支持 task_id、session_id、event_type、after_seq、keyword、limit；契约读接口增加 UTC 时间范围查询及 EventPointer 下钻，按 event_seq 分页，避免一页过滤导致漏查。查询只读；不会生成 Pack、模型调用或历史事件。

引用回执解析仅在内存物化：校验同 task/session、AGENT_TRANSFER 类型、transfer_id、canonical proposal、checkpoint namespace；缺引用诚实失败。v1 仍可读；v2 不接受 summary 副本。旧历史不迁移、不改写。run progress 的旧 usage 只作旧见证读取；新 v2 删除用量副本，以 run_id 查询既有账本。

会话投影按 projection:<handoff_id> 经 HistoryWriter 归档一次，原 handoff 表仅存 handoff/task/session 标识与 event_ref。读取恢复原版本、字段和 token_count，L1 不感知存储布局变化。旧表内完整投影继续只读兼容；同键旧回执重放校验物化内容等价后返回原事件，不能用版本迁移覆盖它。

## 去重裁决

- Pack items/messages 为单次调用视图；manifest/trace 仅统计和索引，不成为新的事实源。
- Envelope 请求和结果为当前转交协议视图；持久摘要/digest 只归 canonical proposal，后续回执转指针；原结果归 STRUCTURED_OUTPUT。
- Envelope 的 L3 只有 run_id 查询指针，不带累计快照。新 RUN_PROGRESS 删除 usage 副本；checkpoint 保留原执行用量见证，恢复以 MODEL_USAGE 保守补齐，不能拿快照另加一笔。
- reserved/settled 是同一调用生命周期事件，结算替换预留；budget trace/call manifest 是观测，账本只从 MODEL_USAGE 算一次。
- 任务 ID、transfer_id、checkpoint_ref、phase/result_refs 是事件关联或本事件观察，references 是查询索引；不能把这些标识当作重复业务正文。
- 既有会话投影仍由原投影器提供相同 L1 内容；其持久内容归档一次，其他持久引用只保存指针。物化读取不反向写事件。

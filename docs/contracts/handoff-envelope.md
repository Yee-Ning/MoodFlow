# Handoff Envelope 契约

版本：业务 Envelope v1；公开 Journal 引用回执 v2、迁移 proposal 指针 v3。Gate 0 的实际字段差异已由 West 确认采用现状。

## 字段与权限

| 字段 | 约束 / 含义 |
| --- | --- |
| schema_version | 1 |
| run_id / tool_call_id | 非空标识，最多 128 字符；原始请求 / 本跳调用 |
| source_agent | 注册 agent |
| request | HandoffRequest：target_agent、reason（1–512）、context_summary（1–2000） |
| original_input | 原用户请求，1–4000 字符；原有授权来源 |
| active_task | RouteTask，必须等于 pending_tasks[0] |
| pending_tasks | 1–32 个 RouteTask |
| completed_results | 最多 32 个 CompletedTaskSummary：task_id、route_key、success、最多 1000 字符 content |
| entities | ReferencedEntities；辅助实体解析，不授予操作权限 |
| evidence_refs | 最多 12 个已有 HistoryPointer |

服务端从可信 run state 构造 Envelope。模型的 TransferArguments 只有 reason/context_summary，不能提供 Envelope。所有 DTO 禁止额外字段；必需字段缺失失败。带默认值的可选字段可省略，由服务端规范化后再生成摘要。总载荷上限 64 KiB；路由边、任务唯一性和所有者约束保留。

Envelope 没有 mood/narration/key_ids，也不新增它们。对应业务信息仍由原任务参数、结果、entities/evidence_refs 表达。Envelope 不携带 L3 快照：使用 run_id 查询 BudgetLedger，不能将其误当长期 History.task_id。旧代码传入 run_id 的 `BudgetLedger.get_task_total(task_id)` 命名继续兼容。

## 完整性纪律（RogueHandoff 原样保留）

发送方 `transfer_payload` 规范化完整业务 Envelope；`payload_digest` 对 `{"contract":"handoff-payload:v1","payload":...}` 做 UTF-8 JSON、sort_keys、紧凑分隔符、allow_nan=False，再计算 SHA-256。字符串内容不裁剪或折叠。摘要在不可变 proposed 事件固定。

接收方先校验实际控制消息、Envelope、目标、任务、原输入、完成结果和 digest，再执行。提交回执必须存在且指向有效 proposal；不一致写幂等 integrity_conflict 并拒绝。不能由收到的载荷替旧事件补造缺失 digest。

## 生命周期和归档

Envelope 仅属于当前转交；现有 checkpoint 保存它以支持恢复，下一状态清除后不再作为上下文来源。公开 History 归档服务端有界摘要及完整 payload 的 SHA-256 锚点，私有 checkpoint 保存执行见证；不把完整 Envelope（原输入、结果、实体）再复制到公开档案。公开结果全文归 STRUCTURED_OUTPUT/文本事件，后续归档用引用。

新 proposed 继续使用 TransferJournalEvent v1。新 rejected/committed/reconciled/target_started/target_finished 使用引用回执 v2：schema_version、phase、transfer_id、summary_ref；提交/目标观察必须有 checkpoint_ref，目标观察另可带 result_refs；拒绝只有本事件的 reason_code/retry_allowed，不能声称 checkpoint 提交。summary_ref 直接指向本 session 的 canonical proposed 事件，不重复存 summary/candidate_summary/payload_digest/completed_result_refs。读接口按引用物化兼容 v1 DTO，物化视图不落库。旧 v1 回执只读兼容，不更新或删除。

namespace 迁移的本地 proposed 使用 TransferProposalReference v3：schema_version=3、phase=proposed、transfer_id、summary_ref。它只引用来源 session 的 proposal，不再次保存正文或 digest；每级引用必须满足同 task/conversation 且目标 session.previous_session_id 等于来源 session。读接口验证 lineage、类型和 transfer_id 后物化，拒绝循环或超出既有 1000 事件界限。后续 v2 回执引用本 namespace 的 proposed 锚点；迁移和保存后的确认流程不改变。

## 与 checkpoint / Journal 的真实边界

West 已确认保留现有顺序：不可变 proposal → 保存 checkpoint → committed 回执；接收方等待回执并复核后执行。保存失败不能写 committed。崩溃在保存后、回执前，使用有效 checkpoint 与匹配不可变 proposal 补 reconciled 回执。target_started/finished 也由已保存状态证明。不能仅由 proposal 推断转交完成。

Journal append-only；checkpoint 是可变执行工作状态和提交见证。两者共同承担当前恢复，checkpoint 不可擅自改变已有 proposal/用量事实。Journal 独立重建完整 checkpoint 是后续优化项，本轮不倒置提交顺序、不重写恢复。

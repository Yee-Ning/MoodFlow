# Phase 2 D：故障恢复自愈验收

日期：2026-10-03。结论：恢复补记与结构化阻塞路径已实现；本轮使用受控音乐 HTTP 服务/模型、真实生产协调器/保存器、新 Python 进程和隔离 PostgreSQL 验证。没有对真实账号或供应商重做写操作，不将测试服务冒充真实网易云/播放器。

## 1. 基线、分支与提交

- 开始时 HEAD：`f19aaef6cf6477b73c16a52c7312bb4b2437edee`，包含 B、入口规则层和 A 实现 `588fa6f776776e2d4662cdaf09e38e872745623a`。
- 独立分支：`codex/recovery-selfhealing-20261003`。
- 实现提交：`a3cc649c643b3167805a2c6e36722b6344d8beef`（`fix: heal recovery receipts from saved evidence`），只含恢复/查询实现、测试与本轮证据。
- 报告另行提交以记录已生成的真实实现 hash；报告提交可用 `git log -1 -- docs/reports/recovery-selfhealing-20261003/report.md` 查询。
- 不修改 A 的 `operations.py`、写三态定义、路由、Guard、转交边、digest、ACK 实现。仅增加恢复扫描/补记、诊断模型/查询及 GeneralChat 的诊断回答。
- 未改生产 `.env`、默认图和灰度开关；未部署、未 push；既有未提交报告改动、删除和未跟踪数据保留。

## 2. 缺口格式与查询接口

`RecoveryGap`（`ai-service/app/agent/foundation/long_horizon/models.py:407`）：

```json
{
  "kind": "write_success",
  "subject_id": "op:<稳定操作ID>",
  "status": "blocked",
  "reason": "creation_privacy_unobservable",
  "evidence_refs": [{"kind":"artifact","target_id":"artifact:<核对证据ID>"}]
}
```

| 字段 | 含义 |
| --- | --- |
| `kind` | `write_success` / `transfer_committed` / `task_result` / `action_ack` |
| `subject_id` | 原 operation ID、transfer ID、任务 ID 或 action ID，不创建新的业务操作身份 |
| `status` | 扫描时 `missing`；核对后 `repaired` 或 `blocked`，与 A 的写三态是不同维度 |
| `reason` | 可查询原因码，例：`readback_confirmed`、`effect_not_observed`、`readback_client_unavailable`、`http_502`、`write_evidence_or_scope_conflict`、`creation_privacy_unobservable`、`action_ack_missing` |
| `evidence_refs` | 原提案、原/补记回执、只读核对证据；不保存 Cookie 或私有模型推理 |

类型化 `REALITY_REVALIDATION` 事件附加 `checkpoint_id`、`receipt_gaps`、`revalidation_reasons`，保留原 action/reason；缺口和最终停止原因可审计。旧事件缺省字段保持可读取。

新增 **`POST /api/ai/continuity/recovery/status`**（`ai-service/app/api/chat.py:251`）：

```json
{"thread_id":"原线程ID","graph_mode":"handoff"}
```

- 200 返回最近一次类型化恢复观察：`status=ready/blocked/unavailable`、原 `run_id`、checkpoint ID、action/reason、结构化缺口与 event 引用；这是已记录的诊断，不触发新核对或业务执行。
- 以 Spring 解析的当前身份校验原 conversation 作用域；账号/graph_mode 不符返回 409，`detail.code=recovery_scope_mismatch`，不读取另一作用域的业务记录。
- graph_mode 必填；缺字段、无效模式、额外字段由 DTO 校验为 422；不使用当前默认图来猜测。
- 无恢复记录返回 `unavailable/recovery_not_observed`；旧 cold-start 同类型但不同 schema 的事件不被当作恢复决定。
- GeneralChat 可回答“恢复卡在哪一步”；有待恢复 checkpoint 时，该问句直接读取诊断，不触发新任务/模型或业务恢复。Foundation/handoff GeneralChat 的直接调用也接入同一查询。

## 3. 补记流程与约束

实现核心：`ai-service/app/agent/foundation/long_horizon/recovery_receipts.py` 的 `scan():24`、`repair():63`、`record():101`；协调器入口 `coordinator.py:230`，恢复决定 `recovery_executor.py:18`。

1. 从实际 saver 读取原 run、session、checkpoint 与 lineage，扫描原 journal 中无终态的写提案、保存的 hop 缺 committed/reconciled、保存结果缺 receipt、播放器动作缺 applied ACK。
2. 写缺口**只调用 A 的 `OperationJournal.reconcile(operation_id)`**；D 不实现 success append，不调用业务 executor。A 的 unknown → 只读核对 → confirmed 流程及证据链完整复用。
3. 转交/结果缺口复用现有 `AgentTransferJournal.confirm_checkpoint(..., reconcile=True)`；原提案、转交载荷和 checkpoint 一致才补 `reconciled`，不把新收到的载荷改造成可信提案，不改 digest。
4. 补记先于继续业务的预算判断；只修账本，不重写 run、起始时间、usage、任务目标或限制。补记后重新读取 snapshot，使已核对写状态参与 pending-writes 判断。
5. 再按原时限、checkpoint usage 和 durable reservations 判断是否可恢复；未结算的调用保留原预算占用。原预算耗尽仍为 `stop/original_run_limit_exhausted`，不因补账获得新额度。
6. 原 `stop_reason=cancelled` 以 `stop/original_run_cancelled` 报告；保留保存的 run 状态/原因。只读核对中的 `CancelledError` 原样传播，未知写不转为 failed；未改既有图的取消状态定义。
7. ACK 缺口不凭播放器效果猜测回执；保留 `action_ack_missing`，等待现有 ACK 路径提交原 DTO。恢复扫描查找同一 server-owned task 的原 ACK，跨会话迁移不把已有 ACK 当作缺失。
8. 明确证据不足时停止业务恢复，保留缺口与原因；原账号作用域无法验证时关闭读取/确认权限。已完成请求的普通 transport replay 仍返回原响应，ACK 缺口由显式恢复诊断。

## 4. V7 D 故障点与新增边界

V7 来源实际位于 `docs/reports/context-loop-live-20261003/validation-7.md` **§6**（背景中的 §4 是 B 播放器章节）。不操作该轮保留的数据库和账号现场。

故障 worker：`ai-service/tests/recovery_selfhealing_worker.py:54/56/62`；父进程测试：`test_recovery_selfhealing_process.py:123`。父进程直接断言 native return code **73**，不把 shell 包装退出码当成 `os._exit` 的证据。

| 故障边界 | 注入方式 | 恢复结果 | 恢复外部请求 |
| --- | --- | --- | --- |
| V7 `after_write_before_result`，创建歌单 | 独立 HTTP 服务成功创建后、write success receipt append 前 `os._exit(73)` | A 读回发现新增同名歌单，但 DTO 缺隐私参数，保留 unknown；结构化 `creation_privacy_unobservable` | 仅 GET 自有歌单，不重复创建 |
| 同边界补充加歌案例 | 加歌 HTTP 成功后、write success append 前 `os._exit(73)` | 读回确认目标成员；补一条 success，`readback_confirmed`；重复恢复仍只有一条成功写回执 | 仅 GET 歌单列表/详情，无第二次 POST |
| V7 `before_action_ack` | 受控播放器已应用 pause 一次，ACK append 前 `os._exit(73)` | 明确 `action_ack_missing`；不补造 ACK，播放器应用次数仍为 1 | 无业务 HTTP 请求，不重放播放器动作 |
| V7 `before_journal_committed` | saver 已持久化转交 checkpoint，committed append 前 `os._exit(73)` | 复用原 journal 补 `reconciled`；原 run/起始时间/限制不变 | 无业务 HTTP 请求 |
| 原运行期限耗尽后缺 committed | 同上终止；测试准备将原 start 写入过去，使用新 checkpoint ID 和正确通道版本保存并读回验证 | 回执可补；恢复决定 `stop/original_run_limit_exhausted`；业务不继续 | 无业务 HTTP 请求 |
| PostgreSQL 重启后的加歌 success 缺口 | Python native exit 后，真实隔离 PG 容器 `docker restart --time 2`，新进程恢复 | 重启前后 checkpoint、run、start 一致；补 success，零业务重写 | 仅 GET |
| PostgreSQL 重启后的 committed 缺口 | 同上 | 重启前后保存状态一致；补 `reconciled`，零业务重写 | 无业务 HTTP 请求 |

所有进程用例断言恢复期新增 HTTP 请求均为 GET；使用独立父进程 HTTP 服务保留已生效状态，避免 dying worker 内存状态消失造成假阳性。加歌重复恢复还断言 success 总数为 1。

隔离环境：专用容器 `moodflow-recovery-selfhealing-20261003-pg`，`postgres:16`，仅 `127.0.0.1:15467`；每例新建随机测试数据库，测试后清理。容器已停止；现有服务和暂停的 V7 PG 保留。PG 重启为真实容器重启，不是模拟连接异常，也不等于主机断电/存储损坏测试。

## 5. 验证命令与结果

在 `ai-service` 执行；临时包装器 `.dev/recovery-selfhealing-20261003/validate.py` 复用原离线设置，仅将测试 PG 端口指向 15467，不写 `.env`。

| 范围 | 命令 | 最终结果 |
| --- | --- | --- |
| 全量业务测试 | `.venv/Scripts/python.exe ../.dev/recovery-selfhealing-20261003/validate.py -m pytest tests -q --junitxml=../.dev/recovery-selfhealing-20261003/business-tests.xml` | **1918 passed、45 skipped、286 subtests passed**，0 failures，250.72s；164 条既有 LangGraph 无 checkpointer 时 durability 无效的 warning |
| 本轮单元/查询/取消/兼容性 | 同包装器 `-m pytest tests/test_recovery_selfhealing.py -q` | **14 passed** |
| 四个 native 故障 + 既有 PG 恢复/载荷完整性 | 包装器 `--postgres -m pytest tests/test_recovery_selfhealing_process.py::test_native_exit_recovers_receipts_or_structured_block_without_external_write tests/test_context_loop_postgres_recovery.py tests/test_context_loop_postgres_payload_integrity.py -q` | **25 passed**，577.55s |
| 原截止时间 + 两个真实 PG 重启 | 包装器 `--postgres -m pytest tests/test_recovery_selfhealing_process.py::test_native_exit_transfer_receipt_repairs_after_original_deadline tests/test_recovery_selfhealing_process.py::test_dedicated_postgres_restart_preserves_gap_and_repairs_without_write -q` | **3 passed**，118.25s |
| Python 静态检查 | `.venv/Scripts/python.exe -m ruff check` 显式列本任务 12 个 Python 文件 | **All checks passed** |
| 范围及空白检查 | 本任务路径 `git diff --check`；提交前检查暂存清单/diff | 本任务通过；既有用户改动保留 |

PG 重启命令另设置测试子进程 `RECOVERY_TEST_PG_CONTAINER=moodflow-recovery-selfhealing-20261003-pg`；测试严格断言容器名、loopback 和 15467 端口，不能重启其他容器。HTTP 测试子进程仅为 localhost 设置 `NO_PROXY/no_proxy`，不改变宿主配置。

单元覆盖：确定效果/未观察到效果/无登录读回客户端/读回 502/作用域异常、证据冲突、原 deadline 与 durable token 耗尽、取消传播、GeneralChat 据账本回答、API 200/422/409 与无副作用、旧 cold-start schema、跨会话保留原 ACK。补账审查点：`RecoveryReceiptRepair.repair()` 的写分支只有 `await operations.reconcile(...)`，本轮 `operations.py` 无 diff。

开发期间保留并修正的失败：首次回归暴露已完成请求重放被 ACK 缺口改变响应的兼容问题；另有取消状态 fixture 不符合原契约、ACK fixture 分配了错误能力 owner。进程准备曾因请求缺明确创建授权、localhost 经过环境代理而未到达注入点；一个已启动旧子进程遇到更新后的 DTO 字段不匹配，最终以新进程重跑。期限 fixture 起初未更新 saver 通道版本，修改未生效；修复后又遇到 UTC `Z/+00:00` 表示差异，改为验证解析后的同一时间。没有删除、跳过或弱化业务断言来掩盖失败。

## 6. 证据与边界

- `evidence/` 归档七个受控故障案例的 JSON：native exit 断言、原 checkpoint、恢复期 HTTP 请求、run/start/limits/usage、结构化诊断与证据引用；都是测试数据。
- 不能证明创建歌单隐私参数时继续 unknown；没有播放器真实 ACK 时明确阻塞。这两项不是零丢失效果的虚假成功，也未修改 A/B 语义。
- 本轮覆盖真实 `os._exit` 与真实 PG 重启，外部音乐/播放器/模型仍为受控依赖；没有真实网易云写入、真实音频播放、付费模型或供应商账单核对。
- 完整业务测试范围为 `ai-service/tests`；`evals` 的 `deepeval` 未安装，未运行。全量离线跳过中的 PG 恢复/载荷项已在上述隔离 PG 单独验证；其他 PG E2E/live smoke、Qdrant、TypeSafe 真实外部用例不属于本轮。
- 记账补齐与运行是否能继续分别报告；不会改旧 TaskResult/旧助手回复，把之前未确认的回答改写成成功。没有新增后台轮询、自主重试写入或重置预算策略。
- 不宣称已覆盖操作系统断电、磁盘损坏、所有并发条件或 C/F 等其他验收缺口。

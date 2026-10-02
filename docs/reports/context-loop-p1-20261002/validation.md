# 上下文工程闭环 P1：实现与验证报告

日期：2026-10-02；起始提交：`ee89c2e`。依据 [闭环审计与设计](../../architecture/context-engineering-closed-loop-audit-design-20261002.md) 与 [P0 验证报告](../context-loop-p0-20261002/validation.md)。

本轮将 P0 接入 Foundation / Handoff 的默认 JSON / SSE 调用路径，并增加恢复执行、公开事件、费用对账与预算下钻。验收仅覆盖本地工程与隔离故障演练。用户确认暂无专用测试账号，真实账号侧写操作不执行。

不修改生产 `.env`、默认图、10% 灰度开关，不部署、不 push；生产迁移待批准。实现与验证文档分别创建独立本地 commit。

## 实现与影响范围

以下 Python 模块位于 `ai-service/app/agent/foundation/long_horizon/`。

| 组件 | 位置 / 接口 | 行为 |
|---|---|---|
| Coordinator | `coordinator.py`：`ContinuityCoordinator.begin_call / after_checkpoint`、`ContinuityCheckpointer` | 服务端绑定 conversation、task、session、graph_mode、登录作用域、checkpoint。区分新请求、request_id 重放、同 run 空输入恢复、新 Session；生产图自动注入 journal。新请求不能覆盖 pending run。 |
| JSON / SSE 执行生命周期 | `runner.py`：`produce / invoke / stream / drain_workers`；`foundation/streaming.py` 图工厂；`app/api/chat.py` | JSON 和 SSE 共用执行器；消费者断线不取消执行和最终回复入账。Foundation 的聊天/当前歌曲可见模型保持逐字回复；Handoff 只发布已校验结果。退出时 drain，再关闭 store / saver。SSE 队列有上限，满时可丢中间通知，最终响应保留完整可见正文。 |
| 恢复执行 | `recovery_executor.py`：`RecoveryExecutor.execute` | 只读刷新当前 manifest / ACK、补 committed receipt 后再核验。未知/变化不当作有效，不自动回退或重建 run。未知下游写效果继续阻断。 |
| 单执行与费用 | `execution.py`：`ExecutionLocks / ExecutionLease`；`accounting.py`：`DurableAccounting`；`app/llm/runtime.py` | PostgreSQL session advisory lock + 持久 fence / revision；根 checkpoint 写入 fence / revision。连接丢失停止旧执行器，限制持有锁的连接数量。SDK dispatch 前持久预留，实际用量结算；丢失 settlement 保留估算，跨进程/namespace 汇总原 run 重试与费用。 |
| 原 namespace 与显式迁移 | `migration.py`：`NamespaceMigrator.migrate / reconcile / recover_missing_target` | 原 namespace 空输入恢复；跨窗单独复制私有父执行 checkpoint，不调用 prepare，保留 run_id / usage / retries / started_at / 控制版本。迁移 intent 先入账，Session lineage 与目标 checkpoint 的间隙可补齐。含 cached pending writes 或属于当前父 checkpoint 的持久子图状态的源必须先在原 namespace 恢复，不能丢弃子执行。 |
| 全事件公开 Writer | `public_events.py`、`history_writer.py`、`operations.py`；ToolNode / direct_actions hook；`handoff/song_context.py.invoke_song_context` | 用户文本/决策、最终可见回复、路由、公共工具参数/operation_id/结果引用、动作/ACK、恢复 revalidation、预算 trace、费用、checkpoint、归档、迁移逐类 schema。直接读取歌曲上下文也登记安全结果引用。只登记 task_summary，不记录模型中间推理。target_started / target_finished 来自执行与真实保存边界。 |
| 写操作与 ACK | `OperationJournal.call / write_receipts`；`/api/ai/actions/ack`；前端 `aiClientActions.ts / aiChatStore.ts / aiChat.ts` | 同 run 相同写参数稳定 operation_id；回执按 task/run 跨 Session 查询，已确认成功用安全 Artifact 重放，未知效果不重发。动作公开回执写入后才发送；ACK 验证登录作用域与原 action_id，只追加且幂等。客户端重复失败动作仍失败，参数变化拒绝。 |
| 预算下钻与归档 | `drilldown.py`：`HistoryDrilldown.read / archive / effective_decision_entries` | pointer/cursor 共用 count≤20、token≤800、byte≤8192 全封装预算；Artifact 默认只读投影。每批归档≤40，游标增量索引，不删除原历史。约束 revoke / supersedes 通过引用撤销投影，不改原事件。 |
| Session 投影 / 安全换窗 | `coordinator.py.draft`、`service.py.apply_revocations`、`revalidation.py.check_fields` | 活跃约束带引用；当前 run 的已确认链聚合，旧链保留引用。新请求继承投影时也核验字段来源/版本/时效，把 changed / unknown 放入模型 context，不推断旧 owner。PostgreSQL 仅在运行已结束、无未知写效果、无未确认动作时按上下文阈值或显式请求换窗；恢复不走此路径。SQLite 自动换窗拒绝。 |

图内 stateless capability 子图保持无 saver；父图转交提交用 sync durability。现有 LangGraph 在无 saver 子图继承 sync 时会等待不存在的 checkpoint future，本轮在两处显式指定子图 async，保留父图提交等待。需要持久化的嵌套 query 子图使用隔离 namespace 和版本化 JSON DTO；没有扩展 checkpoint serde 白名单。

## 调用约定

- 新请求维持现有 `message / thread_id / player_context`，可选 `request_id`。相同 ID 与内容可重放；不同内容拒绝。恢复用 `message=""、resume=true、既有 thread_id`，不得同时送新 Session 或新决策。
- `new_session=true` 只用于已完成运行的新请求；尚在执行的 run 从原 namespace 恢复。跨 namespace 迁移用 `POST /api/ai/continuity/migrate`，携带 `thread_id / migration_id` 及需要核验的当前 `player_context`。
- 新决策使用严格 DTO：`decision_id / text / kind(decision|constraint) / effect(add|revoke|supersede) / supersedes`。撤销/替代引用须属于同 task 的决策事件，恢复请求不能引入新授权。
- `POST /api/ai/actions/ack` 接收 `thread_id / action_id / status(applied|failed)`；服务端解析登录作用域。相同 ACK 返回原回执，矛盾 ACK 为 409，非法请求为 422。

这不是下游写 API 的分布式 exactly-once 协议。没有下游原生幂等键/可查询操作状态时，本轮保证不盲重发未知写操作，无法凭内部 receipt 推断外部实际结果。

## 故障演练与通过标准

专项用官方 PostgreSQL saver、真实迁移、真实 Writer 与 Coordinator，新进程恢复；模型、目录、歌曲服务用受控依赖。进程使用 `os._exit(73)`，不是只抛一个可捕获异常。

| 故障点 / 边界 | 断言 |
|---|---|
| 转交 proposed 后进程退出 | 源 checkpoint 为准；继续原任务，原 proposed 留审计。 |
| cached Command 已写但新 checkpoint 尚未保存 | 检查原 run、链、结果前缀、用量不减、无未知效果；仅让 LangGraph 应用已缓存的纯控制写，后续落盘确认后才执行目标。 |
| accepted checkpoint 保存后、journal 回执前退出 | 幂等补 reconciled，再恢复目标，不重发转交。 |
| target_started 后退出 | 保留 owner、原链与 deadline，继续目标。 |
| 结果保存后、结果入账前退出 | 补结果回执，不重复累加结果。 |
| 最终 checkpoint 保存后、公开回复入账前退出 | 补最终可见回复；同 run 只有一条 final 事件。 |
| 费用 reserved 后、SDK 返回前退出 | 原预留保留估算，model_calls / retries 不清零。 |
| lineage 保存后、迁移目标 checkpoint 前退出 | 用 proposed intent 补齐目标 checkpoint，再核验链与 journal。 |
| lease 过期但 advisory connection 仍活着 | 第二执行器被拒绝；不能仅凭 expires_at 并行执行。 |
| advisory connection 被终止 | 旧执行器检查失败，新执行器获得更高 fence。 |
| 有限并发与连接容量 | 3 个独立执行锁可并发；超容量明确拒绝，历史查询连接仍可用。 |
| SSE 离线 / 动作 / ACK | 最终事件继续入账；动作发送时公开 receipt 已存在，ACK 可在执行持锁期间入账。 |
| Session 改变且模型换 tool_call_id | 同 run 相同写参数只调用下游一次；成功回执重放安全结果，未知回执拒绝再次调用。 |
| 父 checkpoint 下已有持久子图状态 | 拒绝只复制父状态，要求原 namespace 恢复。 |
| Foundation 逐字回复与旧 compiled Handoff 图 | 新执行器首个可见 token 早于子图完成且正文不重复；旧构图方式也自动接入 proposed / committed / target hooks。 |

## 验证命令与结果

工作目录为仓库根目录。测试通过 `.dev/context-loop-p0-20261002/validate.py` 设置隔离子进程变量，不编辑 `.env`；关闭 JEV/live 开关、fake LLM 地址、disabled memory、SQLite / memory checkpoint。Settings 内部仍会使用既有 env_file 加载逻辑，本轮没有打开或导出凭据文件正文。PostgreSQL 专项限定临时容器 loopback 15447，并只创建/销毁随机测试库。

| 命令 | 最终结果 |
|---|---|
| 隔离 wrapper：`-m pytest -q tests` | 最终代码 **1773 passed、29 skipped、286 subtests passed**，971.79 秒，退出码 0。30 项新增 Coordinator / SDK / Writer / 下钻边界测试；普通集合的 PostgreSQL / live 跳过不计为真实依赖通过。 |
| 同 wrapper：`--postgres -m pytest -q tests/test_context_loop_postgres_recovery.py tests/test_postgres_persistence.py` | 19 passed，559.39 秒，退出码 0；其中 12 项 P1 专项、7 项既有 PostgreSQL 持久化专项。末次适配器属性兼容修补前运行；官方 compiled 图具备该属性，修补不改变专项执行的分支。 |
| 同 wrapper：`../.dev/context-loop-p0-20261002/eval.py -q evals/tests` | 198 passed，146.94 秒，退出码 0；复用现有两虚拟环境的依赖，不安装新库。末次适配器属性兼容修补前运行，该两行修补由原世界线用例与随后完整后端集合复验。 |
| `-m ruff check . --output-format json`（cwd=ai-service） | 45 项既有静态问题；新增 0、消失 0，退出码 1。不是 lint 全绿。 |
| 隔离 wrapper：`-m compileall -q app` 与新增专项文件 | 通过。 |
| 前端 `npm run build / lint` | 通过；构建保留已有大 chunk 提示。 |
| 前端 `test:ai-layout / test:ai-stream / test:music-assets / test:search / test:ai-trigger / test:worldline` | 全部通过；stream 专项新增真实 store/service 调用的 applied/failed ACK 与失败动作重放测试，播放器用 MockAudio。 |
| 当前任务文件 `git diff --check` | 通过；不修用户无关报告。 |

原始日志/XML 位于本地忽略目录 `.dev/context-loop-p1-20261002/`；最终计数、日志 hash、静态基线比较在本目录 evidence JSON 中记录。

JUnit 的后端 `tests=2088` 包含 subtests，不能当作 2088 个主测试：主测试通过 1773、跳过 29，另外 286 个子测试通过。JSON 分别记录 pytest 输出与 JUnit 计数/耗时，避免两种统计口径混淆。

## 失败与修复记录

1. 接线后发现无 saver 子图继承 sync durability 的上游异常；修复调用适配并复验。
2. 动作通知早于 checkpoint 时，等待动作 receipt 会阻塞父图推进。改为 checkpoint hook 写公开回执后发布动作，实际 SSE + mid-run ACK 用例通过。
3. 新夹具误用业务 DTO 字段/缺 current_stage/未设 checkpoint channel_versions，按既有严格 schema 修正；没有删、skip 或弱化验收断言。
4. 全量首次收集暴露 schema→agent→LLM 循环导入。决策请求改用独立 transport DTO，在调用时再转换为公共 History DTO；重新执行旧入口与全量测试。
5. 全量回归暴露旧 compiled Handoff 图未带 journal 的兼容问题；执行器补 saver adapter，agent 使用当前 Coordinator 的 journal，原 API 用例和新增兼容测试复验。新增测试第一次误用复合请求配单任务 Router，修正受控输入后验证全部转交阶段。
6. 跨窗复查发现原工具回执仅按 Session 查询；改为同 task/run 的跨 Session 查询，并增加成功/未知写效果与持久子图迁移拒绝测试。改动后中止旧快照验证，以最终代码重跑，不把中止计为通过。
7. 全量暴露带既有 saver 的旧构图路径缺 lifecycle hook，以及旧测试在过时 factory 位置注入 service。保留其 saver 并补适配器；测试改在 Coordinator 处注入隔离 service，原 Foundation/Handoff 会话隔离与切回断言全部保留。旧纯事件 FakeGraph 更新为真实 checkpoint 图，保留 inherited projection / changed_fields / 公开入账断言；该定向集合 86 passed。
8. 保留静态检查基线 45 项与构建大 chunk 提示。诊断中失败的运行不计为最终通过。
9. SDK 边界复查发现 Foundation 的聊天/当前歌曲/工具模型未进入持久费用预留。三处接入 continuity accounting；bound ChatOpenAI 保留工具 schema、复制配置关闭 SDK 隐式重试并启用 usage，工具 schema 计入预留估算。新增断言验证三条模型路径的 reserved / settled、逐字回复，以及不修改共享缓存模型；4 项专项通过。之后再次以最终代码执行完整集合。
10. 公开事件复查发现直接 `get_current_song_context` 调用绕过 ToolNode hook；GeneralChat / Curator / Explorer 的直接调用统一接入公共读取 wrapper，ToolNode 路径保持单次入账。实际 GeneralChat 工具循环和 Curator 当前歌曲路径复验 2 passed，验证公共参数、operation_id、安全 Artifact 引用与零重复读取。最终完整验证在此补丁后执行。
11. 一次完成的全量回归为 1772 passed / 1 failed / 29 skipped，失败在旧世界线 binding 用例使用的 `ainvoke` 对象没有 `.checkpointer` 属性。两处适配改为可选属性读取，不修改原用例及其实体绑定断言；随后专项与完整后端复验。该失败集合不计为最终通过。

## 未验证项与剩余限制

- 无专用测试账号：真实创建/增删测试歌单、云端红心写入、实际播放器 ACK、浏览器刷新/重连后的播放效果未验收。没有对真实账号执行写操作。
- 真实 LLM/JEV/Qdrant/Java 服务联调、供应商账单与重试计费、网络分区和生产强杀未做。费用专项使用受控 usage / durable reservation，不等于 provider 账单对账。
- 有限并发验证不是生产压测、P95/P99、长期 soak 或容量承诺；没有据此批准开闸。
- 默认 fact refresh 可读取 manifest 与存储内 ACK。缺少下游只读查询能力或其结果无法唯一确认时继续 needs_input；不把当前 client 信息提升为服务器操作授权。
- private checkpoint 与 History 仍是两个提交边界，journal 用于对账。已确认转交要求两者一致，不能宣称跨存储原子提交。
- 显式 namespace 迁移覆盖可核验的父图边界；不会迁移正在执行的持久子图树。此类状态及超出有界 lineage 扫描的状态明确拒绝，须先从原 namespace 恢复。
- 归档是游标索引并保留原事件，不是外部冷存储删除/压缩。旧未类型化事件仅安全投影或迁移提示，不批量改写；旧通用 `record_event` API 与显式注入图的兼容测试路径仍保留，不能宣称全仓库所有历史写入口都被替换。
- SQLite 自动 rollover / 跨 namespace 迁移禁用；memory saver 不支持跨进程恢复。真实恢复要求部署本身选择持久 checkpoint backend，本轮不修改现有配置。
- 文本过滤拒绝常见凭据与私有字段，不等于识别任意未标识密钥的 DLP；Token 计数仍为 heuristic。
- 生产 `003 / 004` 迁移与大表耗时未验证；见 [待批准迁移手册](../../operations/context-loop-p1-postgres-migration-20261002.md)。

## 后续独立验收

1. 获得测试登录及可变更歌单后补真实写操作、只读核验和播放器 ACK；保留未知效果与失败证据。
2. 在获批环境完成持久 backend、真实模型/检索/Java 联调及逐点进程故障，核对 provider 账单、原 deadline 和重试上限。
3. 对照生产规模验证迁移、负载、连接池、长历史和监控告警；如需冷存储，另行设计保留与恢复策略。
4. 将独立复验报告交给 West 决定是否开闸。本轮不自动启用 10% 会话灰度。

## 本地提交与清理

实现提交：`8e65f90ac15c5b8ab0c422e8eb2aaf6576324e05`（`Wire context loop P1 production lifecycle and durable recovery`），含 41 个实现、迁移和测试文件。验证文档另行提交；没有 push。

开始时的 58 条用户工作区状态记录保持原状，没有暂存或提交无关文件。专项临时 PostgreSQL 容器 `moodflow-context-loop-p1-pg` 已停止并自动移除；既有容器未操作，原始验证证据保留在本地忽略目录。

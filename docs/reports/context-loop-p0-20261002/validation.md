# 上下文工程闭环 P0 实现与验证报告

日期：2026-10-02；开始基线：`225f6cb`。范围依据[闭环审计与缺口设计](../../architecture/context-engineering-closed-loop-audit-design-20261002.md) §2.2、§2.3、§2.5、§2.7、§2.9。改动限于 Python AI 服务、PostgreSQL 迁移、相关测试及本报告。

**本轮实现四项 P0 能力与显式接入接口；默认生产入口的自动闭环仍待 P1 Coordinator 接线。** 未修改 `.env`、默认图选择和灰度配置，未部署、未 push。新增 journal 必须由调用方显式注入，不会因本次提交自动启用。

## 实现与通过项

| P0 组件 | 实现位置 / 接口 | 行为与验证边界 |
|---|---|---|
| 类型化 Immutable History | `foundation/long_horizon/history_writer.py`：`HistoryWriter.append_public_event / transfer / task_result / run_progress` | 白名单为转交、公共 TaskResult、RunProgress、公共错误四类；严格 schema、默认完整事件 16 KiB 上限、引用数量与任务归属检查。拒绝 scratchpad、任意 data / metadata、原始 prompt 和常见凭据格式。新 transport request ID 的同内容重放返回第一张回执；不同内容拒绝并追加冲突事件。 |
| 稳定幂等存储 | `long_horizon/store.py / postgres_store.py`、`003_context_loop_history.sql` | event_key 唯一、event_seq 游标、不 UPDATE 历史正文。SQLite 文件及内存模式并发重放验证；PostgreSQL 同任务事务锁使游标不越过尚未提交的早期事件。数据库原有不可变触发器保留。 |
| Command 与落盘边界 | `handoff/continuity.py`：`AgentTransferJournal.observe_transfer / confirm_checkpoint / observe_target`、`JournalCheckpointer`；`handoff/tools.py / graph.py` 可选注入 | Command 只能写 proposed / rejected。委托 saver 成功后回读真实快照并检查链、消息配对和 envelope，才写 committed、公共结果与 RunProgress。落盘成功而回执缺失时幂等补 reconciled。target_started / target_finished 有显式 hook；finished 要有已保存 TaskResult。注入 journal 的图在目标节点执行前等待提交回执，等待时间计入原节点 deadline。 |
| V2 Session 投影 | `long_horizon/models.py`、`service.py`；`handoff/continuity.py`：`ProgressProjector.project_progress` | last_agent / run_id / status / 最近链来自已确认 RunProgress；accepted 数量由链推导。proposed / rejected 留审计引用；失败、needs_input 和摘要文本不计入完成。多 run、增量 cursor、跨 Session 继承、旧 V1 unknown、完整封装预算及 JSON serializer round-trip 已覆盖。早期链用不可变 RunProgress 引用，不把最近 run 冒充全部 Session 历史。 |
| Reality Revalidation | `long_horizon/revalidation.py`：`RealityRevalidator.revalidate` | 来源、时效、版本、作用域、Artifact hash、checkpoint digest / namespace / lineage、run / hops / owner、控制消息、提交 journal 与结果回执联合检查。unknown 不当作 unchanged；业务 ACK 不明或 pending writes 未对账时阻止恢复执行。 |
| 恢复决策 | `long_horizon/recovery.py`：`RecoveryPlanner.decide` | 输出 resume_target / resume_source / refresh_facts / needs_input / stop；只做决策。缺回执先要求 reconcile；journal 声称提交而源 checkpoint 未变时拒绝猜 owner。原 run_id、usage、retries、started_at、limits 和 guard retry deadline 保留；不重做已完成任务、不自动回退。 |
| Single-Call 修补 | `foundation/model_input.py`：`prepare_model_input`；WorldLine generation、`llm_routing/runtime.py`、query analyzer；`llm/client.py / runtime.py` | 三处调用消费预算后的 ContextPack 或等效 JSON 适配。WorldLine 从 surviving pack 重建输入，元数据限长且白名单；重选不会复活已丢弃来源。SDK 发送前再次核验真实 request 的 input / instructions / schema / framing，加输出额度和安全余量。必须保留内容或 schema 超限时阻止调用。 |

实现位置均位于 `ai-service/app/agent/`，模型请求公共边界位于 `ai-service/app/llm/`。新增 checkpoint 数据按 `model_dump(mode="json")` 存储并携带 schema_version；未扩大反序列化白名单。TransferArguments 的 `reason + context_summary` 和 extra=forbid 保持兼容，转交摘要由服务端组装，candidate_summary 明确标为非权威参考。

### 转交断线复验

| 可靠证据 | 复验结果 |
|---|---|
| 尚无 accepted hop，源快照完整 | resume_source，保留原 run。 |
| C→E 已生成 accepted Command，尚未保存 | 仍从已确认的 Curator 源 checkpoint 继续，不凭 Command 跳 Explorer。 |
| C→E checkpoint 已保存，journal 回执缺失 | needs_input / reconcile_committed_receipt；补回执后再 resume_target，不重发 transfer。 |
| checkpoint 与 journal 的 C→E 均一致 | resume_target=Explorer，原链、用量、重试数和时间保留。 |
| 回执声称已提交，实际仍是源快照 | integrity conflict，needs_input；不删除 hop 或猜 owner。 |
| 旧未提交提议被后续一致的提交替代 | 保留审计事件，不影响已提交链，不计入 accepted。 |
| 任务结果已保存，结果历史回执缺失 | 先补结果回执；之后恢复 final_response，结果不重复累加。 |
| 保存 accepted checkpoint 失败 | 实际异步图故障注入通过：目标业务未调用、无 committed 事件。 |
| 坏 owner / digest / namespace / next、孤立或缺配对消息、跨任务引用 | 拒绝恢复或明确要求对账 / 输入，不制造 owner。 |
| 时效或来源未知、版本 / 事实变化 | refresh_facts；当前认证不明、权限失去或用户作用域变化则 needs_input。 |
| 已发送客户端 action，ACK 不明 | needs_input；新鲜且同作用域的 applied ACK 后才允许继续。 |
| 原额度、run deadline 或 guard retry deadline 耗尽 | stop，不重置 usage / retries / started_at。 |

## 验证命令与结果

工作目录为仓库根目录。普通测试使用任务隔离子进程：memory checkpoint、SQLite、disabled memory、空 DATABASE_URL、占位 LLM 地址与 key、关闭 live/JEV 调用开关、单线程 OpenBLAS。进程变量覆盖不修改实际配置；Settings 仍存在既有 env_file 加载机制，不能宣称底层完全不读取凭据文件。本轮未打开或导出凭据文件内容。

| 命令 | 结果 |
|---|---|
| `ai-service/.venv/Scripts/python.exe .dev/context-loop-p0-20261002/validate.py -m pytest -q tests` | 最终代码：**1743 passed、17 skipped、286 subtests passed**，754.91 秒，退出码 0。已完成首轮为 1732 passed、16 skipped、286 subtests passed；最终集合包含后续新增边界和权限变化用例。 |
| 上述隔离 Python，`-m pytest -q tests/test_context_loop_history.py tests/test_context_loop_projection.py tests/test_context_loop_recovery.py tests/test_context_loop_budget.py tests/test_long_horizon_context.py` | 边界修补后 86 passed；未删、skip 或弱化失败断言。 |
| 上述隔离 Python，加 `--postgres -m pytest -q tests/test_postgres_persistence.py` | 7 passed。真实隔离 PostgreSQL：迁移重放、不可变保护、并发幂等、游标提交顺序、V2 + journal + 实际 saver + 自动 rollover 持久化、事务回滚、跨进程恢复和 pgvector。 |
| 上述隔离 Python，`../.dev/context-loop-p0-20261002/eval.py -q evals/tests` | 198 passed。主环境优先，只把现有 `.venv-eval/Lib/site-packages` 追加为缺失评测依赖来源；没有安装新依赖。 |
| `ai-service/.venv/Scripts/python.exe -m ruff check ai-service --output-format json` | 保留 45 项既有静态问题，退出码 1；与基线逐项比较，新增 0、消失 0。[比较结果](ruff-baseline-comparison.json)。并非静态检查全绿。 |
| `ai-service/.venv/Scripts/python.exe -m compileall -q ai-service/app` 与当前涉及测试文件 | 通过。 |
| 当前任务文件的 `git diff --check` | 通过；未修复用户原有报告中的 unrelated 空白问题。 |

任务隔离辅助脚本与原始日志位于本地忽略目录 `.dev/context-loop-p0-20261002/`；[验证摘要](evidence.json)仅导出计数、命令和日志 hash。全量普通测试的 skip 不等于真实依赖验证成功；本轮单独运行 PostgreSQL 专项，其余 live 项见下节。前端、Java 代码未修改，本轮未重复其构建与浏览器视觉验收。

### 失败记录与保留问题

1. 首次 PostgreSQL 并发重放同时碰到 event_key 和主键，原先只处理 event_key 的 INSERT 不够。改为冲突后核对同一不可变回执，并加同任务事务锁；复验通过。
2. SQLite 内存共享连接在并发写入期间发生读 API misuse。读、插入及历史写入共用重入锁；文件与内存两种并发重放测试通过。
3. saver 故障注入夹具误用未序列化的 handoff_count，后来改为真实 hops；期间测试失败保留为诊断，不计为通过。最终故障测试确实验证目标业务零调用。
4. PostgreSQL 旧重启断言期望 `handoff` 字符串；按新语义改为 `session_handoff`，其他恢复与持久化断言保留，旧 HANDOFF / node=rollover 另有兼容测试。
5. 主虚拟环境单独运行完整评测缺 deepeval，评测虚拟环境单独运行又缺业务依赖。使用现有两环境的库路径完成 198 项评测；没有通过跳过缺依赖测试取得通过。过程中收集错误不计为通过。
6. 全量运行期间发现边界补丁后，中止尚未完成的旧快照运行，再以最终代码启动全量；中止运行不计为通过。

**保留的失败为原有 Ruff 45 项。** 历史行为 / 知识质量报告未被改写；本轮不新增语料、不切检索索引，也不能据此认定此前周杰伦知识覆盖问题已解决。

## 未验证项与生产边界

- 普通图工厂不默认注入 journal，JSON / SSE 入口尚未调用完整 Coordinator。P0 的明确注入图、持久化接口与恢复规划已验证，默认线上入口并未因此自动闭环。
- 未用真实 LLM / JEV / 音乐账户执行本轮预算或恢复专项；模型 SDK 边界由受控响应验证。未验证真实播放器 ACK、账户写操作、浏览器断线、生产进程强杀、并发负载或 SLA。
- PostgreSQL 专项使用新建无挂载临时容器及测试生成的隔离数据库；生产库未迁移。checkpoint saver 与业务 History store 仍为两个提交边界，由追加 journal 对账，不能声称跨存储原子提交。
- SQLite 的自动 rollover 显式禁用；既有手动 / 测试 rollover 兼容保留，其 transaction 仍非真实原子事务。
- Token 计数沿用 Unicode heuristic，检查覆盖实际序列化 payload；不等于 provider 的精确账单。文本过滤也不等于能识别任意无标识密钥的 DLP；原始 data / scratchpad / prompt 来源不得接入新 Writer。
- 新 Writer 的四类白名单已实施，旧通用 `record_event` / MainGraph 全事件路径尚未统一迁移。不能宣称全仓库任意历史入口已获得相同白名单保证。

## P1 建议清单

1. **Coordinator 与生产接线**：先在获准的目标库执行 `003_context_loop_history.sql` 迁移并复验；统一 JSON / SSE 生命周期，显式区分新请求、同 run 空输入恢复和新 Session；提供服务端 ContinuityIdentity / LiveSnapshot，绑定 graph_mode、登录作用域和 checkpoint ref，再注入 journal。启用前独立复验，继续保留关闭的灰度开关。
2. **恢复执行器与跨窗迁移**：消费 RecoveryPlan，只读刷新事实 / ACK、幂等 reconcile 后再核验；从原 namespace 恢复，跨 namespace 迁移单独实现，避免 prepare 重建 run。补任务锁、state revision / 单执行 fencing 和跨进程未知费用对账。
3. **全事件公共写入**：把用户决策、最终可见回复、工具公共参数 / operation_id、动作 ACK、路由和 revalidation 逐类迁入 Writer；SSE 消费者离线也应入账。生产 hook 自动记录 target_started / target_finished；大结果先安全 Artifact 再传 ref。
4. **预算化下钻与长期归档**：实现设计中的 DrilldownPack，统一 pointer / cursor 的 count、token、byte 上限和惰性 Artifact 投影。当前 projector / journal 的 1000 条窗口、早期链 32 个引用超限会明确拒绝；P1 按游标增量并归档更长历史，处理约束撤销 / supersedes。
5. **真实恢复验收**：生产相同依赖条件下逐点进程故障演练、客户端 ACK、写操作幂等、模型费用 / 重试 / deadline、负载与观测；验收报告通过后再独立决定开闸。

实现独立提交：`bea613c`（23 个代码 / 迁移 / 测试文件）。本轮仅提交当前任务文件，用户已有知识数据、报告修改 / 移动、图片和其他未提交内容保留；初始 58 条 Git 状态记录仍全部存在。新建无挂载容器 `moodflow-context-loop-p0-pg` 已停止并自动移除，原有容器未操作。未 push。

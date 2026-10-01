# MoodFlow Handoff 阶段 4：运行保障

日期：2026-10-01。基线提交 `da5c3a1`，承接 [阶段 3 完整业务网络](handoff-runtime-phase3.md)。本阶段补齐调用计量、预算预留、JEV 转交护栏、有限重试、失败文本兜底和内部 trace。默认图仍为 Foundation，没有修改 `.env`、JEV 首跳策略或线上部署。

## 1. 调用计量与三级预算

L1 继续使用现有 ContextPack 的选取、历史裁剪和确定性压缩。handoff 执行期间，异步 ContextPack 的可用预算还受当前 Agent/整轮剩余量约束；必需上下文仍不能容纳时停止，不裁剪原始任务、授权或必需事实来换取调用。

新增 `app/llm/runtime.py` 的 RequestLedger，通过 ContextVar 在本轮路由、每次 Agent 节点及其子图之间传播。普通 Foundation 没有 ledger 时，公共入口保持原调用行为。模型调用入口覆盖：

- GeneralChat、转交模型、Curator 工具模型和当前歌曲分析的 `ainvoke`；绑定工具后的 schema 也计入预留。
- Responses API 的路由、查询理解、WorldLine 叙事/分支等结构化调用；经同一入口的生成和摘要也计量。已有长期窗口摘要为确定性处理，不虚报模型调用。
- JevClient 的实际 SDK 请求，包含护栏及子流程启用的 JEV；JEV 不增加 SDK 自动重试。

L2 为每 Agent 每轮累计 16,000 token，L3 为整轮 32,000 token，沿用阶段 1 的 RunLimits。初始路由计入 GeneralChat；转交生成和 JEV 护栏计入发起 Agent；后续业务计入 owner。同一 Agent 回访、重试和 checkpoint 恢复都不会重置账本。

每次请求同步预留“现有 Unicode token 估计 + 请求/协议余量 + 最大输出”，并扣除所有正在进行的预留；并发子调用不能占用同一份剩余额度。收到可信 usage 后按 provider 报告结算，支持 LangChain usage metadata、Responses usage 和 JEV usage。没有合法 usage 或失败/取消后没有完整账单时，保留整份预留作为估计，不能当作零消耗。

AgentUsage 增加 `reported_tokens`、`estimated_tokens`、`model_calls`，原 `tokens` 保留累计受控消耗。旧快照没有新字段时使用零默认值；其既有 tokens 不丢失。估计值不是实际账单，实际超出预算仍如实记录。预留估计也不是 provider tokenizer 的精确上界。

超限形成不可清除的本节点停止原因；即便业务子图捕获了异常，父图仍终止。模型已返回工具选择但账单达到上限时，不把选择交给工具执行器。已发出的远程调用及已完成的写操作无法靠预算或超时撤销。

## 2. JEV 转交护栏

transfer 工具先执行原有纯契约检查，只有允许边、下一待办路径、顺序、进展、时间、跳数和预算均通过时才调用 JEV。它评估原始输入、可信待办、来源/目标及候选理由/摘要；理由/摘要作为不可信提案，JEV 不拥有改写任务或授权业务动作的权限。

复用现有 `JEV_ENABLED`、`JEV_SHADOW_MODE`、`jev-latest`、超时和 `JEV_ROUTER_CONFIDENCE_THRESHOLD`（默认 0.8），没有增加配置开关或调整阈值：

| 模式/结果 | 行为 |
|---|---|
| JEV 关闭 | 只应用已有本地契约检查 |
| JEV 开启、Shadow | 记录分数，继续本地契约允许的转交 |
| JEV 开启、非 Shadow，分数 ≥ 阈值 | 再检查 guard 期间产生的预算/时间变化后接受 |
| 非 Shadow，低分 | 返回配对的 `guard_rejected` observation；同 Agent 最多重新决策一次，不增加 hop |
| SDK 不可用、超时或结果无效 | 记录 `guard_unavailable`，一次本地契约兜底，无递归模型询问 |
| 预算/节点或整轮时间耗尽 | 停止，不能用本地兜底绕过硬限制 |

拒绝的预览不保存为已接受 hop，不改变 current Agent。护栏重决策和模型/结构化修复共享每 Agent 每轮最多一次重试。两次低分终止，无音乐操作。无论 JEV 分数多高，禁止边或伪造授权在 JEV 前就会拒绝。

真实 smoke 已验证一次 `jev-latest` 的 Noul 护栏、SDK 和新计量路径：返回 `jev-1.13.0`，报告 494 token，541.51 ms。本例分数 0.68，低于 0.8，**不能证明护栏质量可上线**；保持现有 Shadow，不调整阈值以放行样例。后续需独立标注样本评测和灰度验收。

## 3. 重试、超时与兜底

模型请求仅对连接错误、超时、429 和 5xx 做一次可恢复重试；参数/鉴权等错误不重试。关闭 handoff ChatOpenAI 独立副本和 Responses 的隐藏 SDK 重试，保持共享 Foundation 实例原配置。结构化输出修复也使用同一份重试额度，不另开重试环。

重试发生在单次模型请求边界：Curator 已完成写工具后再生成文本遇到瞬时错误，只重试生成，不重新执行整个 Curator 子图、已完成工具或原任务。JEV SDK 请求本身不重试；低分后重新生成候选最多一次。失败结果、澄清结果和旧任务计划不能因兜底改为成功。

每次 Agent 节点的完整执行窗口 45 秒，含模型、工具、护栏和重试。护栏拒绝后的重新决策继承原截止时间，并将该时间保存到 checkpoint，不重新获得 45 秒。整轮 120 秒含初始路由，并使用原起始时间跨 checkpoint 恢复。耗尽时明确失败并保留已完成结果。取消也保留本地观察到的用量/原因；外部进程退出或传输直接终止不能保证再写入 checkpoint。

普通任务失败提供确定性的 GeneralChat 文本兜底；预算、时间或重试耗尽则直接终答。此兜底不调用额外模型、不派发业务工具、不增加转交，不改写失败状态。保留 5 跳硬限制及已有无进展回跳拦截。

## 4. Trace 与公开协议

新增 TraceRecorder 复用 HandoffTrace 白名单，记录模型用量、重试、接受/拒绝、护栏观察/不可用、预算/时间、任务失败和终态。单轮使用同一个服务端 run ID 作为 trace ID；模型提供的 call ID 先做哈希，避免其自带用户文字。trace 不保存请求、摘要、实体、歌词、scratchpad、账户信息或上游异常正文。

事件写入结构化 `handoff.trace` 日志及父图 JSON `handoff_trace`，可注入外部 sink；sink 失败不影响业务且不记录异常正文。恢复保留此前事件，新轮清空计量、重试和 trace。原 schema version 1 新增计量默认字段、trace 结果码和可选图状态，旧快照兼容。

trace、guard 数据和 usage 都是内部字段，不扩展 JSON/SSE 公开协议。只从原验证后的 TaskResult/动作通道输出；旧 WorldLine 名称、引用和来源、动作去重、两模式会话隔离继续适用。

## 5. 验证边界

新测试覆盖预留拒绝、实际超限、无 usage、并发账本、跨 Agent/恢复、新轮清理、模型及结构化修复的共享重试、HTTP 错误分类、护栏阈值/Shadow/异常/禁止边、取消和超时、工具写入后重试、trace 脱敏和 SSE 兼容。全量结果见 [验收报告](../reports/handoff-phase4-20261001/validation.md)。

真实护栏质量、全链路在线模型成本/时延、PostgreSQL 重启恢复、真实音乐账户与浏览器播放、云端写入和客户端重放幂等仍需后续验收。默认模式与部署配置保持原状。

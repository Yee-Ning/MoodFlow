# MoodFlow Handoff 阶段 1：能力与状态契约 v1

日期：2026-10-01。输入基线：[阶段 0 报告](../reports/handoff-phase0-20261001/baseline.md)，基线提交 `08010fe`。

## 1. 阶段范围

沿用本次开发流程的阶段编号：阶段 1 固化能力、转交、状态和兼容契约；阶段 2 接入最小运行链路；阶段 3 接入 Explorer；阶段 4 完成运行时加固；之后独立验证 JEV、全量验收和灰度。外部 `HANDOFF_PLAN.md` 的四期编号与这个流程不同，其“Phase 1 黄金链路运行图”拆入后续运行链路阶段。

本阶段交付可执行的契约模型、纯状态转换、工具消息校验及测试。没有注册新的生产图模式，没有改变 API、SSE、路由器、四个现有子图、JEV 阈值或前端。新包 `app/agent/handoff/` 不主动创建运行图。现有应用尚未调用这些契约。

附件中的“用户决策”、并行轨道、一天上线、JEV 转正和灰度比例作为方案内容参考，不作为本次启用配置、访问真实账户、部署或推送授权。

## 2. 三 Agent 承接四类能力

Agent 身份是内部执行者，`RouteKey` 是既有任务能力及公开协议名称。两者不合并。

| 旧能力 / 外部 subgraph | 内部执行者 | 职责 | 工具可见域 |
|---|---|---|---|
| `general_chat` | GeneralChatAgent，节点 `general_chat_agent` | 情感陪伴、闲聊、澄清 | 只读当前歌曲上下文 |
| `current_song` | CuratorAgent，节点 `curator_agent` | 目标歌曲解析后的分析与说明；保留无歌曲/排队动作待执行的边界 | 当前歌曲上下文 |
| `tool_calling` | CuratorAgent | 搜索、策展、音乐库操作、播放器控制 | `MUSIC_AGENT_TOOLS`、`CLIENT_ACTION_TOOLS`、当前歌曲上下文 |
| `worldline` | ExplorerAgent，节点 `explorer_agent` | 实体解析、时代/场景/影响探索、带来源的结果 | WorldLine 内部能力、只读当前歌曲上下文 |

`get_current_song_context` 将承担只读歌曲上下文获取；歌曲分析回复由 Curator 负责。这个工具尚未实现。GeneralChat/Explorer 使用歌曲信息不会因此获得播放器或音乐库写权限。WorldLine 的内部子图在后续适配时继续保留。

`ToolDomain` 只声明权限域。运行时按 `task_tool_domains(agent, capability)` 绑定并在执行前再校验：中转 Agent 只可转交，没有业务工具域；Curator 执行 current_song 时只有只读上下文域。按域复用现有工具列表，不能把整个工具池注入所有 Agent。MUSIC 域承接现有 10 个音乐服务工具；CLIENT_ACTION 域承接现有 18 个客户端动作工具。可见工具仍需通过既有用户动作授权、登录条件、实体来源和参数校验。

注册表中每个 `RouteKey` 恰好有一个 owner。`bind_route_tasks` 逐项绑定原有 `RouteTask`，保留 `task_id`、`input_text`、`intent_orders`、`depends_on` 和重复能力任务，输出深拷贝。

例：“搜索晴天，不要播放；探索这首歌的时代；然后播放第二首”保留三项任务，依次为 Curator、Explorer、Curator。不能压成 `{Curator, Explorer}`，也不能因为已经搜索过就删掉最后的播放任务。

## 3. 首跳和运行中转交

首跳处理原有 `RouteDecision`，默认选择第一项任务的 owner；不完整决策直接生成澄清终答。后续最小运行链路可以显式指定 GeneralChat 作为入口，但不能改写任务计划。

转交只允许四条边：

| 来源 | 目标 | 适用条件 |
|---|---|---|
| GeneralChat | Curator | 下一待办为音乐能力，或经 Curator 中转到 Explorer |
| Curator | Explorer | 下一待办为 WorldLine 探索 |
| Curator | GeneralChat | 还有明确的陪伴/聊天任务待办 |
| Explorer | Curator | 下一待办为音乐能力，或经 Curator 中转到 GeneralChat |

边合法并不充分：目标必须是通往下一项待办 owner 的下一节点。直接到 owner 优先；四边网络中其余情况只需经过 Curator 一次中转。例如“探索之后聊天”按 Explorer→Curator→GeneralChat 转接，Curator 不执行聊天任务、不记录该任务结果。中转也计入跳数，每次必须更接近 owner；当前已是 owner 时不能为同一待办再转出。

这个规则完整保留四类能力的任意前后组合，同时禁止跳过任务、依赖和待澄清项。任务全部成功时直接进入终答；“播完交还陪伴”只有在计划确有陪伴待办时才转交，避免为了形式多走一跳。

计划不完整、任务失败或需要输入时不能继续转交。首次分诊仍使用既有路由契约；本阶段不修改 JEV V5 的四能力输出、固定 0.8 阈值或 Hybrid 回退。

## 4. 转交参数与服务端上下文

固定目标的模型工具名：`transfer_to_curator_agent`、`transfer_to_explorer_agent`、`transfer_to_general_chat_agent`。工具模型只可填写 `TransferArguments`：

| 字段 | 约束 / 用途 |
|---|---|
| `reason` | 非空，最多 512 字符；表达转交理由 |
| `context_summary` | 非空，最多 2,000 字符；接收方参考摘要 |

解析器根据实际工具名补充 `HandoffRequest.target_agent`。其余字段禁止作为工具参数，包括用户授权、任务计划、已完成结果、登录凭据。来源 Agent、run ID 和 tool call ID 从运行时取值。

服务端由 `HandoffRun` 组装 `HandoffEnvelope`：

| 字段 | 规则 |
|---|---|
| `schema_version` | 固定为 1 |
| `run_id`、`tool_call_id` | 非空，最多 128 字符；与运行任务和助手调用绑定 |
| `source_agent`、`request` | 必须符合允许边、下一待办路径和工具调用 |
| `original_input` | 完整保留本轮用户输入，最多 4,000 字符 |
| `active_task`、`pending_tasks` | 活跃项是第一项待办；保留顺序、能力和依赖；最多 32 项 |
| `completed_results` | 按任务顺序携带成功结果摘要，每项最多 1,000 字符；不复制任意 `TaskResult.data` |
| `entities` | 复用 `ReferencedEntities` 的歌曲、艺人、专辑、歌单引用及 ID 校验 |
| `evidence_refs` | 最多 12 个现有 `HistoryPointer`，供按需检索历史或产物 |

整个 JSON 的 UTF-8 编码最多 64 KiB。超限或无效上下文在组装时拒绝，不改变运行状态。后续工具工厂须把这个校验错误转为配对的错误 observation 或确定性终答，不能把异常留给模型无限重试。

任务计划中的 intent evidence 必须与原始输入及偏移一致。摘要和实体引用都是信息，不是执行授权。后续 Curator 调用现有授权校验时仍须使用真实用户任务片段，保留否定词，不能改用转交摘要、探索结果或历史文字。会话 cookie、账户凭据从受保护的 runtime context 注入，不进入 transfer 参数、envelope 或 trace。

没有 `intent_orders` 的旧式任务必须保留完整 `original_input`，不能仅挑出用户否定句中的“播放”作为授权片段。有 intent 引用时，继续采用原有 RouteDecision 的证据匹配和覆盖校验。

Agent 对外结果复用现有 `TaskResult`。完整数据保留在运行结果/产物中，转交只携带摘要和真实引用。这里的 `HandoffEnvelope` 是 Agent 控制转交；Long Horizon 的 `HandoffProjection` 是会话窗口继承，两者生命周期不同，只复用 `HistoryPointer`。

## 5. 状态、初始化和终止

`HandoffState` 继承 `BaseAgentState`，保留 `messages` 的 `add_messages` reducer、`current_input` 和 `continuity_context`。新增独立 `handoff_run` 和只读 `routing_context`。Agent 私有 scratchpad 留在各 Agent 的本地状态。

`HandoffRun` 保存本轮输入和计划、entry/current Agent、起始时间、限制、Agent 用量、按序结果、已接受 hops、运行状态、停止原因和终答。

- `pending_tasks` 由计划减去按序结果得到。
- `handoff_count` 由已接受 hops 长度得到；`handoff_chain` 由 entry 和每个 hop 目标得到。两者是派生属性，不重复存储，恢复时不会出现计数和链条互相矛盾。
- `total_tokens` 由各 Agent 的累计用量相加得到。允许记录实际消耗超过预算的事实，不能为了满足校验把账单截成上限。
- 单轮最多 32 个任务、5 次已接受转交。结果必须按任务 ID 和能力匹配，不能重复或越序写入。

| 事件 | 状态变化 | 后续行为 |
|---|---|---|
| 完整计划进入新轮次 | `RUNNING` | 执行下一待办；当前 owner 才能记录结果 |
| 路由不完整 | `NEEDS_INPUT` | 返回路由问题或确定性澄清；不派发任务 |
| 一项成功、仍有待办 | 保持 `RUNNING` | 承接下一待办；必要时转交 |
| 全部成功 | `FINALIZING / ALL_TASKS_DONE` | `finish_run` 生成一次终答，进入 `COMPLETED` |
| 任务失败 | `FINALIZING / TASK_FAILED` | 停止后续派发；脱敏终答后进入 `FAILED` |
| 任务需要输入 | `NEEDS_INPUT / CLARIFICATION` | 直接以问题作本轮终答，等待新请求 |
| 上限、不可用或取消 | 明确 failure reason，经 `finish_run` 到 `FAILED` | 保留已完成结果和待办，不声称全部成功 |

所有终态必须包含非空终答，未完成任务不能标为成功。终态不能再次 finalize，也不能接受结果或转交。`FINALIZING` 是有待生成终答的暂态；后续运行图必须从它进入终答节点，不能直接 END。

新轮次调用 `new_run`，使用新的 run ID 和当前完整输入，清空 hops、结果、用量、重试和旧终答，保留会话消息。恢复同一轮次使用 schema 校验，不调用 `new_run`，必须保留原计数、结果、限制及起始时间。Pydantic frozen 不是深层容器不可变的保证；运行状态由服务端持有，转换/恢复通过重新校验的快照完成，不向模型暴露可写 state。

## 6. 有界转交与失败规则

`request_handoff` 是纯转换函数，不调用 LLM/JEV、业务工具或系统时钟。调用方传入可信时间和现有运行状态；通过后返回新状态和 `HandoffResult`，拒绝时返回原状态及拒绝码。

检查顺序：运行状态 → 来源/重复 call ID → 总时间 → 累计预算 → 跳数 → 允许边 → 无进展回跳 → 下一任务 owner 路径。只有 `ACCEPTED` 携带 envelope。中转可读取任务上下文，`record_task_result` 仍只允许能力 owner 写结果。

“无进展”定义为没有新增成功任务结果。立即回跳 A→B→A 且无进展时拒绝；完成上一任务后为新任务回跳可以通过，例如搜索完成 → 探索完成 → 播放新歌。预算和总超时边界采用 `>=`，达到上限时拒绝下一次转交。

默认限制是后续运行图的初始工程参数，不是质量或性能结论：

| 限制 | 默认值 | 本阶段的实现边界 |
|---|---|---|
| 已接受转交 | 5 跳，硬上限 5 | schema、计数和纯转换检查已实现 |
| Agent 重试 | 每 Agent 每轮最多 1 次 | 用量 schema 限制；实际重试调度后续实现 |
| Agent 超时 | 45 秒 | 参数与一致性校验；实际取消/超时执行后续实现 |
| 整轮超时 | 120 秒 | 固定起始时间、恢复和转交入口检查；执行期间 deadline 后续接入 |
| Agent token | 累计 16,000 | 用量字段和转交入口检查；实际 usage 采集后续接入 |
| 整轮 token | 累计 32,000 | 跨 Agent 合计和转交入口检查；调用前预留/调用后计费后续接入 |

L1 继续使用现有 `ContextPack`。后续 L2/L3 必须统计模型、路由、摘要和护栏的实际消耗，重试与回跳不能重置计数；按需先裁剪历史再压缩摘要，仍不足则终止。不能把本阶段字段和纯函数测试解释为全链路预算已经生效。

后续回退规则：可恢复的执行失败最多重试当前 Agent 一次，再作有限的 GeneralChat 文本兜底，最终进入终答。时间/预算/跳数已耗尽时直接确定性终答，不为了兜底再调用模型。兜底不是新的业务转交，不能绕过允许边继续执行音乐操作，不能把失败结果重写成成功。JEV 不可用时采用有界本地检查/明确拒绝策略，不再无限向 LLM 询问是否转交。

`HandoffTrace` 仅允许 schema/trace/run/call ID、from/to、结果码、可选护栏分数、耗时和 token 用量。分数限制在 [0,1]，计量为非负整数。原始请求、摘要、实体、scratchpad 和登录上下文不能塞入 trace。实际事件写入、JEV guard 和 retry 调度属于后续阶段。

## 7. 消息与公开协议兼容

`parse_transfer_call` 要求一次助手输出恰好包含一个有效 transfer tool call，来源 allowlist、参数 schema、call ID 均合法。transfer 与业务工具并发调用时拒绝；后续工具工厂须对非法整组调用逐项补齐错误 ToolMessage，不能只消失一半消息。

接受和拒绝都需要 AIMessage/ToolMessage 配对，`tool_call_id` 保持一致。`transfer_message_pair` 校验 run ID，按 run/call ID 生成稳定 observation ID；重建同一消息对再经 `add_messages` 不会追加重复控制消息。它在复制控制消息时清空助手推理正文，只输出结果码和 accepted，envelope 单独留在内部状态。接收方不复制源 Agent 私有 scratchpad。

后续图把已接受结果转换为 `Command(goto=AGENT_REGISTRY[target].node_name, update=...)`；本阶段没有执行 Command，也不依赖附件里未核实的 helper API。

公开兼容约束：

1. 继续以原有 `TaskResult.task_id` 和四类 `route_key` 投影 FoundationResponse，内部三 Agent 名称不替换外部 `subgraph`。
2. 继续使用现有 `message/update/action/final/error/done` SSE。transfer arguments、ToolMessage、private state 和 trace 不能直接变成公开回答 token；新增节点需要在后续流适配器中显式识别。
3. 客户端动作仍使用现有 `ClientAction` 校验、稳定 `action_id`、本轮 `task_id` 和前端去重。成功转交不等于浏览器动作执行成功；重试/恢复不能重新生成并重复派发同一个播放动作。
4. WorldLine 的 citations/sources 和音乐结果的公开字段仍经现有 allowlist 输出。转交工具不绕过投影。

本阶段验证了旧响应投影及 reducer，既有完整应用回归验证 API/SSE/动作契约；新的节点白名单和真实动作恢复需在运行图阶段补验。

## 8. Checkpoint 与回滚边界

本阶段只验证 `HandoffRun` JSON round-trip 与不一致状态拒绝，没有访问 PostgreSQL。unknown version、旧 Foundation state、重复 hops、用量异常、伪造终答或替换任务证据不能作为 v1 run 自动恢复。

后续运行图必须做到：

- 用 thread/session/run 区分会话、窗口和本轮请求；同一轮恢复不能重建时间和预算。
- handoff 使用独立版本 namespace（例如 `handoff:v1:<session_id>`），保留 Foundation 的原 namespace；确切接线在接入阶段验证。
- 一个活跃 session 固定 graph mode。切回 Foundation 时开启该模式的新窗口或明确迁移，不能把三 Agent checkpoint 当成四子图 checkpoint 读取。
- 父图统一持久化；各 Agent 私有子图继续沿用现有 adapter 隔离方式。
- 发布前补真实 PostgreSQL 中断恢复、动作去重、跨轮清理和回滚测试。

因此这里定义兼容边界，不宣称只改环境变量就能恢复任意旧/新 checkpoint。

## 9. 契约验收与下一阶段入口

实现位置：

- [注册表](../../ai-service/app/agent/handoff/registry.py)：身份、权限域、允许边和任务绑定。
- [转交模型](../../ai-service/app/agent/handoff/contracts.py)：工具参数、内部上下文、结果及 trace。
- [状态与转换](../../ai-service/app/agent/handoff/state.py)：初始化、记录结果、转交、终答与恢复校验。
- [控制消息](../../ai-service/app/agent/handoff/messages.py)：调用校验和消息配对。
- [测试](../../ai-service/tests/test_handoff_contracts.py)：成功、边界、拒绝和兼容场景。

测试覆盖能力完整映射、四类能力的全部 16 种前后组合、重复任务/依赖/否定保留、四条边、中转和有进展回跳、澄清和失败停止、5/6 跳、预算和时间边界、非法模型参数、超大实体、call ID 配对与重放、恢复/新轮次区分，以及旧公开响应名称。

阶段 2 使用本契约接入 GeneralChat→Curator 最小链路，完成当前歌曲只读工具、复用现有工具权限校验、transfer tool 工厂与 Command、可选 handoff 模式及受控 E2E。Explorer 接线随后完成。运行时上限、真实持久化、JEV 独立评测、视觉/真实播放和灰度验证继续按分阶段验收，不以 schema 测试替代。

具体验证命令、结果及保留限制见[阶段 1 验证报告](../reports/handoff-phase1-20261001/validation.md)。

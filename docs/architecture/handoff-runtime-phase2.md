# MoodFlow Handoff 阶段 2：GeneralChat / Curator 运行链路

日期：2026-10-01。基于 [v1 契约](handoff-contract-v1.md)，本阶段将纯契约接入实际 LangGraph；默认模式仍为 `foundation`。

## 1. 入口与执行流程

1. `prepare_context` 为当前请求记录起始时间并清空本轮 run/终答，保留会话消息。
2. 复用现有 HybridRouter，校验完整 `RouteDecision`，创建新的 `HandoffRun`。不完整计划直接澄清；路由异常返回受控失败。
3. 从 `general_chat_agent` 进入。下一任务属于当前 Agent 时执行；属于另一 Agent 时，模型仅绑定该来源允许的 transfer 工具。
4. 工具工厂校验模型参数和服务器 run，调用 `request_handoff`，返回真正的 `Command(goto=..., update=...)`。接受时保留原始任务及顺序、增加一次 hop，并保存配对的 AI/Tool 控制消息和服务端 envelope。
5. GeneralChat 执行聊天；Curator 按当前任务分别执行只读歌曲分析或原有受保护的工具任务。只有能力 owner 可以记录 `TaskResult`。
6. 成功后继续下一任务，包括 Curator→GeneralChat 返回。失败、需要补充输入、转交拒绝或上限耗尽时停止后续任务，确定性终答。

Explorer 尚未接入。**计划包含任意 `worldline` 任务时整轮预检失败，执行任何音乐动作前停止**，避免阶段 2 模式先产生部分操作。原 Foundation 的 WorldLine 能力不受影响。

## 2. 能力与权限边界

| 当前执行内容 | 模型/执行器可见能力 |
|---|---|
| GeneralChat 聊天 | 仅 `get_current_song_context`，最多两次单独调用，然后必须输出文本 |
| 非 owner 转交 | 仅注册表允许的 transfer；不能同时执行音乐工具 |
| Curator 当前歌曲分析 | 服务端调用只读上下文工具，再复用 CurrentSong 提示词；分析模型不绑定操作工具 |
| Curator 工具任务 | 原有音乐工具、播放器动作与只读上下文；模型和执行器使用同一 allowlist |

Curator 复用原 ToolCalling 子图的五次调用上限、参数校验、搜索实体引用、否定语义、当前任务动作授权、重复操作控制以及稳定 `action_id`。登录凭据只经 `MusicAgentContext` 注入；API 仅转发 `JSESSIONID`，用户 ID 仍从 Spring 服务端会话解析，transfer 参数不能填写凭据或授权字段。

`get_current_song_context` 没有可填写的参数，显式拒绝额外字段。目标优先取本轮可信前置播放动作，否则使用请求的播放器快照。缺少歌曲返回 `needs_input`；尚未解析目标的切歌动作要求等待播放器更新；资料服务失败或返回错误歌曲 ID 输出脱敏错误。分析公开 metadata 保留歌曲身份及资料状态，不公开歌词。

## 3. 状态、历史与恢复

父图新增 JSON primitive 字段 `handoff_run`、`transfer_envelope`、失败/澄清标记及起始时间，继续使用已有 checkpointer 与严格 serializer，不扩大反序列化 allowlist。

子图沿用 Foundation adapter。发送给业务提示词的历史剔除父图控制调用；子图工具 scratchpad 不进入父图。adapter 的完成判断使用当前 run 的结果前缀，旧轮次同名任务不能被误认为本轮已完成；非冲突历史仍可提供原有实体上下文。

受控内存检查点验证了转交后、Curator 执行前中断与恢复：同一 run 不重新初始化，不重复增加 hop。新请求创建新 run，清空本轮结果和计数。当前 API 每次 POST 都表示新请求；进程恢复/重试 API 与业务操作幂等性仍待后续阶段完善。

handoff 使用 `handoff:v1:<public_thread_id>` 作为内部 conversation/checkpoint thread。既有 Long Horizon 为此 conversation 创建独立 task/session，并继续将 `session_id` 映射为检查点存储 namespace。因此 Foundation 与 handoff 的活跃窗口固定各自图类型。切换模式后使用该模式独立会话历史，切回 Foundation 会恢复其原窗口；本阶段不迁移两个模式的会话历史，用户级长期记忆沿用现有共享语义。SQLite continuity + 内存 saver 的受控测试验证了双向切换；真实 PostgreSQL 重启尚未验收。

## 4. HTTP / SSE 兼容与试用

- 新增 `POST /api/ai/handoff` JSON 入口，请求仍为 `ChatRequest`，响应仍为 `FoundationResponse`。
- `POST /api/ai/chat` 仅在 `AI_AGENT_GRAPH_MODE=handoff` 时选用新图；`/foundation` 始终使用原图。
- 不修改 `.env`、默认配置、JEV 策略或前端代码；没有部署或 push。

试用 JSON 示例：

```json
{
  "message": "暂停一下",
  "thread_id": "handoff-demo",
  "player_context": {"has_current_song": true, "current_song_id": 11}
}
```

向 `/api/ai/handoff` 提交此请求可测试 JSON 链路；歌曲 ID 应替换为实际播放器状态。需要测试 SSE 时在**启动进程**设置 `AI_AGENT_GRAPH_MODE=handoff`，重启服务后请求 `/api/ai/chat`。回滚时恢复 `foundation` 并重启，使用 Foundation 的原窗口。

公开 `subgraph` 继续为 `general_chat/current_song/tool_calling/worldline`；新内部节点的进度映射为旧公开节点名。保留 `message/update/action/final/error/done` 事件。新增模式过滤所有原始模型 token 和嵌套更新，仅在父图产生校验后的 TaskResult 时输出文本，并从 Curator 的工具任务派发校验后的动作、按 action ID 去重。异常仍有 `error` 和 `done`，未处理 JSON 异常为脱敏 HTTP 502。

这意味着 handoff 当前按任务完成输出文本，首字延迟可能增加。多任务失败时，已完成结果/动作仍保留在公开 results，final 返回受控失败；成功转交或生成动作不代表浏览器已实际完成播放。

## 5. 本阶段限制与后续工作

已接入：最多五次转交、无进展回跳拒绝、45 秒 Agent 执行超时、120 秒整轮起始时间检查及执行阶段剩余时间约束、非法调用配对与确定性停止。没有自动重试业务工具。

仍待阶段 3/4 及验收阶段：Explorer 运行节点、JEV transfer guard、全链路实际 token usage 与调用前预留、有限重试/兜底、trace 写入、真实 PostgreSQL 恢复、云端操作幂等、真实模型质量/成本/时延、登录账户接口、浏览器播放及灰度验证。**当前 usage 仍为初始化值，不能将 v1 token 字段解释为实际累计预算已经生效。** 超时也不保证已发出的上游写操作被撤销。

验证命令、结果和环境限制见 [阶段 2 验证报告](../reports/handoff-phase2-20261001/validation.md)。

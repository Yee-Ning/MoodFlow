# MoodFlow Handoff 阶段 3：完整业务网络

日期：2026-10-01。承接 [v1 契约](handoff-contract-v1.md) 和 [阶段 2 运行链路](handoff-runtime-phase2.md)。本阶段接入 Explorer，完成三 Agent / 四能力的实际执行网络；默认配置仍为 Foundation。

## 1. 三 Agent 与四条边

| Agent | 执行能力 | 转交目标 |
|---|---|---|
| GeneralChat | 情感陪伴、聊天、按需只读歌曲上下文 | Curator |
| Curator | 搜索及音乐操作、播放器动作、当前歌曲只读分析 | GeneralChat、Explorer |
| Explorer | 复用完整 WorldLine 的实体解析、检索、图谱、时代/场景/影响关系、叙事、分支与来源校验 | Curator |

Explorer 已加入父图，去除阶段 2 对 WorldLine 计划的预先拒绝。入口仍复用 HybridRouter 的严格任务计划，从 GeneralChat 开始；本阶段不改 JEV 阈值、首跳策略或原 Foundation 图。

GeneralChat→Curator→Explorer 可执行单独探索；Explorer→Curator 可继续音乐操作；Explorer→Curator→GeneralChat 可返回陪伴。中转的 Curator 仅绑定 transfer，不运行自己的音乐模型、不产生聊天/探索任务结果。每次接受仍通过同一个 transfer 工具工厂和 `Command`，跳数包含中转。

典型完整链路是“陪我聊聊；暂停一下；探索歌手《周杰伦》的世界线；搜索晴天并播放；继续陪我聊聊”：GeneralChat→Curator→Explorer→Curator→GeneralChat，执行五项任务、接受四次转交。任务顺序、task ID、重复能力、依赖和原始文本保留。没有未完成业务任务时结束，不为完成的单项探索额外转回入口。

## 2. Explorer 复用与可信实体衔接

新增 `handoff/explorer.py` 只负责任务输入准备和结果校验；业务执行仍为原 `create_worldline_subgraph()`。默认子图按需创建并缓存，父图统一持久化，WorldLine 私有子图关闭独立 checkpoint。测试可注入现有子图替身依赖。

Explorer 用 `create_worldline_state()` 初始化完整私有状态，不复制其他 Agent 的 scratchpad。完成后必须返回匹配原 task ID / WorldLine 能力的 `TaskResult`，成功数据再次验证 `WorldLineResult`；可信歌曲目标被替换时失败。私有检索文档、查询计划、模型消息和 trusted target 不直接进入公开 SSE。

实体规则：

1. 明确命名的歌曲/歌手/专辑继续走原 WorldLine 目录解析和查询理解；“搜索周杰伦；探索他的世界线”复用原同轮歌手指代解析。
2. 未明确命名的“这首歌”：本轮存在已指定目标的播放动作时，优先使用该目标；尚未确定新歌曲的切歌动作先要求播放器更新。此前只有搜索时，最近一组可信歌曲结果若唯一就绑定该 ID；多首候选要求用户明确，不凭摘要猜测。
3. “当前歌曲/正在播放”或没有前置搜索时，调用阶段 2 的无参数只读工具，使用可信播放器/前置播放目标。无当前歌曲、资料异常、返回错误 ID 等沿用受控状态并停止后续任务。
4. 唯一可信歌曲被转换为已有 `ResolvedEntity`，只在子查询中替换指代并设 `trusted_target`，避免再按名字猜目录 ID；原 RouteTask 和原始请求不改写。歌曲完整歌词不随此绑定进入公开响应。

transfer envelope 现在携带最近成功工具任务的 `ReferencedEntities`，继续受 v1 schema 与 64 KiB 上限约束。参数中仍只有 reason/context_summary，模型不能填写实体、待办、权限或凭据。接收方从可信 run/result 准备上下文，模型摘要不替换授权来源。

## 3. 探索之后继续播放

成功探索后的节点身份、名称、分支、叙事摘要和 source ID，以有界投影进入下一业务提示词的现有 `ContextPack`，保留原 Long Horizon window handoff。只保留最近一项探索：叙事最多 600 字符、节点最多 8 个且名称最多 120 字符、分支最多 3 个、source ID 最多 8 个；文本标记为不可信数据、没有动作授权。

该投影让 Curator 能理解“播放探索中发现的那首歌”，但不生成 playable `SongReference`。WorldLine 图谱节点只有目录/知识身份，不具备播放器所需完整歌曲字段。Curator 必须搜索歌曲取得可信完整 track，再由原参数、实体引用和当前任务授权规则决定是否派发播放动作。已有 Curator 搜索结果可继续作为可信历史引用；Explorer 或模型不能额外授权歌曲操作。

Explorer 没有音乐写入或播放器命令工具。回到其他 Agent 以后，任务视图仍剔除父图控制调用，父图只接收统一业务结果和配对控制消息。

## 4. 公开协议、失败和恢复

继续使用 `/api/ai/handoff` JSON 和配置为 handoff 的 `/api/ai/chat` SSE。Explorer 进度映射为原 `worldline_subgraph`；结果的 `subgraph` 仍为 `worldline`，citations、sources、worldline_result 沿用原公开投影。只有 Curator 的 ToolCalling 结果可以派发播放器动作，仍校验并按 action ID 去重。新模式继续按任务完成输出文本。

实体/资料异常、生成失败、歧义和超时会保留已完成结果，停止后续任务，返回 failed 或 needs_input。原 WorldLine 的知识检索降级行为保留：检索不可用时可以仅基于已取得的目录事实继续，受控验证只含目录来源，不伪造知识证据。

五跳硬上限、无进展回跳拒绝、Agent/整轮超时、确定性停止和新轮清理继续生效。Explorer 前中断后可用父图 saver 恢复，保留两次已完成的中转，不重新增加 hop；跨轮结果按当前 run 隔离。handoff 和 Foundation 的 conversation/session namespace 沿用阶段 2 的独立规则。

原独立 WorldLine session/分支 API 与前端探索页面保持原行为；本阶段将单轮 WorldLine 任务接入 Agent 网络，没有改造已有独立探索会话 API，也不迁移其 checkpoint。

## 5. 试用、验收边界与后续

可向 `POST /api/ai/handoff` 提交：

```json
{"message":"探索歌手《周杰伦》的世界线","thread_id":"handoff-explore-demo"}
```

测试“当前歌曲”时提供实际 `player_context`。SSE 试用仍需启动进程设置 `AI_AGENT_GRAPH_MODE=handoff` 并重启；切回 Foundation 同样恢复启动配置并重启。本次没有修改 `.env`、默认模式、生产服务、Java 或前端代码，也没有部署或 push。

本阶段验证真实 LangGraph 节点、Command、WorldLine 内部流程及 HTTP/SSE 协议，模型/音乐/检索/生成依赖为受控替身。新增 49 项网络测试包含四能力全部 16 种前后组合、完整业务链、证据关系、目标解析、权限拒绝、异常/超时、五/六跳和内存 checkpoint 恢复；不把这些结果解释为在线质量或实际播放验收。

阶段 4 继续实现 JEV transfer guard、全链路实际 usage/调用前预留、有限重试/兜底与 trace；当前累计 token 字段仍为初始化值。真实 PostgreSQL 重启、云端写入幂等、模型质量/时延/成本、登录账号接口、浏览器音频与灰度验证仍待后续验收。

命令、结果、早期失败与环境限制见 [阶段 3 验证报告](../reports/handoff-phase3-20261001/validation.md)。阶段 1/2 文档作为各阶段历史快照保留。

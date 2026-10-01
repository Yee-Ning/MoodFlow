# MoodFlow Handoff 阶段 6：交付验收

日期：2026-10-01；承接[阶段 5](handoff-runtime-phase5.md)，开始基线 `dbd591a`。

## 验收范围与结论

本阶段交付可重复执行的真实 HTTP 验收工具、真实 PostgreSQL 中断恢复工具、受控测试的环境隔离修复，以及后端、真实依赖、浏览器和前端的验收证据。没有新增依赖，也没有改动业务算法、部署、实际 `.env` 或默认图。

**验收执行与证据交付已经完成，业务验收尚未全部通过。** 当前发现 JEV 入口生效时部分搜索耗尽预算、Hybrid 对明确双任务作过度澄清，以及 WorldLine 返回无关的知识检索结果。记录来源与引用结构完整，不等于检索内容相关或答案正确。这些问题修复并复验前，不满足阶段 7 小流量切换条件。

具体命令、计数、失败记录与未验证项见[验收报告](../reports/handoff-phase6-20261001/validation.md)，可核查数据见[机器证据](../reports/handoff-phase6-20261001/evidence.json)。

## 真实 HTTP 验收

在 `ai-service` 执行：

```powershell
& .venv/Scripts/python.exe -m scripts.handoff_acceptance --base-url http://127.0.0.1:8001 --output ../.dev/phase6-replay/http.json
```

服务须先在隔离验收环境启动。工具自身不启用 handoff/JEV，不设置数据库、不替换图、模型、工具或返回结果；真实业务调用可能使用正常配置的模型。HTTP 客户端禁用环境代理，服务端的本地依赖仍需正常配置 loopback 的 `NO_PROXY`。本次只在子进程补充本地地址，没有改变系统代理配置。

固定用例包括健康检查、三种非法请求、Foundation 兼容、普通聊天、缺少歌曲目标、当前歌曲只读分析、只读搜索、暂停 SSE、暂停后聊天、世界线来源协议。暂停和歌曲分析使用显式的合成播放器快照；验收不会控制实际播放器，也不会写入音乐账户。

判定同时检查 HTTP 状态、公开响应模型、业务状态、路由顺序、类型化客户端动作，以及 SSE 的完整 final/done、动作去重和 action/final 一致性。缺目标可在入口提前澄清，或由 current_song 澄清，二者都保持 `needs_input`；不把澄清算作业务成功。来源用例仅检查非空 sources/citations，相关性必须另行人工验收。

全部检查通过退出 0；任一失败退出 2。没有自动重试、放宽运行预算或以 HTTP 200 替代成功判定。服务连接异常只记录异常类型，不保存请求头、凭据或上游异常正文；业务失败仅保留通过公开 schema 检查的响应。输出目录每次应新建，避免覆盖历史证据。

## PostgreSQL 中断恢复

在专用验收数据库中使用现有迁移、官方 saver 和正常 handoff graph；两次命令分属两个进程。中间由验收环境维护者重启该专用数据库，等待其恢复，不操作现有业务数据库。

```powershell
& .venv/Scripts/python.exe -m scripts.handoff_checkpoint_acceptance create --thread-id phase6:resume:new-run --output ../.dev/phase6-replay/created.json
# 专用数据库恢复后，重新启动 Python 进程：
& .venv/Scripts/python.exe -m scripts.handoff_checkpoint_acceptance resume --thread-id phase6:resume:new-run --output ../.dev/phase6-replay/resumed.json
```

必须显式配置 `CHECKPOINT_BACKEND=postgres` 和验收数据库。工具限制 `phase6:` 线程前缀，create 拒绝已有 checkpoint，resume 拒绝缺失 checkpoint。create 在正常 GeneralChat → Curator 交接完成后、Curator 执行前中断；resume 通过 `ainvoke(None)` 恢复。

证据只导出 run ID、状态、交接链、账本、控制调用配对计数和类型化动作，不保存消息内容、reason/summary 或 scratchpad。实际证明运行中 checkpoint 经数据库和进程重启后完成，同一 run 的 GeneralChat usage、转交次数和调用数没有重算，控制调用与 ToolMessage 成对，最终仅生成一次 pause_player。它证明服务端恢复和动作输出，不能证明浏览器已执行动作或收到实际播放 ACK。

## 测试隔离与交付边界

受控业务网络测试仍使用真实规则分析器，但注入明确的离线分类 runtime，避免场景测试意外调用真实 LLM。验收脚本的环境加载仅在 CLI 入口进行，导入脚本、调用辅助函数均不加载真实 dotenv；回归覆盖进程环境隔离、公开协议、失败流和消息脱敏。

本次真实联调同时运行入口 Shadow 和隔离进程的入口生效模式，交接护栏始终为 Shadow，使用默认 0.8 / 0.2。没有启用阶段 5 的候选 0.7，也没有修改实际 Foundation 默认配置。运行日志只保存已有脱敏事件，所有测试数据库与本地服务都属于本次验收环境。

浏览器检查当前代码的搜索、AI 流式聊天、只读搜索、新会话、世界线创建/分支/刷新恢复与移动和桌面布局。登录态实际播放、收藏/歌单账户写入、客户端 ACK 与重放、真实 Explorer → Curator 组合请求、JEV 护栏阻断模式及负载/SLA 仍未完整验证。报告列明这些限制，不能据此宣布生产启用。

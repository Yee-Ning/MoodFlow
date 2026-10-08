# MoodFlow Handoff 阶段 2 验证记录

日期：2026-10-01，Asia/Shanghai。阶段 1 基线提交 `5b8f532`，分支 `codex/parse-normalize-corpus-v1`。

## 1. 交付内容与影响范围

阶段 2 实现 GeneralChat / Curator 最小运行链路：只读当前歌曲工具、transfer 工具工厂与真实 LangGraph Command、受保护的音乐/播放器工具执行、可选 handoff 模式及受控 HTTP/SSE E2E。

本次新增五个运行模块及 `test_handoff_runtime.py`，修改 AI 服务的 Settings、API 入口和现有公开流适配器，新增 [运行说明](../../architecture/handoff-runtime-phase2.md)、本报告与 [机器证据](evidence.json)。不新增依赖，不修改 Java、前端、JEV 策略或 `.env`，默认继续使用 Foundation。

- GeneralChat→Curator 的控制权由校验后的 transfer 工具返回 `Command` 实际切换；Curator 可按剩余聊天任务返回 GeneralChat。
- 当前歌曲分析归 Curator；两个 Agent 都有按可信快照/前置播放结果取得资料的只读工具。GeneralChat 不获得播放器操作工具。
- Curator 复用 Foundation 的参数、实体引用、否定语义、动作授权、五次工具调用上限和客户端动作 schema。
- 保留任务顺序及依赖、完成前缀、失败/澄清停止；转交次数和时间边界进入实际运行流程。
- 新增 `/api/ai/handoff` JSON 入口；设置 `AI_AGENT_GRAPH_MODE=handoff` 后 `/api/ai/chat` 使用新图，公开 JSON/SSE 字段及动作协议继续兼容。
- 新模式只输出校验后的 TaskResult，屏蔽控制调用、内部 envelope、原始模型 token 和嵌套状态。公开节点名称映射为原有名称。
- 当前 run 的结果决定 adapter 完成状态；旧轮次复用任务 ID 不会跳过本轮任务。handoff conversation/session 与 Foundation 分开，切回原模式恢复其窗口。

Explorer 留到阶段 3。阶段 2 对含 WorldLine 的整个计划预检失败，先于任何音乐动作执行；Foundation 原探索入口仍保留。

本次提交仅包含上述任务文件。用户已有修改、删除项及未跟踪数据/报告保持原状，保护文件 SHA-256 与初始值一致；证据生成时核对 unrelated status 与初始快照一致。

## 2. 最终验证

AI 命令工作目录为 `ai-service`；前端命令工作目录为 `soul-music-room`。完整日志/JUnit 位于被忽略的 `.dev/handoff-phase2-20261001/`，机器证据记录摘要和 SHA-256。43 项新运行测试及原有 94 项契约测试已包含在应用全量结果中，不重复计数。

| 命令 | 最终结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests -q -ra --tb=short` | **1472 passed，14 skipped，1 warning，286 subtests passed**，退出 0；含 43 项新运行测试 |
| 应用环境执行 `evals/tests` 除 Judge 外的 20 个测试文件 | **173 passed**，退出 0 |
| `.venv-eval/Scripts/python.exe -m pytest evals/tests/test_judges.py -q -ra --tb=short` | **4 passed**，退出 0；使用受控客户端 |
| `.venv/Scripts/python.exe -m ruff check app/agent/handoff app/agent/foundation/streaming.py app/api/chat.py app/config/settings.py tests/test_handoff_contracts.py tests/test_handoff_runtime.py` | All checks passed，退出 0 |
| `.venv/Scripts/python.exe -m ruff format --check app/agent/handoff tests/test_handoff_contracts.py tests/test_handoff_runtime.py` | 12 files already formatted，退出 0 |
| `.venv/Scripts/python.exe -m compileall -q app tests evals scripts` | 退出 0 |
| `.venv/Scripts/python.exe -m pip check` | No broken requirements found，退出 0 |
| `.venv/Scripts/python.exe -m ruff check . --output-format json` | 45 项既有问题，退出 1；与阶段 0 path/line/code/message 清单完全一致，差异 0 |
| `npm run test:ai-stream` | AI chat streaming store checks passed，退出 0 |
| `npm run lint` | 退出 0 |
| `npm run build` | TypeScript + Vite 构建成功，退出 0；保留既有大 chunk warning |
| `git diff --cached --check` | 本次任务文件通过 |

评测框架运行方式沿用阶段 1 的依赖隔离：

```powershell
$taskEvalFiles = @(
    Get-ChildItem -LiteralPath 'evals/tests' -Filter 'test_*.py' -File |
        Where-Object { $_.Name -ne 'test_judges.py' } |
        Sort-Object Name |
        ForEach-Object { 'evals/tests/' + $_.Name }
)
& .venv/Scripts/python.exe -m pytest @taskEvalFiles -q -ra --tb=short
```

测试进程使用离线 LLM 占位值、关闭 JEV/live Postgres opt-in、memory checkpoint、sqlite continuity、禁用 memory backend、空数据库 URL。涉及模型、音乐资料、音乐搜索、账号身份和 Judge 的新链路测试使用明确替身；没有写入系统配置或 `.env`。

应用环境未安装 DeepEval，继续分别运行应用与评测环境，没有重新执行已知会收集失败的总入口，也没有删除/跳过文件来规避依赖问题。

## 3. 关键路径验收

| 场景 | 验证结论 |
|---|---|
| General→Curator 暂停 | 实际执行 Command，hop 为 1，生成校验后的暂停动作；控制消息成对且正文清空 |
| Curator→General 继续聊天 | 前一动作结果保留、剩余任务继续，完成后允许返回；文本提示词没有孤立的控制 tool call |
| 搜索→播放→分析 | 真实 guarded 子图执行受控搜索与播放；后续分析使用播放目标 ID，覆盖旧播放器快照 |
| General 读取歌曲 | 仅绑定只读工具；正确提供 ToolMessage 后再回答；尝试播放器操作直接失败，无动作 |
| 否定授权 | “不要播放”时模型尝试播放被既有 authorizer 拒绝，观察结果含拒绝码，未执行动作 |
| 非法 transfer | 普通文本、并行业务调用、未知目标、额外授权字段、坏 JSON、缺失/重复 call ID 均有限失败；每个控制调用都有对应错误 observation |
| 最大 run ID | 错误调用的合成 call ID 有界、稳定且配对，重放得到相同消息 |
| 澄清/资料异常 | 不完整路由、无歌曲、待完成切歌、服务异常、歌曲 ID 不匹配均停止后续任务且输出受控状态 |
| Explorer 未接入 | 单独探索及“音乐操作；探索”都在业务操作前失败，无部分音乐动作 |
| hop / 时间上限 | hop 拒绝不增加次数、已完成动作保留；整轮 deadline 在模型前停止；慢 Agent 被实际超时取消并生成失败结果 |
| checkpoint 恢复 | Curator 前中断、内存 saver 恢复后保持一次转交，不重新初始化本轮计数 |
| 新轮与模式切换 | 复用 task ID 的第二轮仍成功；同公开 thread 的两图 checkpoint 隔离；实际 SQLite continuity session 切换与回滚保持分开 |
| HTTP / SSE | 请求校验、JSON/流 final 兼容、action 单次派发、无内部字段泄漏、未处理 JSON 502、SSE error/done 结束 |
| 登录上下文 | API 只转发 JSESSIONID，可信 user ID 到达真实 ToolRuntime；额外浏览器 cookie 和凭据不进入公开响应 |
| 前端调用方 | 原有 SSE 消费/播放器动作去重脚本、lint、构建通过；没有改变 UI |

这些结论证明受控依赖下的运行及 API/SSE E2E，不代表真实模型输出质量、账号接口或音频播放已经验收。

## 4. 早期失败与纠正记录

初次核心运行为 13 failed / 146 passed。修正新节点公开进度名称不兼容、空参数工具额外字段校验缺口，并修正测试模型 `_generate` 签名、状态断言和暂停用例的播放器/子意图输入；未削弱动作授权或旧协议校验。

随后 runtime 运行有 1 failed / 32 passed：测试错误地在公开 metadata 寻找内部拒绝码，改为验证模型收到的 ToolMessage 含 `unauthorized_action`，同时断言任务失败且没有动作。扩大核心集合后有 2 failed / 165 passed，原因是新增夹具使用 `args` 而非正式 `arguments`，及缺少动作 ID/task ID；按正式 schema 修正，并把身份夹具改成 provider 的实际字符串类型。

SQLite continuity 新用例首次有 1 failed / 1 passed，原因是测试替身直接写 frozen RouteTask；改为新校验快照。强化重复轮次断言时发现 adapter 会把旧同名 ID 当成本轮完成项，已修正任务视图，并验证第二轮实际成功产生新结果。修正后 42 项 runtime 通过；再补最大 run ID 边界，最终 43 项纳入完整回归。

首次完整应用集合 1470 passed / 14 skipped / 286 subtests passed；后续边界与最后修改完成后重新运行完整集合，最终结果以上表为准。原始失败日志及这次复跑均保留在 `.dev` 并记录哈希。

## 5. 风险、环境限制与后续工作

1. 全目录 Ruff 仍退出 1，45 项均为既有问题；改动文件全部通过。
2. 原有 14 项 opt-in skips 保留：真实 JEV 1、Postgres smoke 4、Postgres E2E 4、WorldLine Postgres 重启 3、真实 Qdrant 2。保留一项既有 Qdrant 版本探测 warning。没有重新进行真实 Qdrant/知识质量验收。
3. 尚未接入 Explorer、JEV transfer guard、实际累计 token usage/调用前预留、有限重试/兜底与 trace 写入。usage 保持初始化值，不能声称累计预算已生效。
4. 未验收真实 PostgreSQL 中断/重启、云端写操作幂等和动作恢复重放。超时不保证已发送的上游操作撤销；本阶段不自动重试业务工具。
5. 未调用真实 LLM/JEV 做质量、成本或时延评测，未测试登录音乐账号接口、浏览器播放完成、关键交互或响应式视觉。前端脚本和构建不替代这些验收；Java/UI 未修改，未重跑 Java 构建。
6. 新模式按任务完成输出公开文本，可能增加首字延迟。两模式会话历史独立，未迁移旧/新 checkpoint；用户级长期记忆沿用原语义。

阶段 2 的受控开发验收完成后，可继续阶段 3 Explorer 接线。本阶段没有部署或 push，线上启用须继续完成后续可靠性、质量及灰度验收。

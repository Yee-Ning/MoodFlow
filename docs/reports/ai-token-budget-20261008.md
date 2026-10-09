# AI 推荐 token 预算调整（2026-10-08）

修改始于 2026-10-08，最终全量复测和交付完成于 2026-10-09。

## 原因与范围

截图中的 Nujabes 推荐触发的是单次输出上限：持久化用量回执显示 `deepseek-flash` 输入 524、输出 1352，原单次输出上限为 1024。这是模型报告的实际用量，非输出估算；加上 JEV 的 983 个估算 token，整次任务合计约 2859，尚未达到原 Agent／任务累计上限。现有通用预算提示未区分单次输出超限与累计额度耗尽。

同步调整业务预算策略、`RunLimits` 默认值、服务默认设置和 `.env.example`，避免多个上限取最小值后仍被旧配置拦截。模型包装器和直接记账调用在未显式指定输出上限时，使用对应模型的预算策略。显式设置更小上限继续生效。

| 项目 | 原上限 | 新上限 |
| --- | ---: | ---: |
| 普通模型单次输出 | 1024 | 4096 |
| 单个 Agent 累计输入与输出 | 16000 | 64000 |
| 单次任务累计输入与输出 | 32000 | 128000 |
| 当前歌曲／普通聊天单次输入、输出和安全余量合计 | 8192 | 12288 |
| 工具／WorldLine 单次输入、输出和安全余量合计 | 12288 | 16384 |

各业务路径均为输出预留 4096、安全余量预留 256，当前歌曲／普通聊天输入可用空间为 7936，工具／WorldLine 为 12032。JEV 单次输出上限保持 256，来源配额、任务时间、重试与转交上限保持原策略。额度是消耗上限，实际结算仍采用真实用量，并保留超限账单和后续调用拦截。

本地忽略文件 `ai-service/.env` 仅更新 `LLM_MAX_OUTPUT_TOKENS=4096`，已重启 AI 服务。未输出或修改凭据，未修改数据库结构、前端代码及用户原有未提交文件。开发文档补充已有 `.env` 的升级方式和旧任务恢复语义：旧任务继续保留原始额度及消耗，新发送的请求使用新默认值。

## 自动化验证

新增 18 项回归场景，覆盖原 1352-token 输出成功、4096/4097 输出边界、连续五次模型调用合计 41760 token、64000 与 128000 累计边界及超额、正确预算层级的调用前拒绝、各路由输入与输出预留、Chat／Responses SDK 的实际上限及更小显式设置。原测试随默认值更新边界，历史压缩仍保留原来的 696-token 输入压力，渲染重新选择仍保留原来的输入空间。

Python 命令从 `ai-service` 执行，使用既有 `.venv/Scripts/python.exe`；测试进程采用 `OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`、`PYTHONUTF8=1`，未改变系统设置或依赖。

首次全量验证发现三项 Handoff 保护测试仍按旧默认累计上限构造超额用量，已改为根据 `RunLimits` 生成边界，并保留原超额量、停止调用及禁止执行后续业务的断言。修正后定向验证和最终全量复测均通过。

| 命令 | 结果 |
| --- | --- |
| `python -m pytest tests -q --tb=short --ff --durations=15 -o faulthandler_timeout=120` | **2229 passed、48 skipped、188 warnings、288 subtests passed**；退出码 0，耗时 1561.29 秒 |
| `python -m pytest tests/test_budget_defaults.py tests/test_handoff_budget.py::test_required_context_budget_failure_is_sticky_and_optional_history_shrinks tests/test_context_loop_coordinator.py::test_auxiliary_overshoot_is_refused_before_final_and_replay_keeps_refusal -q --tb=short` | 17 passed；随后增加的三项任务累计边界由全量测试覆盖 |
| `python -m pytest` 三项 Handoff 路由／转交／审查超限保护测试 | 3 passed；保留各自超额量及禁止后续业务执行的断言 |
| `python -m ruff check` 本次修改的 Python 文件 | 通过 |
| `python -m compileall -q app tests` | 通过 |
| `git diff --check --` 本次修改的文件 | 通过 |
| `npm run test:ai-stream`（`soul-music-room`） | 通过，覆盖流式存储及持久化 ACK 兼容性 |

## 真实服务验证

经已有前端代理 `http://127.0.0.1:5173/api/ai/chat` 验证，所有成功／需补充输入的请求均收到 `final`、`done`，无 SSE error。验收使用独立匿名会话及 `acceptance` 标记。

| 请求 | 结果 |
| --- | --- |
| 空白消息、无效歌曲 ID | 均返回 HTTP 422 |
| 根据现在这首歌推荐，未提供当前歌曲 | HTTP 200，`needs_input`，提示先播放歌曲 |
| 普通聊天 | HTTP 200，`success` |
| 原始请求“根据现在这首歌推荐”，歌曲 1398649519：Luv(sic) pt6 Uyama Hiroto Remix Instrumentals | HTTP 200，`success`；实际输入 524、输出 **1501**、总计 2025，已超过旧输出上限并正常返回，新回执上限为 4096 |
| 同一会话再次发送原始请求 | HTTP 200，`success`；实际输入 2325、输出 770、总计 3095，回执上限仍为 4096 |

两次推荐返回完整文字回答，未修改推荐卡片或播放器动作逻辑。证据保存于忽略目录 `.dev/token-budget-20261008/`，包含调整前用量、配置快照、测试日志、SSE 和调整后真实回执，仅保留本次匿名验收数据与数值诊断。

## 限制

- 更高累计上限允许复杂任务消耗更多 token，仍按实际用量结算，不保证任意复杂请求均不触发上限。
- 已完成或恢复中的旧任务不重置历史消耗；前端“再试一次”重新发送请求，可使用新默认额度。
- 本次改动在后端预算与配置范围内，未执行浏览器登录会话和响应式视觉验收；没有修改前端 UI。
- 48 项需要显式启用或外部环境的集成测试未执行，包括 PostgreSQL 实机／恢复、Qdrant 和真实 JEV 等场景；最终全量结果不代表这些外部集成已全部通过。本次另行完成上述真实服务推荐验证。
- 全量测试仍有 188 条警告，主要涉及既有 LangGraph 无 checkpointer 时的 durability 设置、Qdrant 版本兼容性检查失败等；未通过删除、跳过或弱化测试规避失败。
- 最终交付前 AI 健康检查返回 HTTP 200。没有修改前端源文件，前端兼容性由 `npm run test:ai-stream` 验证，未在本次后端配置调整中重新执行前端构建。

# MoodFlow Handoff 阶段 1 验证记录

日期：2026-10-01，Asia/Shanghai。基线提交 `08010fe265ecc990cdc045fa1103bccd9368b381`，分支 `codex/parse-normalize-corpus-v1`。

## 1. 交付结论与范围

阶段 1 的能力与状态契约已完成并通过受控测试，可进入下一阶段的最小运行链路开发。

本次新增 `app/agent/handoff/` 五个模块、一份契约测试、[设计文档](../../architecture/handoff-contract-v1.md)、本报告和 [evidence.json](evidence.json)：

- GeneralChat / Curator / Explorer 注册表、四能力映射和四条允许边。
- 按任务能力收窄的工具域：中转只有 transfer，歌曲分析只有只读上下文。
- 保留多意图任务的顺序、重复项、依赖、原始输入和否定语义；四类能力的 16 种前后组合均可沿允许边承接。
- 结构化 transfer 参数、服务端 envelope、成功/拒绝结果和只含允许字段的 trace schema。
- 任务级状态、初始化/恢复区分、有限 hops、失败/澄清停止、确定性终答。
- 控制调用校验、AI/Tool 消息配对及 run/call ID 稳定消息去重。

这些是独立契约和纯转换函数，现有应用尚未调用。没有实现或启用 handoff 运行图、`Command` 路由、真实模型护栏和累计 usage 采集，也没有切换 API、SSE、JEV、生产配置或前端。当前 Foundation 行为继续由原测试覆盖。

本次没有新增依赖；未修改用户原有文件、恢复用户删除项或提交未跟踪的数据/报告。验证期间暂存区只包含本阶段文件，初始 unrelated status 和已有修改文件 SHA-256 保持一致，机器证据记录了核对结果。

## 2. 已运行验证

下面命令的工作目录为 `ai-service`，使用已有应用/评测虚拟环境。完整日志和 JUnit XML 在被忽略的 `.dev/handoff-phase1-20261001/`，机器证据记录摘要和 SHA-256。94 项契约测试是完整应用测试的子集，不重复计入总数。

| 命令 | 最终结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests/test_handoff_contracts.py -q -ra --tb=short` | **94 passed**；退出 0 |
| `.venv/Scripts/python.exe -m pytest tests -q -ra --tb=short` | **1429 passed，14 skipped，1 warning，286 subtests passed**；退出 0 |
| 应用环境执行 `evals/tests` 除 Judge 外的 20 个测试文件，见下方命令 | **173 passed**；退出 0 |
| `.venv-eval/Scripts/python.exe -m pytest evals/tests/test_judges.py -q -ra --tb=short` | **4 passed**；退出 0；使用测试客户端 |
| `.venv/Scripts/python.exe -m ruff check app/agent/handoff tests/test_handoff_contracts.py` | `All checks passed`；退出 0 |
| `.venv/Scripts/python.exe -m ruff format --check app/agent/handoff tests/test_handoff_contracts.py` | 6 files already formatted；退出 0 |
| `.venv/Scripts/python.exe -m compileall -q app tests evals scripts` | 退出 0 |
| `.venv/Scripts/python.exe -m pip check` | No broken requirements found；退出 0 |
| `.venv/Scripts/python.exe -m ruff check . --output-format json` | **45 项既有问题**；退出 1；与阶段 0 的 path/line/code/message 清单完全一致，差异 0 |
| `git diff --cached --check`，仓库根目录 | 本阶段文件通过 |

评测框架命令：

```powershell
$taskEvalFiles = @(
    Get-ChildItem -LiteralPath 'evals/tests' -Filter 'test_*.py' -File |
        Where-Object { $_.Name -ne 'test_judges.py' } |
        Sort-Object Name |
        ForEach-Object { 'evals/tests/' + $_.Name }
)
& .venv/Scripts/python.exe -m pytest @taskEvalFiles -q -ra --tb=short
```

验证环境沿用阶段 0 的进程级隔离：JEV/live Postgres opt-in 关闭，checkpoint 为 memory，persistence 为 sqlite，memory backend 禁用，数据库 URL 留空，LLM key 只用离线占位值；不写入 `.env` 或系统设置。测试中需要 JEV/LLM/工具行为的部分使用显式测试替身。

没有再次运行应用环境的仓库总入口 `pytest -q`：阶段 0 已确认其会因为应用环境未装 DeepEval 而收集失败。本次继续按现有依赖隔离执行应用和评测两组测试，没有安装依赖、跳过文件或弱化断言来规避问题。

## 3. 关键场景的验证含义

| 场景 | 本阶段证明的内容 | 尚未证明的内容 |
|---|---|---|
| General→Curator→Explorer→Curator→General | 契约转换保存任务、结果、依赖和计数；只有 owner 可记录结果 | 真实模型选择 transfer、运行图跳转和响应质量 |
| 四能力 16 种前后组合 | 包括探索→聊天等需要中转的组合；中转不执行业务、不扩大工具域 | 运行节点对工具可见域和执行权限的实际绑定 |
| 5/6 跳及无进展回跳 | 第五跳允许；第六跳/无进展立即回跳拒绝；拒绝不改计数或 current Agent | 运行图对拒绝 observation、重试和兜底的接线 |
| token / deadline 边界 | 达到累计上限或总 deadline 时拒绝下一次转交；恢复保留起始时间和用量 | 实际 token 采集、调用前预留、执行期间取消与单 Agent 超时 |
| 非法参数和超大上下文 | 模型不能填授权/凭据/待办；envelope 超过 64 KiB 不转交 | 工具工厂将异常映射为有限、配对的错误结果 |
| 控制消息重放 | accepted/denied 均配对；重新构造同一消息对经 reducer 去重 | PostgreSQL 重启后的图恢复、客户端动作重放 |
| public TaskResult 投影 | task ID、四类旧 subgraph 名称与既有 JSON 投影兼容 | 新 Agent 节点 SSE 白名单和真实浏览器播放 |
| checkpoint schema | JSON round-trip；unknown/legacy/不一致状态拒绝；新轮次清空任务计数 | pg checkpoint 序列化、中断恢复与跨模式迁移 |

这里的“成功链路”是纯契约转换测试，不是在线 handoff E2E。完整应用回归覆盖既有请求校验、成功响应、错误处理、流事件及调用方契约，但不能代替未来新模式的 API E2E。

## 4. 保留风险与未验证内容

1. 全目录 Ruff 仍为退出 1：45 项是阶段 0 记录的既有问题，没有新增；本次没有修 unrelated 文件。
2. 完整应用测试保留原有 14 项 opt-in skips：真实 JEV 1 项、Postgres smoke 4 项、Postgres E2E 4 项、WorldLine Postgres 重启 3 项、真实 Qdrant 2 项。本阶段没有再次执行阶段 0 的 Qdrant 追加集成测试，新契约没有改变检索实现。
3. 保留一项既有 Qdrant 版本探测 warning；没有修改检查条件。
4. 未验证真实 LLM/JEV 质量、时延、成本、在线护栏、真实索引知识质量或已登录网易云接口。
5. 未验证新的 handoff 节点 SSE/动作适配、真实数据库恢复、累计计费、有限重试与超时取消。代码中的限制字段和纯检查尚未作用于生产请求。
6. 没有改动 Java 或前端，因此未重跑其构建、交互和响应式视觉验收；它们的阶段 0 结果保留为基线。真实浏览器音频播放仍未验收。
7. `get_current_song_context` 工具和实际 transfer factory 尚待阶段 2 实现。Foundation/handoff 的独立 namespace、session 模式固定和回滚恢复规则已在设计文档明确，尚未接入数据库。

阶段 2 按契约实现 GeneralChat→Curator 最小运行链路与受控 API/SSE E2E；之后再接入 Explorer 和运行时加固。本阶段没有部署或 push。

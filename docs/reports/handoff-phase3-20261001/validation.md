# MoodFlow Handoff 阶段 3 验证记录

日期：2026-10-01，Asia/Shanghai。阶段 2 基线提交 `41e0fe9`，分支 `codex/parse-normalize-corpus-v1`。

## 1. 交付内容与影响范围

阶段 3“扩展完整业务网络”已完成受控开发验收。新增 Explorer 执行适配和可信上下文投影两个模块、49 项网络测试，以及 [运行说明](../../architecture/handoff-runtime-phase3.md)、本报告和 [机器证据](evidence.json)。修改 handoff 的父图、Agent 执行、transfer envelope 接线、公开流适配器及阶段 2 的探索占位测试。

- 三 Agent / 四能力全部接入实际 LangGraph：GeneralChat、Curator、Explorer；按原注册表的四条允许边转交。
- Explorer 复用原 WorldLine 私有图，包含实体解析、检索、证据校验、时代/场景/影响关系、图谱、叙事及探索分支。
- 衔接当前歌曲、前置搜索/播放和同轮歌手指代；有歧义时澄清，不凭模型摘要猜 ID。可信歌曲目标在子查询中绑定，原始任务不改写。
- transfer envelope 携带最近可信工具实体；探索结果以有界、无动作授权的投影提供给下一 Agent。Curator 仍需可信完整歌曲和当前任务授权才能播放。
- Explorer→Curator→GeneralChat 中转不执行业务任务；保留多意图顺序、重复任务、依赖和结果，继续执行新轮清理、五跳限制、超时与确定性终止。
- Explorer SSE 进度映射为旧 WorldLine 节点，公开结果/citations/sources 和 JSON/SSE 协议兼容；动作只从 Curator 的工具任务派发。

没有新增依赖，没有改动 Java、前端代码、JEV 策略、路由阈值或 `.env`。默认仍为 Foundation。原独立 WorldLine 会话/分支 API 保持原行为，没有迁移其 checkpoint。用户已有修改、删除及未跟踪的数据/报告保持原状；证据生成时核对 unrelated status 和保护文件 SHA-256。

## 2. 已运行验证

AI 命令工作目录为 `ai-service`，前端命令工作目录为 `soul-music-room`。日志/JUnit 存于被忽略的 `.dev/handoff-phase3-20261001/`，机器证据记录摘要、退出码和哈希。49 项新增测试、43 项阶段 2 运行测试、94 项 v1 契约测试均包含在应用全量结果中，不重复计数。

| 命令 | 最终结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests -q -ra --tb=short` | **1521 passed，14 skipped，1 warning，286 subtests passed**；退出 0 |
| `.venv/Scripts/python.exe -m pytest tests/test_handoff_network.py tests/test_handoff_runtime.py tests/test_handoff_contracts.py tests/test_foundation_streaming.py -q -ra --tb=short` | **191 passed，1 warning**；退出 0；最后新增的 4 项结果边界测试随后由全量集合覆盖 |
| 应用环境运行 `evals/tests` 除 Judge 外的 20 个测试文件 | **173 passed**；退出 0 |
| `.venv-eval/Scripts/python.exe -m pytest evals/tests/test_judges.py -q -ra --tb=short` | **4 passed**；退出 0，受控客户端 |
| `.venv/Scripts/python.exe -m ruff check app/agent/handoff app/agent/foundation/streaming.py tests/test_handoff_contracts.py tests/test_handoff_runtime.py tests/test_handoff_network.py` | All checks passed；退出 0 |
| `.venv/Scripts/python.exe -m ruff format --check app/agent/handoff tests/test_handoff_contracts.py tests/test_handoff_runtime.py tests/test_handoff_network.py` | 15 files already formatted；退出 0 |
| `.venv/Scripts/python.exe -m compileall -q app tests evals scripts` | 退出 0 |
| `.venv/Scripts/python.exe -m pip check` | No broken requirements found；退出 0 |
| `.venv/Scripts/python.exe -m ruff check . --output-format json` | 45 项既有问题，退出 1；与阶段 0 的 path/line/code/message 完全一致，差异 0 |
| `npm run test:ai-stream` | AI chat streaming store checks passed；退出 0 |
| `npm run test:worldline` | frontend flow / view passed；退出 0 |
| `npm run lint` | 退出 0 |
| `npm run build` | TypeScript + Vite 构建成功；退出 0；保留原有大 chunk warning |
| `git diff --cached --check` | 本阶段文件通过 |

评测框架继续按原依赖环境分开运行：

```powershell
$taskEvalFiles = @(
    Get-ChildItem -LiteralPath 'evals/tests' -Filter 'test_*.py' -File |
        Where-Object { $_.Name -ne 'test_judges.py' } |
        Sort-Object Name |
        ForEach-Object { 'evals/tests/' + $_.Name }
)
& .venv/Scripts/python.exe -m pytest @taskEvalFiles -q -ra --tb=short
```

验证进程沿用离线 profile：LLM 仅占位值；JEV 和 live Postgres opt-in 关闭；checkpoint memory、persistence sqlite、memory backend disabled、空数据库 URL。新 E2E 的模型、音乐、检索、生成和 Judge 依赖使用显式测试替身，没有改写 `.env` 或安装依赖。应用环境未装 DeepEval，未重新执行已知会收集失败的总入口；应用和评测测试分别完整执行。

## 3. 关键验收结论

| 场景 | 受控验证结论 |
|---|---|
| 四能力全部 16 种前后组合 | 真实执行对应业务节点和 Command；保留顺序、task ID、依赖、重复能力；链条符合允许路径 |
| 聊天→暂停→探索→搜索播放→聊天 | 五项任务成功、四次转交；两种播放器动作；探索来源及后续结果保留 |
| 单独探索 / 探索后聊天 | General→Curator→Explorer；需要聊天时经 Curator 中转，不产生假的中转业务结果 |
| 时代 / 场景 / 影响 | 复用已有知识夹具及真实 WorldLine 图；关系 evidence、图边和公开 sources 对应 |
| 当前歌曲 / 搜索 / 播放目标 | 无参数只读工具解析当前及前置播放 ID；唯一搜索目标可直接绑定；原输入与 RouteTask 不改写 |
| 同轮歌手指代 | 搜索任务命名的唯一歌手进入 Explorer 查询，复用已有指代解析 |
| 多歌曲 / 切歌待执行 / 缺歌曲 / 资料异常 | 澄清或受控失败；不读取旧快照进行探索，不继续后续动作 |
| 探索后播放 | 下一 Curator 提示词得到有界投影并先搜索；仅图谱目录节点无法直接通过播放引用校验 |
| 权限及路径拒绝 | 禁止 General→Explorer、Explorer→General 直接跳转；中转只绑定 transfer；拒绝不扩大音乐工具域 |
| 依赖失效 | 资料/生成失败、实体歧义停止后续；检索失败沿用仅目录事实的降级成功，不伪造知识来源 |
| 超时 / 五六跳 | Explorer 实际超时后终止；第五跳允许、第六跳拒绝，已完成结果保留 |
| checkpoint / 新轮 | Explorer 前中断恢复保留两次转交；新轮复用 task ID 也只输出本轮探索结果 |
| 结果边界 | task ID、能力、WorldLine schema 或可信目标不匹配均失败，坏数据不进入公开结果 |
| HTTP / SSE 调用方 | JSON/流 final 一致；原 WorldLine 名称及 citations/sources；隐藏内部状态；动作只从 Curator 派发并去重；原请求/错误路径回归通过 |
| 前端 | 现有动作/SSE 消费、WorldLine flow/view 脚本、lint、构建通过 |

这证明受控依赖下的业务网络和 API/SSE E2E，不证明真实模型、账号接口或浏览器音频已经验收。

## 4. 早期失败与纠正

首次相关集合 **1 failed / 183 passed**：测试把知识检索异常预期为整轮失败，但源码与原测试规定 WorldLine 可在可信目录事实可用时继续。保留现有行为，改为明确验证仅目录来源和后续授权动作成功；资料、生成和歧义用例仍验证停止。没有删除测试、降低来源/授权校验或修改原 WorldLine 降级策略。

随后扩大集合 **191 passed**。补充了有界探索投影及实体衔接、禁止图谱节点直接授权播放、五/六跳和懒加载；最后增加 4 项返回结果一致性检查，完整应用集合 **1521 passed**。两轮相关日志和最终完整结果都保留并记录哈希。

## 5. 保留风险及环境验证限制

1. 全目录 Ruff 仍退出 1，45 项均为原有问题；改动文件静态检查通过。
2. 原 14 项 opt-in skips 保留：真实 JEV 1、Postgres smoke 4、Postgres E2E 4、WorldLine Postgres 重启 3、真实 Qdrant 2。保留一项既有 Qdrant 版本探测 warning。本次未做真实知识索引质量验收。
3. 实际累计 token usage、调用前预算预留、JEV transfer guard、有限重试/兜底和 trace 写入尚待阶段 4，不能把当前 usage 初始值或受控测试解释为全链路预算已生效。
4. 未验收真实 PostgreSQL 进程重启、云端操作幂等、播放器动作重放；当前仅验证内存 saver 的中断恢复。超时不保证已发出的上游操作被撤销。
5. 未进行真实模型/JEV 质量、成本/时延评测、登录音乐账户接口或浏览器音频/关键交互/响应式视觉验收。前端脚本和构建不能代替真实交互；本次没有 UI/Java 改动，未重跑 Java 构建。
6. 新模式继续按任务完成输出文本；两模式会话历史独立，不迁移已有 checkpoint。独立 WorldLine session API 的状态继续由其原服务管理。

阶段 3 的受控开发验收通过后，可继续阶段 4 运行时加固。本次未切换默认模式、部署或 push，生产启用仍需后续质量和灰度验收。

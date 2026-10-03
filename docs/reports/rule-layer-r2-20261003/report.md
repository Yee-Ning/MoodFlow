# Phase R2：入口 span 对齐与快判对比

日期：2026-10-03。**入口 span 误拒已修复，V7 原句可返回完整计划。50 例工程请求的 V5 对比和离线重放完成；真实用户流量抽样仍缺数据，不能按该项全验收。** 用户确认没有指定的真实请求日志。本报告将工程样本与真实流量分开计数。

## 基线与提交

- 开始时最新 HEAD：`f19aaef6cf6477b73c16a52c7312bb4b2437edee`。
- 原规则层实现 commit：**`87b2831dba8b93e5d93ad61e2bb76679ed488ff3`**，`feat: add handoff preflight and rule routing fast path`。这补齐了[上份报告](../../architecture/handoff-entry-rules.md)缺失的实现 hash。
- R2 实现、测试及证据 commit：**`feea34747f98081f613467be71cef00614e73d9e`**。
- 独立分支：`codex/rule-layer-r2`；独立工作树：`C:/Users/33994/.codex/worktrees/rule-layer-r2/MoodFlow`。未合入 C 分支提交，未改变其他 Phase 的工作树。

## 诊断与 span 对齐决策

根因位于 **handoff 入口的 HybridFallback 完整性校验**。Preflight 的语法切分移除连接词，Hybrid 的既有规则匹配可能保留连接词；两者并未对同一个动作作出不同的路由决策。

证据链如下：

1. V7 原句“播放周杰伦的晴天，并探索周杰伦的世界线”的 preflight 片段为 `[0,8)`、`[10,19)`；Hybrid 已生成完整的 ToolCalling、Worldline 计划，意图 span 为 `[0,8)`、`[9,19)`。
2. 基线 `routing.py:68` 抛出 `ValueError("ungrounded_hybrid_plan")`：第二个意图的 start=9 小于 clause.start=10。外层异常处理返回澄清，run 为 `needs_input`，任务结果为 0。未进入子任务执行。
3. 将基线 routing 模块仅加载到独立测试进程，再运行新增回归的 V7 用例，确实得到上述误拒；工作树文件未回退或替换。
4. 修复后，使用原仓库的真实分类器资产、CPU Hybrid 和 `configured_entry_router` 复验原句，返回完整非澄清计划；任务路由为 `tool_calling`、`worldline`，与 Hybrid 原计划签名一致。原始 `[9,19)` 证据保留。见[真实入口证据](entry-live-evidence.json)及[验证摘要](validation.json)。

**选择选项一：入口校验接受 Hybrid 保留的连接词前缀。** 不改变 RuleGuard 的切分、Hybrid 的匹配与任务构造，也不修改意图证据或任务输入。改动仅为允许 start 位于 clause.start 前、且两者之间全部属于连接词、空白或分隔标点的情况；end 必须仍位于该肯定片段内，并至少覆盖一个片段正文字符。

理由：修改 Hybrid span 会影响规则匹配、覆盖率、本地分类器片段、证据合并及任务输入。入口的这一处兼容检查改动更小，保留所有既有下游原文及路由语义。这里使用的是语法前缀白名单，不是按任意字数放宽边界。

原文证据仍须与 `text[start:end]` 完全一致；跨越另一个动作正文、包含被 preflight 排除的否定动作、仅包含连接词、伪造证据的计划继续被拒绝。没有扩大未提供意图证据的任务的可接受范围。

## 改动与回归覆盖

| 文件 | 改动 |
| --- | --- |
| `ai-service/app/agent/handoff/routing.py` | 新增语法前缀检查，替换 HybridFallback 的严格 start 包含判断；生产代码仅此文件，18 行新增、2 行删除 |
| `ai-service/tests/test_handoff_span_alignment.py` | 41 项 span 回归：34 个默认入口执行用例、6 个非法 span 用例、1 个否定动作隔离用例 |
| `ai-service/evals/rule_layer_r2.py` | 50 例样本选择、真实 JEV 响应记录、严格匹配请求的离线重放；不 dispatch 子图 |
| `ai-service/tests/test_rule_layer_r2_eval.py` | 4 项采样、响应重放、回退分母及 50 例存档回放测试 |
| 本目录 | 报告、结构化证据及可恢复换行的离线检查包装器 |

默认图回归实际经过 preflight、快判未命中、LazyHybridRouter、Hybrid 规则/证据合并/任务构造、HybridFallback、真实 handoff 图的转交与子图适配器。未注入固定 RouteDecision。分类器响应、转交模型与任务 worker 为受控夹具；Worldline worker 使用既有子图生成的合法结构化结果，避免将实体解析或播放器行为混入 span 验收。

17 种分隔写法各验证两任务和三任务：中文/英文逗号加“并”、“并且”、“接着”、“随后”、“顺便”、“另外”、“同时”、“以及”、“再”，分号加“然后”、“最后”，单独逗号/分号，感叹号加“并”，以及连接词两侧空白、换行和制表符。每例断言完整计划、原始证据、任务顺序/依赖签名、全部结果成功及无 needs_input。

## 快判与 V5 抽样

### 来源与方法

抽样总体来自仓库已有 `manual_seeds.jsonl` 以及规则、入口、handoff 网络和 V5 测试中的字符串。仅保留能够完整命中快判的请求，按原文去重，以随机种子 `20261003` 选择 **50** 条；其中人工种子 16 条，已有测试语句 34 条。没有新写请求来补足分母，也没有把同一请求重复计数。

这 50 条是工程语料，**真实用户流量样本数为 0**。已有 V5 盲测集也注明为人工编写，不能改标为真实流量。本次样本有快判命中筛选偏差，结果不能推断全部请求的路由准确率。

固定上下文为测试歌曲存在、song_id=11；没有读取实际播放器或歌曲资料。先在隔离评测进程调用实际 `JevRouterV5` 和 `JevClient`，保存经过校验的响应，再用 `ReplayClient` 完全离线运行同一个 V5 决策、Guard 和 TaskOrchestrator。Replay 按 state SHA256、问题名及未消费的响应匹配；不从标签或快判结果构造 JEV 回答。

V5 回退按既有 Shadow 方式复用快判 baseline，**所有回退均排除在可比较决策分母之外**，不能将 baseline 自比较算作一致。计划签名比较完整性、任务路由、原文片段、任务顺序和依赖索引，忽略随机 task_id、置信度及来源标签。

实际调用 JEV 57 次，响应模型为 `jev-1.13.0`；供应商返回的输入/输出 token 合计为 69,236 / 3,754。评测没有执行音乐操作或修改部署配置。见[响应存档](provider-capture.json)、[记录时比较](capture-comparison.json)、[离线比较](replay-comparison.json)。存档仅含公开工程语句及结构化响应，不含凭据或实际用户日志。

### 结果与分歧归因

| 指标 | 结果 |
| --- | ---: |
| 唯一快判命中请求 | 50 |
| 任务数 | 60：ToolCalling 36、CurrentSong 14、Worldline 7、GeneralChat 3 |
| V5 可比较直接决策 | 46：Single 36、Multi 10 |
| 可比较计划一致 | 46/46 |
| 可比较计划分歧率 | **0/46 = 0%** |
| V5 回退 | **4/50 = 8%**，不计为一致 |
| 真实用户流量 | **0** |

记录时与离线比较 JSON 完全一致，SHA256 均为 `a52103a47268a74bb189c7fead998868e0a47f908b4a257ff7f00832085a713a`。回退属于路径差异，单列归因：

| 来源 | 请求 | 归因 |
| --- | --- | --- |
| `manual_seeds.jsonl:11` | 帮我分析一下这首歌的创作背景 | Choice 选择 CurrentSong，confidence=.39，低于冻结 .8 阈值；JEV 回答存档存在，V5 转回退 |
| `test_handoff_entry_rules.py:28` | 不要订机票，暂停播放 | 否定片段移除后的“暂停播放”触发既有多谓词分解审查；0 次 JEV 调用 |
| `test_handoff_entry_rules.py:77` | 暂停播放 | 同上，V5 将“暂停”“播放”统计为多个谓词；0 次 JEV 调用 |
| `test_rule_router.py:36` | 播放我的《私人收藏》歌单 | 标题里的“收藏”与“播放”触发既有分解审查；0 次 JEV 调用 |

后三项结合 V5 的 `needs_decomposition_review` 静态核对归因。它们不是此次 span bug，本轮未调整分解启发式、快判或置信度阈值。没有把 0% 分歧解释为 50/50 独立 V5 成功。

## 验证与复现

全量相关范围为 36 个测试文件，名单存于 `validation.json`。首轮执行结果为 **612 passed、4 failed、23 subtests passed**，413.14s；4 项失败均由 Windows 自动 CRLF 换行改变冻结输入的字节 SHA256 引起。恢复原始 Git LF blob 后，相关冻结检查及新增离线测试最终复验 **11 passed**，55.12s。父测试按 classname/name 合并去重，所有 **617 项测试及 23 项子测试** 最终均通过；这不是宣称一次命令打印了 617 passed。

另有定向结果：span 回归 41 passed（85.72s），评测回归 4 passed（27.41s）。5 个 Python 文件的 Ruff、compileall 及 Git diff 检查通过。没有前端、Java 或 API 协议改动，因此未重复前端/Java 构建。

换行处理未修改冻结内容、预期 hash 或测试断言。`check.py` 在内容确实仅有换行差异时临时使用 Git blob，并在 finally 中恢复原始字节；同时设置子进程级离线环境。它不写 `.env`。复现命令从工作树根目录运行：

```powershell
$python = 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe'
$check = 'docs/reports/rule-layer-r2-20261003/check.py'
$files = Get-ChildItem ai-service/tests/test_*.py |
  Where-Object { $_.Name -match '^test_(handoff|jev_router|rule|intent_merger|hybrid|model_router|foundation|tool_calling|general_chat|main_graph|subgraph|worldline_graph)' } |
  ForEach-Object { 'tests/' + $_.Name }
& $python $check -m pytest @files -q
& $python $check -m evals.rule_layer_r2 replay
& $python -m ruff check ai-service/app/agent/handoff/routing.py ai-service/evals/rule_layer_r2.py ai-service/tests/test_handoff_span_alignment.py ai-service/tests/test_rule_layer_r2_eval.py docs/reports/rule-layer-r2-20261003/check.py
git diff --check
```

## 验收边界

| 验收项 | 状态 |
| --- | --- |
| V7 原句默认 handoff 入口不再因 span 被拒 | 通过：真实分类器返回完整两任务计划、无澄清 |
| 多段默认入口执行链路、span 与否定边界回归 | 通过：41 项受控回归 |
| 原规则层实现 hash 与 R2 实现 hash | 已记录 |
| 50 例快判/V5 工程样本及离线重放 | 完成：0/46 可比较分歧，4/50 回退 |
| ≥50 例真实用户请求抽样 | **未满足：无指定真实流量数据** |
| 全量相关测试及静态检查 | 通过，包含换行恢复后的失败文件复验 |

入口真实复验只执行到计划，不构成原组合请求的真实音乐执行、播放器 ACK 或 Qdrant 业务验收；这些仍属于 C 的验收范围。保留连接词可能使下游实体解析因业务原因澄清；本次对接真实 Worldline 夹具时观察到部分带连接词输入的实体解析 needs_input，这没有被入口当作 span 错误，也未在 R2 修改实体解析。无分隔且被 Hybrid 贪婪匹配成跨动作单意图的请求仍受原完整性边界限制，本轮没有放宽跨动作正文的证据。

未修改路由选择、RuleGuard/转交 Guard、Journal/digest、ACK、生产 `.env`、默认图或灰度开关。真实入口检查确认默认图仍为 `foundation`、canary 为 false。未部署、未 push；暂存与提交只包含本任务文件。

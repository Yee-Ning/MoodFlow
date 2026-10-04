# Phase 3 C：组合请求诊断与修复

日期：2026-10-03（北京时间）。

**交付状态：工具层修复及自动化验证完成，原始 V7 case 的真实端到端验收尚未闭环。** 默认入口仍返回澄清；隔离执行入口后，真实目录未返回可确认的指定原唱，不能把需要选择版本算作成功，也不能用替代歌曲的成功覆盖原 case。

## 1. 基线与隔离

- 开始 HEAD：`87b2831dba8b93e5d93ad61e2bb76679ed488ff3`，已确认 `f97d5d2` 是其祖先，包含 B 修复与规则层实现。
- 分支：`codex/phase3-combo-robustness`。
- Worktree：`C:/Users/33994/.codex/worktrees/phase3-combo/MoodFlow`。原工作区的未提交代码、数据与报告保持原样；本任务不在原分支提交。
- 不修改路由、Guard、journal/digest、记账、恢复或 ACK 实现；不修改生产 `.env`，默认图保持 `foundation`，灰度关闭；不部署、不 push。
- V7 原数据库保持暂停，未恢复、重试或补写原 run。
- 实现与测试 commit：`37ffdabb8e7c5a959027928b94b3de092b54be23`。报告及证据另作独立提交，最终交付消息给出该提交号。

## 2. 先行诊断结论与证据链

### 2.1 可复现的执行失败：工具层请求范围过大

**层级判定：ToolCalling 工具执行层的模型请求构造与工具范围；不是 journal/持久记账/恢复层缺陷。** 未修改 `app/llm/runtime.py` 或提高任何预算。该层按现有上限正确拒绝请求，修复的是调用方无关工具 schema 与扩展查询导致的资源消耗。

证据链如下：

1. V7 原 run `a8247447339547bc97f06bb18e0f6c00` 的公开历史显示，Curator 的歌曲搜索与实体搜索均具有 success 记录（seq 39、40）。其四项已结算用量为 4517、389、5049、389，合计 **10,344 tokens**。按每 Agent 16,000 的现有上限，剩余 **5,656**。之后 seq 46 只有规范化 `tool_calling/failed`，历史没有捕获原始异常栈。
2. 在基线代码的新隔离 run 中，使用真实 Hybrid 路由、真实模型、真实 Java，并保持同一资源上限，复现 `general_chat → curator` 后失败、WorldLine pending 的形状。为隔离入口另一个问题，此执行诊断没有使用新的 HandoffEntryRouter；**该实验不计作默认入口验收**。
3. 新 run `251411c1f8e34a619b089dbf39282962` 捕获 `RuntimeLimitError`、`reason=token_limit`。第三次 Curator 模型请求预留 **8,613**，当时剩余 **5,663**，在派发前被拒绝。关键栈为 `execute_owned_task → invoke_subgraph → ToolCalling.agent → answer_tool_calling_task → AccountedChatModel.ainvoke → accounted_call → RequestLedger.reserve`。
4. 请求包含完整音乐/播放器工具目录；模型先完成歌曲搜索，再扩展实体查询，仍未发出播放动作。完整 schema 随每轮请求重复发送，与 V7 两轮后剩余预算不足的证据一致。
5. 隔离 run 未启用 `active_operations`、durable accounting 或 transfer journal，失败仍发生。因此没有证据指向 A/D 的 journal、记账或恢复缺陷，门控没有触发；修复工具调用方，不动上述模块。

历史 V7 的具体异常类型未被保存，不能补造原始栈。本次获得的是**同形真实执行的确定根因**；对历史 V7 的归因是由调用次数、成功步骤与预算数字支持的强关联推断。

补充观测：一次基线扩展实体查询返回 502，属于真实上游响应字段问题。本任务没有修改 Java 或实体响应适配器；该调用也不属于简单歌曲播放的必要步骤。

证据：[V7 脱敏提取](v7-evidence-extract.json)、[执行诊断](diagnostic-execution.json)。保存类型、关键栈帧、预算数字与任务状态，不保存 prompt、用户身份、Cookie、供应商请求/响应正文。

### 2.2 当前 HEAD 的独立入口阻碍

真实 Hybrid 计划第二段 span 为 `[9,19)`，新 preflight 第二段允许范围为 `[10,19)`，差异在连接词。`HybridFallback` 的完整性检查因此拒绝计划，默认入口返回 `needs_input/clarification`、无任务。

用户已明确确认：**“由并行 Phase 修复，C 保持原范围。”** 本分支未修改路由文件、规则或 Guard，也未将诊断时绕过入口的实验算作原 case 通过。

证据：[入口诊断](diagnostic-entry.json)、[修复后默认入口真实复验](live-original-default.json)。后者 run 为 `59f794de0cab416ca9e19629066996a3`。

## 3. 修复内容与影响范围

1. 简单的指定歌曲播放子任务仅绑定 `search_music`、`play_song`；复用已有搜索范围与动作校验。歌单、专辑、复合操作、否定操作及当前歌曲指代保持原工具范围。没有改任务分解或权限判断。
2. 播放提示要求准确歌手/版本匹配，最多一次有针对性的补查，不扩展查询歌手或专辑。找不到匹配或没有生成播放指令时返回 `needs_input`，不能用一段回答冒充播放成功。
3. 非校验类工具执行错误不能被其他成功读取掩盖；失败内容以安全错误结果为准。步骤明细记录工具名称、success/error 与公开错误码/原因。
4. 资源限制与畸形模型输出发生在已有步骤之后时，保留成功步骤、可信实体及已生成的播放器动作。父图保留失败结果，不用通用错误覆盖这些事实。畸形参数仍不执行，也不暴露参数原文。
5. 失败/澄清的组合请求按步骤展示完成、失败与 pending；指令生成统一说明仍待客户端确认，绝不把它称为实际播放完成。
6. `TaskResult.data` 保存只读组合进度投影。GeneralChat 在存在组合记录时可调用 `get_combination_task_status`，查询最近请求的已完成/失败/待办状态；无记录时不增加该工具。复用现有公开 `message` 投影，让上下文过滤后仍保留解释。没有新增数据库表、journal 事件或 digest 字段。
7. 新增真实链路探针 `scripts/combo_robustness_live.py`，默认使用正式入口；显式 `--entry hybrid` 只供执行隔离诊断。通过既有 runtime 配置消费凭据，不输出凭据，不替换模型、Java 或 Qdrant 返回结果。

影响文件仅为 ToolCalling 的范围/提示/结果处理、Handoff 的结果解释与只读状态查询、相关测试、探针和本报告。公共 HTTP 请求 DTO 和 ACK 实现未改；公开失败 metadata 增加安全 `reason` 字段，相应契约测试继续严格验证不发布损坏子图数据。

## 4. 验收结果

| 项目 | 结果 | 证据及限制 |
| --- | --- | --- |
| 原 V7 case，当前默认入口 | **未通过** | 返回澄清，无子任务；等待并行 Phase 的入口修复。 |
| 原 V7 case，真实 Hybrid 隔离执行 | **预算故障消除，但业务未通过** | 首次 Curator 预留从 7,089 降至 3,015；两次歌曲搜索成功，未得到可确认的指定原唱，返回 needs_input，WorldLine pending；没有派发错误版本播放。见 `live-original-after.json`，run `6d5875a930584769966480f80b3c2d56`。 |
| 播放＋探索自动化组合 | **通过** | 两个子任务均 success，播放动作参数来源可信搜索，pending 为空。 |
| 探索＋暂停自动化组合 | **通过** | 两个子任务均 success，生成 pause_player，pending 为空。 |
| 搜索＋收藏自动化组合 | **通过** | 两个子任务均 success，可信歌曲 ID 传给收藏接口，云端成功后才生成 set_liked。该项使用受控测试依赖，不计作真实账号写入验收。 |
| 真实播放＋探索 | **通过任务链** | run `47db659c5b3f419ba0d8f74257edf0f6`；两个子任务 success；真实 DeepSeek、Java 与 Qdrant `points/query/batch` 200。播放器动作仅验证生成，没有执行音频或 ACK。 |
| 真实探索＋暂停 | **通过任务链** | run `b0bcc0b4f18241fdb800309084af4e15`；两个子任务 success；真实 Qdrant 查询 200。暂停使用调用方提供的播放器上下文，没有浏览器实播验证。 |
| 必失败组合 | **通过断言** | 第一任务成功、第二探索明确失败、第三 pending；GeneralChat 真实执行其只读状态工具循环，取得经过现有上下文投影的公开解释，测试断言三种状态和具体失败原因。模型与失败依赖使用受控测试对象。 |
| 内部部分成功后失败 | **通过断言** | 搜索成功后收藏必失败，不能采信模型“全部完成”；资源限制/畸形输出发生在播放动作生成后，动作和两个成功工具步骤仍保留。 |

自动化三组组合与真实两组链路分别计数，不把受控测试依赖冒充真实供应商/账号。

## 5. 验证命令与结果

解释器使用已有 `D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe`，未新增依赖。离线测试仅在进程内配置占位 LLM 参数、Java 地址并默认关闭 JEV；测试自己的 mock/fixture 可以按原约定开启测试分支，不使用生产凭据。

```powershell
# ai-service 目录；选取全部相关测试文件，不删除或跳过测试
$testPaths = @(Get-ChildItem tests -File | Where-Object {
  $_.Name -match '^test_(handoff|tool_calling|general_chat|main_graph|subgraph|context_engineering|context_adapter|foundation|continuity|long_horizon|action_ack|history)'
} | ForEach-Object { $_.FullName })
python -m pytest -q @testPaths --tb=short
python -m pytest -q tests/test_handoff_combo_robustness.py tests/test_tool_calling_subgraph.py --tb=short
python -m ruff check app/agent/handoff/agents.py app/agent/handoff/graph.py app/agent/handoff/progress.py app/agent/foundation/subgraphs/tool_calling/assistant.py app/agent/foundation/subgraphs/tool_calling/graph.py app/agent/foundation/subgraphs/tool_calling/scope.py scripts/combo_robustness_live.py tests/test_handoff_combo_robustness.py tests/test_handoff_network.py
python -m compileall -q app/agent/handoff app/agent/foundation/subgraphs/tool_calling scripts/combo_robustness_live.py tests/test_handoff_combo_robustness.py
git diff --check

python -m scripts.combo_robustness_live --environment-root D:/code/ProjectsAll/MoodFlow/ai-service --case original --output ../docs/reports/combo-robustness-20261003/live-original-default.json
python -m scripts.combo_robustness_live --environment-root D:/code/ProjectsAll/MoodFlow/ai-service --case play_explore --output ../docs/reports/combo-robustness-20261003/live-play-explore.json
python -m scripts.combo_robustness_live --environment-root D:/code/ProjectsAll/MoodFlow/ai-service --case explore_pause --output ../docs/reports/combo-robustness-20261003/live-explore-pause.json
```

- 相关全量回归：**499 passed，4 subtests passed，0 failed**（86.26 秒）；文件清单见 `validation-summary.json`。
- 直接变更回归：**46 passed**，包括新增 **16** 项组合/边界/失败测试。
- Ruff、compileall、`git diff --check`：**通过**。
- 原有 LangGraph 无 saver 时 `durability` 无效的 warning 仍存在，未为消除 warning 改持久化代码。
- 初次扩展回归暴露隔离 worktree 无 `.env`，以及 checkout CRLF 使冻结契约原始字节哈希变化。前者用进程内离线测试配置解决；后者读取 HEAD 中该 JSON 原始字节进行验证，没有改契约内容、阈值、校验或 Git blob；测试后已恢复 checkout 的原始 CRLF 字节，该文件不在 commit 中。未来在同类 CRLF checkout 重跑冻结校验时仍需先读取 HEAD 原始字节；中间失败不作通过结果。

## 6. 未解决风险与未完成验证

- **原 case 不能验收通过**：默认入口 span 问题明确留给并行 Phase；即使隔离入口，本轮目录没有可确认的指定原唱，仍需要准确版本与可播放目录结果。
- 搜索＋收藏尚未完成真实账号链路。没有本轮可用的已确认测试账号会话，未读取旧 Cookie、访问主账号或进行远程收藏写入；自动化用例已经覆盖成功、失败、可信 ID 与本地动作顺序。
- 两组真实成功案例使用独立进程内图，没有复用 V7 PostgreSQL、journal 或历史恢复。它们证明模型/Java/Qdrant/子任务执行链，不能用于宣称 A/D 的持久化验收通过。
- 未做浏览器音频交互、响应式视觉、真实 ACK 或部署验证；本任务没有前端修改，ACK 属于 B 的范围。所有播放器“成功”均仅指子任务生成命令，客户端仍待确认。
- GeneralChat 查询依赖当前 checkpoint 内保留的最近任务结果；不引入跨窗口 History/journal 检索或崩溃恢复能力，留在 A/D 既有范围。
- 在并行入口修复完成后，需要从其新基线复验 V7 原句；当前分支不包含该修复，不能预先将其计为通过。

脱敏证据的文件哈希见 [evidence-sha256.json](evidence-sha256.json)。

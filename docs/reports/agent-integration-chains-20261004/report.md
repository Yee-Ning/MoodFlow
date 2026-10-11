# 三 Agent 协作链集成验收报告

日期：2026-10-04。分支：`codex/agent-integration-chains`。工作树：`C:/Users/33994/.codex/worktrees/agent-integration-chains/MoodFlow`。

本轮接通 Explorer Journey 消费、Curator 歌单导语承接和逐节点讲解三条链。沿用三 Agent 已冻结的 v1/v2 能力、载荷和四态表达，没有开启新单体功能轮。链 3 按用户明确确认，以合法 Explorer 起点验收；完整 GeneralChat 入口超过 5 跳时的停止行为另行验收。

## 基线、提交与范围

- 基线：`fc5944357c1287ffddb6d7146c6fa3753b8a4a18`，包含 GeneralChat v1/v2、Curator v1/v2、Explorer v1/v2 及已有可靠性工作。
- 实现与测试 commit：`61f75b5be69bd69f904895c7517dc5371e11aaa4`，仅含表中的三个接线实现文件及集成测试。报告和离线复现脚本另行独立提交，避免报告内自引用 commit；可用 `git log -1 -- docs/reports/agent-integration-chains-20261004/report.md` 查询报告提交。
- 原工作区 `D:/code/ProjectsAll/MoodFlow` 保持观测分支 `codex/handoff-observability-canary-20261004`，HEAD `bca48e208ddbd40e56bd37295489b343fb0a82f4`，未提交改动未被覆盖或提交。

| 文件 | 接线职责 |
| --- | --- |
| `ai-service/app/agent/handoff/journey_consumer.py` | 核对冻结的 Journey JSON，按已规划任务消费队列，目录复核与现有播放指令生成，知识节点跳过播放，记录消费进度。 |
| `ai-service/app/agent/handoff/chain_receivers.py` | C→G 摘要引用及服务端完整事实解析；复用 GeneralChat v2 适配器和 A/C 回执，组织中文导语与节点讲解。 |
| `ai-service/app/agent/handoff/agents.py` | 仅增加三个接入点：转交摘要、Curator 消费器、GeneralChat 原回调中的接收器。 |
| `ai-service/tests/test_agent_integration_chains.py` | 三条真实父图链、载荷/边/顺序/状态断言及失败边界。 |
| 本目录 `validate.py` | 隔离离线测试、复用已有锁定夹具、校验来源并恢复测试涉及的文件字节。 |

冻结的 `journey.py`、Curator/Explorer 单体实现、GeneralChat 子图、路由、父图、转交边、Guard、envelope/contracts/state 和记账代码与基线一致。没有新增数据源、音乐工具、依赖或生产配置，没有部署或 push。普通聊天和偏好/澄清仍先走原有 GeneralChat 能力分发；承接器只处理已分配的导语/节点讲解子句，不把建单后的普通陪伴替换为歌单说明。

## 链 1：Explorer → Curator

1. 冻结的 Explorer 生产者通过 `transfer_to_curator_agent` 发出 `schema_version: 1` 的 `journey_queue` JSON，仍存放于 `envelope.request.context_summary`。
2. Curator 使用原 `JourneyTransferPayload.model_validate_json` 解析，核对实际服务端 Explorer `TaskResult.data.listening_journey` 的投影。版本、歌曲 ID 或顺序不一致均返回 NEEDS_INPUT，生成动作前停止。
3. 接收数据必须对应已接受的当前转交：run、原输入、tool call、来源 Agent、目标 Agent、活动任务及完成任务 ID 都与现有 run 一致。用户文字/摘要不能成为歌曲真实性或执行授权。
4. 可播放节点先调用现有 `search_music`，要求查询对应、目录中 ID 与歌名精确匹配且唯一；每段所有可播放候选复核完成后才逐条调用现有 `play_song`。完整曲目对象仍经过原 `_reference_errors` 和 `_action_intent_errors`。
5. 原 ToolNode、`journal_tool_call`、错误处理、实体提取及规范 action ID 生成直接复用。没有新增执行渠道或绕过记账。`song_id=null` 的节点只记消费和讲解引用，不搜索、不生成播放动作。
6. 单个播放任务消费余下队列；已有后续 Curator 任务时分段消费，为原计划中的 C→G→C 留出节点边界。G→C 返回后按服务端已消费前缀继续，支持“继续播放”措辞，不重复节点。不新增任务或改写路由计划。

验收走完整生产入口 G→C→E→C：Explorer 实际生成 3 节点 Journey，其中 2 个可播放、1 个纯知识；冻结 JSON 与队列完全一致，两条规范播放动作的 ID 和曲目顺序与队列一致，纯知识节点没有播放动作。

这里的“按序播放”验收对象是**按序生成现有播放指令**。未收到 ACK 时不会宣称音频正在播放；没有新增客户端播完事件、自动排程或播放器实现。

## 链 2：Curator → GeneralChat

冻结 Curator 通过真实 `create_playlist_tool` / `add_song_to_playlist` 生成歌单，`data.curation.polish_request` 保持原 `draft / songs / result_facts` 定义。C→G 转交摘要只携带完成任务的引用说明；接收器在已接受的 envelope 与当前 run 对齐后，从服务端完成结果读取完整载荷。

没有将 arbitrary `TaskResult.data` 加入 envelope，也没有依赖模型生成的 800 字符摘要恢复事实。GeneralChat 读取的歌曲集合必须等于 `accepted_song_ids`，每个完整曲目对象必须存在于实际目录选择中。待加歌、失败或无回执的歌曲不会进入导语。

接收器调用冻结的 Curator 适配器和 `capability_response`，保留完整 facts/sources/evidence_refs，按 F/S 编号展示及分页。中文导语只用实际场景、加歌回执曲目及其数量组织，不照抄原稿中的完成说法、不添加风格/歌词/年代故事。过长标题不在导语重复堆叠，完整曲目名称仍保存在结构化事实中。

实际 15 首建单→C→G 用例保留 31 条事实：15 条目录曲目事实和 16 条创建/加歌操作事实。v1 会话查询默认读最近 8 次写入；本轮为完整 `polish_request` 的其余操作引用调用同一 A 查询及冻结的 Curator `_operation_fact`，仅补齐相同真实 operation ID 的回执。没有扩展或改写记账状态。

## 链 3：Journey 节点讲解及返回

Curator 的内部消费记录保存原 Explorer 任务 ID 和实际消费节点 ID。C→G 接收器依这些引用读取冻结 Explorer 节点的 `narration` 与来源记录，交给 GeneralChat v2 原适配器。效果事实按实际 Curator 动作引用补入原接收格式，由 A/C 决定状态；叙述文字不能充当 ACK。

验收从合法 Explorer run 开始，使用未改动的生产 Agent/转交/最终节点，实际路径为 **E→C→G→C→G**：第一首生成动作后由 C→G 讲解第一个节点，G→C 后继续第二首，再 C→G 讲解第二个节点。两个节点的 statement 和来源引用均正确出现，完成数随每次转交推进，动作 ID、歌曲及消费顺序一致。E→G 非法边被原机制拒绝，没有新增直连。

测试只在测试构图时连接合法 Explorer 起始状态；生产 `prepare_context`、入口路由及 GeneralChat 起点均保持原样。

## 四态与边界

| 情况 | 真实依据与表达 |
| --- | --- |
| 已生成 | C 投影中的规范动作；“已生成指令，待客户端执行/确认”，不宣称已经在播。 |
| 已执行 | 原动作、同会话且引用匹配的 applied ACK；“已执行：客户端 ACK”。 |
| 已确认 | A 的只读 readback；“只读核对已确认”，不伪称客户端 ACK。 |
| 未知 | 失败 ACK 或 A unknown；“效果未知”，停止后续不确定写入。 |
| 后端成功响应 | 沿用原 v1 的成功回执事实，明确“未另行只读核对”，不升级为已确认/已执行。 |

覆盖空队列、纯知识队列、目录缺失/重复、缺搜索或播放工具、用户否定播放、版本/ID/顺序被改写、重复消费、第二首播放生成失败、禁止 E→G、E→C/C→G Guard 拒绝、加歌 unknown 和 readback 恢复。拒绝转交保持已完成业务回执并诚实标记未执行步骤；部分动作生成后失败时保留已有待执行动作，不声称全队列完成。

## 5 跳预算分析

不修改冻结的 `max_handoffs=5`。生产入口 G→C→E 已消耗 2 跳。

| 链路 | Explorer 起点 | 完整 GeneralChat 入口 | 验收结果 |
| --- | --- | --- | --- |
| Journey 队列只由 Curator 生成动作 | E→C：1 跳 | G→C→E→C：3 跳 | 3 节点、2 动作与纯知识跳过通过。 |
| 建单及导语 | 不涉及 Explorer | G→C→G：2 跳 | 创建、加歌、真实 C→G 承接通过。 |
| 1 节点逐节点讲解 | E→C→G：2 跳 | G→C→E→C→G：4 跳 | ACK/四态链用例通过。 |
| 2 节点逐节点讲解 | E→C→G→C→G：4 跳 | 6 跳 | Explorer 起点闭环通过；完整入口在第 6 跳尝试前以 HANDOFF_LIMIT 停止。 |
| 3 节点逐节点讲解 | 6 跳 | 8 跳 | 超出冻结预算；本轮不宣称此完整往返可运行。 |

N 个逐节点讲解从 Explorer 起点需 `2N` 跳，完整入口需 `2+2N` 跳。用户已明确选择“按 Explorer 起点验收，保留完整入口超限测试”。完整入口的两节点测试断言只有 5 次实际转交、保留 4 个已返回任务结果、最后一项讲解仍为待办，并诚实输出“尚未执行”。原计划决定 Agent 所有权和往返顺序；本轮没有增加跳数、自动拆分任务或调整路由。

## 验证结果

专项回归已通过 **443 passed、2 subtests passed，零失败**，125.52 秒；覆盖三 Agent v1/v2、GeneralChat 子图、父图运行时、网络、Guard、组合任务及预算。随后补充的长元数据分页用例也单独通过，最终实现的 **25 个集成用例全部被全量测试再次覆盖并通过**。

完整 AI 服务与部署契约测试：**2428 passed、46 skipped、288 subtests passed，零失败**，358.72 秒。396 条 warning 均为既有 LangGraph 在无 checkpointer 时忽略 `durability` 的提示。

Ruff 检查、格式检查、Python 编译、`git diff --check` 及冻结文件范围比较全部通过。`agents.py` 仅新增 18 行接入代码，其余原内容保持一致。全量结束后的格式修正仅涉及测试字典缩进，没有功能变化。两份未跟踪知识副本已按来源记录的 SHA256 与绝对路径核对并清理。

复现命令（在本工作树根目录执行）：

```powershell
$integrationPython = 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe'
& $integrationPython docs/reports/agent-integration-chains-20261004/validate.py --fixture-root D:/code/ProjectsAll/MoodFlow -m pytest tests/test_agent_integration_chains.py tests/test_general_chat_v2.py tests/test_general_chat_results_v1.py tests/test_general_chat_subgraph.py tests/test_curator_curation_v1.py tests/test_curator_curation_v2.py tests/test_explorer_journey_v1.py tests/test_explorer_v2.py tests/test_handoff_runtime.py tests/test_handoff_network.py tests/test_handoff_guard.py tests/test_handoff_combo_robustness.py tests/test_handoff_budget.py -q
& $integrationPython docs/reports/agent-integration-chains-20261004/validate.py --fixture-root D:/code/ProjectsAll/MoodFlow -m pytest tests ../deploy/tests -q -ra
& $integrationPython -m ruff check ai-service/app/agent/handoff/agents.py ai-service/app/agent/handoff/chain_receivers.py ai-service/app/agent/handoff/journey_consumer.py ai-service/tests/test_agent_integration_chains.py docs/reports/agent-integration-chains-20261004/validate.py
& $integrationPython -m ruff format --check ai-service/app/agent/handoff/chain_receivers.py ai-service/app/agent/handoff/journey_consumer.py ai-service/tests/test_agent_integration_chains.py docs/reports/agent-integration-chains-20261004/validate.py
& $integrationPython -m compileall -q ai-service/app/agent/handoff/agents.py ai-service/app/agent/handoff/chain_receivers.py ai-service/app/agent/handoff/journey_consumer.py ai-service/tests/test_agent_integration_chains.py docs/reports/agent-integration-chains-20261004/validate.py
git diff --check
```

## 验证限制

- 端到端边界是实际父图入口/Agent 节点/转交工具/业务工具/接收器/最终结果，实际生产者未用 mock 替换。只控制外部 WorldLine 返回、目录/后端响应、模型选择和合法 RouteDecision；A 使用真实 SQLite journal。Journey 曲目 ID 来自仓库已有目录抓取夹具，歌单目录来自受控音乐服务夹具；未宣称本轮重新查询线上账号证明可播性。
- 没有调用真实音乐账号、收费模型或浏览器播放器，未验收线上音频、自然语言模型质量、自动路由选择质量、真实服务时延或播放器逐首播放完成事件。API/部署兼容性由现有全量契约测试覆盖。
- 离线复现脚本只给测试子进程设置参数，不读取生产密钥，不修改生产配置；复用既有知识/trace/锁定模型夹具。冻结测试输入先核对 HEAD 与 SHA256，结束后恢复原字节及测试生成报告。来源记录在本地 `.dev/agent-integration-chains/fixture-provenance.json`；两份未跟踪知识副本经 SHA256 与路径核对后清理，不进入提交。
- 46 项跳过沿用既有环境条件：42 项需要隔离 PostgreSQL、真实数据库/进程恢复及重启验收，2 项需要真实 Qdrant，1 项需要真实 TypeSafe API，1 项缺少 Clean v0.2 历史夹具。不计为通过；本轮没有新增跳过或放松冻结回归断言。
- 生产完整入口的两节点逐节点讲解仍需 6 跳，按冻结预算诚实停止；用户批准的 Explorer 起点验收不能解释为生产入口已支持该 6 跳任务。长 Journey 的逐节点往返限制留在后续规划，本轮没有新增功能。

三 Agent 单体功能保持冻结。本轮只完成协作接线；新的单体能力不在本轮实现。

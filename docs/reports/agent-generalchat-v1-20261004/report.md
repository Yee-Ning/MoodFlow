# GeneralChat v1：结果承接与澄清验收报告

日期：2026-10-04。基线为 `6667b70ecc1ad27786559bacb389f6f65d42098b`，含 B/A/C/R2/D/T1T2。工作在独立分支 `codex/agent-generalchat-v1`、隔离工作树 `C:/Users/33994/.codex/worktrees/agent-generalchat-v1/MoodFlow` 完成。原目录的 `codex/handoff-observability-canary-20261004` 分支及用户未提交修改保持原样。

能力定义参考原目录中的 `docs/architecture/moodflow-three-agent-capabilities-20261003.md` §3.1 与 §6 GeneralChat；请求提到的 `workspace/user/files/` 路径未在仓库中出现。

## 四态定义与证据来源

这些状态属于 GeneralChat 的效果表达，独立于 `TaskStatus.SUCCESS/FAILED/NEEDS_INPUT`；没有改动 A 的三态记账契约。

| 状态 | 必要证据 | 面向用户的表达 | 不允许推导的结论 |
| --- | --- | --- | --- |
| 已生成 `generated` | C 的 `get_combination_task_status` 投影包含生成的客户端动作；尚无匹配 applied ACK | 已生成指令，待客户端执行/确认；尚未确认实际效果 | 音频已经播放、实际操作已经完成 |
| 已执行 `executed` | 当前服务端连续任务内的 `CLIENT_ACTION` 与 `ACTION_ACK`：动作 ID、名称、事件引用、原动作会话一致，ACK 为 `applied` | 客户端 ACK 已确认该指令执行 | 此刻仍在播放，或后续组合任务也成功 |
| 已确认 `confirmed` | A 的 `OperationJournal.state(operation_id)` 返回 `confirmed`，且 `confirmation_source=readback` | 只读核对已确认目标效果 | 无证据的其他目标或客户端动作也已执行 |
| 未知 `unknown` | A 返回 `unknown`，ACK 失败、证据冲突、缺少可核对的效果来源 | 效果未知，尚不能确认；不会据此重复执行 | 确定成功、确定失败、盲目重试 |

A 的确定失败单独表达“后端已明确拒绝”，不改称效果未知；A 的原始后端成功响应单独表达“已有成功回执，未另行只读核对”，不提升到 readback 或客户端 ACK。这两类记录保留原证据维度，四态效果字段为空，不新增第五种效果状态。

## 实现与影响范围

- `general_chat/results.py`：读取 C 的现有工具投影及 A 的现有三态查询；只读检索既有 ACK 历史并核对原动作引用。生成含状态、来源、引用的结果摘要及固定中文表述。新投影不会调用业务写入、`reconcile` 或补记回执。
- GeneralChat Foundation assistant 与 `handoff/agents.py` 的 general chat 分支：将结构化摘要加入现有上下文 pack。状态追问、以及检测到的模型效果声明由程序按可信摘要渲染；模型的 `success`、自由文本、用户输入、伪造的 ACK/readback 字段无法提升效果状态。A/D 的既有写状态和恢复查询仍优先使用原查询能力。
- `general_chat/clarification.py` 与 GeneralChat graph：对缺操作、缺目标的短模糊请求直接自然澄清，返回 `NEEDS_INPUT`；不调用模型或执行业务工具。prompt 要求其他模糊请求主动询问，补充信息结合原请求及澄清历史继续，当前否定/取消优先。
- 保留原有上下文预算、历史选择、情感陪伴及 `get_current_song_context` 只读轻交流。有效和无效业务工具调用均拒绝。

未修改 Curator、Explorer、路由、转交边、Guard、组合投影实现、A/B/D 记账或 ACK 写入端点；没有新增依赖、改变生产配置、部署或 push。

## 验收覆盖

新增 `tests/test_general_chat_results_v1.py` 的 **37 个验收用例**使用真实本地 SQLite 账本和受控模型，覆盖：

1. 四态各自来源及最终话术；模型一律返回夸大的完成话术，最终输出仍按证据表达。读取新投影前后历史条数相同。
2. 原始响应成功与 readback 分离；确定拒绝与未知分离；账本证据冲突保守回答。
3. 缺 ACK、failed ACK、错误引用、错误名称、异会话、伪造结果字段均不能提升为已执行/已确认；迁移后仍读取原动作会话的合法 ACK。
4. 缺记录时不以播放器快照、任务 `success` 或模型陈述作为外部执行证明。
5. handoff 模型未调用状态工具也不能夸大；调用 C 工具后，组合失败/待办/已生成仍分别表达。
6. “帮我弄一下”“处理一下”“帮我播放”“收藏它”“删除那个”先澄清；真实 checkpoint 保留原请求及澄清问题；明确补充“暂停一下”后沿用原执行链生成动作。
7. 情感陪伴与歌曲只读交流正常；个人“刚刚完成工作”等表达不误当任务进度查询；拒绝无效工具调用。

## 验证命令与结果

验证使用既有 Python 3.12 虚拟环境 `D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe`。下表中 `python` 指该解释器，命令从隔离工作树根目录执行；`validate.py` 只为子进程设置离线占位配置，没有创建或复制 `.env`。

结果汇总见 [validation-summary.json](validation-summary.json)。

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 专项及协作回归 | `python docs/reports/agent-generalchat-v1-20261004/validate.py -m pytest tests/test_general_chat_results_v1.py tests/test_general_chat_subgraph.py tests/test_handoff_runtime.py tests/test_handoff_combo_robustness.py tests/test_write_reconciliation.py tests/test_recovery_combo_query_compat.py evals/tests/test_handoff_jev.py -q` | **151 passed、2 subtests passed；0 failures/0 errors**，57.99s |
| 全量业务测试 | `python docs/reports/agent-generalchat-v1-20261004/validate.py -m pytest tests -q` | **2063 passed、46 skipped、288 subtests passed；0 failures/0 errors**，274.84s |
| 静态检查 | `python -m ruff check ai-service/app/agent/foundation/subgraphs/general_chat ai-service/app/agent/handoff/agents.py ai-service/tests/test_general_chat_results_v1.py docs/reports/agent-generalchat-v1-20261004/validate.py` | 通过 |
| Python 编译检查 | `python -m compileall -q ai-service/app` | 通过 |
| 提交差异检查 | `git diff --check`（显式限定本任务文件） | 通过 |

测试过程保留在工作树 `.dev/agent-generalchat-v1/` 的日志与 JUnit 文件中。初次直接运行相关测试有 1 项因隔离工作树缺少必填配置失败；使用离线子进程配置后消除。第一次全量出现 99 failures/17 errors，来源为冻结字节换行和缺失的未入库测试夹具；第二次全量剩 3 项历史 RAG 冻结哈希失败。没有修改测试断言或冻结 hash：路由/JEV 夹具从 HEAD 原始 blob 恢复；4 个 RAG 冻结数据文件使用原目录中符合冻结 hash 的原始字节；仅复制测试需要的知识 release manifest、Parent 数据和两份历史 trace 到隔离工作树。测试夹具不进入任务提交，原目录不修改。复现全量测试也需这些既有本地数据和正确的冻结文件字节。

验证结束后，冻结文件已恢复 Git 默认检出形式，提交差异中没有 `evals` 文件；Windows 默认换行转换仍可能使冻结校验失败。复现时，在测试工作树中恢复 `ai-service/evals/config/router_capability_contract.json`、`router_eval_gold_v2.jsonl` 等路由冻结文件的 HEAD 原始字节，RAG 的 `rag_eval_gold_v3.ai_assisted_source_resolution.jsonl`、`rag_eval_gold_v4.candidate.jsonl`、`rag_eval_gold_v4.expanded_50.candidate.jsonl`、`rag_eval_gold_v4.frozen.jsonl` 使用原目录冻结字节，保持原 hash 断言。知识数据复制件留作本地测试输入，未暂存。

## 限制与未验证项

- 未调用真实模型供应商、真实账号 API 或生产服务；模型回答和后端失败由受控依赖模拟。真实自然语言质量及超出检测表达的开放式话术未穷尽验证；prompt 与程序兜底覆盖本次验收用例，不能等同于任意语言生成的形式化证明。
- 未执行真实浏览器播放/ACK、实时音频核验、生产 PostgreSQL 重启或 Qdrant 集成。相关可选环境测试按既有条件跳过，未弱化测试。SQLite 的来源核验及迁移会话读取有自动化证据，不能替代真实端到端验收。
- 模糊输入处理发生在已分配给 GeneralChat 的任务内；本轮不改变入口路由。澄清后的明确业务请求仍依赖现有路由/转交，已用受控路由和真实 checkpoint 验证续接，没有为模糊短答增加隐式执行授权。
- 结果摘要沿用有界最近结果窗口（最多 8 项），最终用户文字沿用 2000 字符限制。长组合、跨长程窗口的完整钻取不在本轮范围。
- 无前端 UI 改动，因此不执行前端构建及响应式视觉验证；服务代码以自动化回归、静态检查和编译检查验证。

## Commit

实现与测试 commit：`583931ba350e515e35a0105092f7cadb8c402366`（`feat: ground GeneralChat results and clarify ambiguous requests`），仅包含 GeneralChat 实现及验收测试 6 个文件。

报告与验证摘要另作独立 commit；本报告所在 commit 可通过 `git log -- docs/reports/agent-generalchat-v1-20261004/report.md` 查询，避免将 commit 自身哈希写入导致循环修改。

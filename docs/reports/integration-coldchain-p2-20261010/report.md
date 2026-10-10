# 最小冷门链路与 Redis P2 基线整合记录

日期：2026-10-10（Asia/Shanghai）。整合分支：`codex/integration-coldchain-p2`。

当前验收状态：Curator 阶段已通过完整回归，待创建合并提交；Redis P2 阶段待合入。下面保留换行恢复和历史停止点的审计记录，最新验收结果以末节为准。

基线为 `codex/journal-checkpoint-recovery` 的 `787598fbd557ab28bbcce9ac73666c914d83d0ad`；仅整合 `codex/agent-curator-v2`（`08a809c7832463a365b05467eb8b3e77fb79a287`）与 `codex/redis-p2-cache-lock`（`e20a7214b88cc2297b4166c19562269a0417d97a`）。原工作区的用户改动未复制或覆盖；不 push、不部署、不改生产开关。

## 七个历史评测文件的冻结字节恢复

恢复范围均位于 `ai-service/evals/reports/semantic_relations_v1/`。只将 LF 恢复为 CRLF；恢复前后将 CRLF 归一为 LF 后的字节完全相同。每个恢复值均与 `evals/reports/semantic_relations_v1_influence_context_repair/protected_artifacts.json` 的对应冻结摘要逐项一致；该保护清单本身的摘要未改变。

| 文件 | 冻结 SHA-256（恢复后实际值相同） | 结果 |
| --- | --- | --- |
| coverage_inventory.json | `ad872d63552ce5a4c87eefda8c7f9253fc16d8484a5b5576cf2baf6fb93563e5` | 一致，仅换行变化 |
| failures.jsonl | `4e2ef1d91bc58ad15e61e41891c85224b95f41571f0c6807586c3abc9f3fdd22` | 一致，仅换行变化 |
| human_review_pack.md | `8295f8d86d64bb6f4310399887ffc2cb7fd7630609d80d26da278b9ac9309497` | 一致，仅换行变化 |
| metrics.json | `31ab4cdc0f0f0109f9ace7d919cb54b0df8d2e50f9a6d788a4de147ce6fd0225` | 一致，仅换行变化 |
| results.jsonl | `d65e25fe8a45ef010d0936e973785b79ac9b52cbbd3f139cb1055b184b0bd3dd` | 一致，仅换行变化 |
| semantic_relations_evaluation_v1_report.md | `2d605412c6aa65d90ad9c796a37f59c5e1c8cefae069112e1423bd86383483c9` | 一致，仅换行变化 |
| validation.json | `925e4ddd93b92460c218f6e17964a494e2a86164cfea25da76880706596f8741` | 一致，仅换行变化 |

完整的恢复前摘要、Git blob 摘要、冻结摘要和恢复后摘要见 [eol_restore_verification.json](eol_restore_verification.json)。未修改评测内容、冻结摘要或断言。由于 Git 的换行归一化，七个文件没有产生内容 diff。

### 换行被改写的原因

系统配置 `D:/Git/etc/gitconfig` 设置 `core.autocrlf=true`；这七个路径的 `text`、`eol` 属性均未显式设置。Git blob 保存 LF，而原保护清单冻结了 Windows 工作树的 CRLF 字节。

直接原因是此前测试环境准备脚本将 `git show HEAD:<path>` 得到的 blob 字节写回工作树，绕过了 Git 签出的 CRLF 转换。随后按全局摘要集合选择表示形式时，LF 摘要也存在于其他历史记录中，不能替代本保护清单的逐项要求，因此保留了错误的 LF 表示。本轮不修改 Git 配置或 `.gitattributes`。

后续环境准备应使用每个文件所属的冻结清单校验字节，禁止批量将所有历史数据写成 Git blob 的换行格式。

## 基线与冲突处理

源分支基线：Curator v1/v2 82 passed；Redis P2 51 passed；当前 HEAD 相关回归 523 passed，另有 42 subtests passed。共同祖先分别为 `bca48e2` 与 `d1ff818`，与 Gate 0 要求一致。

Curator 的实际冲突文件为 `ai-service/app/agent/foundation/subgraphs/general_chat/assistant.py`。保留原 Agent Pack、历史作用域及 `route_task_id`，同时保留四态回执投影、确定性状态校验和澄清；冻结的 `general-chat-base/v1` 摘要仍为 `4626a71fee1d1d7adaa979cd27837dbed5c93da9b4e5ab2a28da5b11dc6e38e9`。新增四态文本使用独立的 `general-chat-result-evidence/v1`、variant `result-evidence`。

选曲调用复用 AgentPackBuilder、ContextSelector 的有序取舍；候选仅向模型投影目录事实和知识证据身份，完整证据保留在服务端。按照真实任务的 route_key 筛选意图，组装预留输出及请求封装开销，拒绝模型返回预算组装后未可见的 ID。L1/L2/L3、原意图筛选器、预算配置和原边界断言均未修改。新增超预算用例使用合法任务文本和超预算必需上下文，原拒绝断言保留。

Redis P2 合并及联合验证尚未执行；Curator 合并尚未提交。全量回归将在七项摘要核验通过后执行，任何回归失败立即停止。

## 日志与当前验证状态

测试范围为 `tests + evals/tests`；只使用无生产 `.env` 的隔离工作树、受控模型/音乐目录、本机独立 Redis 和一次性 loopback PostgreSQL。执行器实时落盘完整输出，并由每个 worker 立即保存失败用例名、完整堆栈和进度；进度使用追加写入，避免 Windows 文件读取占用导致原子替换失败。

此前失败已定位到 `evals/tests/test_influence_context_input_repair.py::test_frozen_export_hashes_metrics_scope_and_old_artifacts` 第 116 行。此前一次记录器内部错误及失败试跑不作为验收通过结果。七项恢复校验已通过，回归结束后再次逐项验算仍全部一致。

恢复后的完整回归命令为 `python -B ../.dev/coldchain-p2-20261010/run_validation.py curator_full_crlf tests evals/tests`，使用 4 个 worker、`--dist loadfile`、`--maxfail=1`、完整堆栈和实时落盘日志。结果：**1 failed、2264 passed、1 skipped、149 subtests passed、318 warnings**，用时 377.58 秒；pytest 因首个失败停止，退出码为 2。完整回归没有完成，不能作为零回归验收。

失败用例：`tests/test_recovery_selfhealing_process.py::test_native_exit_recovers_receipts_or_structured_block_without_external_write[after_write_before_result]`。

关键堆栈：

```text
tests/test_recovery_selfhealing_process.py:126 -> worker(..., "crash", ...)
tests/test_recovery_selfhealing_process.py:99 -> assert result.returncode == 73
AssertionError: assert 0 == 73
app/tools/music.py:145 -> SpringLikedSongsResponse.model_validate(payload)
ValidationError: data should be a valid list; actual data is a playlist-detail object
```

完整证据：[输出与完整堆栈](curator_full_crlf.log)、[最终统计](curator_full_crlf.json)、[实时失败记录](curator_full_crlf.controller.failures.jsonl)。

源码核对发现，该进程用例的请求为“新建私密歌单 MoodFlowTest-process-fixture”，旧模型脚本返回直接创建歌单的工具调用；新增 `curation_request` 将其识别为默认 15 首的场景策展，先调用红心读取并进入 JSON 规划，未到达原写后退出注入点。HTTP fixture 对红心读取没有对应列表响应，因此出现上述校验错误。当前需要处理的是直接命名创建请求与场景策展的识别兼容性；尚未修复，没有放宽响应 schema、修改退出码断言或修改恢复测试。

按“任何回归失败立即停止”要求，未提交 Curator 合并，未合入 Redis P2，联合验证未执行。整合分支 HEAD 仍为 `787598fbd557ab28bbcce9ac73666c914d83d0ad`，保留待提交的 Curator 合并工作；原工作区 HEAD、分支及用户修改保持原状。隔离测试 PostgreSQL 已清理。

## 直接操作识别修复与后续停止点

根据后续授权，`curation_request` 增加前置规则：`创建/新建 [隐私] 歌单 <明确名称>` 与歌单加歌、删歌等直接操作先返回原工具调用链；场景词即使出现在明确目标名称中，也不构成策展授权。策展仅接收带场景/情绪/年代信号或明确生成、策展、策划等动词的请求。规则只决定分派，不执行操作，原工具链的权限、目标引用和操作回执检查保留。

新增 `test_curator_direct_request_routing.py`，覆盖明确命名创建、加歌/删歌、场景策展保留，以及直接私密创建不读取偏好/目录的原工具路径。SpringLikedSongsResponse、恢复 fixture 和退出码 73 断言均未修改。

定向命令：`python -B ../.dev/coldchain-p2-20261010/run_validation.py curator_named_targeted tests/test_curator_direct_request_routing.py tests/test_curator_curation_v1.py tests/test_curator_curation_v2.py tests/test_curator_context_budget.py tests/test_recovery_selfhealing_process.py`。

结果：**1 failed、99 passed、2 skipped、124 warnings**，158.40 秒。失败用例是 `tests/test_curator_context_budget.py::test_optional_candidate_drop_is_atomic_and_model_cannot_reintroduce_its_id` 第 71 行：预期 `visible == {2}`，实际为 `set()`。

候选 1 的高相关性大项超过单次预算，候选 2 为低相关性小项。现有 ContextSelector 先按相关性淘汰候选 2，仍超预算后再淘汰候选 1，故最终为空。当前没有修改筛选器、预算护栏或断言；该生产侧候选投影/取舍兼容性问题尚未处理。

完整证据：[定向完整输出](curator_named_targeted.log)、[最终统计](curator_named_targeted.json)、[失败用例与完整堆栈](curator_named_targeted.controller.failures.jsonl)。根据失败即停止要求，本次未启动新的全量回归，未提交合并，Redis P2 和联合验证仍未执行。七个历史冻结文件再次验算仍全部一致。

## Curator 阶段最新验收

按后续授权，在原 ContextSelector 取舍流程中加入可选候选的原子预过滤：单项按真实组装口径计算后已超过可用输入预算时，先记录 `total_budget_exceeded` 并整项淘汰，再执行原去重、来源配额和 relevance 取舍。必需项不做静默淘汰，仍沿用原压缩/拒绝路径。淘汰在去重前执行，避免不可行候选先占用其他可行候选的证据身份；模型输出 ID 必须属于最终可见集合。

L1/L2/L3 数值、ContextPolicy 和 POLICIES 的 AST、原意图筛选条件、SpringLikedSongsResponse、退出码 73 断言及恢复 fixture 均保持原样。保护项核对见 [atomic_protected_contracts.json](atomic_protected_contracts.json)。新增通用回归覆盖原子淘汰、证据身份不被占用、必需项拒绝、恰好预算边界和正常 relevance 顺序；原边界断言未放宽。

| 验证 | 结果 |
| --- | --- |
| 指定定向 5 文件 | 103 passed，2 项原有 dedicated-container skipped，0 failed |
| 全量 `tests + evals/tests` | 2673 passed，12 项原有环境 skipped，288 subtests passed，331 warnings，0 failed / errors，退出码 0 |
| Ruff：本轮修改/新增 Python 文件 | 通过 |
| `git diff --check` | 通过 |
| 完整运行前后 10 份源码/测试摘要 | 无变化 |
| 七个历史文件冻结摘要复核 | 全部一致 |

全量命令：`python -B ../.dev/coldchain-p2-20261010/run_validation.py curator_atomic_full tests evals/tests`。pytest 用时 396.23 秒；此次已正常跑完，没有人为中断。12 个 skipped 是既有的 live JEV、专用 PG/重启容器和真实 Qdrant opt-in 项，没有为本轮规避失败新增 skip。

证据：[完整日志](curator_atomic_full.log)、[最终统计与 skipped 用例清单](curator_atomic_full.json)、[定向日志](curator_atomic_targeted.log)、[定向统计](curator_atomic_targeted.json)、[完整运行前后源码摘要](curator_atomic_source_hashes.json)。仅已验证的 Curator 合并将提交到整合分支；Redis P2 验证仍待执行。

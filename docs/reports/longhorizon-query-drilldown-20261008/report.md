# 当前问题驱动的 Journal History Drill-down

## Gate 0 与范围

基线为 Phase 3 `d1ff818c9318b532dfe5de81fb0c5cf2865a789e`，隔离分支 `codex/longhorizon-query-drilldown`。当前主工作区 HEAD 属于 agent-pack；West 确认以指定 Phase 3 commit 直接审计，完成四项只读审计并暂停后，再确认创建 worktree 和实施。本轮不合入 Prompt 1、agent-pack、RAG 或 Redis 分支。

| 审计项 | Phase 3 实际代码及结论 |
| --- | --- |
| 下钻入口 | `ContinuityCoordinator.begin_call` 新请求路径唯一生产调用 `history.read(identity)`，不传当前问题；仅将返回的 items 放入 `continuity_context.key_events`，进入 ContextPack |
| 选择、游标、预算 | `HistoryDrilldown.read` 在无 pointers 时读取 task 的 `event_seq > cursor` 前 count+1 条，默认从 0 开始；游标是最后成功装入的事件序号；无相关性策略。默认最大 20 条、800 tokens、8192 bytes，对完整 envelope 预留开销并最终重算校验 |
| 引用与解析 | `HistoryPointer` 为事件/artifact 粒度 `{kind,target_id}`；`EventPointer` 为 `{task_id,event_id,offset?}`，offset 仅定位，不裁切事实。解析检查 task/conversation，artifact 受公开 schema/hash 校验 |
| Journal 查询接口 | store 可按 task、session、event_type、keyword、after_seq、limit、descending 查；event_id 精确读取。`JournalReader.query` 可按带时区 start/end 过滤，并跨批扫描时间匹配；其 session 过滤需使用 store 接口 |

West 另确认：相关性排序后不能用事件序号代表已读进度，需覆盖引用去重和单条超预算空页卡死。本轮实现遵循该修正。

## 输入与确定性选择

`HistoryDrilldown.read` 新增可选 `query` 文本；生产入口传入当前 `message`。不提供 query，或提供空/空白 query，执行原顺序读取路径。既有显式引用、legacy 处理、预算、返回 schema 保持兼容。

query 模式只检索当前 continuity task 内的公开 typed Journal 事件，可以跨同 task 的 session。候选公共内容经过原解析规则验证；不读取模型 scratchpad，不检索知识库，不调用模型/embedding，不写 Journal。选择代码独立于 RAG source，仅复用基线已经使用的 token counter。

规则为 NFKC + casefold 标准化，英文/数字完整词匹配，中文连续二/三字和整段匹配；常见延续词/功能词不作独立相关性依据。每条 query 最多 4000 字符，最多 128 个匹配项。匹配权重之和优先，然后按事件类型权重（decision 5、structured output 4、assistant 3、user/tool result 2、transfer 1），再按提交序号由新到旧，最后以 event_id 稳定破同分。不使用实时钟，分页过程中排序不漂移。只有词面命中的候选才返回；没有匹配可以为空。

对 public_text artifact，校验公开类型、任务归属和 content hash，使用完整安全文本作匹配，默认仍返回 artifact 的原引用和 lazy projection，显式 expand_artifacts 才展开。多个事件引用同一 artifact 只保留一个候选。普通事件保持原事件引用粒度；没有合成或改写历史事件。

## query 游标与翻页

保留 `DrilldownPack.next_cursor` 的非负整数类型及 `HistoryPointer` 原格式。query 模式的整数是 opaque continuation token，编码格式标记、query/task/conversation/显式引用列表/展开模式 fingerprint、首次读取的事件序号上界、已消费的排名位置。调用方只能原样传回同一 query；不能按事件序号或普通数值作运算。不是 Journal 事件序号。

首次查询固定可见历史上界，每次重建该上界内的确定性候选排名；后续传 next_cursor 按排名继续。不依赖进程内缓存、数据库写入或旧 reader 对象。分页期间新增事件不影响原快照，重新 cursor=0 查询即可纳入。引用按 `(kind,target_id)` 去重后再排序，分页不会因为较晚事件排到前面而丢掉较早候选。

query 游标绑定查询范围，换 query/task/显式引用集/展开模式会明确拒绝；原事件序号不能当 query 游标。无 query 仍使用原 event_seq 游标，两种模式的 continuation 不能混用。

## 预算与空页

最大 20 条 / 800 tokens / 8192 bytes 不变，完整 query 游标及 envelope 都参与计数，仍预留 80 tokens / 256 bytes，并最终重算。

- 候选连空页都装不下：消费该排名，继续检查后续更小候选，不重复卡在同一条。
- 候选单独可装、当前页剩余空间不够：留到下一页，不消费、不遗漏。
- 没有更多可装候选：返回空/少，`truncated=false` 表示分页结束；再次调用终止游标稳定。
- 连空 envelope 都超预算：显式 ValueError，不无止境返回无法推进的空页。
- 超预算候选不会为了相关性放宽上限或裁改原始事实。若提高允许范围内的读取预算，需要 cursor=0 重新选择之前跳过的候选。

无 query 路径保持基线行为，包括单条过大时的原 truncated/cursor 表现；本轮修复的是新 query 分页路径。

## 验证与限制

最终命令、计数、12 项 skip 清单、静态基线对比、源码 hash 和环境见 `verification-summary.json`。

验证包括同一数据集第 126 条重要事件旧入口漏取、新入口取回；多页全部匹配且单条可装时无重复/遗漏；同分排序、增量写入冻结快照、reader 重建/重放、英文数字实体边界、没有匹配、显式重复引用、artifact 别名、超预算首条/全部超预算、超小 envelope、query/scope 校验、legacy 无 query 回归。真实 PG 验证跨 session 查询、分页稳定和实际 MainGraph → coordinator → ContextPack 接入；Foundation/Handoff 的共享入口均覆盖。

直接 query 读取前后，历史行数及每条原事件内容完全一致。实际业务请求仍执行既有 runner 的正常 Writer/归档动作；本轮没有新增或修改这些动作。

| 最终验证 | 结果 |
| --- | --- |
| 本轮专项 | 27 passed，0 failed / 0 skipped；含真实 PG 4 项 |
| Handoff / Phase 3 回归 | 最终全量中对应 9 个测试文件共 160 passed |
| 无 query 精确基线对比 | 从指定 Phase 3 git blob 加载原 reader，对同一历史进行 35 组返回 JSON / 错误类型与消息对比，全部一致；读取前后历史不变 |
| 全量 `tests evals/tests` | 2359 passed、288 subtests passed、12 skipped、0 failed / 0 errors；pytest 耗时 974.72 秒 |
| skip 清单 | 与已验收环境的原 12 项逐个 nodeid 一致；完整清单保留在 verification-summary.json |
| 源码冻结 | 全量前后 app/tests 所有 Python/Lua 文件 hash 不变；本轮 5 个源码/测试文件 hash 已保存 |
| 新增/修改下钻代码与测试 Ruff | 通过 |
| 全量 Ruff | 基线 5 项、当前 5 项，新增 0；既有告警不在本轮范围 |
| Git whitespace | `git diff --check` 通过 |

执行环境为 Python 3.12.10，父/子进程 UTF-8；复用仓库外验证入口设置测试环境，显式加载 pytest_asyncio / xdist，以本轮独立 PG 运行。query 的 4000 字符上限与既有 ChatRequest.message 一致。

```text
python -X utf8 <validation-driver> <focused-result.json> tests/test_query_drilldown.py tests/test_query_drilldown_postgres.py -q --disable-warnings --tb=short
python -X utf8 <validation-driver> <full-result.json> -p xdist.plugin -n 4 --dist loadfile tests evals/tests -q --disable-warnings --tb=short
python -X utf8 C:/Users/33994/.codex/tmp/query-drilldown-legacy-compat.py
ruff check app/agent/foundation/long_horizon/drilldown.py app/agent/foundation/long_horizon/query_drilldown.py tests/test_query_drilldown.py tests/test_query_drilldown_postgres.py
ruff check app tests --output-format json
git diff --check
```

`validation-driver` 为 `C:/Users/33994/.codex/tmp/query-drilldown-validation.py`；完整原始结果位于同目录 `query-drilldown-focused-final.json` / `query-drilldown-full-final.json`。仓库内摘要不含测试连接凭证。全量 Ruff 对比使用指定 Phase 3 git blob 的独立静态基线。

本轮专用 PG 容器已按 container ID + task label 核对后删除，匿名数据卷一并清理；未操作其他应用或测试容器。12 项既有环境 skip 的相应外部/live 路径未在本轮验证，其范围与验收基线相同。

限制：词面规则不提供语义检索，泛化延续句可能选不出历史；已有 Handoff/有效决策上下文仍独立生效。全局排序需要按 100 条/批扫描快照并保存匹配候选，耗时随 task 历史规模增长；本轮不新增索引、外部服务或历史契约。opaque 整数游标是内部 Python 接口；若今后暴露到 JavaScript 客户端，应按精确整数/字符串传输，避免 Number 精度损失，本轮不新增外部 API。

不 push、不部署、不改生产配置。测试 PG 为本轮专用 loopback 容器，不使用应用或生产 DB；复用已验收测试资产，知识资产不提交。Prompt 1 与本轮独立，未来集成时保留 coordinator 的换窗投影接入与 query 接入两处改动。

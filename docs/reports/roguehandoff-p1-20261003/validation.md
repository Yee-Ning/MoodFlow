# P1 转交载荷一致性校验：实现与验证报告

日期：2026-10-03。起始提交：`7efb8d60e4c5a9bf68e89a89ede8cf67c74b3a8d`。

补丁实现与本地验证完成。后端全量、隔离 PostgreSQL 专项和 eval 集合均无失败；静态检查保留 5 项既有问题，没有新增问题。逐用例结果、跳过原因和受验代码 SHA-256 见 [验证证据](pytest-results.json)。

## 实现与影响范围

发送方在 `AgentTransferJournal` 的 proposed 事件中固定规范化载荷的 SHA-256 摘要；实际 checkpoint 经核验后，committed / reconciled receipt 保留相同摘要及原 proposal 引用。摘要位于既有 `AgentTransferSummaryV1.payload_digest`，由 P0 `HistoryWriter` 的类型白名单入账。没有新增数据库表或迁移。

| 路径 | 校验与拒绝行为 |
|---|---|
| 正常转交 | 复用同一载荷构造函数生成 envelope 和发送方摘要。目标节点等待真实 committed receipt，并在任何模型、业务工具调用前核验实际节点输入。 |
| journal 对账恢复 | 先比较 checkpoint 中的实际控制消息、任务参数和 envelope 与不可变 proposal / receipt；通过后才补 reconciled。缺失或冲突不从接收载荷生成“原摘要”。 |
| 跨 namespace 恢复 | 迁移补齐路径先核验原 namespace 的实际源，再复制。目标 namespace 对复制的原 proposal 与实际目标 checkpoint 重新核验；Session / namespace 不进入载荷摘要。 |
| cached Command | P1 的纯 pending writes 判断同时核验载荷摘要；保存和目标执行边界再次核验。 |
| 冲突 | 通过既有 `PublicHistoryError` 追加幂等的 `integrity_conflict`，只记录关联键摘要。目标节点发现冲突时发出 `guard_rejected`；JSON / SSE 返回安全的需要确认响应，checkpoint 与恢复核验拒绝继续。保留冲突现场，不重建任务、不修复载荷、不自动重试。 |

规范化使用版本化、键排序、UTF-8、紧凑分隔符的 JSON，拒绝 NaN / Infinity，保留字符串内容。摘要覆盖 run / call / source / target、reason、context_summary、原始输入、当前及剩余任务的完整 DTO、已完成结果的公开摘要、目录实体引用和 evidence 引用。后续检查历史 hop 时只使用其记录的 completed_count 与实际保存字段，不把新结果混入旧载荷。

当前转交 DTO 没有 `mood` 字段；在已入账转交的 request / 控制消息中注入该字段属于非法形状，核验拒绝并记录冲突。没有为测试扩展允许的业务参数。

旧事件缺少 payload_digest 时仍可反序列化、查询和展示；这些旧事件无法授权活跃转交执行或补 receipt。本轮没有批量更新历史或用收到的载荷补造摘要。

## 验证命令与结果

所有测试在本机运行。隔离 wrapper 为 `.dev/context-loop-p0-20261002/validate.py`：测试子进程关闭 live / 灰度，使用受控模型与服务；不会编辑 `.env`。PostgreSQL 专项使用本轮创建的 loopback 临时容器和随机测试库，包含真实 PostgreSQL saver、迁移、追加历史及 `os._exit(73)` 后的新进程恢复。

| 检查 | 结果 |
|---|---|
| 修改前相关用例 | 420 passed、12 skipped；93.81 秒。这里跳过的 PostgreSQL 用例不算通过。 |
| 首轮现有关键回归 | 130 passed；60.30 秒。 |
| 新增载荷与恢复核验专项 | 63 passed；27.83 秒；后续最终全量还包含新增源 checkpoint 孤立控制用例。 |
| PostgreSQL 篡改冒烟 | 1 passed；23.05 秒。 |
| 最终后端全量 `-m pytest -q tests` | **1810 passed、38 skipped、286 subtests passed**；252.34 秒，退出码 0。37 个新增本地用例均通过。 |
| PostgreSQL `--postgres -m pytest -q tests/test_context_loop_postgres_recovery.py tests/test_context_loop_postgres_payload_integrity.py tests/test_postgres_persistence.py` | **28 passed、0 skipped**；391.05 秒，退出码 0。原有 12 个 P1 场景、9 个新增篡改场景、7 个持久化回归均通过。 |
| 独立 eval 集合 `eval.py -q evals/tests` | 198 passed；46.33 秒。 |
| 本轮变更模块与新测试的 Ruff | 通过。 |
| `ruff check ai-service/app ai-service/tests --output-format json` | 前后均有 5 项既有问题，逐项路径 / 编号 / 位置比对，无新增问题；全范围命令仍非零退出。 |
| `compileall -q`：handoff、long_horizon、新测试 | 通过；Python 模块无需另建前端产物。 |
| 当前任务的 `git diff --check` | 通过。 |

新增验收覆盖分别改动 reason、context_summary、注入 mood；envelope 与控制消息同时改动和仅改 envelope；目标、任务参数、实体与原始授权改动；公开 state digest 重算也无法绕过原摘要；旧 hop 在任务完成后仍受核验；未篡改的 receipt、幂等冲突事件、历史无摘要事件，以及真实图 / JSON / SSE 中目标业务调用为零。PostgreSQL 新增覆盖 receipt 间隙、已有目标 namespace 和源 namespace 的迁移间隙。

全量中的 38 个环境跳过项：12 个 P1 PostgreSQL、9 个新增 PostgreSQL、7 个持久化用例已由上述专项实际补跑通过；余下为 4 个 live PostgreSQL smoke、3 个 worldline PostgreSQL restart、1 个 live JEV、2 个 Qdrant 实际服务用例。本轮没有把这 10 个未执行项计为通过。全量保留 160 条既有 LangGraph durability 警告。

过程中首轮新增用例有 8 项失败，分别为测试夹具使用了无效实体字段、响应断言误用 content，以及任务参数夹具不满足原 run DTO；已修正夹具并保留强拒绝断言，最终全部通过。最终全量启动前的复查还补回零 hop 源 checkpoint 的孤立控制检查；中断的旧验证轮次不计为通过，表中为最终代码重跑结果。

## 保留的问题与未验证项

- 保留的 5 项 Ruff 问题：`foundation/routing.py` 两处 F401；`rag/parse_normalize/pdf/adapters.py` 一处 F841；`rag/source_ingestion/auto_service.py`、`rag/source_ingestion/service.py` 各一处 F401。它们在修改前已存在，没有改动这些模块或弱化规则。
- 用户已确认现在有测试账号。本补丁没有使用真实账号写歌单、调用生产模型或产生播放器 ACK；此前生产迁移报告中跳过的真实写操作复验仍需要另行补做。不能将本地受控测试视为真实账号验收。
- 没有运行 Tencent RogueHandoff-20 完整安全基准，不能宣称降低其有害执行率。背景参考：[Tencent AI-Infra-Guard](https://github.com/Tencent/AI-Infra-Guard)。
- 摘要证明发送方入账后的载荷一致性，依赖服务端和不可变 journal 可信；它不能证明发送方首次提供的文本安全，也不抵御能够同时篡改可信历史与执行状态的攻击者。JEV 的边与意图检查继续负责其原有范围。
- 未修改前端、Java、默认图、灰度开关或生产配置，不部署、不 push。没有生产负载或新代码上线后的性能测量。

## 本地提交与清理

本报告与实现、测试、验证证据一起创建独立本地 commit，提交号在交付回复中给出。仅包含本补丁文件；用户原有未提交数据、评测报告修改和根报告移动不纳入本次 commit。

测试临时 PostgreSQL 容器 `moodflow-roguehandoff-test-20261003` 使用既有 pgvector / PostgreSQL 16 镜像。专项结束后已核对本轮标签及容器身份，并删除本轮容器及匿名卷；既有容器保持运行。原始日志和 JUnit 留在本地忽略目录 `.dev/context-loop-roguehandoff-20261003/`，公开证据未复制捕获日志、私有 checkpoint 或连接信息。

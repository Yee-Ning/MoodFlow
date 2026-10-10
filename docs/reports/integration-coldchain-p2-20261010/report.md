# 最小冷门链路与 Redis P2 基线整合验收

日期：2026-10-10。分支：`codex/integration-coldchain-p2`。Curator 与 Redis P2 两次合并及联合验证已完成，完整回归 **2782 passed、288 subtests passed、12 原有 skipped，0 failed / errors，退出码 0**。

原工作区仍为 `codex/journal-checkpoint-recovery`、`787598fbd557ab28bbcce9ac73666c914d83d0ad`，用户未提交修改未被覆盖。全部实现位于独立整合工作树；没有合并 main、push、部署或修改生产 `.env`、默认图及灰度开关。

## Gate 0 与基线

已批准的 Gate 0 第 1–4 项沿用原结论，没有重新执行合并前核对。

| 项目 | 结论与证据 |
| --- | --- |
| 三分支可访问 | 通过。journal HEAD `787598fbd557ab28bbcce9ac73666c914d83d0ad`；Curator v2 HEAD `08a809c7832463a365b05467eb8b3e77fb79a287`；Redis P2 HEAD `e20a7214b88cc2297b4166c19562269a0417d97a` |
| 合并基点 | 通过。Curator/journal 最近共同祖先 `bca48e2`；Redis P2/journal 为 `d1ff818` |
| 自给自足 | 通过。Curator 源分支包含知识扩展和目录校验；Redis P2 包含 Redis 客户端、派生缓存及 P0/P1 基础历史。整合后位置：`curator_knowledge.py:192`、`curator.py:194,487`、`redis_reuse.py:36`、`derived_cache.py:91` |
| 冲突预列 | 通过。Curator 实际需处理 `foundation/subgraphs/general_chat/assistant.py`；Redis P2 的两个重叠文件自动合并，无未解决内容冲突 |
| 修订后的三项测试基线 | 全通过：Curator 源工作树 v1/v2 **82 passed**；Redis P2 源工作树两文件 **51 passed**；journal 原 HEAD 相关回归 **523 passed + 42 subtests passed** |

## 两次合并与取舍

| 阶段 | 合并提交 | 冲突与处理 |
| --- | --- | --- |
| Curator v2 | `4c4ff182d673dcc2949bb09ea801cc7667039df3` | `general_chat/assistant.py` 保留 Agent Pack、历史作用域、route_task_id，同时保留四态回执投影、确定性状态校验及澄清。冻结 base/v1 不变，新增结果证据文字使用独立版本/变体 |
| Redis P2 | `62b594754c97a70bd77eec89449e561d6a90b7c1` | `foundation/context_engineering.py` 与 `llm/runtime.py` 自动合并，无内容冲突。保留候选原子预过滤、AgentPack 组装及 P2 纯派生缓存；P0/P1 基础随 P2 的原历史继承 |

只合入以上两个分支；`agent-integration-chains`、两个 longhorizon 分支、`memory-hardening`、`redis-infra` 的分支 HEAD 均未进入整合历史，见 [final_verification.json](final_verification.json)。

### Curator 兼容性修复

- **Prompt 冻结**：`general-chat-base/v1` 摘要保持 `4626a71fee1d1d7adaa979cd27837dbed5c93da9b4e5ab2a28da5b11dc6e38e9`；新增四态文本使用 `general-chat-result-evidence/v1`、variant `result-evidence`，拥有独立不可变 digest。
- **预算组装**：40 首候选通过 `AgentPackBuilder + ContextSelector` 组装，保留目录事实和知识证据身份，完整证据留在服务端；按真实选曲 route_key 筛选意图，预留输出及请求封装开销，发起模型调用前执行原 ProviderBudget 校验。
- **原子淘汰**：单项真实组装大小已超过输入预算的可选候选先整项淘汰，再沿用原去重、来源配额及 relevance 取舍；必需上下文继续走原压缩/拒绝路径。模型返回 ID 必须属于预算组装后的可见集合，不能重新引入已淘汰候选。
- **直接操作快路**：明确命名的建歌单、加歌、删歌请求先走原工具链；策展分类接收场景/情绪/年代信号或明确策展动词。没有修改音乐响应 schema、恢复 fixture 或退出码 73 断言。
- **合法拒绝用例**：超预算测试使用短任务文本与足够大的必需场景上下文；任务满足 RouteTask 的 4000 字符约束，原预算拒绝断言保留。

整合前后 ContextPolicy、POLICIES、原意图筛选条件和 ProviderBudget 的 AST 一致；预算配置、预算边界测试、进程恢复测试和 MusicServiceClient 内容一致，见 [combined_integrity_after.json](combined_integrity_after.json)。L1/L2/L3 预算数值未调高，原断言未放宽。

### Redis P2 缓存契约

派生 key 保持 `contextpack:derived:{user_id}:{input_hash}:unicode-v1-derived-v1`，默认 TTL 300 秒。缓存只存 token 计数及去重摘要，Collector/Selector、预算校验和审计每次仍运行，不缓存最终 Pack 或模型输出。

本次没有改变 Unicode 计数/去重计算的语义；原子预过滤只改变选择顺序，缺少的计数可重新计算，因此保留派生版本号。Curator 的 `curation-provider-json:v1` 自定义计数器按既有 P2 规则旁路该缓存，避免复用不同计数口径。冒烟已验证真实 user_id 场景下也不会进入该缓存。

Redis 连接、TTL 和各缓存开关沿用源分支可选配置，默认保持 none；未开启生产 Redis 模式。歌单/收藏业务事实仍由音乐 API 提供，其操作在 PG 记录审计；token 计数与去重派生缓存只进 Redis，不成为音乐业务事实源。

## 验证结果与执行方式

| 验证 | 结果 |
| --- | --- |
| Curator：用户指定定向 5 文件 | **103 passed，2 原有 skipped** |
| Curator 合并前全量 `tests + evals/tests` | **2673 passed，288 subtests passed，12 原有 skipped，0 failed / errors** |
| Redis P2 + Curator + 原相关基线，19 文件 | **371 passed，2 原有 skipped，0 failed**，193.73 秒 |
| 两次合并后全量 `tests + evals/tests` | **2782 passed，288 subtests passed，12 原有 skipped，336 warnings，0 failed / errors**，406.81 秒，退出码 0 |
| 导入及关键路径冒烟 | 7 模块导入、版本化 key、默认开关、冻结 prompt、直接/策展分派、可见集合 `{2}`、拒绝模型重新引入 ID、严格目录 ID 校验全部通过 |
| Ruff、Python 语法编译 | **46 个本轮 Python 文件通过**；编译未写入 bytecode |
| `git diff --check`、`git diff --cached --check` | 通过 |
| 全量运行前后源码摘要 | 全部一致；合并提交后再次验算一致 |
| 冻结评测字节 | **373 个摘要全部一致**，其中下列 7 个历史文件保持批准的 CRLF 字节 |

完整回归命令（整合工作树 `ai-service` 目录）：

```powershell
& 'D:\code\ProjectsAll\MoodFlow\ai-service\.venv\Scripts\python.exe' -B ../.dev/coldchain-p2-20261010/run_validation.py coldchain_p2_full tests evals/tests
```

执行器使用 4 workers、loadfile、`--maxfail=1`、完整堆栈；stdout/stderr 实时 flush/fsync 保存，worker 的失败记录和进度均采用追加写入。此次全量正常跑完，没有人为中断。受控模型与音乐目录、本机独立随机端口真实 Redis、一次性 loopback PostgreSQL 参与验证；Settings 禁用 dotenv，不读取生产 secret。Redis fixtures 正常退出，核验完整容器 ID 后已清理一次性 PG；未触碰其他容器。

12 项 skipped 与 Curator 全量基线的 nodeid 集合完全相同，属于既有 live JEV、外部模型/专用 PostgreSQL 重启及真实 Qdrant 的 opt-in 验证；未新增 skip。这些真实外部依赖路径本轮未验证，没有前端修改或部署验证。

证据：[最终统计](coldchain_p2_full.json)、[完整输出](coldchain_p2_full.log)、[组合定向统计](redis_p2_combined_targeted.json)、[组合定向输出](redis_p2_combined_targeted.log)、[冒烟](combined_smoke.json)、[运行前摘要](combined_integrity_before.json)、[运行后摘要与保护契约](combined_integrity_after.json)、[测试环境清理](environment_cleanup.json)。

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

Redis 合并前一次 Git 中止/回滚的重新签出也触发了评测资料的换行转换；随后逐文件按所属冻结摘要恢复了 597 个文件的字节表示，归一化内容未改变，没有纳入源码合并提交。恢复记录见 [post_merge_fixture_verification.json](post_merge_fixture_verification.json)；373 个受保护文件在回归前后仍全部匹配。工作树可能显示换行造成的 stat 差异，但 `git diff --quiet` 和内容 diff 均确认无未提交内容改动。

## 历史失败审计

此前每个失败均按硬约束停止，后续修复均基于逐次授权；失败试跑不计入验收通过数。冻结字节问题对应 `evals/tests/test_influence_context_input_repair.py::test_frozen_export_hashes_metrics_scope_and_old_artifacts`；直接创建被误判为策展对应 `tests/test_recovery_selfhealing_process.py::test_native_exit_recovers_receipts_or_structured_block_without_external_write[after_write_before_result]`（退出码实际 0、预期 73）；atomic drop 对应 `tests/test_curator_context_budget.py::test_optional_candidate_drop_is_atomic_and_model_cannot_reintroduce_its_id`（实际空集、预期 `{2}`）。

恢复进程失败的完整堆栈及最终统计保留在 [curator_full_crlf.controller.failures.jsonl](curator_full_crlf.controller.failures.jsonl)、[curator_full_crlf.json](curator_full_crlf.json)；atomic 失败保留在 [curator_named_targeted.controller.failures.jsonl](curator_named_targeted.controller.failures.jsonl)、[curator_named_targeted.json](curator_named_targeted.json)。修复后的独立 Curator 完整验收见 [curator_atomic_full.log](curator_atomic_full.log)、[curator_atomic_full.json](curator_atomic_full.json)。

## 热度过滤 Gate 0 前提与限制

**已具备冷门知识扩展链路和 Redis 封装，但评论数读取接口及真实返回字段尚未核实，因此还没有满足“热度过滤 prompt”的全部 Gate 0 前提。**

当前发现依据是知识关联和初轮搜索之外的目录歌曲，不等同于按平台热度判定冷门；本轮没有实现热度过滤、评论数缓存、可配置推荐权重、A/B 或效果评估。目录验证使用 `MusicServiceClient.search_music` → Spring `/api/music/search` → 网易云 `/cloudsearch`（type=1），验证可信目录 ID 和搜索契约，未验证实际播放版权或地域可用性。阈值合理性及真实评论数抽查留待热度过滤任务通过 Gate 0 后进行。

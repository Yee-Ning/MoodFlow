# Foundation 换窗投影补齐

## 基线与 Gate 0

- 基线：Phase 3 `d1ff818c9318b532dfe5de81fb0c5cf2865a789e`。
- 隔离分支：`codex/longhorizon-foundation-projection`。
- Gate 0 只读审计后暂停；West 已确认继续，并修正 Journal 边界。

| 审计项 | Phase 3 实际代码 | 结论 |
| --- | --- | --- |
| Handoff 投影入口 | `ContinuityCoordinator.draft` → `ProgressProjector.project_progress` | 输入为已提交 RUN_PROGRESS、checkpoint、转交收据及 STRUCTURED_OUTPUT；从执行快照原始输入取得 goal |
| Handoff 输出与预算 | `HandoffDraftV2` → `LongHorizonContextService.build_handoff` → `HandoffProjectionV2` | 完整 JSON envelope 使用 `unicode_heuristic:v1` 计数；默认 1200 tokens，按 summary、key_message_refs、completed_steps、important_entities、preferences 顺序裁剪，必需字段仍超限则抛错 |
| Foundation 换窗 | `ContinuityCoordinator.begin_call` 的既有 rollover 分支 | 之前用上一轮 `current_input` 作 goal，snapshot 为 None，缺少 summary、completed_steps、pending_items、next_actions 和关键消息引用 |
| 生成机制 | Handoff `ProgressProjector` | 确定性代码选择，没有模型调用；Foundation 可采用同一机制 |
| 归档边界 | `rollover` → `put_handoff` → `archive_projection` → SESSION_PROJECTION | 原有 Phase 3 canonical projection 归档保留；生成器不新增 Journal 写入，不改 Writer 或 Immutable History 契约 |

## 实现与选择规则

本轮只增加 `FoundationProgressProjector`，在 Foundation 的既有 rollover 分支调用。Handoff 的生成器、预算器、schema、归档器均未修改。

1. goal 优先沿用继承投影，否则选同一 continuity task 最早的非空 typed USER_MESSAGE。延续指令不会覆盖原目标。公有文本可来自经过类型、任务归属及 hash 校验的 `public_text:v1` artifact；goal 沿用既有 4000 字符 schema 上限，完整原文保留引用。没有可用历史时使用现有 seed。
2. constraints / decisions 复用 `effective_decision_entries` 及现有撤销机制，不从自由文本猜测约束。
3. 成功的 typed STRUCTURED_OUTPUT 在存在该 run 的已提交 CHECKPOINT 后才计入 completed_steps。保留最近 32 条结果候选，优先最近成功结论，然后继承进度；summary 同时纳入最近的已提交可见回复及继承摘要。私有 task_results/data、scratchpad、工具原始响应不进入投影。
4. 待办来自继承投影、当前已提交路由对应的未成功任务，以及同窗口更早轮次留下的公开待确认/失败结果。更早轮次没有完整 saved route 时使用已提交结果的公开文本，不猜测 input。当前保存的 RouteDecision 必须与公开 PublicRoute 匹配；缺少公开提交证据或不一致时拒绝构造，不伪造快照。当前成功任务仅移除与 input_text 完全一致的继承待办；同 task_id 后续成功结果可消除同窗口早期未成功结果。不作跨 ID 或不同文本的语义完成推断。
5. 输出使用既有 V2 schema。Foundation 没有 Handoff 的转交链或 agent owner，因此保留 unknown / None / 空链；不合成 RUN_PROGRESS 或 CheckpointRef。
6. 100 条一页读取历史，候选选择由代码执行。预算最终仍由既有 `build_handoff` 对完整投影计数及裁剪；不新增模型调用。summary 候选遵守既有 12000 字符 schema 上限。
7. PG 显式换窗与 token 压力换窗触发点保持原样。SQLite 不自动换窗，显式请求仍按原边界拒绝；未确认客户端动作、未完成运行和未知写入结果等既有门禁不变。

## 验收

最终测试结果见同目录 `verification-summary.json`。本轮包含真实 PostgreSQL saver + MainGraph + coordinator 的显式/自动换窗续接场景，检查 goal、约束、结论、待办、next_actions、关键消息引用的缺失项为零；检查新窗口接收完整投影、session lineage 正确、只有一次既有 SESSION_PROJECTION 归档、历史原记录不变及无新增生成模型执行。

另外覆盖必需字段超预算报错、普通投影完整 envelope ≤1200、低价值内容优先裁剪、私有/未提交结果排除、继承窗口、artifact goal 与任务隔离、SQLite 边界，并运行 Handoff/Phase 3 回归和全量测试。

| 最终验证 | 结果 |
| --- | --- |
| 本轮 Foundation 专项 | 13 passed；含真实 PG 显式/自动换窗 2 项，0 skip / 0 failed |
| Handoff / Phase 3 相关回归 | 最终全量中对应 9 个测试文件共 160 passed，行为不变 |
| 全量 `tests evals/tests` | 2345 passed、288 subtests passed、12 skipped、0 failed / 0 errors；耗时 327.94 秒 |
| skip 清单 | 与已验收环境的原 12 项逐个 nodeid 比较一致；清单保留在 verification-summary.json |
| 源码冻结 | 测试前后 app/tests 所有 Python/Lua 文件 hash 不变；本轮 4 个源码/测试文件 hash 已保存 |
| 新增文件 Ruff | 通过 |
| 全量 Ruff | 基线 5 项、当前 5 项，新增 0；既有告警不在本轮范围 |
| Git diff whitespace | `git diff --check` 通过 |

执行环境为 Python 3.12.10，父/子进程均启用 UTF-8。专项与全量通过同一个仓库外验证入口设置无生产凭证的测试环境，显式加载 pytest_asyncio、xdist，使用本轮独立 PG。具体 pytest 参数保存在 verification-summary.json，主要命令为：

```text
python -X utf8 <validation-driver> <focused-result.json> tests/test_foundation_projection.py tests/test_foundation_projection_postgres.py -q --disable-warnings --tb=short
python -X utf8 <validation-driver> <full-result.json> -p xdist.plugin -n 4 --dist loadfile tests evals/tests -q --disable-warnings --tb=short
ruff check app/agent/foundation/long_horizon/foundation_projection.py tests/test_foundation_projection.py tests/test_foundation_projection_postgres.py
ruff check app tests --output-format json
git diff --check
```

`validation-driver` 为 `C:/Users/33994/.codex/tmp/foundation-projection-validation.py`；完整原始结果位于同一临时目录 `foundation-projection-focused-final.json` / `foundation-projection-full-final.json`，仓库内摘要不包含测试连接凭证。全量 Ruff 与指定 Phase 3 git blob 生成的独立静态基线比较，五项现有告警完全一致。

本轮专用 PG 容器已按 container ID + task label 核对后删除，匿名数据卷一并清理；其余应用和测试容器未操作。

## 边界与交付范围

- Select 是确定性材料选择，不对自然语言作语义摘要或隐式推断任务完成。任务切换依赖既有 continuity task 生命周期；同一 task 内不会把普通后续输入猜作新目标。
- token 限额沿用项目启发式计数器，代表该 counter 的 1200 上限，不宣称等于任意模型 tokenizer。
- 必需 goal/constraints/pending/状态字段本身超限时仍显式报错，不静默超发。
- 不改 Journal Writer、历史/schema、Handoff 逻辑、agent-pack、RAG、Redis、生产配置；不 push、不部署。
- 复用原环境 Python / eval 依赖及已验收测试资产；隔离工作树中的知识资产仅用于验证，不提交。真实 PG 为本轮独立 loopback 测试容器，不连接应用或生产 DB。

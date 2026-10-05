# Context Engineering Phase 1 交付报告

日期：2026-10-05。基线：`bca48e2`。分支：`codex/context-engineering-phase1`。

## 设计依据与范围

用户补充的 `C:/Users/33994/Downloads/context-pack-collect-select.md` 第一部分“设计与过程”与本轮任务一致，作为设计依据。原请求中的 `workspace/moodflow-review/context-engineering-entry-pack.md` 在工作区不存在。

复用现有 L1 的 Collect → Select → Dedup → Token Budget → Compress → Assemble。本轮新增入口两阶段、画像文档和历史回忆来源、配置化配额、预算防旁路验证和 manifest；保留现有路由判定、Guard、记账和转交边。没有修改生产配置、部署应用或 push。工作区原有未提交文档及数据改动未纳入本次提交。

## 实现

### 两阶段入口

- Foundation 默认入口先做纯规则快判：完整规则覆盖才短路，复用原有 IntentMerger/RouteDecisionBuilder 生成任务；命中时不构建 lite、不调用分诊。
- 三 agent 协作入口复用既有 HandoffEntryRouter 的规则计划和 preflight，以保持否定语句、上下文缺失检查和任务 span 行为；命中时跳过 lite 和模型分诊，保留本地路由遥测。
- 未命中时构建 lite，随后进入原 router，再运行完整 L1。使用请求级 ContextVar 将已截断的 lite 材料送入 JEV 和 LLM 分类调用边界，离开作用域后自动清理。
- 显式注入的 router 保留既有优先级；需要验证规则入口时可同时注入 EntryContextEngine。显式 development_routes 已知意图，直接完成 full 打包。
- lite 只有本轮输入、最近 6 条 user/assistant 消息和会话投影；总材料预算 1800 token，投影最多 400 token。先保留本轮输入，再保留投影和最近消息；超限直接截断，不启用意图门控或压缩。
- 本轮意图不回填 Collect：作为 Select 参数、Assembler 的 `<current_intent>` 区段以及 state 的 `entry_intent` 保存。多任务按每个 route 的既有 L1 policy 分别打包；后续每次模型调用继续接受其自身的 L1/最终请求预算检查。

### 画像与记忆

- ProfileDocument 是按 user_id 隔离的版本化文档；ProfileEntry 有稳定 key、值、来源、带时区更新时间、置信度和适用意图。
- SQLite/PostgreSQL 保存不可变版本。ProfileWriter 使用 expected_version 乐观并发控制，拒绝过期版本和比已有条目更旧的更新；文档 schema 拒绝重复 key 和空白条目字段。
- 与 HistoryWriter 共用 `validate_public_document`：Pydantic 字段白名单、私有/凭证文本检测、16 KiB 文档上限。原 HistoryWriter 的身份、引用、幂等和事件封装检查保留。
- 复用已有 MemoryRuntime 提取结果，将成功的 PROFILE 记录同步到结构化画像文档；类别到意图的映射在 context_policy 中。失败只记录异常类型，不输出内容，不使既有记忆链路失败。
- user_profile Source 按更新时间/置信度取得有界候选，按当前 query 提取短片段，由 Select 根据本轮意图筛选；不把画像全文送给模型。
- history_memory Source 从当前 conversation 所属的最多 4 个历史 task、每 task 最近 24 个 immutable 事件取候选，支持跨 session 回忆。只读取公开 USER_MESSAGE/ASSISTANT_MESSAGE，按同一请求的历史 route 标注适用意图，排除本轮请求和重复本轮输入，附 event/session/时间和引用。
- 历史回忆作为 `long_term_memory` 数据区段发送，类型为 episodic_history；与当前消息分区，文本字段不获得指令权。未知历史路由保守归为 general_chat，不覆盖本轮意图。
- `006_user_profile_document.sql` 提供 PostgreSQL 表和禁止更新/删除的触发器。本轮仅对独立测试数据库应用，未迁移生产数据库。

### 注册、策略与预算

- ContextSource/SourceRequest/SourceRegistry 统一接入新来源；SourceRequest 没有本轮 intent 参数，Select 才进行意图门控。
- `context_policy.json` 配置新来源类型、优先级、token 配额、候选数和片段长度：画像 priority=10、240 token、16 候选、120 字符片段；回忆 priority=20、600 token、24 候选、400 字符片段。画像为最低优先级。
- 新来源必须属于注册配置、类型匹配、且为 optional；Collect 重新计 token，不信任来源自行声明的 token_count。新来源不参与旧 JEV context selector 的打分，交由确定性 Select 处理。
- 所有新增文本通过 ContextAssembler 进入调用输入。JEV 的 state 和 questions 最终 framing 一起过 ProviderBudget，保留既有 request accounting；LLM 路由继续使用 prepare_model_input。
- 自定义 Responses renderer 也通过 Assembler 补入意图区段，补入后重新检查最终请求预算，避免 Worldline 等调用丢失意图区段或在 framing 之后追加未经计数的文本。

### 可观测

- 每次 ContextPack manifest 含各来源 candidate_tokens/final_tokens、截断/压缩/预算丢弃/意图排除事件、dedup_dropped；即使没有候选，两个新来源也有零值条目。
- MainState 的 context_manifest 区分 lite/full，完整包按 route 列出，并附来源 available/empty/unavailable 状态。
- 每次实际 L1 调用记录 `context_engineering.manifest`，包括 token 和取舍信息，不记录原始画像、历史文本或 prompt。
- 各来源 token 数是条目内容的计数；pack 的总 token 另包含 Assembler 区段标签、意图等开销，最终调用门还计入 provider/schema framing。

## 验证

最终完整验证通过：**2287 passed、12 skipped、288 subtests passed，0 failures / 0 errors**，退出码 0，用时约 12 分 22 秒。原有 180 个 warning 未作为失败。完整套件包含新增的 2 个评测工具测试；脱敏统计见 `verification-summary.json`。

完整套件从 `ai-service` 目录执行，测试数据库为一次性本地容器。可复现命令（数据库 URL 只含测试凭证）：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -c "import os,sys,pytest; os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PERSISTENCE_BACKEND='sqlite',AI_CONTINUITY_STORE_MODE='sqlite',DATABASE_URL='postgresql://postgres:phase1-test-only@127.0.0.1:15465/moodflow_phase1_test',MEMORY_BACKEND='memory',CHECKPOINT_BACKEND='memory',JEV_ENABLED='false',DEEPEVAL_DISABLE_DOTENV='1',AI_RAG_RETRIEVAL_MODE='empty'); sys.path.append('.venv-eval/Lib/site-packages'); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests','evals/tests','-q','--disable-warnings','--tb=no']))"
```

静态检查使用 `.venv/Scripts/python.exe -m ruff check <本轮修改的 Python 文件>`；编译检查使用 `.venv/Scripts/python.exe -m compileall -q app evals/context_entry_phase1.py evals/tests/test_context_entry_phase1.py tests/test_context_entry_phase1.py tests/test_context_entry_postgres.py tests/test_handoff_entry_rules.py`；本轮文件的 `git diff --check` 通过。全仓库 diff 检查存在原有 `gold_review_v0_blind_pack.md` 尾随空白，未修改该无关文件。没有前端改动，未执行前端视觉验证。

已完成的分组验证：

- 初始既有 L1/Foundation/V5 回归：41 passed。
- 入口、公开历史、JEV、规则相关验证：101 passed。
- API、入口、画像、协作运行、流式输出和 V5 回归：173 passed。
- 最新 Assembler/自定义 renderer/预算和入口验证：91 passed。
- 单独评测测试：198 passed，使用现有 `.venv` 和 `.venv-eval` 的已安装依赖，未安装新依赖。
- 修改文件的 `ruff check` 和 Python `compileall` 通过。

核心新增测试覆盖：规则命中跳过 lite、未命中消费 lite、两条入口时序、意图不进入 Collect、按意图排除、低配额、伪造 token_count、required/未注册来源拒绝、真实模型调用边界、XML 边界逃逸防护、画像隔离与版本冲突、旧版本持久化、历史跨 session 回忆、来源异常降级、manifest dedup/截断归因和 ContextVar 并发隔离。

| 验收项 | 验证证据 |
| --- | --- |
| 来源注册、不同意图取得不同材料 | `test_registry_collects_without_intent_and_select_gates_with_configured_quota`、`test_history_recall_cross_session_gated_and_not_current_context`；`manifest-example.json` |
| 规则命中短路、未命中 lite → router → full | `test_foundation_rule_hit_skips_lite_and_miss_consumes_lite`、`test_handoff_entry_shares_lite_and_full_without_changing_transfer_edges` 和既有 handoff 规则集 |
| lite/full 精度对比 | `lite-full-eval.json`，38 个配对样本；评测工具自身另有 2 个通过/失败门槛测试 |
| 新来源无法绕过预算 | `test_unregistered_required_and_forged_tokens_cannot_bypass_budget`、显式排除/空来源覆盖测试、真实 Worldline/LLM/JEV 调用边界测试和来源模块 AST 约束 |
| 新来源 manifest | 来源 token、intent_excluded、dedup、片段截断归因测试和合成示例 |
| 画像存储与白名单 | SQLite 版本/隔离/不可变/冲突测试、共享公开文本门测试，以及独立 PostgreSQL 持久化测试 |

### lite/full 配对评测

命令：

```powershell
.venv/Scripts/python.exe -m evals.context_entry_phase1 --output ../docs/reports/context-engineering-phase1-20261005/lite-full-eval.json
```

实际运行 JevRouterV5，30 个冻结 V5 样本加 8 个指代/续聊样本，共 38 个。后 8 个添加 10 条更早的独立历史消息，确保 full 与 lite 使用不同大小的上下文。lite 的材料为 175–185 token，full 为 349–359 token。

| 指标 | lite | full |
| --- | --- | --- |
| 路由/澄清结果正确 | 35/38（92.11%） | 35/38（92.11%） |
| 历史场景正确 | 6/8（75%） | 6/8（75%） |
| Hybrid 回退 | 1 | 1 |

差值 0 个百分点，通过预设“最多下降 5 个百分点”门槛。两侧相同的失败：v5-17 多任务 task_conflict 回退后的任务缺失，以及 ctx-01/ctx-04 指代任务被现有编排要求澄清。结果文件逐例记录 router_source、fallback_reason、response_model、api_calls、输入材料 token 和正确性。这里测量的是现有路由与编排结果；不是独立的 JEV head 精度，也不声称指代执行能力已完整解决。

## 测试环境处理与限制

- 首次将 tests/evals/tests 放在默认 `.venv` 时，缺少 deepeval，收集失败；单独 `.venv-eval` 缺少业务侧 langgraph/typesafe_sdk。后续仅组合现有依赖路径解决。
- 评测库 dotenv 自动加载曾使测试进入不可用的既有 PostgreSQL 配置，导致 PoolTimeout；停止该轮后采用进程内显式测试配置，未修改 `.env` 或生产配置。
- 首轮全量验证的失败输出含敏感配置值；原始 XML 已删除，交付证据仅保留脱敏统计。应轮换受影响的 TYPESAFE_API_KEY 和 MINERU_API_TOKEN，不在报告中记录值。
- 最终常规套件禁用评测库的 dotenv 自动加载，明确指定 memory checkpoint、空检索适配器和本地测试持久化后端；Windows 子进程使用 UTF-8，避免默认中文编码与现有 UTF-8 读取器不匹配。没有为此删除、跳过或弱化任何断言。
- 中间全量运行得到 2284 passed、1 failed、12 skipped、288 subtests passed；唯一失败是 `test_history_and_lineage_survive_store_reopen` 的中文解码。仅在运行中设置 PYTHONUTF8 不会改变父进程已经确定的默认编码，改为启动 Python 前设置后，相关模块 17 项通过，再执行最终完整套件。
- 原有条件跳过涉及 1 个 live JEV 用例、4 个开发数据库 smoke 用例、3 个 Worldline 跨进程 opt-in 用例、2 个指定旧容器的重启验收和 2 个真实 Qdrant 服务用例。本轮画像 PostgreSQL 与崩溃恢复测试使用新容器实际执行；未启用指向既有环境的额外 opt-in 验收。
- 最终 PostgreSQL 验证使用新建的 `moodflow-context-phase1-test-20261005` 容器（已有 pgvector/pgvector:pg16 镜像），仅监听 127.0.0.1:15465；各 PostgreSQL fixture 创建随机测试数据库。测试完成后移除该独立容器。
- 初始规则入口覆盖显式注入 router 的问题已修复；保留 JSON/SSE 契约的原有断言，未删除或弱化测试。
- 部署时 PostgreSQL 画像表需要运维应用新增 migration；本轮交付代码与迁移文件，没有执行生产迁移。
- 用户画像的意图映射、候选上限和片段长度是初始确定性策略；历史回忆的新来源限定当前 conversation 的公开档案，既有 user 级 episodic memory 保持原有行为。
- 分类样本只有 38 个，未覆盖全部真实分布。已有 L1 token counter 仍为 Unicode heuristic，本轮没有调整 token 算法、L2/L3、公共契约统一、Guard 或转交权限。

## 提交

实现、测试、配对评测结果、manifest 合成示例及本报告作为一个独立 commit 提交到 `codex/context-engineering-phase1`；commit 标识以该分支 `git log -1 --oneline` 为准。本轮没有 push、部署或修改生产配置，原有无关工作区变更保留。

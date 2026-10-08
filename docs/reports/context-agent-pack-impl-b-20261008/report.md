# Agent Pack 实现 B 验收报告

日期：2026-10-08。分支：`codex/context-agent-pack-impl-b`。基线：A 实现提交 `55ee1fb9e5d8411251c1b9c4dc621c5588812a1f`。设计依据：`codex/context-agent-pack-design` 的 `8b88dc2ee8ea992ea75fd5d721620116472ee914`，`docs/architecture/context-agent-pack-design-20261008.md` §6。

本轮新增正式 `received_envelope`／`session_view` Source、可信当期 Hop 作用域及来源准备／组装链路。具体业务 agent 的 LLM 调用改用 Agent Pack 属于 C/D，本轮没有修改其业务入口。

## 1. Gate 0 与确认

Gate 0 只读核对完成、输出审计表后停止；West 随后明确确认三处处理并要求继续 B。

| 核对项 | 实际形态与处理 |
| --- | --- |
| 基线 | `git log -1 --format=%H codex/context-agent-pack-impl-a` 等于上述完整 SHA，从其创建 B 分支 |
| A 接口 | `AgentPackBuilder`、`AgentPackInvocation`、`prompt_bindings.py` 和严格 `agent_context` DTO 均存在；A 的 envelope 字段只有审计引用，没有完整载荷；沿现有 `inputs.registered_items` 接入，A 三个实现文件未修改 |
| Envelope v1 | `handoff/contracts.py` 的统一结构，含 run/tool_call/source、request、active/pending tasks、成功结果摘要、实体及 evidence_refs；context_summary 在 request 中，64KiB 载荷门保留 |
| 接受流程 | `request_handoff` 检查边、目标、状态、预算、重复 call 和无进展回转；工具流程再走原 Guard，并生成配对控制消息与 transfer_envelope |
| 提交校验 | 图内先 `await_committed`，验证 committed/reconciled、原 proposal digest、真实消息／Envelope，再执行原 `before_agent`。B 的临时作用域位于这些检查之后 |
| 四条边 | Registry 仅 G→C、C→G、C→E、E→C；能力 owner、两跳 Curator 中转、禁止无进展回转等原约束保持 |
| Source 接口 | `SourceRequest(query, continuity, profile_store=None, history_store=None, allow_new_sources=True)`；`register(source)` 要求 JSON 配额；`collect` 返回候选与状态。接口未改 |
| Registry 错误处理 | 原实现将所有来源异常变成 unavailable；按确认增加专用完整性异常透传，普通故障仍按原规则降级 |
| 生命周期 | 原业务完成、拒绝与 final 路径清除 Envelope；中转成功用新 Envelope 替换。历史 hops 保留，不能把“有历史 hops”当作当期必有 Envelope |
| Session 进度 | 复用 `ProgressProjector._validate_record` 对 run、提交链、checkpoint、结果引用的既有校验；旧完整投影继续走原 required 继承路径 |

三个已确认裁决均落实：临时可信快照经原 A 输入接入；完整性错误专用异常硬拒绝；有载荷却无提交证明时正式 Source 拒绝，无 Journal 的原兼容图执行继续保留，不制造 receipt。

## 2. 接收、准备、消费

`handoff/hop_context.py` 的 `hop_source_scope` 在接收门后创建 `CurrentHopSnapshot`。载荷被固定为 JSON 字符串，task、run、owner、chain 和真实 Journal 引用随当期快照绑定；未读取任意私有 TaskResult.data、凭据或隐藏推理。原完整性核对继续使用 Phase 3 统一 Envelope／消息协议，不新增 per-edge DTO。

快照只存在于 ContextVar，不写图状态、checkpoint、continuity 或公开 Journal。Hop 作用域结束立即撤销 lease 并恢复 ContextVar；子任务即使继承该上下文，也不能在 lease 失效后再次拉取来源。原 transfer_envelope 的清除／替换路径保持原样。

`foundation/hop_sources.py` 提供：

- `prepare_hop_sources(invocation, registry=None)`：核对可信作用域与本次 run／owner／完整 task，复用已有预算预处理，注册并收集两个 Source，返回 `PreparedHopSources(invocation, source_status)`。
- `build_hop_pack(invocation, builder=None, registry=None, **kwargs)`：通过原 A Builder 组装，返回 `HopPackResult(pack, source_status)`。候选经既有 Collect → 类型／意图 Select → Dedup → Budget → Assemble；B 只提供确定性的边投影和当前 route 输入。

准备后的候选通过 `AgentPackInputs.registered_items` 进入 A；没有修改 A 的适配器、DTO 或 Prompt 接口。新的来源名称仅在 Hop 专用 Registry 中注册，原 entry 默认的画像／历史注册组合不变。`source_status` 是准备结果的观测字段，未向严格 PackManifest 塞入未知顶层字段。

当期 Envelope 的验证先于预算停拉，budget_stopped 不能掩盖篡改或作用域错误。Source 不执行工具、模型、提交、归档或 evidence 自动下钻。实际 reserve／settle、Guard、授权和转交控制权仍由原流程管理。

## 3. 两个 Source 与选择规则

| Source | 类型／配额 | 来源与行为 |
| --- | --- | --- |
| received_envelope | optional business_state，priority 90；JSON 最大 24 候选／1280 token，单项 256 token | 只读取可信快照中的已提交 Envelope；按字段、单条结果、完整目录身份投影、单条证据引用输出 |
| session_view | optional business_state，priority 75；8 候选／600 token，单项 256 token | 有界读取当前 session 的 RUN_PROGRESS，选当前 run 的有效公开状态、chain、pending task ID 和结果摘要／引用 |

Envelope recipe 的更小软上限沿设计：GeneralChat 16 候选／768 token，Curator 24／1024，Explorer 24／1280；relay 最大 4 候选／256 token，实际只输出一条最小路径说明。Source 总额及 L1 超额继续由原 Selector 写 dropped_items／sources.events。

| 边 | 取用 | 排除 |
| --- | --- | --- |
| G→C | context_summary、公开成功结果摘要、相关歌曲／歌手／专辑／歌单身份、evidence 引用 | 原始任务／授权全文的第二份复制、播放完整对象、动作参数；摘要不授权工具操作 |
| C→G | 摘要、公开成功结果、音乐实体的 ID/type/name/source 身份、证据引用 | 播放对象、工具协议、播放器字段；不能用成功摘要宣称 failed/pending 工作已完成 |
| C→E | 摘要、相关音乐／检索结果、歌曲／歌手／专辑身份、evidence 引用 | 普通聊天结果、歌单及播放／写操作字段；不自动读证据正文 |
| E→C 业务接收 | 摘要、WorldLine 公开完成结果、有来源实体与引用 | 无关已完成工具结果、歌单对象、播放动作参数；叙事不能授予操作权限 |
| E→C 或 G→C 的 relay | 当前 task ID／route、当前 owner、下一跳；query 仍来自 runtime task | 不取 Envelope 业务摘要／实体／工具结果，不读取画像、历史记忆、RAG，不带业务工具对象 |

实体为一个完整、只读的目录身份投影，排除 duration、cover_url、position、playable、queueable 等播放对象字段。超过 256 token 的实体不截坏 JSON，改为单条引用并标明 reference_only／省略原因。文本单元保留结构和引用，用确定性前缀摘录控制到 256 token，内容与 metadata 标记 truncated，原 sources.events 记录摘录前后 token。

Session 不重复输出 Envelope 已提供的成功结果；输入含 required 继承投影时，已有 pending／completed 字段、有效 run_status／chain 字段只保留 required 提供者。旧 v1 投影仍完整、只读，不猜测 owner、projection version 或 revision。RUN_PROGRESS 的 schema 版本不冒充 session projection 版本。

## 4. 引用与完整性

来源 ID 使用 `envelope:{transfer_id}:{field}:{logical_id}`；transfer_id 仍由原 `transfer_identity(run_id, tool_call_id)` 生成。每个 Envelope 候选的 metadata 只含字段路径、原 proposal／commit 引用、完整 payload digest 和摘录数字标记，不重复保存摘要原文档案。

最终 manifest 的 envelope 记录同一 proposal、提交回执和完整 digest，selected_fields 来自实际 pack.items；source_selection 保留最终 item、出处引用和 token。未选中项的丢弃／压缩仍使用原聚合统计。event 引用携带长期 task_id，避免把 run_id 当成历史作用域。

`SourceIntegrityError` 继承原 `TransferIntegrityConflict`：Registry 透传，图沿原硬拒绝分支处理，不把冲突当 empty/unavailable，不合成业务失败结果或发起重试。校验覆盖：缺提交、缺当期载荷、完整 digest 不符、错误 owner/run/task、错误提交链及 Session 引用。校验与 Source 路径只读，不新写一份 proposal 或完整载荷档案。

原 Guard retry Command 会清除已经消费过的接收 Envelope。为了保持该流程，B 仅在存在真实 outgoing rejected 回执、允许原 retry、配对 guard_rejected 控制消息以及匹配原 proposal digest 时，认定旧输入已经消费；retry 标记本身不构成证明。此时后续 relay 只使用 runtime 最小任务路径，不复活旧载荷。未改变 Guard、retry 次数或归档格式。

无 Journal 的图路径继续执行原业务流程，但正式 Source 若发现当期载荷没有提交证明则硬拒绝；首跳无 Envelope 合法返回 empty 和 envelope=null。跨窗口继续依赖原 session 投影与公开引用，完整 Envelope 不成为长期记忆。

## 5. 验收

| 检查 | 结果 |
| --- | --- |
| B 新增测试 | 29 passed，27.33 秒；四条边、relay、首跳空、未提交／篡改／错误 owner/run/task、Registry 硬拒绝与普通故障、文本上限／丢弃审计、Session 提交链、旧投影、子任务 lease 过期、无 Journal、预算停拉、reconciled、故障、前 hop 重注入、禁止 evidence 下钻、真实图消费边界／清除、Guard retry 回执 |
| A 与原契约／入口／coordinator 相关验证 | 首轮原测试与 A 通过；新增问题已修复：Source 配额取值层级及 route 输入，新增测试夹具与压缩事件断言。没有修改原测试或弱化断言 |
| 全量 tests＋evals/tests | 2408 passed、0 failed、12 skipped、185 warnings；288 subtests passed；975.96 秒（16:15）。JUnit 核对 0 failures、0 errors、12 skipped，没有增加环境 skip |
| Ruff | 本轮 6 个 Python 文件通过 |
| 编译、diff 与接口／范围核对 | compileall、8 个暂存任务文件 git diff --cached --check 通过；对照 A 的 Git 内容，三个 A 文件及 pipeline／Guard／Registry／Envelope／budget／runtime 等 6 个核心文件不变；policy 除新增两个 Source 条目外，其余逐字段相等 |

相关命令在 `ai-service` 下执行；pytest 使用同下方受控环境（单测不设置 DATABASE_URL），通过 `pytest.main(['-p','pytest_asyncio.plugin','tests/test_agent_pack_impl_b.py','-q','--tb=short'])` 调用。

全量使用本机已有 pgvector/pgvector:pg16 的独立测试容器，仅监听 127.0.0.1:15479，使用合成测试凭据，fixture 自建随机数据库；不连接生产／应用数据库。

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -c "import os,sys,pytest; os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PERSISTENCE_BACKEND='sqlite',AI_CONTINUITY_STORE_MODE='sqlite',DATABASE_URL='postgresql://postgres:agent_pack_b_test_only@127.0.0.1:15479/agent_pack_b_test',MEMORY_BACKEND='memory',CHECKPOINT_BACKEND='memory',JEV_ENABLED='false',DEEPEVAL_DISABLE_DOTENV='1',AI_RAG_RETRIEVAL_MODE='empty'); sys.path.append('.venv-eval/Lib/site-packages'); raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','tests','evals/tests','-q','--disable-warnings','--tb=short','--junitxml=.pytest_cache/agent-pack-b-full.xml']))"
.venv/Scripts/ruff.exe check app/agent/foundation/hop_sources.py app/agent/foundation/source_errors.py app/agent/foundation/context_sources.py app/agent/handoff/hop_context.py app/agent/handoff/graph.py tests/test_agent_pack_impl_b.py
.venv/Scripts/python.exe -m compileall -q app/agent/foundation/hop_sources.py app/agent/foundation/source_errors.py app/agent/foundation/context_sources.py app/agent/handoff/hop_context.py app/agent/handoff/graph.py tests/test_agent_pack_impl_b.py
git diff --cached --check
```

剩余 12 项原环境 skip：线上 TypeSafe/JEV 调用 1 项；真实开发 PG smoke（含真实 Qwen embedding）4 项；真实 Qdrant 写入／hybrid 检索 2 项；专用 PG 容器真实重启恢复 2 项；WorldLine 真实 PG 重启验收 3 项。这些 opt-in 外部 API／服务重启行为未在本轮验证，原断言和 skip 条件未改。普通 pgvector 持久化测试本轮正常执行并通过。

JUnit 留在忽略目录 `.pytest_cache/agent-pack-b-full.xml`，未纳入提交。临时 PG 容器按创建时的完整 ID 核对名称后移除自身容器及匿名卷，未清理其他容器或用户文件。

## 6. 范围、限制与提交

只改 B 的来源层、当期 Hop 作用域、图内提交门后的作用域挂载、Registry 专用异常透传和两个新来源配额，并新增本轮测试／报告。A 三个交付源码文件、SourceRequest 接口、旧 Source 默认注册与配额、Collector／Selector／Assembler、L1/L2/L3 阈值、预算 Protocol、Guard、转交边与授权、Journal 归档 DTO 均未修改。

没有接入 GeneralChat／Curator／Explorer 的实际 LLM 调用，没有实现 RAG，也没有改 RAG 或 Redis 分支；没有新增依赖、push、部署或改生产配置。图内真实接收作用域已经挂载，消费准备及正常组装 API 已可用；后续 C/D 在各模型阶段调用此 API。

原有 gold review 改动、四个根目录报告删除和其他未跟踪资料不进入本轮提交。独立 commit 号在最终答复给出，可通过 `git log -1 --format=%H -- docs/reports/context-agent-pack-impl-b-20261008/report.md` 查询报告所属提交。

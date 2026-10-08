# MoodFlow Handoff 改造：阶段 0 基线

日期：2026-10-01，Asia/Shanghai。分析与验证对象为本地工作树，代码基准为 `e4e78cd6e2f040cf60df7ed06688e82600903fb8`，分支为 `codex/parse-normalize-corpus-v1`。

## 1. 结论与交付边界

阶段 0 已完成：建立实际版本、工作区、依赖、测试入口和 Foundation 行为基线，并记录既有静态检查问题与未验证内容。可以据此进入阶段 1 的能力和状态契约设计。

当前基线不是“全量检查全部通过”：

- 应用环境完整 AI 测试：**1335 passed，14 skipped，286 subtests passed**。
- Foundation 核心测试：**177 passed，37 subtests passed**；属于完整应用测试的子集，不重复计入总数。
- 评测框架测试：应用环境 **173 passed**，隔离 DeepEval 环境 **4 passed**。
- 追加真实 Qdrant 集成测试：**2 passed**，补验应用全量运行中原本跳过的两项；其他 12 项实时集成测试仍未执行。
- Java：**75 tests，0 failures/errors/skips，`verify` 构建成功**。
- 前端：六组自动检查、ESLint、TypeScript/Vite 构建全部成功。
- Python 语法编译和两个 Python 环境的 `pip check` 成功。
- 应用环境直接执行仓库总入口 `pytest -q`：**收集阶段失败**，原因是该环境没有 `deepeval`。已经按现有依赖隔离约定分别执行所有应用测试和 `evals/tests` 测试，没有删除或弱化测试。
- Ruff 全目录检查：**45 项既有问题，18 个文件，退出码 1**，保留为改造前静态检查基线。

本阶段只新增本报告和 [evidence.json](evidence.json)。未修改业务源码、测试、依赖声明、运行配置或模型产物，也没有启用新的 handoff 模式、训练模型或切换线上路由。检查产生了被忽略的缓存、SQLite 测试记录、构建产物及本地验证日志。

原始日志、JUnit XML 和运行诊断保存在仓库根目录 `.dev/handoff-phase0-20261001/`，不提交。`evidence.json` 保存命令、退出码、测试摘要、依赖版本、Ruff 清单和原始文本日志的 SHA-256，未记录实际凭据值。

## 2. 版本与工作区保护

外部计划以 `3a11237c` 为分析起点，本次实际代码版本为 `e4e78cd6e2f040cf60df7ed06688e82600903fb8`。后续实施必须以本次基线及届时工作树为准。

开始时暂存区为空，工作区已有：

- 一项已跟踪修改：`ai-service/evals/reports/gold_review_v0_blind_pack.md`。
- 四项已跟踪删除：根目录的 Context Engineering 两份报告、Foundation E2E 报告和 Structured Output 报告。
- 多组未跟踪的数据、评测报告、审计文件、验收响应及截图。

初始完整 `git status --porcelain=v1 --untracked-files=normal` 已保存在机器证据中。验证结束后，除本阶段新增报告外，状态列表与初始一致；开始时已修改文件的 SHA-256 也保持一致。没有还原删除项、移动已有数据或暂存用户改动。

全工作区 `git diff --check` 还报告上述已有修改文件第 26 行存在 trailing whitespace，属于原有未提交改动。本阶段不改动该文件；新增报告和机器证据单独接受 diff 检查。

## 3. 依赖与运行环境

| 项目 | 本次观察 | 对后续开发的含义 |
|---|---|---|
| Python | 3.12.10；已有 `.venv` 和 `.venv-eval` | 继续使用现有环境 |
| LangGraph | 1.2.11；Checkpoint Postgres 3.1.2 | 转交和状态设计需按实际版本验证 |
| LangChain | Core 1.6.1；OpenAI 1.6.0 | 可复用现有消息、模型和工具接口 |
| JEV SDK | `typesafe-sdk` 0.7.1 | 不新增替代 SDK |
| 应用 OpenAI SDK | 2.54.0 | 与应用声明一致 |
| 评测环境 | DeepEval 4.2.3；OpenAI 3.14.0 | 依赖按 `evals/requirements.txt` 保持隔离 |
| 测试与静态检查 | pytest 9.1.1；pytest-asyncio 1.4.0；Ruff 0.13.2 | 工具已可用；应用依赖文件没有完整声明测试工具，复现需要这些版本 |
| Node / npm | 22.21.1 / 10.9.4 | `npm ls --depth=0 --json` 成功 |
| Maven / Java | Maven 3.9.4；默认 Java 17.0.1；本机另有 Java 21.0.1 | POM 要求 Java 21，验证进程使用本机 Java 21，未修改系统设置 |
| BGE 产物 | encoder、classifier head、标签、阈值、训练配置和指标文件存在 | 旧路由保留现有模型加载路径；没有重新训练或下载模型 |
| Docker / Qdrant | 首次 Docker 探针失败；检查过程中 Docker 可用，服务端版本 29.7.2；Qdrant HTTP 返回 1.19.0 | 按时间区分观察结果，不把首次不可用作为最终状态 |

应用 `requirements.txt` 的 24 项声明依赖均满足当前版本约束。两个 Python 环境的 `pip check` 都返回 `No broken requirements found`。详细版本见机器证据。

前端锁文件 SHA-256 为 `09b00dd80785e8cba69cbaf806c2a596f2c454d449e203a38bbc21526bd330f4`。Python 依赖多数使用范围约束，因此本次环境版本快照是复现基线的一部分，并不等同于新增锁文件。

检查后期观察到已有 rehearsal 容器处于 healthy 状态，前端映射端口为 18088，另有本地 Qdrant 映射到 6333。没有验证这些镜像对应当前代码基准，也没有重建、重启或更改容器；healthy 不能作为当前 HEAD 的功能验收证据。

## 4. 可复现的测试入口与结果

以下命令的工作目录分别为 `ai-service`、`soul-music-room` 或 `music-backend`。JUnit 输出参数和完整核心测试文件列表保存在 `evidence.json`；表中命令省略日志重定向。

| 工作目录 / 命令 | 结果 |
|---|---|
| AI：`.venv/Scripts/python.exe -m pytest tests -q -ra --tb=short` | 1335 passed，14 skipped，1 warning，286 subtests passed；退出 0 |
| AI：16 个 Foundation/API/工具授权/上下文/V5 核心测试文件 | 177 passed，1 warning，37 subtests passed；退出 0 |
| AI：`.venv/Scripts/python.exe -m pytest -q -ra --tb=short` | 缺少 DeepEval，1 项收集错误；退出 2；未执行测试体 |
| AI：应用环境执行 `evals/tests` 中除 Judge 外的全部 20 个文件 | 173 passed；退出 0 |
| AI：`.venv-eval/Scripts/python.exe -m pytest evals/tests/test_judges.py -q -ra --tb=short` | 4 passed；退出 0；Judge 网络客户端使用测试替身 |
| AI：`.venv/Scripts/python.exe -m pytest tests/test_jev_settings.py -q -ra --tb=short` | 3 passed；用于确认环境参数纠正有效 |
| AI：两个精确 Qdrant integration node IDs | 2 passed；退出 0；真实服务、测试集合 |
| AI：`.venv/Scripts/python.exe -m ruff check . --output-format json` | 45 findings；退出 1 |
| AI：`.venv/Scripts/python.exe -m compileall -q app tests evals scripts` | 退出 0 |
| AI：两环境分别执行 `python.exe -m pip check` | 均退出 0 |
| 前端：`npm run test:ai-layout` | 退出 0；源码/CSS 断言 |
| 前端：`npm run test:ai-stream` | 退出 0；流事件、状态与动作处理 |
| 前端：`npm run test:music-assets` | 退出 0；音乐资产与写操作边界 |
| 前端：`npm run test:search` | 退出 0；搜索状态、取消和错误处理 |
| 前端：`npm run test:ai-trigger` | 退出 0 |
| 前端：`npm run test:worldline` | flow 和 view 两个脚本均成功 |
| 前端：`npm run lint` | 退出 0 |
| 前端：`npm run build` | TypeScript 和 Vite 成功；大于 500 kB 的 chunk 警告保留 |
| 前端：`npm ls --depth=0 --json` | 退出 0 |
| Java：`mvn.cmd --batch-mode --no-transfer-progress -o verify` | 75 tests；BUILD SUCCESS；退出 0；使用 Java 21 |

### 4.1 应用测试使用的受控环境

这些覆盖仅作用于验证子进程，不写入 `.env` 或系统环境。它们用于明确区分受控测试和真实部署验证，不代表项目实际部署配置。

```powershell
$env:PYTHONUTF8 = '1'
$env:OPENBLAS_NUM_THREADS = '1'
$env:CHECKPOINT_BACKEND = 'memory'
$env:PERSISTENCE_BACKEND = 'sqlite'
$env:MEMORY_BACKEND = 'disabled'
$env:JEV_ENABLED = 'false'
$env:JEV_LIVE_TEST = '0'
$env:RUN_LIVE_POSTGRES_SMOKE = '0'
$env:RUN_WORLDLINE_POSTGRES_RESTART = '0'
$env:DATABASE_URL = ''
$env:LLM_API_KEY = 'phase0-offline-placeholder'
Remove-Item Env:TYPESAFE_API_KEY -ErrorAction SilentlyContinue
```

现有 `tests/conftest.py` 也会逐测试关闭 JEV，并清理 Settings 缓存；需要 JEV 行为的测试显式注入测试客户端或自己的临时配置。真实 JEV 探针仍按原有 opt-in 条件跳过。

完整应用运行的 14 项跳过按功能分组为：JEV 实时调用 1 项，Postgres smoke 4 项，Postgres E2E 4 项，WorldLine Postgres 重启 3 项，Qdrant 2 项。最后两项已追加执行，仍未验证的实时集成为其余 12 项。

### 4.2 Qdrant 追加验证与代理诊断

使用原有两个测试：

- `tests/test_rag_indexing.py::QdrantStoreTests::test_real_qdrant_accepts_dense_bm25_and_parent_payload`
- `tests/test_retrieval_fusion_qdrant.py::QdrantHybridSearchBackendTests::test_real_qdrant_runs_named_dense_and_bm25_legs`

两项测试都使用 UUID 命名的新集合，并在 finally 中清理自己创建的集合。运行前后只读查询确认这些测试前缀的集合均为空，没有修改已有知识集合。

默认 HTTPX 环境感知连接到 `http://127.0.0.1:6333/` 返回 502；同一目标设置 `trust_env=False` 返回 200。环境中的常见代理变量均未设置，不能据此认定没有系统代理。仅在追加测试进程设置以下配置后，原有两项测试均通过：

```powershell
$env:QDRANT_INTEGRATION_URL = 'http://127.0.0.1:6333'
$env:NO_PROXY = '127.0.0.1,localhost'
```

这个结果证明测试夹具的 dense/BM25 写入、payload 读取和双通道检索可以在真实 Qdrant 上运行；不证明当前生产知识索引的内容质量或完整 WorldLine 在线质量。应用全量测试保留的一项 Qdrant 版本探测 warning 也不计为业务断言失败。

### 4.3 保留的运行纠正记录

没有为了获得通过结果修改代码、降低断言或删除测试。以下早期运行属于测试命令/环境参数问题，原始结果保留，但不记为代码基线缺陷：

1. 核心测试把会话存储误设为 `PERSISTENCE_BACKEND=memory`，产生 14 failed / 163 passed。该服务只支持 sqlite/postgres；改成 sqlite 后，同一完整核心集合得到 177 passed。
2. 完整应用测试中空的 `TYPESAFE_API_KEY` 环境变量覆盖临时 `.env` 夹具，产生 1 failed / 1334 passed。去除进程覆盖后，Settings 3 项通过，完整应用集合重新运行得到 1335 passed。
3. 追加 Qdrant 命令第一次使用了不存在的测试类名，退出 4、没有测试体执行。核对源码类名后重跑；默认连接的 502 和最终绕过本地代理的通过结果分别保留为环境诊断。

## 5. Foundation 行为基线

以下是源码契约与本次受控测试共同支持的行为。后续改造需要保留对应测试，不把 Fake LLM/JEV/检索器的结果解释为真实模型质量。

| 能力 / 契约 | 当前行为 | 主要验证入口 |
|---|---|---|
| 默认入口 | Settings 默认 foundation；保留 legacy；尚无 handoff 选项 | `test_api.py`、`test_hybrid_graph.py` |
| 动态路由 | Rule 解析片段，Model/LLM 处理未解决片段，经 Merger/Builder 生成严格 RouteDecision | `test_multi_intent_routing.py`、`test_hybrid_graph.py`、各路由单测 |
| 任务调度 | 按任务顺序与依赖执行；支持同路由多任务；不完整决策先澄清；FAILED/NEEDS_INPUT 停止后续派发 | `test_graph_foundation.py`、`test_hybrid_graph.py`、`test_jev_router_v5_e2e.py` |
| 状态隔离 | messages 使用 add_messages；TaskResult/errors 追加；adapter 隔离子图私有状态；终答只取本轮任务结果 | `test_graph_foundation.py`、`test_foundation_response.py` |
| CurrentSong | 根据当前播放器或前置播放结果解析歌曲；缺少歌曲请求输入；上下文失败返回受控错误；提供只读分析 | `test_foundation_current_song.py`、`test_foundation_e2e_acceptance.py` |
| ToolCalling | 服务端工具与客户端动作分离；每个工具任务最多 5 次调用；保留实体引用、参数和当前用户动作授权校验 | `test_tool_calling_subgraph.py`、`test_tool_action_authorization.py`、`test_tool_calling_contracts.py` |
| GeneralChat | 无工具对话；收到历史与前置任务结果；异常工具调用输出拒绝执行 | `test_general_chat_subgraph.py`、`test_foundation_e2e_acceptance.py` |
| WorldLine | 实体解析、检索、生成及来源输出；歧义澄清；服务失败有受控结果；场景/时代/影响保留各自测试 | `test_foundation_e2e_acceptance.py`、WorldLine 测试集合 |
| HTTP 请求 | message 非空且最多 4000 字符；thread_id 最多 128；兼容旧 context 和新 player_context；非法输入拒绝 | `test_api.py`、`test_schemas.py` |
| JSON 错误 | 成功、failed、needs_input 由 FoundationResponse 表达；未处理执行异常转为脱敏 HTTP 502 | `test_api.py`、`test_foundation_response.py`、Foundation E2E |
| SSE | message/update/action/final/error/done 协议；嵌套私有状态和非回答 token 不公开；动作按 action_id 去重 | `test_foundation_streaming.py`、`test_api.py`、前端 stream 脚本 |
| 播放动作 | ToolCalling 生成 action，流适配器派发，前端测试验证动作消费；本次没有真实浏览器音频播放 | `test_jev_router_v5_e2e.py`、`test_foundation_streaming.py`、前端 stream/assets 脚本 |
| 上下文 | ContextPolicy/ContextPack 已有调用级裁剪、证据保留和输出预留；Long Horizon 有摘要/指针/会话继承 | `test_context_engineering.py`、`test_long_horizon_context.py` |
| 持久化边界 | MainGraph 统一注入 checkpointer；thread_id 为运行配置；session_id 隔离 checkpoint namespace | `test_graph_foundation.py`、`test_long_horizon_context.py` |
| JEV V5 | Choice 单意图阈值固定 0.8；多意图分解和任务编排；低置信/结构/超时/冲突回退旧 Hybrid | `test_jev_router_v5.py`、`test_jev_router_v5_e2e.py` |

源码定位：

- [MainGraph](../../../ai-service/app/agent/foundation/main_graph.py)、[State](../../../ai-service/app/agent/foundation/state/main.py)、[adapter](../../../ai-service/app/agent/foundation/adapters.py)。
- [HTTP/SSE 入口](../../../ai-service/app/api/chat.py)、[流式转换](../../../ai-service/app/agent/foundation/streaming.py)、[外部 Schema](../../../ai-service/app/schemas/chat.py)。
- [Checkpoint](../../../ai-service/app/agent/checkpointing.py)、[Long Horizon runtime](../../../ai-service/app/agent/foundation/long_horizon/runtime.py)、[V5](../../../ai-service/app/agent/decision/jev/router_v5.py)。

## 6. 既有问题、风险与未验证内容

### 6.1 静态检查与工具链

Ruff 45 项问题中，E402 40 项位于脚本/CLI 导入位置；F401 4 项、F841 1 项位于应用源码。详细文件、行号和规则见 `evidence.json` 的 `lint_baseline`。

Foundation 路由模块有两项既有 unused import。改造不能把现有全目录失败当作新回归，但必须比较新旧清单，确保不增加问题，并对实际修改文件运行检查。当前阶段没有自动修复 unrelated findings。

应用环境与 DeepEval 环境需要分别执行测试。后续若建设统一 CI，优先按环境分作业，不把 DeepEval 直接装进应用环境规避收集失败。前端现有 chunk 大小和 Java 动态 agent warning 保留，不扩大本次范围去优化。

### 6.2 改造前必须明确的契约缺口

1. 外部 Schema 的 subgraph 字段目前限定四个旧名称；内部改成三个 Agent 时，要先定义兼容映射。
2. 流适配器有节点白名单、`_subgraph` 名称约定和仅从 ToolCalling 派发动作的分支；新增节点不能绕过结果验证或丢失动作。
3. 当前 adapter 通过本轮 RouteTask 选任务，aggregate/final 也依赖 RouteDecision 的任务 ID。动态转交需要显式定义待办任务、完成和终止规则，不能只增加 current_agent 字段。
4. 当前 CurrentSong 不只是查歌曲上下文，还包含目标解析、只读分析和边界处理。能力降为工具后，分析回复必须有明确归属。
5. 既有 Long Horizon 的 handoff 是会话窗口继承，不是 Agent 控制权转交；应复用其结构化摘要、指针与任务信息，同时区分生命周期和命名。
6. 现有调用级 ContextPack 不等于已具备跨 Agent 累计预算；跳数、总时间和总 token 限制需要新增专门契约。
7. V5 仍按四类能力工作，并依赖旧 Hybrid 回退。不能在 handoff 骨架落地时同步删除 BGE/Merger 或直接改阈值。

### 6.3 本次仍未验证

- 真实 PostgreSQL/pgvector 写入、进程重启恢复和持久化竞争；本次没有访问数据库凭据或启用建库/删库测试。
- 真实 LLM/JEV 的新质量评测、时延、成本和 production shadow 观察窗口。已有 V5 报告是历史证据，本次没有重新生成其效果数据。
- 已登录账户的网易云上游接口、真实歌曲播放完成和浏览器动作反馈。
- 浏览器关键交互、响应式布局和截图视觉验证。前端 layout/view 脚本及构建不替代这些验证；本阶段未修改 UI。
- 真实知识索引的代表性检索/回答质量；Qdrant 两项集成测试只验证夹具协议和通道。
- handoff 图自身、跨模式旧 checkpoint 迁移及 handoff→foundation 回滚。这些属于后续实现与验收范围。

历史 [V5 production readiness](../../../ai-service/evals/reports/jev_router_v5_production_readiness.md) 仍记录独立安全 holdout、真实影子流量、回退尾延迟和浏览器播放的缺口。没有因为本次受控单测通过就宣称 V5 已具备线上转正条件。

## 7. 阶段 1 输入与验收边界

阶段 1 首先产出能力矩阵和契约，不切流。至少明确：

1. 三 Agent 与四类旧能力的映射，特别是歌曲分析和外部响应名称。
2. 首跳分诊与运行中转交的区别；保留多意图顺序、依赖、否定和澄清语义。
3. HandoffRequest/Result 的原始任务、结构化实体、授权动作、已完成结果和待办信息。
4. 会话级与任务级状态、初始化/恢复行为、允许边、有限重试和最终结束条件。
5. 消息配对、公开 SSE、动作去重、Checkpoint 版本与回滚兼容的测试矩阵。
6. 跳数、无进展循环、时间与预算限制，以及 JEV 故障时不再无限依赖模型的终答路径。

后续每个逻辑改动同步维护相关测试，检查 diff/status 后只提交任务文件；不得用本报告作为 push、灰度切流或 JEV 转正授权。

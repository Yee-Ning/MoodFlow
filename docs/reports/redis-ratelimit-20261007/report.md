# Redis Phase B-P1：API 限流验收报告

日期：2026-10-07。分支：`codex/redis-ratelimit`。
基线：P0 验收 HEAD `5744ce6f9bbae9af8e1f1b1bfba545367e78804b`。
实现 commit：`d6fe13da874f572d79ff7b9a4748890289f1fe52`（`feat: add optional Redis API rate limiting`），仅包含六个本任务实现/测试文件。

## Gate 0 与基线隔离

首次核对时，共享工作区位于 `codex/redis-p2-cache-lock`，HEAD `e20a7214b88cc2297b4166c19562269a0417d97a`，比指定 P0 多 4 个 P1/P2 提交；设计文档路径也不在仓库内。因此按硬门停止，没有直接套用后续缓存实现。

West 随后明确授权从 P0 HEAD 创建隔离 worktree 和 `codex/redis-ratelimit`，并提供 §1 设计要点：网关 IP 粗限流、应用 session 细限流、滑动窗口计数器、Lua 原子执行、位置先于 EntryContextEngine.preflight。设计文档仅作背景，以任务 prompt 为完整规格。

| 核对项 | P0 实际形态与本轮处理 |
| --- | --- |
| Redis 依赖 | `requirements.redis.txt` 包含默认依赖并固定 `redis==5.2.1`；本轮不新增或修改依赖 |
| URL/环境变量 | P0 使用 `REDIS_URL`，`BUDGET_LEDGER_BACKEND` 和 `BUDGET_REDIS_*` 控制可选热预算；本轮使用独立 `RATELIMIT_*`，复用同一 URL 与连接工厂 |
| 连接工厂 | `app.llm.redis_budget.redis_client(config)` 调用 `Redis.from_url`，使用 decode_responses、连接/命令超时、retry_on_timeout=False；本轮直接调用该工厂，不编辑 RedisBudgetLedger |
| 请求入口 | HTTP JSON/SSE → Foundation/Handoff runner → MainGraph prepare_context → EntryContextEngine.preflight → RuleRouter；preflight 已在 prepare_context 中调用，不能放在后续 intent_router 节点 |
| session 身份 | HTTP 输入是 thread_id；服务端 LongHorizonContextService.open_conversation 生成或读取实际 session_id。Handoff conversation 使用 `handoff:v1:` 前缀 |
| Lua | P0 只有 `app/llm/lua/reserve_budget.lua`，没有滑动窗口脚本；本轮新建 `app/api/lua/sliding_window_counter.lua`，沿用模块旁 lua 目录的存放风格 |

隔离目录：`C:/Users/33994/.codex/worktrees/redis-ratelimit/MoodFlow`。原工作区的报告修改、删除及未跟踪数据保留；没有切换原工作区分支，也未提交其文件。

## 实现与配置

网关为 AI 服务 ASGI 入口的 GatewayRateLimitMiddleware，覆盖 `/api/ai/*`，在读取请求体和进入路由前执行。使用 ASGI client 来源 IP，key 为 `ratelimit:gateway:{ip}`；不自行读取或信任 X-Forwarded-For。OPTIONS 和 `/health` 不计数。

应用层覆盖 `/api/ai/foundation`、`/api/ai/handoff`、`/api/ai/chat`。Foundation/Handoff 使用现有 coordinator、执行锁及会话服务解析服务端 session_id，key 为 `ratelimit:session:{session_id}`，然后释放短期 admission lease，再进入既有 runner。首请求可以创建原有 task/session 和锁记录，但不启动图、路由、模型、预算预留或转交。SSE 在返回 StreamingResponse 前执行该检查，确保超限返回 HTTP 429，而非先发 200 再产生 SSE error。JSON 与 SSE 共享同一 session bucket。

检查发生于进入请求的 session，先于 new_session 或自动 rollover。已被拒绝的 resume/new_session 请求不会进入后续流程。Legacy 没有 durable session，使用 `legacy:<sha256(thread_id)>` 作为 session key 的身份，保留原 thread 合约。其他独立 API 由网关粗限流覆盖；本轮不改 Worldline 等非聊天 API 的会话模型。

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `RATELIMIT_BACKEND` | `none` | `none` 保留原路径；显式 `redis` 开启两层限流，与预算后端开关独立 |
| `REDIS_URL` | 无 | 与 P0 复用，仅启用 Redis 时读取 |
| `RATELIMIT_GATEWAY_LIMIT` | `600` | 每个来源 IP 的窗口请求阈值 |
| `RATELIMIT_GATEWAY_WINDOW_SECONDS` | `60` | 网关窗口长度，正整数秒 |
| `RATELIMIT_SESSION_LIMIT` | `20` | 每个进入 session 的窗口请求阈值 |
| `RATELIMIT_SESSION_WINDOW_SECONDS` | `60` | session 窗口长度，正整数秒 |
| `RATELIMIT_REDIS_TIMEOUT_SECONDS` | `0.25` | 复用 P0 工厂的连接和命令超时，必须为有限正数 |

策略参数是正整数，乘积受 Lua 安全整数范围校验。非法 backend、阈值、窗口和 timeout 是配置错误，不会静默放宽。进程缓存配置，调整环境变量需要重启；没有编辑生产配置。

每个身份只有一个 Redis hash，记录 bucket、current、previous、window_ms，存储 O(1)。生产脚本使用 Redis TIME，避免应用节点时钟偏差；可选第三个脚本参数只用于测试时间推进，HTTP 和生产 limiter 不提供客户端时间入口。新窗口将 current 移至 previous；跨过两窗口后清零；服务器时钟倒退时保留已消耗的桶；窗口长度调整时开启新策略周期，避免将不同长度的桶编号误判为时钟回退。

脚本使用整数加权单位比较 `current * window_ms + previous * remaining_ms + window_ms <= limit * window_ms`，通过后才增加 current。拒绝不增加 session 计数；网关按入站尝试计数，所以 session 拒绝仍消耗已放行的网关名额。所有请求处理结果均按一次入站请求计费限流，不退款，也不改原预算账本。hash TTL 为 2 倍窗口，检查时刷新。

拒绝返回 `429`、`Retry-After` 秒数及 `detail.code=rate_limit_exceeded`、`detail.layer=gateway/session`。Retry-After 根据权重衰减计算，当前桶满时也计入下一桶的衰减，向上取整且至少为 1 秒；它假设等待期间没有其他请求消耗额度，不预留未来名额。拒绝直接终止入口，不触发模型 fallback 或更贵路径。

## fail-open 决策记录

按任务明确要求，Redis URL 缺席、可选依赖缺席、连接失败、命令超时、脚本/Redis 数据类型错误、执行成功后回复丢失，均放行到既有处理路径并记录 WARNING。默认 `none` 是显式关闭，不产生故障 warning。

日志只记录缺席的配置项、层级和异常类型，不打印 URL、异常详情、session/thread/IP 或凭证。Redis 运行时故障不缓存成永久关闭，后续请求仍会尝试 Redis，恢复后自动继续计数；缺 URL/依赖的初始化结果缓存到进程重启。不增加客户端重复扣减或替代限流算法；回复丢失可使一次已计数请求被放行，这属于明确授权的 fail-open 风险。

限流只数 API 请求；原 L1/L2/L3、预算阈值、预算降级顺序、Guard、路由和转交继续由已有实现负责，相关源码和 P0 测试相对基线没有 diff。

## 验收证据

| 验收项 | 证据 |
| --- | --- |
| 超阈值 HTTP 429 | Foundation/Handoff JSON，Foundation/Handoff/Legacy SSE 均断言 status=429、Retry-After ≥1、层级及拒绝不执行下游；SSE 断言没有 event 帧 |
| 真 Redis 并发 | gateway/session 各 24 线程、240 次请求，阈值 37，恰好 37 次通过，Redis current=37；跨窗口上一桶=10、半窗口权重=5，240 次并发恰好只放行 5 次 |
| 固定窗口临界突刺对比 | 同一真实 Redis、窗口=1000ms、阈值=10；999ms 和 1000ms 各突刺 10 次：原子固定窗口放行 20，滑动窗口计数器放行 10 |
| 计算与边界 | 不向下取整的小数权重、恰好容量边界、Retry-After 后重新通过、跨两窗口清零、时钟回退、窗口配置改变、独立身份、TTL 回收 |
| 身份与入口顺序 | JSON/SSE 共享真实服务端 session_id；两个真实 PostgreSQL 用例覆盖 Foundation/Handoff 身份及成功/拒绝释放执行锁；实际 HTTP → worker → MainGraph → RuleRouter 用例证明第三次拒绝不再 preflight，也不走模型/LLM fallback |
| Redis 缺席/故障 | 缺 URL、缺依赖、真实 loopback 无 Redis 服务连接/超时、真实 Redis WRONGTYPE、成功执行后回复丢失：放行、warn、无敏感内容，无自动重复计数 |
| 原 API 兼容性 | 422 请求校验、健康检查、OPTIONS、现有 API 用例，以及全套原预算/上下文/契约/转交流程 |

最终专项：**126 passed，2 warnings，退出码 0**，包含 41 个本轮限流用例及既有入口/预算/契约回归。最终完整套件：**2396 passed、12 skipped、288 subtests passed、186 warnings，0 failures / 0 errors，退出码 0**。pytest 用时 334.37 秒，统计驱动含启动开销为 334.80 秒。

相对 P0 的 2355 passed 新增 41 个通过用例；两个真实 PostgreSQL session gate 用例和所有真实 Redis 用例均执行。12 个 skipped nodeid 与 P0 逐项一致；288 个 subtest 与原套件相同且独立计数，不重复计入 2396。完整统计、失败/跳过清单及六个实现/测试文件的冻结 SHA-256 保存于 `verification-summary.json`，运行前后哈希完全一致。

## 验证环境与命令

采用原工作区已有 `.venv` 和 `.venv-eval`，没有新增安装依赖。测试仅使用本机独立 Redis 8.10.0 进程和监听 `127.0.0.1:15471` 的一次性 pgvector/pgvector:pg16 容器，数据库均使用随机测试名称，不连接应用数据库。复制既有 intent_model 运行资产、knowledge/v2 离线知识库、两份历史 trace、Clean v0.2 及 normalized 测试文档到隔离 worktree；资产副本不纳入提交。

隔离 worktree 未复制 `.env`。完整验证早期发现六项必填 Settings 缺席，以测试占位配置补齐；另发现 checkout 换行转换和缺少离线资产使既有冻结校验/RAG 用例失败。通过 P0 Git blob 核对内容后恢复原始字节；个别旧冻结资产要求验收工作区的 CRLF，只有规范化换行后内容与 P0 相等时才复制其原工作区字节。相关 Git diff 为空，P0 报告的九个源文件 SHA-256 全部一致；没有修改冻结哈希或断言，也没有复制 P1/P2 源码差异或用户修改。环境准备期间的失败/中止运行，以及窗口配置边界修补前的试跑，均不作为最终完整验收。

下列命令从隔离 worktree 的 ai-service 目录执行。全量命令中的 URL/Key 均为本次一次性测试值：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
& 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe' -c "import os,sys,pytest;os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PERSISTENCE_BACKEND='sqlite',AI_CONTINUITY_STORE_MODE='sqlite',DATABASE_URL='postgresql://postgres:ratelimit-test-only@127.0.0.1:15471/moodflow_ratelimit_test',MEMORY_BACKEND='memory',CHECKPOINT_BACKEND='memory',JEV_ENABLED='false',DEEPEVAL_DISABLE_DOTENV='1',AI_RAG_RETRIEVAL_MODE='empty',BUDGET_LEDGER_BACKEND='history',RATELIMIT_BACKEND='none',LLM_API_KEY='ratelimit-test-only',LLM_BASE_URL='https://example.test',LLM_MODEL='test-model',LLM_TIMEOUT_SECONDS='5',SPRING_BOOT_BASE_URL='http://127.0.0.1:1',SPRING_BOOT_TIMEOUT_SECONDS='5');sys.path.append('D:/code/ProjectsAll/MoodFlow/ai-service/.venv-eval/Lib/site-packages');raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','-p','xdist.plugin','-n','4','--dist','loadfile','tests','evals/tests','-q','--disable-warnings','--tb=short']))"
```

专项使用相同环境运行 pytest_asyncio.plugin，文件清单为 `tests/test_redis_ratelimit.py tests/test_redis_ratelimit_postgres.py tests/test_handoff_entry_rules.py tests/test_context_budget_phase2.py tests/test_context_contracts_phase3.py`。前一轮预算专项另外覆盖 `tests/test_api.py tests/test_redis_budget.py tests/test_redis_budget_postgres.py tests/test_handoff_budget.py`；原预算相关文件未改变。

以下静态/编译命令通过，退出码 0：

```powershell
& 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/ruff.exe' check app/api/rate_limit.py app/api/chat.py app/main.py tests/test_redis_ratelimit.py tests/test_redis_ratelimit_postgres.py
& 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe' -m compileall -q app/api/rate_limit.py app/api/chat.py app/main.py tests/test_redis_ratelimit.py tests/test_redis_ratelimit_postgres.py
git -c core.safecrlf=false diff --check
```

Lua 在真实 Redis 加载和执行；Python 后端没有独立前端构建步骤，本轮无 UI 改动。实现提交前已核对 status、完整相关 diff、精确暂存清单及 cached diff --check，全部通过。工作区换行转换造成的 stat 缓存标记已刷新，刷新前后 index blob 清单完全一致，没有暂存额外文件变化。

测试容器完整 ID `3f02165fa0bae0204c0be8372e3d6e08cfa3d8d0d71065ce0aa23266f9be225b`、名称和 `codex.task=redis-ratelimit` 标签核对后，已单独移除该容器及匿名测试卷。离线资产副本、临时统计/环境准备驱动的组合删除命令被自动审批拒绝，仅返回 `blocked by policy`，未提供更具体原因；该组合命令没有执行，文件副本保留且不提交。ignored 模型/trace/清洗文档保留供复验；knowledge/v2 副本仍为本 worktree 的未跟踪测试资产，原工作区文件没有修改。

## 限制与交付范围

- 滑动窗口计数器是对上一窗口分布的加权估算，严格保证的是原子判定下的估算阈值；不能声明任意真实滚动区间都精确 ≤N。边界对比证明抑制固定窗口 2x 临界突刺，不是全流量分布的数学上界。[Redis 官方算法说明](https://redis.io/tutorials/howtos/ratelimiting/)
- 按要求 fail-open，Redis 故障期间没有本轮新增的分布式限流保证；原 P0 预算和既有处理继续工作。窗口长度调整会清零该身份的旧计数，属于配置变更后的新策略周期。
- session 额度跟随 session；创建其他 thread/session 可获得另一个桶，仍受 IP 网关额度约束。本轮没有新增用户/账号额度或直接图调用限流。独立非聊天 API 只有网关层；现有会话模型不做扩展。
- 来源 IP 取 ASGI client，其在真实代理链中的含义由现有服务器/代理配置决定；部署代理信任范围、跨机网络、Redis Cluster/Sentinel、故障转移及供应商 live 压测未验证。没有改生产代理配置或将测试当作部署验收。
- 原套件 12 项 live/opt-in 跳过清单已逐项与 P0 比对，完全一致：live JEV、开发库 smoke、真实 Qdrant、Worldline 重启及指定旧容器重启。本轮新增真实 Redis/PostgreSQL 用例全部执行。重新验证全量需要恢复基线原本不在 Git 中的离线知识库和 trace/清洗文档；本轮没有将这些资产或环境凭证提交到仓库。
- 默认关闭，需设置 `RATELIMIT_BACKEND=redis` 并提供 REDIS_URL 才启用；没有部署、push、生产配置变更或远程历史改写。只提交本任务六个实现/测试文件及本目录报告。

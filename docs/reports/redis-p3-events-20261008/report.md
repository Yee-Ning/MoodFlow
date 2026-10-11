# Redis Phase B-P3：计数与事件验收报告

日期：2026-10-08。分支：`codex/redis-p3-events`。
基线：P1 验收 HEAD `69a4601fd841dca73a57b891030d658e8376e267`，P1 实现 `112d52aea46fd155a7639138b5f0f19028ebac10`。
本轮直接从该 HEAD 创建分支，与 P2 平行，不包含 P2 提交。

## Gate 0 与已确认修订

动手前只读核对 P1 报告、验收 JSON、Redis 配置、HistoryWriter/Journal、后端聊天 SSE、前端消费器、AI/Java/前端的播放路径。P1 验收为 2390 passed、12 skipped、288 subtests passed、185 warnings，退出码 0。

| 核对项 | 实际形态 |
| --- | --- |
| Redis 接入 | `requirements.redis.txt` 继承默认依赖并包含 `redis==5.2.1`。P0/P1 均显式开关启用、读取 `REDIS_URL`，默认不连接 Redis。本轮复用该依赖与 URL，新增独立开关；原配置未改 |
| P1 配置 | `SESSION_CACHE_BACKEND=none/redis`；Session TTL 3600、view TTL 60、timeout 0.25 秒。执行/恢复见证继续读 PG；本轮没有改 P1 实现 |
| 转交身份 | `handoff/continuity.py::transfer_identity(run_id, call_id)` 生成 `transfer:<sha256>`；同一 transfer 有 proposed/committed/reconciled/target_started/target_finished 等独立 Journal 事件 |
| PG 最终仲裁 | `HistoryWriter.event_id/append_public_event` 以 task/session/逻辑 event_key 的摘要生成 `evt:<sha256>` 与 `public:<sha256>`。PG 的 `agent_history_event_key` 是 **event_key 全局唯一索引**；task/session 作用域包含在摘要内，event_id 另为主键。`same_event_content` 验证重试内容，冲突保留原 `idempotency_conflict` ERROR 写入 |
| 转交校验 | HistoryWriter 先做身份、schema、版本转换、引用作用域、公开内容及 16KiB 校验；v1/v2/v3 的回执兼容逻辑已有实现，不能凭 Redis key 绕过 |
| 播放计数 | 基线没有本地播放计数表或播放事实账本；获取音乐 URL 不能作为已播放事实。新增显式播放上报后端 API 与统计存储 |
| 原 SSE | `POST /api/ai/chat` 输出 `event/data`，没有事件 ID。前端 fetch/ReadableStream 只解析 event/data；现有 action ACK 是客户端动作回执。`runner.stream` 在断开后移除传输 sink，后台 worker 继续执行，原临时 queue 不保存续传进度 |

发现原需求与基线不匹配后先报告并停止修改。West 已明确确认三项修订，本轮据此执行：

1. 去重 key 为 `transfer:idempotency:{transfer_id}:{event_id}`，PG 回执校验和冲突仲裁保留。
2. 播放统计恢复到最近一次成功 flush；健康路径不逐次写 PG 播放事实，未 flush 热增量允许在 Redis 丢失时丢失。
3. SSE 本轮只做后端 Stream、单调 ID 与 `run_id + since` resume 查询。旧 POST 接口兼容，前端 ID/ACK/重连留待后续。

## 改动范围与接入点

新增 `ai-service/app/redis_events/`，包含配置、PG 存储、转交适配器、播放计数、Stream 和生命周期组件；新增 `app/api/redis_events.py`、`008_redis_events.sql`、单次 flush 脚本和两组测试。

既有代码仅改两处：`long_horizon/runner.py` 在图执行前按开关给原 Journal writer 加转交适配器，在原公开 UpdateStreamEvent 发出前记录节点进度；`app/main.py` 注册新增 API、启动周期 flush、退出时等待在途 flush 并关闭资源。旧 `app/api/chat.py`、前端、BudgetLedger Protocol、P0/P1 文件、路由选择、Guard、转交算法与 WorldLine 互斥实现均未修改。预算及 budget.exceeded 仍按原 PG History 路径执行。

## Key、表与可选配置

| Key | 内容与生命周期 |
| --- | --- |
| `transfer:idempotency:{transfer_id}:{event_id}` | string，候选回执包含 event_id 与随机 owner。`SET NX PX`；默认 TTL 3,600,000ms。值仅作加速提示，PG 回执是事实 |
| `playcount:{song_id}` | 十进制整数累计计数，INCR。无 TTL；持久化前不主动按时间删除热增量 |
| `playcount:{song_id}:epoch` | hash，计数器这一代的随机 epoch、PG 基数 base。与计数器一起重建，用于 flush 水位和 PG 回退增量对齐；无 TTL |
| `events:{run_id}` | Redis Stream，仅白名单节点完成事件 `{event,node,status}`。ID 为 PG 递增序列对应的 `sequence-0`，默认 TTL 86400 秒，`XTRIM MAXLEN 1000` 精确裁剪 |

占位符表示原身份字符串，不额外添加 Redis Cluster hash tag。生产应继续使用单实例 Redis；本轮没有实现多 key Redis Cluster 路由或跨槽协议。

| 可选环境项 | 默认及约束 |
| --- | --- |
| `TRANSFER_IDEMPOTENCY_BACKEND` | `none`；显式 `redis` 启用。适配原 PG Journal；SQLite 保留原 writer |
| `PLAYCOUNT_BACKEND` | `none`；显式 `redis` 启用播放后端及周期 flush，PG 为故障回退和快照存储 |
| `PROGRESS_EVENTS_BACKEND` | `none`；显式 `redis` 启用进度 outbox/Stream/resume |
| `REDIS_URL` | 复用 P0/P1；缺 URL 或可选依赖时 warn，启用项直接使用 PG |
| `DATABASE_URL` | 新播放/进度后端复用现有连接配置；schema 缺席时报错，服务不自动迁移 |
| `TRANSFER_IDEMPOTENCY_TTL_MS` | 3600000，有限正整数 |
| `PROGRESS_EVENTS_TTL_SECONDS` | 86400，有限正整数 |
| `PROGRESS_EVENTS_MAXLEN` | 1000，有限正整数 |
| `PLAYCOUNT_FLUSH_INTERVAL_SECONDS` | 60，有限正数 |
| `REDIS_EVENTS_TIMEOUT_SECONDS` | 0.25，有限正数 |

三个开关均默认 none；缺席时不建立 P3 Redis/PG 连接。配置只在代码中增加可选项，未修改生产 env、Compose 或部署文件。沿用现有依赖，不新增第三方包。

新增迁移通过已有 migrator 执行，包含 `agent_song_playcount`、`agent_playcount_flush`、`agent_progress_stream`、`agent_progress_event`、`agent_progress_delivery`。本轮仅在随机隔离验收库应用迁移；未应用到用户数据库。

## 转交幂等

适配器复用原 HistoryWriter，所有验证完成后才在 `append_event_once` 边界尝试占坑。NX 成功走原 PG INSERT；NX 失败先读 PG 对应 event_id，并按原 `same_event_content` 比对，只有相同持久回执才直接返回。

缓存存在而 PG 回执不存在时，可能是另一个事务尚未提交、producer 已退出、事务回滚或候选 TTL 尚未结束；仍走原 PG 唯一仲裁，避免错误丢弃合法转交。缓存存在但内容不同也走原仲裁，原 HistoryWriter 继续记录 ERROR 并拒绝。PG 写失败时比较 owner 后清理自己的候选 key，清理失败保留短 TTL 提示并 warn。

因此正常重复事件跳过第二次 INSERT，但不会宣称所有竞争时序都只发一次 INSERT：尚无 PG 回执的并发首次写入仍可能共同进入唯一约束。不同 phase 的 event_id 不同，后续 committed/target_started 不被 proposed 的 key 阻断。预算/accounting 仍使用 coordinator 的原 PG writer，P0 的 adapter 身份没有被包装改变。

## 播放计数、flush 与恢复

首次缺 key 时 WATCH 计数器和 epoch，再读取 PG 最近累计快照并在事务内建立这一代 base；在途增量不能被另一个初始化覆盖。热路径原子 INCR，仅以很短的 Lua 检查两个 key 完整并执行 INCR，返回 GET 的整数文本，避免 Lua 浮点数对大整数的舍入。健康逐次播放没有 PG 写入。

flush 原子读取计数器和 epoch/base，计算 `delta = hot_count - base`；PG 在 song 行锁下只累计该 epoch 尚未 flush 的部分，并事务保存水位。多个 worker、周期任务重试、PG 提交回执丢失后的再次 flush 都不会重复计数。PG 故障回退的增量与健康 peer 的 Redis 增量可合并；必要时另一个短 Lua 比对 epoch/base，INCRBY 调整热基数，避免把 PG 回退增量再次 flush。Redis 整数命令承担整数运算，不依赖 Lua double。

周期任务每 60 秒扫描播放 key，服务退出也 flush；取消周期协程时先等待其 to_thread worker 完成，再关连接。PG flush 失败只记录异常类型并保留 Redis 快照，下一轮可安全重试。运维也可运行 `python -m scripts.flush_redis_playcounts` 做单次 flush。

计数器或 epoch 丢失时，`PlayCounts.rebuild(song_id)` 从 PG 最近成功累计快照重建新 epoch；未 flush 增量按已确认方案丢失。不会从不存在的逐次播放事实宣称无损 replay。Redis 确认不可用时当前服务实例固定回退 PG，后续调用直接原子增加 PG 计数；其他健康实例仍可使用 Redis，并在 flush 时合并。重新创建服务实例可重新启用 Redis。

INCR 已发出却丢失回执属于不确定结果：该次 API 返回通用 503，**不自动再在 PG 增加同一次播放**；该实例后续请求走 PG。已确认播放与不确定请求不能当作同一个 exactly-once 承诺，客户端后续集成也不能盲目把该次 503 重试成新的播放。直接连接拒绝、缺 URL/依赖时正常回退 PG，不存在该不确定窗口。

## 后端 Stream 与 resume

producer 先把白名单节点完成事件提交 PG outbox，在同一 run 行锁下分配单调 seq。随后 WATCH Stream，按 seq 顺序 XADD 尚未复制的事件，执行精确 XTRIM 和 EXPIRE；没有使用 Pub/Sub。显式 `sequence-0` ID 是为 Redis 故障/重建前后保持同一游标，不采用会改变恢复游标的自动时钟 ID。[Redis Stream 支持显式外部递增 ID](https://redis.io/docs/latest/develop/data-types/streams/)。

记录发生在后台 `produce` 内，与 SSE queue 是否附着无关。旧客户端断开后执行继续、进度仍提交 outbox/Stream；旧 POST 的 event/data 内容和最终 History 写入保持兼容。不记录 LLM 文本、tool 参数、ClientAction 或 private state。

`ProgressEvents.consume` 提供后端消费者组：PG 递送回执决定已 ACK/待 ACK 的有序事件，再用 XREADGROUP 读取对应 Stream 数据；同一 consumer 的 pending 重试使用 ID 0，新消息使用 `>`，可按空闲时间 XAUTOCLAIM。ACK 先写 PG 持久回执，再 XACK；Redis ACK 回执丢失、TTL、数据丢失或裁剪后，PG pending/ACK 仍决定下一次交付，避免从较新的尾部跳过早期未 ACK 事件。语义是至少一次，处理者须在成功处理后 ACK。[XREADGROUP 的 pending 与 ACK 规则](https://redis.io/docs/latest/commands/xreadgroup/)。

新增 `GET /api/ai/events/resume?run_id=...&since=12-0&graph_mode=foundation&limit=100` 返回有限 SSE 重放页，每条带 `id/event/data`。since 是排他游标，初始 `0-0`，limit 1–1000。每次按现有 UserIdentityProvider 解析身份，PG 保存的 `digest([graph_mode,user_id])` 与原 ExecutionLocks 同作用域；不存在/其他用户/graph_mode 不匹配统一 404，非法游标或参数 422，存储故障通用 503。该 GET 不创建浏览器消费者组，不隐式 ACK，也不是持续 live 订阅。

resume 优先 XRANGE；PG head 用于确认连续区间，若 Redis TTL/miss/XTRIM 导致缺段或最新 outbox 尚未复制，则回源 PG 并重建 Stream。Redis 长度仍受 MAXLEN 限制，PG outbox 保留冷重放事实；不能把 XTRIM 当作已处理确认。官方说明裁剪可能删除尚在 PEL 的消息，因此本轮保留独立持久递送回执和冷数据。[裁剪与 pending 的关系](https://redis.io/docs/latest/commands/xreadgroup/)。Pub/Sub 的 at-most-once 会丢弃断线期间消息，不符合这里的恢复要求。[官方 Pub/Sub 递送语义](https://redis.io/docs/latest/develop/pubsub/)。

后续项：前端接收 run_id/进度 ID、保存 ACK/游标、重连与 live 流组合；浏览器 ACK API/消费者身份协议；播放上报 API 的播放器接入；冷进度表及 epoch 水位的归档/保留策略。当前新增播放 API 为 `POST /api/ai/playcounts/{song_id}` 上报一次、`GET` 读取统计；没有修改播放器 UI。

## 验收证据

新增 68 个用例，真实 Redis 执行 NX、Lua 内 INCR、WATCH/MULTI、XADD/XREADGROUP/XACK/XAUTOCLAIM/XTRIM；PG 使用专用 loopback 15471 实例和每项随机数据库。Redis 为本机临时原生进程，未进行 Redis Docker 部署轮次。PG 容器仅为本轮临时测试环境，不改已有业务容器。

| 验收项 | 证据用例 |
| --- | --- |
| 事件作用域去重，PG 仅一条事实 | duplicate 和 concurrent transfer 重试断言第二次 INSERT 为零/竞争重试仅一次 INSERT；proposed/committed/target_started 三个 key 独立 |
| PG 校验与冲突仲裁 | cached receipt 内容冲突仍有原 ERROR；未知字段拒绝；stale claim 没有 PG 回执时允许正确恢复 |
| 并发 INCR 精确 | 16 线程、8 adapter、300 次返回值精确为 1–300；健康逐次播放 PG 累计仍为 0；并发 flush 后为 300 |
| flush 水位与恢复 | PG commit 回执丢失后重试不重复；丢失恢复最后 flush；epoch/计数器分别丢失；flush 对齐中丢键；PG 回退与健康 peer 增量合并；2^53 以上精确整数 |
| 三项 Redis 故障 | 真实 loopback 连接拒绝，转交仍写 PG proposed/committed，播放 PG 增加，进度写/重放 PG，并断言 warn；另有故障注入覆盖读/flush/ACK 与不确定 INCR |
| Stream 交付/续跑 | 写→消费→ACK→pending 重试→重新实例化 consumer→新事件→排他 resume；旧 consumer 不能 ACK 被新 consumer claim 的事件 |
| 裁剪与丢失 | MAXLEN=3 时保留 6-0/7-0/8-0，冷 resume 仍完整；删除 Stream 重建后 ID/ACK 不重置；并发 outbox 1–40 严格递增 |
| HTTP 校验与兼容 | 新 API 成功/422/404/503/身份隔离；旧 SSE 编码字节内容未变；沿用全部原 API 和 coordinator 回归 |
| producer 接入 | 实际图 worker 在旧 SSE 消费者断开后继续，最终 History 一条，Stream 可重放 final_response 节点，事件字段严格为白名单 |
| 生命周期与配置 | 开关默认 none 不连接；缺 URL warn；非法/非有限 TTL 拒绝；周期失败可续跑，退出 join 在途 flush，关闭资源 |

最终相关命令（`ai-service` 目录，隔离测试 URL 与测试环境开关已设置）：

```text
python -m pytest -p pytest_asyncio.plugin tests/test_redis_p3_events.py tests/test_redis_p3_postgres.py tests/test_context_loop_coordinator.py tests/test_api.py -q --disable-warnings --tb=short
```

结果：112 passed、42 warnings，78.90 秒，退出码 0。初次新套件发现 Lua 大整数返回舍入，已修正；转交测试先采用非允许边，改为真实 general_chat→curator 链；周期测试从固定 sleep 改为等待成功 flush 事件，避免执行器负载造成时序误判。没有修改既有测试或弱化原断言。一次相关命令误写不存在的 `test_api_chat.py`，无测试执行；改用实际 `test_api.py` 后通过。

完整固定源码验收：**2458 passed、12 skipped、207 warnings、288 subtests passed，326.99 秒，退出码 0，无失败/错误**。相对 P1 增加 68 项，原 12 个环境条件跳过项的 nodeid 集合完全一致。范围 `tests + evals/tests`，四 worker、loadfile，P3 开关默认 none；开启路径由新增真实 PG/Redis 用例显式实例化验证。结果写 `verification-summary.json`；本轮实现、测试及迁移的 SHA-256 在完整运行前后完全一致，P1 保护文件与其验收哈希也完全一致。

完整命令使用 `.venv/Scripts/python.exe` 和 `pytest.main`，参数如下；eval 依赖从 `.venv-eval/Lib/site-packages` 追加到 sys.path，禁用第三方 pytest 插件自动加载，仅显式启用以下两个插件：

```text
-p pytest_asyncio.plugin -p xdist.plugin -n 4 --dist loadfile tests evals/tests -q --disable-warnings --tb=short
```

测试环境：`PERSISTENCE_BACKEND=sqlite`、`AI_CONTINUITY_STORE_MODE=sqlite`、`MEMORY_BACKEND=memory`、`CHECKPOINT_BACKEND=memory`、`JEV_ENABLED=false`、`AI_RAG_RETRIEVAL_MODE=empty`、`DEEPEVAL_DISABLE_DOTENV=1`、`BUDGET_LEDGER_BACKEND=history`、`SESSION_CACHE_BACKEND=none`，本轮三个 BACKEND 均 none；`DATABASE_URL` 仅指向 loopback 15471 专用测试实例。Redis fixture 启动随机 loopback 端口的真实服务器，测试结束关闭进程。没有借用生产 URL。

静态检查：Ruff 所有本轮 Python 文件通过；compileall 通过；相关 git diff --check 通过。仅后端修改，无前端构建/视觉验证需求。没有进行云部署、Redis Docker 部署、生产迁移或外部真实 LLM/Qdrant/业务服务验收；原环境依赖项的跳过限制保留。

## 交付与边界

实现 commit：`447692c53388810cd3f248eb38140a0855531578`。该提交仅含 16 个本任务实现/测试/迁移文件。报告提交另保存固定源码验收结果和实现 commit。

Redis 默认关闭，生产配置未改。无部署、push、rebase。未提交用户已有 eval 报告修改及四份根目录报告删除；其他 untracked 文件未纳入本任务。验收结束后按创建时完整 container ID 核对并移除专用 PG 容器及其匿名卷，临时验证 driver 已删除；Redis fixture 已正常退出，未清理既有业务容器或用户文件。

播放恢复只保证最后 flush，INCR 不确定回执不承诺 exactly-once；进度持久化整体 PG 故障时 warn 并保留原图执行，不能声称未持久化的进度仍可恢复。前端 ACK/重连和 Docker/云服务器部署按既定安排留给后续轮次。

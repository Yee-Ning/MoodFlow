# Redis Infra 集成与本地验收报告

日期：2026-10-08。交付分支：`codex/redis-infra`；infra 实现 commit：`eca6e0af19d9e91d1821fbc0515d8526acb09b03`。

五个特性已按指定顺序合并；Compose、镜像依赖、容量文档和故障验收完成。合并基线全量 **2550 passed / 12 skipped / 288 subtests passed**；infra 后全量 **2561 passed / 12 skipped / 288 subtests passed**，0 failed、0 errors。Redis 专项 **229 passed**（包括 11 项真实 OOM），部署契约 **127 passed**。

## Gate 0 与边界

只读核对后暂停，并获得 West 对五个 HEAD、隔离分支、可选健康依赖及 Dockerfile Redis 依赖的确认。实际 HEAD 都包含给定参考实现；差异为后续验收报告 commit。

| 分支 | Gate 0 HEAD | 参考实现 |
|---|---|---|
| main | `0fdf4f71dfa19a9a7fb57b04ed6b73719c02cd72` | 集成起点 |
| codex/redis-budget-ledger | `5744ce6f9bbae9af8e1f1b1bfba545367e78804b` | `065297e` |
| codex/redis-session-cache | `69a4601fd841dca73a57b891030d658e8376e267` | `112d52a` |
| codex/redis-ratelimit | `ca8b02f187d6ff01e4ad852d5a4e596c6d0a7a72` | `d6fe13d` |
| codex/redis-p2-cache-lock | `e20a7214b88cc2297b4166c19562269a0417d97a` | `d73b1d1` |
| codex/redis-p3-events | `10e90595ebdef5425e8cb3d5edf01a6ec7e13bfe` | `447692c` |

生产文件 `docker-compose.production.yml` 原有 postgres/qdrant/migrate/netease/java/ai-service/frontend、backend 网络；只有 frontend 的 127.0.0.1:8088 对宿主发布。开发文件 `ai-service/docker-compose.qdrant.yml` 原本只包含 Qdrant，6333/6334 绑定 loopback。生产通过 `--env-file .env.production` 插值、显式 environment 和 secret 文件引用；本轮没有读取或修改 env/secret 内容。

工作树：`C:/Users/33994/.codex/worktrees/redis-infra/MoodFlow`。main 和五个特性分支 HEAD 保持上述值。原工作区已有用户修改，操作均在隔离工作树执行；没有 push、rebase 或公网/生产部署。五个分支共享的既有历史随 merge 进入集成线，特别是 main → P0 包含此前 Phase 1/2/3 历史；不把这些历史文件计作本轮 infra 修改。

## 合并过程

| 顺序 | Merge commit | 冲突与合并后冒烟 |
|---|---|---|
| P0 | `8f8de51` | 无冲突；23 passed，后续全部用目标 Linux Redis 重验 |
| P1-session | `33dbdbf` | 无冲突；35 passed |
| P1-限流 | `4385259` | 无代码冲突；41 passed；修复多 key TTL 验收的等待条件 |
| P2 | `e88c5f3` | 无冲突；51 passed |
| P3 | `dfe54cc` | `app/main.py` 相邻 import 冲突；P3 + 限流 109 passed |

`app/main.py` 同时保留 GatewayRateLimitMiddleware import/注册、redis_events_router import/注册、P3 start_flush/close_runtime。`app/api/chat.py` 自动合并，入口限流和原 graph/transfer 调用均保留。L1/L2/L3、Guard、预算阈值、路由和 P0 RedisBudgetLedger 未改。

限流 TTL 用例原来只等第一个 key 消失，再断言三个 key 都过期；目标 Redis 的写入时刻不同，存在真实竞态。集成线将等待条件改成 `exists(one, two, gateway)`，仍保留同一 2 秒 deadline 和三个 key 全部消失的最终断言。第一次 40 passed / 1 failed，修复后 41 passed；五个特性分支自身不变。

先完成五次 merge，得到 `dfe54cc`；**2550/12/288 合并基线通过后**才修改 Compose。基线同时通过 deploy 124 项、Java 92 项、前端六套脚本及 build/lint。

## Compose 与镜像变更

- 生产/开发新增同定义 redis，`maxmemory 512mb`、`noeviction`，PING healthcheck 每 10 秒。
- 无 ports、无 volumes，单独 `redis_internal: internal: true`；AI（及继承 x-ai 的迁移容器）加入此网与原 backend。原服务挂载与发布端口保持原样。
- `--save "" --appendonly no`；目标镜像无匿名 data volume。重启丢热状态，从 PG 重建，避免跨部署陈旧缓存；播放计数未 flush 的增量仍有既有损失窗口。
- `REDIS_URL=${REDIS_URL:-redis://redis:6379/0}` 可外部覆盖。九个开关透传，预算默认 history，其余默认 none。默认图 foundation、灰度开关与 Guard 保持原值。
- AI Redis 依赖 `condition: service_healthy` + `required: false`，实测可选依赖故障不阻止 AI。
- Dockerfile 安装既有 `requirements.redis.txt`；`.dockerignore` 放行该文件及预算 reconciliation / 播放计数 flush CLI，镜像中包含两个维护入口。没有自动启用生产定时任务。

Redis 镜像：`redis:8@sha256:2c2dff791878316e3b083188be0d578423b24813cdda5de0e80ea4f7b3e6bfd6`（解析的完整 multi-platform index）。linux/amd64 manifest：`sha256:6e9101032a2126aeeb4b1e862ac77d2fcf26b831ae37e5ff2e69169f82ddd411`，实际 Redis **8.10.2**。

AI 构建 tag：`moodflow/ai-service:redis-infra-validation-20261008`，本地 digest `sha256:117a7a516c51d246891fe15a5bd7af6089bd1df186e3b31b93de19a0bd2cee40`。构建与 pip check、frozen taxonomy hash 校验通过；镜像内实测 redis-py 5.2.1，两个维护脚本 import 冒烟通过。使用单独 tag，没有改现有 rehearsal 镜像/容器。

完整盘点和内存决策见 [redis-keyspace.md](../../redis-keyspace.md)。实际 `MEMORY USAGE SAMPLES 0` 采样覆盖每 run 的 6 个预算 key、session/view、derived、profile/version 指针、锁、transfer、限流、song/epoch 和含 groups/PEL 的 Stream。按文档规划合计 **121.23 MiB**，直接余量 4.22 倍；计入 20% 波动和 10 MiB 基线后 **155.48 MiB / 3.29 倍余量**。这些是容量假设，session 字节数、无 TTL song 基数、receipt/version 和 PEL 均需监测。

## 容器验收

本地 Docker Server 29.7.2（Linux containers）、Compose v5.5.1。验收脚本生成独立 Compose 项目，复用生产 Redis 服务/可选依赖原定义；通过离线测试配置运行真实 app.main/lifespan，并连接隔离 PG。没有读取生产 secret、调用外部模型或执行生产 adapter 验证。

| 场景 | 实测结果 |
|---|---|
| 一次 compose up | Redis healthy、AI healthy；AI 通过 redis 服务名 PING 成功 |
| 6379 隔离 | Ports `6379/tcp: null`、Mounts `[]`、唯一 internal 网络；宿主 127.0.0.1:6379 连接失败；另一 backend 网络的容器无法连接 Redis IP:6379 |
| REDIS_URL | 默认服务名 URL；外部环境覆盖配置断言通过 |
| 九个开关逐一启用 | RedisBudgetLedger / CachedPostgresContinuityStore / RedisRateLimiter / RedisDerivedCache / CachedProfileStore / RedisDedupLocks / TransferStore / PlayCounts / ProgressEvents 全部通过真实组件冒烟 |
| 九开关同时启用 | playcount POST 200、count=1；非法 song_id 422 |
| Redis running unhealthy | 将 Redis 改成只有 Unix socket；compose exit=0、AI healthy、API 200/422；warn + 限流 fail-open + PG counter |
| Redis 启动退出 | 错误 server option，状态 exited unhealthy；compose exit=0、AI healthy、API 200/422；同样降级 |
| 重启热数据 | 写入临时 key，restart Redis 后 key 不存在 |
| 开发 Compose | 实际文件单独启动 redis，healthy、512 MiB/noeviction、RDB/AOF 关闭、无映射/卷、internal 网络；随后删除本轮项目 |

启动故障两项的实测结果收录于 verification-summary.json。完整生产服务栈的公网/provider/release 启动未执行；本轮本地组合只验证 Redis + AI + 隔离 PG 集成路径。

## 内存打满与 fail-safe

11 个新增测试使用独立的 digest 固定 Linux Redis 容器及真实 PostgreSQL；先把 maxmemory 设为 used_memory + 64 KiB 并填充直到实际 OOM，再调至 1 字节维持稳定 OOM。断言 SET 被拒绝、`evicted_keys=0`，没有用 fakeredis 或异常 mock 代替 OOM。这个小配额用于故障注入，不是生产 512 MiB 的负载压测。

| 组件 | 实测断言 |
|---|---|
| 预算 | 健康时用量 27，故障后累计 PG 81；原热余额/receipt 不变；同 ledger 与新 ledger 都按含 framing 的预留拒绝下一调用；不重置额度、不超发 |
| gateway/session 限流（各 1） | 健康 2 次 200、第 3 次 429 + Retry-After；OOM 后 200 + warn fail-open |
| session cache | 两次读实际 PG，OOM refill 不发布热 key |
| profile | 新版本正常写 PG，读取最新版本而非旧热指针 |
| derived | 原计算 ContextPack 保持相同，OOM 不写缓存 |
| dedup lock | 回进程内 slot，重复 acquire 仍被拒绝 |
| transfer | 实际 PG receipt/唯一约束仲裁，重复提议不新增事件 |
| progress | PG outbox 可 publish/resume/consume/ACK；Redis 写失败不丢冷事件 |
| playcount（2） | 冷 key rebuild 在 dispatch 前失败：200 + PG 1；已有热 key INCR 无法确认：首次受控 503 + PG 0，随后 200 + PG 1，不重试 INCR |

OOM 初稿曾错误忽略预算 framing，以及未先建立热 song/epoch 就期待 INCR 503；8 passed / 2 failed。修正测试前提并增加冷 key 场景后 11 passed；未改消费者算法或放宽旧断言。

## 测试、命令与限制

| 验证 | 最终结果 |
|---|---|
| 合并基线 `tests evals/tests` | 2550 passed、12 skipped、288 subtests、213 warnings |
| Redis 11 个专项文件 | 229 passed、0 skipped、32 warnings，75.16 秒 |
| 独立 OOM 文件 | 11 passed、0 skipped、4 warnings |
| infra 后 `tests evals/tests` | 2561 passed、12 skipped、288 subtests、217 warnings，394.36 秒 |
| deploy/tests | 127 passed、0 skipped、0 warnings |
| `mvn -o test`（JDK 21.0.1） | 92 tests、0 failed/errors/skipped |
| 前端 `npm run test:ai-layout/ai-stream/music-assets/search/ai-trigger/worldline` | 六套均通过 |
| `npm run build` / `npm run lint` | 均通过；原有 >500 kB chunk 提示 |
| 新 Python 文件 `ruff check` / `git diff --check` | 均通过 |
| `docker compose -f docker-compose.production.yml config --no-interpolate --no-env-resolution --quiet` | 通过，不读取生产 env |

AI pytest 实际调用原工作区 .venv Python `-X utf8` + 外部只做环境设置/统计的 `redis-infra-validation.py`，底层参数为 `-p pytest_asyncio.plugin -p xdist.plugin -n 4 --dist loadfile tests evals/tests -q --disable-warnings --tb=short`；专项文件不使用 xdist。进程设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、OMP/MKL threads=1，禁用无关插件自动加载。真实 Redis 地址通过 BUDGET_TEST_REDIS_URL 指向本轮容器的临时 loopback 15473；真实 PG 测试使用隔离 PG schema。凭证/测试 payload 不入库。

镜像实际构建命令：`docker build --build-context wheelhouse=<既有已验证 wheelhouse> -f deploy/ai.Dockerfile -t moodflow/ai-service:redis-infra-validation-20261008 .`。容器验收实际运行 `python -X utf8 deploy/redis_infra_acceptance.py --image moodflow/ai-service:redis-infra-validation-20261008 --postgres-network moodflow-redis-infra-test-backend --output <临时结果文件>`；隔离数据库 URL 仅从进程环境读取。

Windows 环境初轮失败来自冻结资源字节/EOL 与子进程 GBK/UTF-8：先恢复内容一致资源的已验收字节（包括 .gitattributes 等无扩展名 metadata），再统一父子进程 UTF-8。仅 PYTHONIOENCODING 不够，最终同时使用 PYTHONUTF8=1 与 -X utf8。未改冻结 hash、不改既有生产行为/测试逻辑；merge 基线与最终全量期间 app/tests 源码 hash 冻结，原有源码从基线到最终一致。

保留的 **12 项环境 skip 与 P0 列表完全一致**，详见 JSON：live JEV、opt-in live PG/restart/Qwen 与真实 Qdrant 专项。Redis/PG 新专项及 OOM 没有 skip；允许的旧环境 skip 不等于所有真实外部模型/Qdrant 路径都已验证。当前前端无代码/视觉改动，未另做 UI 视觉巡检。

宿主原生 pytest 使用独立验证 Redis 容器的临时 loopback 15473 转发；这与交付 Compose 的无 ports Redis 分开。结束后删除本轮 PG/Redis/OOM 容器及专用网络、临时 Compose 项目，移除测试端口映射；原 rehearsal 容器不动。工作树内供既有测试使用的忽略模型/Gold 资源、未跟踪 knowledge/v2 未提交。本轮提交不含测试数据、env 或凭证。

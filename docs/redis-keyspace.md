# Redis 热层 keyspace 与容量决策

本轮基线是从 main 集成 P0、P1-session、P1-限流、P2、P3 的本地分支。Redis 是热层；预算和转交事实、session/checkpoint、profile、播放计数已 flush 的快照、progress outbox 与 ACK 均由 PostgreSQL 保存。所有 backend 开关仍默认关闭（预算默认 history）。

## 完整 key 盘点

表中的“允许丢失”说明组件语义；实际单实例统一采用 noeviction，不主动淘汰任何 key。

| Key | 类型 | 允许丢失/淘汰 | 默认 TTL / 边界 | 丢失后果与恢复路径 |
|---|---|---|---|---|
| `budget:l2:{run_id}:general_chat/curator/explorer` | string，3 个执行 owner | 否 | `BUDGET_REDIS_TTL_SECONDS=3600`；绑定时至少覆盖 run timeout | 热余额丢失可能放宽额度；只能由不可变 PG MODEL_USAGE 与保守 floor/receipt 重建，不能视作普通缓存 |
| `budget:l3:{run_id}` | string | 否 | 同上 | 同一 run 总用量；Redis 故障沿用进程内预留和 PG 用量，保持 L2/L3 拒绝 |
| `budget:receipts:{run_id}` | hash，call_id → reserve/settle receipt | 否 | 同上 | 连接热预留与冷写入之间的间隙；淘汰会丢失在途额度，noeviction 是正确性要求 |
| `budget:floor:{run_id}` | hash，owner → 保守用量下界 | 否 | 同上 | 防重建减少已确认用量；回放 PG snapshot 并取保守最大值 |
| `ratelimit:gateway:{ip}` | hash，当前/上一窗口 | 是 | 两倍窗口；默认 120 秒；接受和拒绝均刷新 | 暂时放宽；Redis 错误 warn + fail-open，429 仍由健康 Redis/Lua 执行 |
| `ratelimit:session:{session_id}` | hash，同上 | 是 | 两倍窗口；默认 120 秒 | 同上；接入位置保持请求入口、RuleRouter 之前 |
| `session:{session_id}` | hash，record/checkpoint + 每字段到期时间 + generation/thread 标签 | 是 | `SESSION_CACHE_TTL_SECONDS=3600`；至多 32 个业务字段；字段也检查独立有效期 | 回 PG，miss 后尝试 refill；执行/恢复证据仍直接读 PG；没有每字段字节硬上限 |
| `sessionview:{session_id}` | hash，archive/progress 的参数维度变体 | 是 | `SESSION_VIEW_TTL_SECONDS=60`；至多 32 个业务字段 | 回 PG/原投影计算；失效 tombstone 防旧 refill；没有每字段字节硬上限 |
| `contextpack:derived:{user_id}:{input_hash}:unicode-v1-derived-v1` | string，纯 token/dedup 派生 JSON | 是 | `CONTEXT_DERIVED_CACHE_TTL_SECONDS=300`；保存上限 262144 字节 | 原 collector/selector 和计数计算照常执行；不存生成文本或完整 ContextPack |
| `profile:{user_id}:{version}` | string，不可变 profile JSON | 是 | `PROFILE_CACHE_TTL_SECONDS=3600`；读取校验 16 KiB | 回 PG；合并写入直接读 PG，PG 版本约束仲裁 |
| `profile:{user_id}:latest` | string，版本指针 / `0` tombstone | 是 | 同上 | 回 PG；dirty/tombstone 防失效失败后信任旧指针 |
| `lock:{resource}` | string，随机 owner token | 是 | 每次 acquire 显式 TTL，向上取整到毫秒；resource ≤512 字符 | 最多重复一次去重工作；故障回进程内 slot；不是 WorldLine 正确性锁，原 asyncio/PG advisory lock 不改 |
| `transfer:idempotency:{transfer_id}:{event_id}` | string，owner/事件 receipt | 是 | `TRANSFER_IDEMPOTENCY_TTL_MS=3600000` | Redis 只加速；读 PG 校验 receipt 内容，PG 唯一约束最终仲裁 |
| `playcount:{song_id}` | string，整数计数 | 尽量不 | **无 TTL**；song_id ≤128 字符 | 重建最近 PG flush 快照；未 flush 增量可能丢失，这是 P3 已接受的热数据损失窗口 |
| `playcount:{song_id}:epoch` | hash，epoch/base | 尽量不 | **无 TTL**；与 counter 成对 | 丢任一半均显式 rebuild；PG epoch/delta flush 幂等，避免重放重复累计 |
| `events:{run_id}` | stream，含消费组/消费者/PEL 元数据 | 是 | `PROGRESS_EVENTS_TTL_SECONDS=86400`；精确 `MAXLEN=1000` | PG outbox 回放，seq-0 ID、pending 和 ACK 由 PG 恢复；MAXLEN 限制 entries，不能单独限制组/PEL 元数据 |

播放计数 flush 周期默认 `PLAYCOUNT_FLUSH_INTERVAL_SECONDS=60`。P0 reconciliation 默认周期 `BUDGET_REDIS_RECONCILE_SECONDS=60`。镜像包含两个手工维护入口 `scripts.reconcile_redis_budget`、`scripts.flush_redis_playcounts`，本轮不启动生产定时任务。

## 内存采样与 512 MiB 依据

在 digest 固定的 Linux Redis 8.10.2 上对临时 key 执行 `MEMORY USAGE ... SAMPLES 0`，采样后删除。以下是**单机初始容量规划假设**，不是生产流量测量，也不是代码保证的最大基数。使用满尺寸派生/profile、32 个字段和消费组元数据计算，避免只按 key 数估算。

| 采样单位 | 实测字节/单位 | 规划数量 | 小计 MiB |
|---|---:|---:|---:|
| 一个 budget run：3 L2 + L3 + floor + 200 receipts | 20013 | 500 | 9.54 |
| session + sessionview：各 32 个 8 KiB 业务字段，含 expiry | 663174 | 100 | 63.25 |
| 一个 16 KiB profile version + latest 指针 | 20617 | 500 | 9.83 |
| 一个 256 KiB derived 文档 | 327753 | 64 | 20.00 |
| 一个限流 identity | 131 | 10000 | 1.25 |
| 一个去重 slot | 106 | 1000 | 0.10 |
| 一个 transfer receipt | 186 | 10000 | 1.77 |
| 一个 song counter + epoch | 189 | 10000 | 1.80 |
| 一个 stream：1000 entries、2 groups、4 consumers、400 pending | 143448 | 100 | 13.68 |
| **合计** | | | **121.23** |

`512 MiB / 121.23 MiB = 4.22`。另按 key 估算预留 20% 的全局元数据/分配器/客户端波动和 10 MiB 基线，规划约 155.48 MiB，仍有约 **3.29 倍**余量。采样 allocator fragmentation ratio 为 1.06；实际 Redis RSS 不等于 maxmemory。本轮设置 Redis maxmemory，避免额外把容器 RSS 限制在同样的 512 MiB 上。

session/checkpoint 字段尺寸、预算 receipt 数、用户/version/input 基数、播放计数 song 基数和 stream group/PEL 可能超出上述假设。播放计数没有 TTL，不能把 flush 当作删 key。需要按实际 `INFO memory/stats`、`MEMORY USAGE`、`SCAN` 基数和 warn/OOM 监测复核容量；容量变更另行审核，不自动删除预算 key。TTL 仍按组件的原协议生效，noeviction 不阻止到期。

## 单实例 noeviction 决策

预算 key 不能和普通缓存一起淘汰。单实例统一 noeviction：内存到阈值时拒绝需要分配内存的写入，保留既有余额、receipt、floor，触发组件现有错误路径。[Redis key eviction 文档](https://redis.io/docs/latest/develop/reference/eviction/)

缓存回 PG/原计算、限流 warn + fail-open、转交回 PG 唯一约束、progress 保留 PG outbox。预算回原门控并读取 PG 已确认用量，不重置用量或修改阈值。播放计数沿用 P3 的保守行为：发出 INCR 后无法确认结果时，首次 HTTP 请求可能返回通用 503，随后在该进程回 PG；不自动重试 INCR，避免重复累计。

当前规模不引入 noeviction/LRU 双实例，避免额外连接、监控、恢复和部署路径。本轮也不改五个消费者的算法或降级协议。

## 网络、持久化与启动

生产和开发 Redis 使用相同定义：`redis:8@sha256:2c2dff791878316e3b083188be0d578423b24813cdda5de0e80ea4f7b3e6bfd6`（multi-platform index）；本次 linux/amd64 manifest 为 `sha256:6e9101032a2126aeeb4b1e862ac77d2fcf26b831ae37e5ff2e69169f82ddd411`，版本 8.10.2。

Redis 无 ports，无 volumes，仅连接 `redis_internal`（`internal: true`）。AI 同时连接 backend 与 redis_internal，保持现有外部服务访问；Redis 无外网路由。[Docker internal network 文档](https://docs.docker.com/compose/how-tos/networking/#internal-networks)

显式 `--save "" --appendonly no`，不保留 RDB/AOF；该 pinned 镜像也未声明匿名 data volume。Redis 重启/替换丢热状态，PG 冷事实用于重建，避免跨部署带入陈旧缓存/锁。播放计数最近一次成功 flush 后的增量仍有已接受的损失窗口；无持久化不是零数据损失承诺。

AI 的 Redis 依赖为 `condition: service_healthy`、`required: false`：健康检查 PING，每 10 秒；异常时警告后允许 AI 启动，运行期各路径继续降级。[Docker depends_on 文档](https://docs.docker.com/reference/compose-file/services/#depends_on)

## 配置与本地验收入口

Compose 注入 `REDIS_URL=${REDIS_URL:-redis://redis:6379/0}`，外部 shell/指定 env 文件可覆盖。九个 backend 开关透传外部配置，默认沿用原值。限流、TTL、timeout 等组件参数沿用模块原配置；需要调节未透传的参数时使用单独 Compose override，不编辑生产 env/secret。默认图、灰度开关、Guard 与预算阈值保持原样。

生产仍使用既有 `docker compose --env-file .env.production -f docker-compose.production.yml up -d --build` 流程，需要既有已验证 wheelhouse、模型/release/index 挂载和 secret 文件。本轮只运行隔离本地验收，不执行这个生产命令。

开发 `ai-service/docker-compose.qdrant.yml` 仍只编排 Qdrant 和新增 Redis。Redis 不发布端口，因此宿主原生 AI 不能直接用 `localhost:6379`；开发容器/测试工具需加入该 Compose 的 redis_internal 网络，通过 `redis:6379` 使用。默认 backend 关闭，不改变原生开发路径。

`deploy/redis_infra_acceptance.py` 提供本地 Docker 启动/端口/九开关/重启/故障验收；只接受带 `codex.task=redis-infra` 标签的隔离 PG 网络，数据库 URL 通过进程环境输入。它复用 Redis 服务和可选依赖原定义，以离线测试设置运行真实 `app.main` 与 lifespan，不读取生产 env/secret，不调用外部模型。具体命令和执行结果见本轮验收报告。

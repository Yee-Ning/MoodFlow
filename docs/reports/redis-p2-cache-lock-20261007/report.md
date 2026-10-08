# Redis Phase B-P2：派生缓存、画像缓存与去重占坑锁

日期：2026-10-07。分支：`codex/redis-p2-cache-lock`。
基线：P1 验收 HEAD `69a4601fd841dca73a57b891030d658e8376e267`。

## Gate 0 与选型确认

先只读核对基线、P1/Phase 1 报告及实际代码；发现锁语义差异后停止，没有先建分支或修改代码。West 随后明确确认：“保留 WorldLine 现有 PG 互斥锁（asyncio.Lock + pg advisory lock）不动；Redis 统一锁组件仅用于新的去重占坑场景，不替换临界区互斥。”本轮按该修订继续。

| 核对项 | 实际形态与接入边界 |
| --- | --- |
| P1 基线 | P1 实现 `112d52aea46fd155a7639138b5f0f19028ebac10`，验收 HEAD 为本轮基线。报告记录 2390 passed、12 原有 skipped、288 subtests、185 warnings |
| Redis/config | `requirements.redis.txt` 已有 `redis==5.2.1`。P1 `SESSION_CACHE_BACKEND=none/redis` 默认 none；显式 redis 读取 `REDIS_URL`。Session/view TTL 分别 3600/60 秒，timeout 0.25 秒。P2 复用依赖和 URL，添加独立可选开关，没有编辑 P0/P1 实现 |
| 纯派生点 | `ContextCollector.collect` 计数、`_dedup_keys` 规范化/摘要，以及 Selector 内计数是确定性纯计算。`_truncate/_compress_song_xml/_compress_tool_json` 是原提取式算法。Collector 的活动 revalidation、全部显式输入，Selector 的完整 items/policy/intent/trace，及来源 policy 都纳入输入快照 |
| 不可整体缓存的边界 | `build_context[_async]` 的累积预算预处理、异步 JEV 选择、最终 manifest 设置，以及 `ContextSelector.build` 的 lease 检查、CONTEXT_TRACE 写入必须每次执行。没有缓存这些调用的返回对象 |
| 画像 | `agent_user_profile_document(user_id, version, body JSONB)`，主键 `(user_id, version)`；禁止 UPDATE/DELETE 的触发器保持。`ProfileDocument.schema_version=1`，独立递增 `version`；ProfileWriter 和 PG 写入保留 expected_version 检查。共用 `validate_public_document` 的字段白名单、私有文本门及 16384 字节限制 |
| WorldLine 锁差异 | `worldline/session.py::_session_lock` 同时使用 asyncio.Lock 和 `pg_advisory_xact_lock(41721, hashtext(session_id))`，覆盖 explore 的读状态、生成、检查 revision、提交。其作用是临界区互斥，不能换成可能提前过期的 Redis 占坑锁。原文件完全未改 |

## Key、配置与实现

| Key | 内容 | 生命周期 |
| --- | --- | --- |
| `contextpack:derived:{user_id}:{input_hash}:unicode-v1-derived-v1` | JSON，仅保存文本摘要到 token 数的映射及 item 摘要到去重 key 的映射。Collect/Select 的 stage 也进入 hash | 默认 300 秒，输入寻址失效，TTL 清理旧变体 |
| `profile:{user_id}:{version}` | Phase 1 白名单 ProfileDocument JSON，不可变版本快照 | 默认 3600 秒；成功更新后失效前一版与新版缓存 |
| `profile:{user_id}:latest` | 最新版本号；`0` 为失效墓碑，不能当成版本 | 与画像 TTL 一致；更新后设墓碑，miss 回源 PG 后重新定位最新版本 |
| `lock:{resource}` | 唯一 `redis:<UUID>` 所有权 token | `acquire(resource, ttl)` 的 ttl 单位为秒，向上取整为毫秒后用 PX 设置 |

新增可选开关：`CONTEXT_DERIVED_CACHE_BACKEND`、`PROFILE_CACHE_BACKEND`、`DEDUP_LOCK_BACKEND`，默认全部 `none`，显式 `redis` 才构造连接。共用 `REDIS_URL`；各自支持 `<前缀>_TIMEOUT_SECONDS`，默认 0.25 秒。两个缓存分别支持 `CONTEXT_DERIVED_CACHE_TTL_SECONDS=300`、`PROFILE_CACHE_TTL_SECONDS=3600`。锁每次 acquire 显式指定 TTL。非法 backend/TTL/timeout 是配置错误；URL 缺席、可选依赖缺席或 Redis 不可用均记录脱敏 warn 并回原路径。没有修改生产配置或依赖文件。

### ContextPack 派生缓存

接入 Collect/Select 的装饰器只替换一次调用中的 counter，并通过 ContextVar 隔离 user 和去重派生批次；复制实例以避免并发修改原对象。两阶段始终运行原算法，成功后才保存派生批次。重复输入命中后，原 Unicode token 正则计数和规范化内容去重计算均不再调用，原选择、组装、预算验证、日志和 History 副作用继续执行。该收益不表示网络、输入 hash 或整体组装耗时为零。

hash 使用完整规范化输入，包含消息内容/元数据、当前 query、system、投影、画像候选及版本、策略、intent、counter 身份、stage、来源配置和活动 revalidation。set/frozenset 排序，BaseModel 按 JSON 形态编码；未知对象直接旁路，避免以 repr 假装覆盖其状态。只有已知的 UnicodeHeuristicTokenCounter 可复用；自定义/有隐藏状态的 counter 保持原路径。缺少 user_id 时不启用派生缓存。

源变更采用**输入寻址失效**：新消息或新版本画像进入实际组装输入后，input_hash 改变，旧派生结果不能用于新输入；不修改消息写入链或扫描删除其他用户的 key。旧变体到 TTL 后清除。读取的全部输入仍来自已有 P1/PG/Phase 1 来源路径，没有缓存来源读取本身。Selector 位于 JEV 之后，因此 JEV 本次输出的候选变化也进入它的 hash；JEV 每次仍执行。Redis value 不包含候选内容、messages、prompt、LLM 输出或生成结果，仅包含数值、摘要和 evidence 身份。单份派生结果最多 256KiB，错误文档旁路重新计算。

算法/序列化语义变更时必须递增 `VERSION` 与相应 counter 版本，避免跨发布复用旧派生语义。

### 画像缓存与失效

只在既有 `get_profile_store()` 的 PG 分支选择 CachedProfileStore；默认与 SQLite 分支保持原适配器。latest 热读只查 Redis 指针和版本 key，命中零 PG 查询。miss 读取原 PG，再回填。显式旧版读取保持 Phase 1 的不可变版本语义，不把最新版代替旧版。

原 PG write 成功返回后才失效；写入失败或版本冲突不修改缓存。`ProfileWriter.update` 在适配器存在 `read_for_update` 时用 PG 当前版本参与合并，避免丢失失效通知时让旧热画像参与写入决策。PG advisory lock 和 expected_version 仍是画像写入事实门。

miss 回填前 WATCH latest 指针；失效将其设为墓碑，包含此前没有 key 的情况。旧回源读取在并发更新后只能返回它已经读取的快照，不能重新污染缓存。读取命中同样重新验证 schema、user/version、私有文本门和 16KiB 字节门。Redis 故障直读 PG；失效失败在本进程标记 dirty，恢复后先失效再读。Redis 数据丢失自然回源，无 Redis 事实源假设。

### 统一去重占坑组件

`dedup_locks.py` 提供 `acquire(resource, ttl) -> token | None` 和 `release(resource, token) -> bool`。资源应由新调用者命名为自己的去重命名空间，例如 `profile-warm:<user_id>:<version>`；本轮没有为现有请求新增拒绝路径，也没有改接 WorldLine explore。

Redis acquire 使用单命令 `SET lock:{resource} token NX PX milliseconds`。独立 Lua 文件 `app/agent/lua/release_dedup_lock.lua` 原子比较 token 后 DEL；错误 token、过期持有者都不能删除后继占坑。TTL 自动释放，不加续租或 Redlock。这里使用单实例 Redis 的原因是需求为重复工作抑制，允许占坑自然过期；它不能保证长时临界区互斥或 exactly-once。

默认或故障回退为 LocalDedupLocks（RLock + 单调时间 + token 校验）。Redis 持有者也保留本地影子占坑，避免同一进程在故障/恢复切换中再次成功占坑。过期条目定期清理。Redis 持有者释放失败返回 false，让服务端 TTL 清理，不伪造释放成功。

**选型规则：WorldLine/画像写入等 correctness-critical 临界区继续使用已有 PG/进程互斥；Redis 组件仅用于新的去重占坑。Redis 故障时不承诺跨进程去重，本地回退也不能冒充分布式互斥。**

## 验收证据

最终完整验证：**2441 passed、12 原有 skipped、288 subtests passed、190 warnings，0 failures / 0 errors**，退出码 0，pytest 用时 **976.63 秒（16 分 16 秒）**。源码/测试/Lua 的 9 份哈希在运行前后保持一致；skip nodeid 集合与 P1 完全相同；原有测试文件未改。本轮新增 **51 个验收用例**，全部执行通过。脱敏统计及哈希见 `verification-summary.json`。

从 `ai-service` 目录复现完整验证（连接串仅含一次性测试凭证）：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
.venv/Scripts/python.exe -c "import os,sys,pytest;os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PERSISTENCE_BACKEND='sqlite',AI_CONTINUITY_STORE_MODE='sqlite',DATABASE_URL='postgresql://postgres:redis-p2-test-only@127.0.0.1:15470/moodflow_redis_p2_test',MEMORY_BACKEND='memory',CHECKPOINT_BACKEND='memory',JEV_ENABLED='false',DEEPEVAL_DISABLE_DOTENV='1',AI_RAG_RETRIEVAL_MODE='empty',BUDGET_LEDGER_BACKEND='history',SESSION_CACHE_BACKEND='none',CONTEXT_DERIVED_CACHE_BACKEND='none',PROFILE_CACHE_BACKEND='none',DEDUP_LOCK_BACKEND='none');sys.path.append('.venv-eval/Lib/site-packages');raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','-p','xdist.plugin','-n','4','--dist','loadfile','tests','evals/tests','-q','--disable-warnings','--tb=short']))"
```

专项使用相同隔离环境执行 `pytest -p pytest_asyncio.plugin tests/test_redis_p2_cache_lock.py tests/test_redis_p2_postgres.py -q --disable-warnings --tb=short`：**51 passed、5 warnings，28.91 秒**。相关回归另执行 `test_context_engineering.py`、`test_context_entry_phase1.py`、`test_context_entry_postgres.py`、`test_worldline_session.py` 和当时的专项：92 passed、6 warnings，96.91 秒；随后补充真实连接拒绝与 JEV 不旁路用例，并在最终专项和全量中验证。

静态/编译检查：`.venv/Scripts/python.exe -m ruff check <本轮 8 个 Python 文件>`、`.venv/Scripts/python.exe -m compileall -q <相同文件>` 均通过。提交前还检查本轮 `git diff --cached --check`。没有使用全仓库 diff 的原有无关尾随空白来阻塞或修改用户文档。

| 验收项 | 用例 |
| --- | --- |
| 相同输入不重复纯计算 | `test_repeated_derived_input_has_zero_token_or_dedup_recomputation`，二次调用断言原 counter 和 dedup compute 均零调用，并断言 value 不包含合成生成文本 |
| 新输入失效 | 八组输入变化；真实 PG 画像更新后的 ContextPack 使用 version=2 和新 hash |
| 预算/审计/JEV 保持 | `test_derived_hits_preserve_history_writes_and_lease_checks`、`test_derived_hits_do_not_bypass_cumulative_budget_failure`、`test_cache_hits_still_execute_jev_selection`；完整既有 L1/L2/L3 套件 |
| 画像 hit/miss/新版 | 真实 PG pool.connection 计数：首读 1、命中 0，SourceRegistry 命中 0，更新读取新版；显式 version=1 仍恢复旧版 |
| 失效竞态 | WATCH + 并发 PG 更新，旧读返回后 latest 仍为墓碑，旧值没有重新入 Redis |
| 白名单/错误路径 | scope、version、extra、私有文本、oversize、版本冲突、PG loader 错误；错误不吞、不重试写入 |
| 锁原子性 | 16 个独立持有者并发 acquire，仅一个成功；真实 Redis Lua 验证 wrong-token 和 stale-owner；真实 PTTL 及到期后再占坑 |
| 故障回退 | 三组件真实 loopback connection refused、各自 RedisError 和失效失败恢复、缺 URL/缺可选依赖、进程内占坑并发及过期清理 |

## 范围与限制

P0/P1 实现、WorldLine session 文件、BudgetLedger Protocol、预算阈值、路由、Guard、转交逻辑及原有测试均未改。没有增加新依赖、修改生产配置、迁移生产库、部署或 push。用户原有 5 个 tracked 修改/删除及其他无关未跟踪文件未纳入提交。

测试 Redis 为本机原生独立随机 loopback 端口、禁用持久化的真实 redis-server；PG 为一次性 loopback 15470 容器，每用例建独立测试数据库。本轮只启动测试环境，Docker Redis/统一云部署仍按用户决策排到 P2/P3 落地之后。没有验证目标 Linux Redis 镜像、真实外部模型服务、云网络或 Redis 故障期间跨实例去重。原有需要外部凭证/专用容器的 12 个 skip 保持，不改变 skip 条件。没有前端改动。

完整验证后，fixture 结束原生 Redis 测试进程；核对本轮容器的完整 ID 后删除一次性 PG 测试容器及其匿名卷，并删除本轮 ignored 临时验证脚本。未删除用户其他容器或工作区文件。

画像写与 Redis 失效没有跨系统事务：跨进程失效失败时普通热读可能在 TTL 内读到旧画像；本进程 dirty 处理和 PG 写入合并检查防止缓存成为写入事实源。派生缓存只复用其实际输入的计算，不扩张这个已有 cache-aside 一致性边界。画像热读与运行关键状态的事实校验不可混用。

## 提交

实现及测试 commit：`d73b1d1f42a37a9aef8d17072f93b1ecbd2579db`（`Add Redis derived/profile caches and dedup slot locks`），直接基于 P1 验收 HEAD，包含本任务的 9 个文件。

报告另建独立文档 commit，避免自引用 commit hash；HEAD 可由 `git log -1 codex/redis-p2-cache-lock` 获取。本轮提交未包含用户原有修改，未 push。

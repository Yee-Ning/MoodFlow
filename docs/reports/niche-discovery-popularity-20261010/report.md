# 冷门挖掘热度过滤验收

日期：2026-10-10（Asia/Shanghai）。分支：`codex/niche-discovery-popularity`。基线：`codex/integration-coldchain-p2` 的实际 HEAD `4a6a9558b22e15f4e82843551b602ca6c2a94063`。

已完成评论总数读取、Redis 派生缓存、双边冷门判定及 Curator 接入。Python 完整回归 **2829 passed + 288 subtests passed，12 原有 skipped，0 failed/errors**；Java **100 tests，0 failures/errors/skipped，JAR 构建通过**。

## Gate 0：实际字段证据

结论：**通过，评论总数为上游 body 顶层 `total`**。在源码检查不能确认字段后，通过现有网易云测试容器的 HTTP 接口发起一次真实只读调用，没有读取或输出认证 token、cookie、用户标识或评论内容。

- fixture ID：`185809`，来源 `ai-service/tests/test_agent_runner.py:55`。
- 请求：`GET /comment/music?id=185809&limit=1&offset=0&before=0`。
- 已确认的上游路径：`/api/v1/resource/comments/R_SO_4_${query.id}`；前置路径与参数结论沿用，不重新建立 Gate。
- HTTP 200；实际返回 1 条普通评论，`total` 为整数，不能以本页条数代替总数。

真实返回的脱敏片段：

```json
{"code":200,"more":true,"total":68320}
```

记录见 [gate0.json](gate0.json)。Gate 通过后才创建工作分支并开始修改代码。

## 实现与行为

**热度数据层**：`app/tools/popularity.py::get_song_popularity(song_ids)` 返回已知的 `song_id → comment_count`；缺失 key 表示未知。单首解析、网络或服务失败不补 0、不重试、不阻止其他正常歌曲；真实返回的 0 保留为已知值。

复用 `MusicServiceClient._request` 和 Java `NeteaseMusicService.get`，新增只读 `GET /api/songs/{id}/comment-summary`，只返回歌曲 ID 与评论数；不注册新 Agent 工具。旧 Python 客户端方法、Java 原请求封装和写工具保持原行为。

**Redis 派生缓存**：复用 P2 `RedisDerivedCache` 的客户端、连接配置与故障边界，使用独立 value schema 和版本化 key，避免混入 ContextPack token 计数缓存：

```text
contextpack:derived:public-song-popularity:{hash(song_id)}:netease-comment-total-v1-popularity-v1
```

TTL 固定 **604800 秒（7 天）**；已知数据命中不调 API，未知失败不缓存为零。缓存损坏或 Redis 不可用时回原只读 API。复用既有 `CONTEXT_DERIVED_CACHE_BACKEND`，默认 none 保持不变；本轮没有开启或修改生产 Redis 配置。真实独立 Redis 用例验证了存储格式与 7 天 TTL，没有新增 PG 热度表、热度字段或热度回源路径。

**读取约束**：批次最多 60 个去重 ID，与原 Curator 候选窗口一致；新读取按事件循环共享串行限流器，以至少 1 秒的发起间隔读取，不突发、不自动重试。上游 HTTP 429 仅在新 summary 路径保留为 429，批次停止后续读取，剩余歌曲为未知。读取复用原任务截止时间；整批截止超时立即结束批次，单首服务超时继续隔离。

**冷门判定**：`NicheConfig` 集中定义上下限；`is_niche` 与 `rank_niche` 不内嵌阈值。初始配置为 **100 ≤ 评论数 ≤ 10000**，包含两端边界；未知、低于下限及高于上限都不入选。排序按评论数升序，等值稳定保留输入顺序，取前 k 首；空集或全部不合格返回空，不放宽区间。

该初始区间依据 West 授权的本轮抽查设定，通过独立策略对象供调用方注入；没有修改 Settings、`.env`、默认图或灰度开关，也未实现权重配置、A/B 或推荐效果评估。

**Curator 冷门请求**：知识关联扩展 → 现有目录核对 → 评论数读取与双边过滤 → 排序 → 原结构化选曲 → 讲解。每首冷门推荐附 `comment_count`、经原知识验证器核实的图谱边、claim IDs 和确定性讲解；年代/场景/影响边直接投影已有关系，风格发现保留两位艺人与共同风格的真实证据边。

候选未经图谱证据关联、热度未知或不在区间内时不能被标为冷门；模型也不能重新引入已过滤 ID。全部不合格时不回退为普通搜索歌曲、不创建歌单；不足请求数量时如实交付较少曲目，不凑数。普通策展请求不读取热度，保留原候选及排序行为；原输入/结果 schema 和写工具契约保留，仅增补冷门证据字段。

旧冷门测试补充了受控评论数依赖，并把“热度未知也创建普通歌单”的旧期待更新为本轮明确的空候选不回退契约；未删除测试、降低断言或新增 skip。普通策展、目录校验、预算拒绝、恢复及权限回归继续执行。

## 8 首真实抽查

抽查时间：**2026-10-10 22:05:56（Asia/Shanghai）**。每首使用 `/comment/music`、limit=1，串行间隔 1 秒；全部 HTTP 200、code=200，保存的是当次 `total`，没有把活数据作为数据库中的固定断言。

| 歌曲 ID | 曲目（已通过真实目录查询确认时填写） | 评论总数 | 本轮区间判定 |
| --- | --- | ---: | --- |
| 185809 | fixture ID | 68320 | 超上限 |
| 186016 | fixture ID | 1970246 | 超上限 |
| 863073353 | 水之湄 — 惘闻 | 9239 | 入区间 |
| 385322 | Lonely God — 惘闻 | 57904 | 超上限 |
| 409931403 | 21世纪不适症 — 惘闻 | 6651 | 入区间 |
| 28629168 | 第八层地狱 — 惘闻 | 1419 | 入区间 |
| 385343 | 焦虑抑制剂 — 惘闻 | 3711 | 入区间 |
| 2119790495 | Lonely God — 惘闻 | 374 | 入区间 |

记录见 [live_sample.json](live_sample.json)。5/8 处于初始区间；小样本说明该区间能筛分这些记录，不证明总体推荐质量。抽查未覆盖低于 100 的真实曲目，下限仍是人为噪声门，边界通过单测验证。相同歌名的不同歌曲 ID 热度差异很大，判定针对目录记录而非整部作品。

## 验证与保护项

| 验证 | 结果 |
| --- | --- |
| 数据层初版单测 | 17 passed |
| 双边判定 + 数据层 | 38 passed |
| Curator/预算/路由/Agent Pack 初次组合 | 253 passed |
| 截止超时修复后 + Curator + Redis P2/PG | 169 passed，0 failed |
| `tests + evals/tests` 最终完整回归 | **2829 passed，288 subtests passed，12 skipped，348 warnings，0 failed/errors**；401.85 秒；退出码 0 |
| Java `mvn --batch-mode --no-transfer-progress -o verify`，JDK 21 | **100 tests，0 failures/errors/skipped；BUILD SUCCESS** |
| Ruff、Python 语法编译、`git diff --check` | 全部通过 |
| 373 个冻结文件与冻结清单摘要 | 全部一致，本轮只读 |
| 新建 Python 文件 | 全部 LF |
| 原冻结 prompt、预算值、ContextSelector、意图筛选器与预算边界测试 | 内容不变 |
| 全量运行前后记录的源码摘要 | 一致 |

integration 基线 2782 个通过用例全部纳入完整回归，最终增加 **47 个 Python 用例**。12 个 skipped 的 nodeid 集合与基线完全相同，是既有外部模型、live JEV、专用 PG 重启及真实 Qdrant opt-in 路径；没有跳过新增测试。Java 增加 8 个用例，覆盖成功、输入校验、非法/缺失字段、上游失败和 429。

证据：[Python 完整输出](popularity_full.log)、[最终统计与 skipped 清单](popularity_full.json)、[Java 完整验证](backend_final_verify.log)、[运行后摘要与保护项](integrity_after.json)、[汇总](verification.json)、[此前失败完整堆栈](popularity_final_targeted.controller.failures.jsonl)、[此前失败统计](popularity_final_targeted.json)、[修复后组合回归](popularity_deadline_repair.json)、[测试环境清理](cleanup.json)。

原始日志的全部字节另保存在同名 `.log.gz` 归档中，解压后逐字节与执行器落盘日志一致，摘要见 [log_archives.json](log_archives.json)。用于直接阅读的 `.log` 副本仅规范换行及行尾空白，以通过 diff 检查；测试内容、堆栈、输出与最终统计完整保留。

Python 命令（整合工作树 `ai-service` 目录）：

```powershell
& 'D:\code\ProjectsAll\MoodFlow\ai-service\.venv\Scripts\python.exe' -B ../.dev/niche-popularity-20261010/run_validation.py popularity_full tests evals/tests
```

执行器禁用生产 dotenv，使用受控模型/音乐目录、独立真实 Redis 和一次性 loopback PG；4 workers、loadfile、完整堆栈、`--maxfail=1`。日志持续 flush/fsync，worker 在转发失败前保存完整堆栈，完整回归正常结束没有人为中断。已核验本轮容器完整 ID 并清理 PG，未触碰其他容器。

此前新增截止时间用例曾失败，nodeid 为 `tests/test_popularity_data.py::test_existing_request_deadline_bounds_new_reads_without_changing_budget`，完整记录 **1 failed、172 passed**，实际读取 2 次、断言预期 1 次。根因为整批截止超时被当作单首失败继续调度；已改用 timeout scope 的 expired 状态区分整批截止与单首失败，原断言和预算保持不变。失败统计及完整堆栈保留在报告目录，不计入验收通过结果。

## 提交、边界与未验证项

- `d079c57`：只读评论统计接口与 Redis 热度数据层。
- `8a9fa29`：独立配置对象、双边判定及稳定排序。
- `fb193e3`：Curator 过滤与知识证据接入、截止时间及 429 读取保护。

全部实现提交仅在 `codex/niche-discovery-popularity`；integration 分支仍是原基线，原主工作区的 journal HEAD 和用户修改保留。没有合并 integration/main、push、部署、改前端或生产配置。

**诚实边界**：评论数是热度代理，**不是播放量**，还受发行时间、版本、评论习惯和缓存新鲜度影响。上下限人为定义，“冷门”相对于当前候选与此策略成立；低于下限不等于音乐质量差，“值得听”属于知识关联支持的策展判断。Redis 缓存允许最长 7 天的统计滞后，无法取得统计的歌曲保持未知。

真实抽查验证上游统计，新增 Spring 适配层及 Curator 闭环用受控服务测试，未部署新代码去触碰现有运行服务。未验证既有 12 个外部依赖 opt-in 路径、多实例统一读取配额或跨进程全局限流；1 秒间隔是本轮保守本地读取策略，不宣称是网易云公布的官方配额。歌单/收藏仍以网易云 API 为事实源；新增派生缓存只在 Redis，未新增 PG 热度事实存储或写工具。

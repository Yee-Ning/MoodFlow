# 三因子权重与 A/B 实验验收

日期：2026-10-11（Asia/Shanghai）。分支：`codex/curation-weights-ab`；实际基线：`codex/niche-discovery-popularity` 的 `7b0197a11382ac79bb95e6f433f846ae2d27a2bb`。

已完成独立权重配置、默认排序兼容、确定性多变体分流及推荐事件记录。**播放历史未接入，本轮按允许的降级方案保留仅红心偏好。** 最终完整回归 **2860 passed + 288 subtests passed，12 原有 skipped，0 failed/errors**。

## Gate 0：五项核对

| 项目 | 结论 | 源码/执行证据 |
| --- | --- | --- |
| 三因子混排 | 通过。原实现为固定规则，没有可配置数值权重 | 基线 `curator.py:546–559`：红心 membership、`discovery_ids` membership、场景标签命中数；`:622–648`：红心优先后交替混排，冷门按评论数排序 |
| 播放历史事实源 | 通过，按明确允许的降级分支执行：**未接入** | 前端 `StaticDestinationPage.tsx:50–55` 是历史入口占位；`playerStore.ts:14–79` 保存当前歌曲/队列，`:81–107` 仅持久化音量。Python/Java 无播放历史读取适配或事件存储 schema |
| NicheConfig 模式 | 通过 | `niche.py:7、23、32、36`：不可变 dataclass、getter、调用方 config 注入；基线 `curator.py:284、577` 使用该对象 |
| A/B 基础 | 通过，复用既有能力 | 基线 `rollout.py:6–22` 的 SHA-256/10000 桶；`observability.py:35–57` 的结构化事件与 UTC 时间；可信 user_id 由 `chat.py:79、111` → `long_horizon/runner.py:108` 注入 |
| 测试基线 | 通过 | 在基线分支运行 10 个推荐/配置/灰度/事件测试文件，**176 passed，0 failed，67.97 秒，退出码 0** |

明细见 [gate0.json](gate0.json)、[基线统计](curation_weights_gate0_baseline.json)、[基线完整输出](curation_weights_gate0_baseline.log)。Gate 0 完成前没有修改代码或创建工作分支。

播放历史的项目来源、schema、更新频率和保留时长均未建立。网易云 SDK 虽有 `user_record.js`（周/全时段听歌排行）和 `record_recent_song.js`，当前推荐端没有接入且未验证可用返回契约，不视作已连接的事实源。画像 `ProfileEntry.updated_at`、播放器队列、客户端 action ACK 均不等于真实播放记录。本轮不读取认证 secret 来补此缺口，不生成历史或推测播放次数。

## 权重配置及默认兼容

`app/agent/handoff/recommendation.py::RecommendationWeights` 为不可变独立对象，沿用 NicheConfig 的 getter 和显式参数注入模式。负数、全零、NaN/Infinity、布尔值及非数值权重均拒绝。

| 因子 | 输入与分值 | 默认权重 |
| --- | --- | ---: |
| 用户偏好 | 仅经原网易云红心工具读取的 membership，0/1 | 4 |
| 知识关联 | 沿用原 `discovery_ids`：初轮搜索之外的已核对候选，0/1；不是关联质量或图谱度数 | 2 |
| 场景标签 | 标签在歌名、专辑、歌手文本中的命中数 / 标签数量，0–1；仍是原场景匹配启发式，不是已核实情绪属性 | 1 |

分数为 `Σ(归一化权重 × 因子分)`，稳定降序排列。使用精确 Fraction 比较避免浮点舍入造成次序变化，模型可见摘要使用 0–1 分数。

默认 4:2:1 满足 `4 > 2+1`、`2 > 1`，因此保持原“红心 > 知识新增候选 > 标签命中数”的初排次序。默认配置及其等比例缩放保留原选曲后交替混排；冷门保留原评论数排序。单测覆盖三因子全部 28 种组合、稳定等分、默认后处理与冷门次序。

非默认权重调整候选排序及所选歌曲的最终顺序，不再被固定交替规则覆盖；召回查询与工具契约不变。冷门过滤仍先要求目录和图谱证据、已知评论数及原 NicheConfig 区间，自定义权重不能重新引入不合格歌曲。候选数量上限和 Agent Pack/L1/L2/L3 校验仍按原机制执行。

`user_preference_factor` 明确标记 `playback_history_status=not_connected`。带时间戳、不带时间戳的未验证历史输入均忽略，不改排序分；本轮没有实现时间衰减或宣称完成真实历史融合。相应边界已通过单测。

## A/B 分流与配置注入

`recommendation_experiments.py` 提供 `ExperimentConfig`、`ExperimentVariant` 和 `ExperimentAssignment`。每个变体绑定一套已验证权重与非负流量配比；至少两个唯一变体，总流量须正值。配比可写为 50:30:20 或等比例小数；零流量变体不分配用户。

旧灰度算法抽取为 `rollout.deterministic_bucket`，原 graph selector 使用相同盐、会话标识及计算方式，旧结果不变；新实验复用该基础，以 `curation-experiment:v1:{experiment_id}` 为独立命名空间，对现有可信 `MusicAgentContext.user_id` 分流。没有复制另一套哈希算法或修改任何灰度开关。

相同实验配置、相同 user_id 在不同调用和进程实例中得到同一变体；10000 桶的流量粒度约 0.01%。缺少可信 user_id 时不生成 guest ID、不改用 thread ID 或 cookie；标记 `missing_user_id`，使用调用方权重。实验变体权重优先，调用方权重是实验未分配时的 fallback。

配置例子（由可信 Python 调用方注入，非生产配置文件）：

```python
from app.agent.handoff.recommendation import RecommendationWeights
from app.agent.handoff.recommendation_experiments import ExperimentConfig, ExperimentVariant

weights = RecommendationWeights(user_preference=1, knowledge_association=4, scene_tags=1)
experiment = ExperimentConfig("curation-weights-v1", (
    ExperimentVariant("control", RecommendationWeights(), 50),
    ExperimentVariant("knowledge", RecommendationWeights(1, 4, 1), 30),
    ExperimentVariant("scene", RecommendationWeights(0, 0, 1), 20),
))
await execute_curation(..., weights=weights, experiment=experiment)
```

未传参时使用 getter；权重 getter 返回默认 4:2:1，实验 getter 返回 None，因此本轮没有开启线上 A/B 或改变运行配置。当前不提供前端配置界面或新增 HTTP 参数。

## 事件记录与隐私边界

每次进入有效推荐流程的结果通过既有 `observability.emit` 记录 `curation.recommendation`。字段包括实验 ID、变体 ID、分配状态、配置摘要、权重、UTC 时间、推荐上下文（候选数、实际推荐歌曲 ID、标签数量、是否冷门、任务状态）及播放历史未接入状态。

复用现有 request_key/thread_key 摘要与 event_id；不新增原始或哈希 user_id 日志字段，不记录原始查询、历史、红心 ID 列表、播放量或行为测量。实验/变体 ID 采用有界公共标识，沿用既有 private-text gate；没有新建用户画像、分组表、PG 推荐事实表或写工具。推荐歌曲事实仍由原网易云目录链提供。

该通道是现有结构化日志的事件记录，不是新增事务性 PG 日志或粘性存储，依赖原日志采集/留存策略；原 emit 的日志异常处理行为保留。未计算或采集红心命中率、播放完成率、收藏转化率。

## 验证及提交

| 验证 | 结果 |
| --- | --- |
| Gate 0 基线 | 176 passed |
| 权重/历史降级单测 | 14 passed |
| 实验 + 既有灰度/事件 + 权重 | 52 passed |
| 推荐链组合（含全部基线） | 207 passed，0 failed |
| 完整 `tests + evals/tests` | **2860 passed、288 subtests passed、12 skipped、363 warnings，0 failed/errors，退出码 0**；402.33 秒 |
| Ruff、Python 语法编译、`git diff --check` | 通过 |
| 373 个冻结文件及清单 | 摘要全部一致、本轮只读 |
| 原 prompt、预算、意图筛选、工具及 NicheConfig | 内容保持不变 |
| 新建 Python 文件 | 5 个均为 LF |
| 运行前后 8 份源码/测试摘要 | 一致 |

相较基线 2829 增加 **31 个用例**；12 个 skipped 的 nodeid 集合完全相同，未修改或弱化既有断言，未新增 skip。完整执行使用隔离 dotenv、受控模型/目录、独立真实 Redis 和一次性 loopback PG，4 workers、loadfile、完整堆栈、实时 fsync 落盘日志。正常完成，没有人为中断。

完整命令（工作树 `ai-service` 目录）：

```powershell
& 'D:\code\ProjectsAll\MoodFlow\ai-service\.venv\Scripts\python.exe' -B ../.dev/curation-weights-ab-20261011/run_validation.py curation_weights_full tests evals/tests
```

实现提交：`d2388de`（权重与红心降级），`dad9439`（推荐链、哈希复用和实验事件接入）。仅在 `codex/curation-weights-ab`，未合并、push 或部署；基线分支与原 journal 工作区的 HEAD 和用户修改保留。生产 `.env`、默认图、灰度及其他运行配置未改。测试 PG 已按完整 ID 验证后清理，未触碰其他容器。

证据：[完整输出](curation_weights_full.log)、[最终统计与 skipped 清单](curation_weights_full.json)、[组合回归](curation_weights_integration.json)、[运行后摘要](integrity_after.json)、[汇总](verification.json)、[环境清理](cleanup.json)。原始日志保留在同名 `.log.gz` 归档中，解压字节与执行器日志一致；可读副本仅规范换行/行尾空白，摘要见 [log_archives.json](log_archives.json)。

诚实边界：**播放历史未接入；A/B v1 无粘性存储；没有推荐效果评估指标采集或计算。** 修改实验 ID、变体顺序或流量配比后可能重新分组，配置摘要用于区分记录。同一变体权重改变会改变排序；本轮仅验证配置、分流、记录和排序行为，没有线上实验结果或效果提升结论。既有 12 个外部依赖 opt-in 路径仍未验证，本轮只改 Python Agent 代码，没有重新验证无改动的 Java/前端构建。

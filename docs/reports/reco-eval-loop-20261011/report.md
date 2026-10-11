# 推荐效果评估闭环：v2 交付报告

基线 `codex/curation-weights-ab`，实际 HEAD `646fc1db082fffbdd0daf26a5974d66cb391c0db`。Gate 0 全部满足 v2 口径后，从该 HEAD 新建 `codex/reco-eval-loop`；实现提交 `103d4aa`（指标与只读回放）、`b8ec5ec`（发射时快照）。未合并、push、部署。

## Gate 0

| 项目 | 结论 | 源码/执行证据 |
|---|---|---|
| 1. 推荐事件 | 沿用 v1 通过结论 | 基线 recommendation_experiments.py:94–104 定义实验/变体、配置摘要、权重、候选数、song_ids、请求级 niche_requested 和状态；observability.py:35–58 增补 event_id、UTC 时间与已有会话摘要。Curator finish 用已校验 selected IDs 发射。 |
| 2. 点赞时间事实源 | 沿用 v1 已证伪结论，符合 v2 的受限口径 | app/schemas/music.py:268、277、315：设置响应无状态转换信息，当前红心响应只有 ID；app/tools/music.py:355、570 只读当前集合。history_writer.py:171 是回执观测时间，不是 liked_at；write_reconciliation.py:106 只确认当前状态。未补造历史时间。 |
| 3. 播放完成源 | 通过：未接入，交付 schema 与 not_connected | soul-music-room/src/store/playerStore.ts:667–679 只更新当前进度；:702–720 的 ended 只循环/停播/切下一首，没有持久化事件。对 app、Java 与前端源码搜索 play_completion/playback_completed/播放完成等无已接入指标事件源。 |
| 4. 评估基建 | 通过：复用既有能力 | evals/README.md:79 起有确定性组件/会话回放、JSON 与 Markdown 报告和 synthetic/real 区分；worldline_eval_report.py:11 复用分子/分母及 N/A 渲染。沿用独立 evals 模块、Pydantic 契约、全新 reports 目录、失败审计与现有隔离测试执行器。DeepEval 仍用于既有语义 Judge，本轮确定性指标无需另建 Judge 或新增依赖。 |
| 5. 测试基线 | 通过：233 passed，0 failed/skip | [基线统计](reco_eval_gate0_baseline.json) 的 targets 为完整命令清单；[完整输出](reco_eval_gate0_baseline.log)。基线运行时分支/HEAD 已确认，尚未修改代码、建分支。 |

## 实现范围

指标定义、配置、随机 holdout、转化窗口计算和只读 Journal adapter 位于 `ai-service/evals/recommendation_metrics.py`。默认 holdout 20%、种子 20261011、窗口 7 天；默认值可由独立配置注入，不改运行设置。验证集与偏好输入无交集；每组输出分子、分母和样本量，零分母为 N/A。

`recommendation_eval_runner.py` 提供保存目录响应的排序阶段 CLI，以及完整 Curator 读阶段的异步回放。后者原样复用召回、知识扩展、预算、模型选曲、目录与 ID 校验，在调用任何业务写实现前拒绝执行，读取已校验的 selected_songs；不伪造创建/加歌成功。回放使用独立进程、全新一次性 Journal 与干净任务状态，不能携带包含完整红心偏好的旧上下文。当前红心只作为切分来源，回放中的红心读取只返回训练集合，评估事件不会混进线上推荐日志。

唯一生产侧改动在 Curator 结果发射及 recommendation_experiments：发射前通过现有 MusicServiceClient 的 get_liked_song_ids 做一次只读快照，记录每首推荐的 was_liked_before、快照版本/来源/UTC 时间。成功读取才能给 true/false；失败给 null，旧事件缺字段也按未知处理。快照不进入排序、模型输入、业务工具预算或 TaskResult。原工具实现、权重、召回规则、预算护栏、提示词、运行配置、默认图、灰度、Java 和前端均未修改。

在线作业只读调用既有 store 的 query_task_history/get_artifact，验证回执成功、response/readback 来源、授权 scope、任务归属、artifact 摘要及 song_id/liked 一致性。按原 ACK 的会话规范化/digest 连接日志，按实验/变体/配置摘要聚合。只写新评估报告，不初始化 PG/DDL，不新增业务表或写工具。

三个指标的分子、分母、来源、时间与边界见 [metrics.md](metrics.md)。播放完成只提供 `PlaybackCompletionEvent` 契约，报告始终为 not_connected/null；不拿播放器状态、客户端 ACK 或播放请求充当完成事件。

可供后续独立 plumbing 使用的 [播放完成事件 JSON Schema](../../../ai-service/evals/reports/recommendation_eval_v1_examples/playback_completion_event.schema.json) 已导出，本轮没有接入或计算。

## 验证

| 检查 | 结果 |
|---|---|
| Gate 0 既有相关基线 | 233 passed |
| 新增测试第一次执行 | 1 failed / 17 passed：新增回放 fixture 的 limit=10 不符合原 Curator limit=50 契约；完整用例名和堆栈见 [输出](reco_eval_new_tests.log)。仅将自有 fixture 改为合法 limit=50，原校验、断言和既有测试未改。 |
| 修正后新增测试 | 29 passed；随后补充完整读阶段、readback 与身份校验 3 项 |
| 组合回归（全部基线 + 新增） | **265 passed，0 failed**；[统计](reco_eval_targeted.json) |
| 全量 tests + evals/tests | **2892 passed、12 skipped、288 subtests passed、372 warnings，0 failed/errors，退出码 0**；401.04 秒；[完整输出](reco_eval_full.log)、[统计](reco_eval_full.json) |
| 新增用例 | 32；相较基线全量 2860 精确增加 32 |
| 跳过集合 | 与基线完全相同，没有新增 skip/弱化断言 |
| Ruff、语法解析、git diff --check | 通过 |
| 373 个冻结文件 | 原始字节摘要全部一致，清单摘要一致，本轮只读 |
| 推荐算法/权重/原工具/预算/配置内容 | 与基线一致；[完整核对](integrity_after.json) |
| 新建 Python 文件 | 4 个全部 LF；源文件摘要在全量运行前后完全相同 |

执行命令（专用工作树 ai-service 目录）：

```powershell
& 'D:\code\ProjectsAll\MoodFlow\ai-service\.venv\Scripts\python.exe' -B ../.dev/curation-weights-ab-20261011/run_validation.py reco_eval_gate0_baseline <reco_eval_gate0_baseline.json 中 targets>
& 'D:\code\ProjectsAll\MoodFlow\ai-service\.venv\Scripts\python.exe' -B ../.dev/curation-weights-ab-20261011/run_validation.py reco_eval_targeted <reco_eval_targeted.json 中 targets>
& 'D:\code\ProjectsAll\MoodFlow\ai-service\.venv\Scripts\python.exe' -B ../.dev/curation-weights-ab-20261011/run_validation.py reco_eval_full tests evals/tests
python -m ruff check app/agent/handoff/curator.py app/agent/handoff/recommendation_experiments.py evals/recommendation_metrics.py evals/recommendation_eval_runner.py evals/tests/test_recommendation_eval.py tests/test_recommendation_like_snapshot.py
git -c core.safecrlf=false diff --check
```

测试使用无 dotenv、无真实模型调用的隔离执行器，真实 Redis 测试与一次性 loopback PostgreSQL。4 workers/loadfile，输出实时 fsync，完整堆栈和最终统计均落盘，正常完成无人工中断。`.log.gz` 为原始输出逐字节归档，可读 `.log` 仅规范换行/行尾空白；[摘要与校验](log_archives.json)。[verification.json](verification.json) 汇总计数和范围。

## 报告样例与诚实边界

- [离线样例](../../../ai-service/evals/reports/recommendation_eval_v1_examples/offline/report.md)：固定 seed=20261011，15 首当前喜欢中 12 首输入、3 首 holdout，3/3 命中；复用完整 Curator 读阶段，在首次写入前被拒绝，外部业务写入为 0。
- [在线样例](../../../ai-service/evals/reports/recommendation_eval_v1_examples/online/report.md)：control 为 1/1，已红心 1 首排除；treatment 为 0/1。使用一次性 SQLite Journal 的受控确认回执验证连接与分母，不是线上 A/B 结果。
- 两份均为 **synthetic_contract_fixture**。样例在提交前的工作树运行，报告 git_commit 是基线锚点；实际执行源码与最终实现字节一致，由 integrity_before/after 的摘要绑定，不能把样例读作基线代码的效果测量。
- 离线指标测偏好恢复，不测时间泛化，不证明冷门发现质量；候选覆盖、推荐数量和当前喜欢集合的存活偏差均影响结果。
- 在线指标是“发射快照未红心 + 窗口内确认回执”的可观测曝光比率，回执时间不等于状态转换时间。用户可能独立收藏；没有因果归因、跨会话用户追踪或外部客户端行为覆盖。
- 快照读取与发射不原子，存在短暂竞态；新增一次读取及现有 HTTP timeout 内的延迟。快照异常不改选曲或任务成功/失败状态，未知样本从分母排除。
- 未结束的窗口排除；重复曝光各自计数，同一回执可能满足重叠窗口。旧日志无快照不能追补，日志丢失/保留由现有采集体系决定。
- 播放完成未接入；A/B 无显著性检验、无粘性存储。12 个原有外部模型/Qdrant/特殊 PG 重启 opt-in 用例仍未执行；未运行真实用户质量评估。Java/前端未改，未重复其构建。

没有读取或修改生产 `.env`、凭证、默认图与灰度配置；没有新增依赖、业务数据源、PG 业务表、写工具或前端。原 MoodFlow 主工作区仍在 `codex/journal-checkpoint-recovery` / `787598fbd557ab28bbcce9ac73666c914d83d0ad`，原有未提交修改保留。仅清理本轮独立测试 PG 容器，未触碰其他容器；清理证据见 cleanup.json。

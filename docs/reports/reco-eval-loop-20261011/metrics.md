# 推荐效果指标 v1：受限可观测口径

配置由 `EvaluationConfig` 注入：`holdout_ratio=0.2`、固定 `seed=20261011`、`conversion_window_days=7`、预留 `completion_fraction=0.9`。合法 holdout 比例为 [0,1]，窗口必须为正，完成阈值为 (0,1]；不修改运行配置或推荐权重。

## 离线历史红心命中率

- 事实源：`get_liked_songs` 的当前云端红心 ID 集合，无 `liked_at`。
- 将 ID 去重并排序，使用局部固定种子的随机生成器抽取 `floor(N × holdout_ratio)` 首作为验证集；剩余红心作为唯一偏好输入。
- 分子：推荐 ID 与验证集的去重交集大小；分母：验证集大小。返回分子、分母、样本量及比率；空验证集为 N/A，不填 0，不调用推荐器。
- 空偏好输入仍按真实空集合调用只读推荐器，允许目录/知识信息产生候选；绝不把验证集放回偏好。
- 完整读阶段适配器复用原 Curator 的召回、知识扩展、预算组装、模型选曲与结果校验，在创建歌单前拒绝所有业务写入，并只读取已校验选曲。需要独立评估进程与全新一次性 Journal；拒绝写入不是伪造成功。
- 轻量 CLI 复用原目录校验及排序、选曲顺序函数，回放独立保存的搜索响应；该模式只覆盖排序阶段，报告显式标记范围。目录响应遵守原 `limit=50` 契约，不按验证标签增加候选。
- **含义：偏好恢复的 sanity check；不测时间泛化，不证明冷门发现质量。** 当前喜欢集合本身有存活/取消收藏偏差；候选覆盖与推荐数量影响命中率，100% 合成例子不代表真实效果。

## 在线收藏转化率

- 事实源：`curation.recommendation` 发射时新增的一次云端只读红心快照，以及 OperationJournal 的确认红心回执。
- 每首推荐记录 `was_liked_before=true/false/null`；仅记录推荐歌曲对应的布尔值，不记录完整红心集合、原始用户 ID、Cookie 或新增用户摘要。
- 快照版本：`recommendation-like-snapshot-v1`，来源 `get_liked_songs`，记录读取开始及观测 UTC 时间。`available` 才表示读取成功；读取失败为 `unavailable` + null，空推荐不发请求。旧事件缺字段也按未知排除。
- 分母：任务状态 success、快照 available、`was_liked_before=false`，且完整观察窗口已结束的推荐歌曲曝光数。
- 分子：上述曝光中，存在同一授权会话、同一歌曲，且时间落在 `(推荐 at_utc, at_utc + N 天]` 的 `liked=true` 确认回执的数量。
- 确认来源只接受 response/readback；回执必须引用同任务、摘要有效的结果 artifact，且工具名、song_id、liked、synced 状态与参数一致。proposed/failed/unknown、取消红心、缺证据、身份不匹配均不计。
- 回执时间表示**确认/观测时间**，不是实际状态转换时间。readback 可能晚于原操作；不补造 `liked_at`。
- 分组键为实验 ID、变体 ID、配置摘要；配置不同不混池。每组输出分子/分母、样本量、事件数及排除计数。按曝光计算，重复采集的同 event_id 去重；相同 ID 内容冲突拒绝计算。
- 会话连接沿用既有 ACK 的 conversation 前缀规范化与 digest。调用方提供已授权的 Journal identity/auth_scope；不从公开日志推断用户，不跨会话追踪用户。
- 重复推荐是独立曝光，重叠窗口中同一回执可能满足多次曝光；指标单位必须随报告说明。未结束窗口排除，以避免右删失直接被记为不转化。
- **归因边界：用户可能独立搜索或收藏，不宣称推荐导致收藏。** 外部客户端行为和没有 Journal 回执的行为不可见；该指标只覆盖有快照、有会话连接和确认回执的可观测样本。
- 快照在发射前读取，不能与事件发射原子化；两者间存在短暂竞态。增加一次读取及已有 HTTP 超时范围内的延迟；失败不改变选曲、工具预算、已有写回执或任务结果。

## 播放完成率

- 定义：达到配置完成比例的有效播放次数 / 有效开始播放次数，时间范围由评估配置控制。
- 预留 `PlaybackCompletionEvent`：event_id、playback_id、既有 graph_mode/thread_key、UTC 时间、song_id、实验/变体、实际收听秒数、歌曲时长。
- `play_completion_status=not_connected`，`play_completion_rate=null`。没有采集器、计算器或虚构事件；播放器 ended、播放请求或客户端 ACK 均不充当播放完成事实。

## 分析边界与使用

v1 输出分组点估计与样本量，不做显著性检验；沿用 A/B 无粘性存储的限制，配置变更可能重新分配。评估不调权重，不改变推荐算法，不创建 PG 业务表、用户画像或写工具。

离线 CLI（在 ai-service 目录，以已配置且获授权的评估环境运行）：

```powershell
python -m evals.recommendation_eval_runner --cases <只读响应样本.json> --output evals/reports/<全新目录> --holdout-ratio 0.2 --seed 20261011
```

输入为带 `dataset_kind` 的 cases：每项提供 liked_ids、目录搜索响应 catalog_response，以及来自原链的 knowledge_ids/tags、可选权重和实验/变体。目录样本必须独立于 holdout 标签；人工构造数据必须标注 synthetic_contract_fixture。报告写入全新目录，拒绝覆盖旧文件。

完整读阶段使用 `offline_replay_async` + `replay_curator_read_phase`，传入既有 task/state/runtime/model/tools、全新一次性 Journal 与配置。当前红心集合只用于切分；回放中的红心读取被替换为训练集合，避免再次读完整集合泄漏标签。模型调用、目录 GET 与知识读取沿用原链；所有业务写入在调用实现前被拒绝，评估事件不混入线上推荐日志。

在线批处理使用 `run_online_job(log_path, existing_store, authorized_identity, expected_auth_scope=..., observed_until=..., output=...)`。只查询既有 store 的历史与 artifact，不初始化 PG/DDL，不回放写入；caller 必须先取得既有授权。输入支持结构化日志或推荐事件 JSONL，输出 JSON + Markdown。日志保留/丢失沿用现有通道，不能宣称完整捕获所有曝光。

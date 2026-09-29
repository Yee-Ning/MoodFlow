# Jev Router criteria 与 Capability Contract 对齐 V2

## 来源与实现

`evals/config/router_capability_contract.json` v1.0 是四条 route 的能力真值。`app/agent/decision/jev/capability_criteria.py` 在首次调用时读取并缓存该文件，检查版本、route key 集合、每条 route 的必需字段与非空内容，然后依次把 `core_capabilities`、`supported_intents`、`can_handle`、`requires`、`cannot_handle`、`positive_examples` 和 `negative_examples` 投影成 Choice criteria。`app/agent/decision/jev/router.py` 使用该投影；没有维护第二份 V2 能力文案。历史 A 组的四句旧 criteria 仅封存在评测脚本中，供同一 Gold V2 回放。

容器镜像明确复制同一 JSON 文件至 `/app/evals/config/router_capability_contract.json`。缺失、route key 不匹配或必需字段为空时投影抛错，不静默降级到另一套手写描述。投影保持旧 Choice 的 route 键顺序和原有 question instructions；生产仍使用原来的 `jev-latest`、5 秒超时、零 SDK 重试、Choice、0.8 判定阈值与 Shadow Mode。

| Route | Contract 投影出的核心边界 |
| --- | --- |
| CurrentSong | 以当前播放歌曲及 SongContext 中的歌词/元数据为前提，处理主题、情绪、叙事与审慎听感；普通和声、编曲等概念不进入此路。 |
| ToolCalling | 搜索音乐目录、读取歌词/歌单、执行支持的音乐服务或播放器操作；知识解释不构成工具调用；已排队的浏览器命令不等于执行成功。 |
| GeneralChat | 无工具、无当前歌曲上下文、无实体证据图时的普通概念解释与开放对话；可审慎讨论假设，但不能核实实时事实或宣称具体反事实史实。 |
| Worldline | 以可解析的 song/artist/album 为根，探索证据、关系图、引用和 Scene/Influence 等支持的关系；不接普通百科定义、播放器动作或历史反事实模拟。 |

GeneralChat ↔ Worldline 的界线来自 Contract 的 `supported_intents`、`can_handle`、`cannot_handle` 和正反例：**具名实体本身不够，还需探索证据或关系；普通音乐概念不需关系图。** 反事实问题不因提及歌手/专辑就进入 Worldline。当前 Contract 明确将 `Counterfactual simulation` 标为 C，并在 Worldline `cannot_handle` 与 `negative_examples` 中排除。

## 成对边界样本

Gold V2 新增七对、十四条 Single 样本。四对集中在概念解释与实体关系探索，另三对分别比较普通伤感原理与当前歌曲听感、音乐服务搜索与 Worldline 探索、播放动作与当前歌曲分析。样本 ID 和全文见 `router_gold_v2_review.md` 及 `datasets/router_eval_gold_v2.jsonl`。这些新样本在结果中单独标注，不能当作 Gold 修正收益；它们与 Contract 正反例有语义重合，因此 100% 命中也不能证明泛化稳定。

## 验证与限制

单元测试检查 Contract 投影、字段修改传播、无效 Contract 失败、生产 `compare_route` 使用投影、Shadow 保留及镜像复制路径。评测仅比较 Jev 提议的 Single Choice；不切换正式路由、不改变 Subgraph 能力、不调 Multi、Noul 或阈值。Contract 中标为 B 的真实浏览器完成、代表性 Worldline 在线链路等仍须沿用其当前验收状态；criteria 文案不代表这些能力已升级为 A。

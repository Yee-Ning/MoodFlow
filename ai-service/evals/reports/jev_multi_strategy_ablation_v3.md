# Jev Multi selection strategy ablation V3（dev only）

## 冻结与实验边界

`multi_dev_v3.jsonl` 41 条，SHA256 `4d5eba1b34142593fde46997e355fdea62885f43b48de3988d7f81129174eb0c`；Contract SHA256 `238d373ff43db0eea72dae17525525276a666c3819f959de8925206b0915a97c`。其中 11 条为 Gold V2 历史 Multi 种子，22 条为新增真 Multi，5 条为单意图 guard，3 条为部分不支持或实体模糊。所有 Gold 在 Jev/Hybrid 调用前冻结。`multi_blind_test_v3.jsonl` 未参与本报告的调参或选择。

Jev TypeSafe SDK 的一次 System One 调用提交四个独立问题。**Noul** 记录每条 route 的 yes 概率；**Score** 记录各 route 的 0/1/2 有序 rubric 分布，并以 `P(score=2)` 作为可选阈值证据。四路提问中的能力描述直接引用已冻结 Contract V2 投影；无 query 关键词规则。实际响应模型均为 `jev-1.13.0`，alias `jev-latest`、5 秒超时、零重试、Shadow Mode 均不变。生产 Single Choice criteria 和 0.8 阈值没有修改；这些多选策略只针对 Multi 候选流程，本数据集的单意图 guard 用来检验误判风险。

## 选择方法与结果

共比较 143 个 dev 配置：全局阈值、逐路阈值、Top-K 加最低分、Noul 原生 yes 概率 ≥0.5。阈值只在 dev 的 0.10–0.90 网格中搜索；逐路阈值从 dev 最好的三个全局方案分别做坐标搜索两轮。先排除在 Exact Match、Micro F1、over、under 四维均被支配的配置；12 个候选留在 Pareto 集，再按预先实现的效用 `.45 × Exact + .35 × Micro F1 − .10 × Over − .10 × Under` 选出配置。该准则同时惩罚漏路由与多路由，而非只求 Recall。

| Head / 策略 | dev 配置 | Exact | Micro F1 | Over | Under |
| --- | --- | ---: | ---: | ---: | ---: |
| Score / 全局阈值 | 0.20 | 82.93% | 95.60% | 12.20% | 4.88% |
| Score / 逐路阈值 | C .15, T .20, W .20, G .25 | 87.80% | 96.86% | 9.76% | 2.44% |
| Score / Top-K + 最低分 | K=3, min .20 | 82.93% | 95.60% | 12.20% | 4.88% |
| Noul / 全局阈值 | 0.30 | 90.24% | 97.47% | 7.32% | 2.44% |
| **Noul / 逐路阈值（冻结选择）** | **C .30, T .25, W .45, G .50** | **95.12%** | **98.73%** | **4.88%** | **0.00%** |
| Noul / Top-K + 最低分 | K=3, min .30 | 90.24% | 97.47% | 7.32% | 2.44% |
| Noul / 原生多选 | yes ≥ .50 | 87.80% | 96.73% | 2.44% | 9.76% |
| Hybrid control | 现有三层 Router 原样 | 43.90% | 74.82% | 21.95% | 48.78% |

冻结配置精确保存在 `evals/config/jev_multi_frozen_strategy_v3.json`，包括数据集、Contract、dev 逐条结果哈希与选择时刻。blind runner 只读取该文件，不包含调参逻辑。所有 41 条的 Hybrid 输出、Score/Noul 独立证据、模型版本、耗时与 token 在 `jev_multi_dev_v3_results.jsonl`；所有候选家族优胜者和完整指标在 `jev_multi_strategy_ablation_v3.json`。

## dev 错误与运行特征

冻结 Jev 配置 dev 为 **39/41**：`d-tw-02` 多选了 CurrentSong；`d-p-03` 对未解析的同名歌手多选了 Worldline。新增真 Multi 子集 Jev **32/33** Exact，单意图 guard **5/5**，部分不支持/模糊 **2/3**；Hybrid 对应 **17/33**、**0/5**、**1/3**。这仍是当前自编数据集的开发集结果，不能用于宣布生产架构胜者。

dev 运行中 Hybrid 41 条总共 42 次 LLM API 调用、76 次本地分类模型调用，输入/输出 token 57,200/2,097，wall P95/P99 为 10,643/10,874 ms。Noul 每条一次 API，共 41 次，输入/输出 token 52,513/3,034，wall P95/P99 为 702/1,015 ms；Score 为 57,269/2,542，P95/P99 为 969/1,259 ms。三组均无 API、schema 或 Router 异常。Hybrid 的 LLM 升级与 needs_clarification 在逐条结果中保留；Jev 实验没有部署级失败回退，出现 Jev 失败时纯 Jev 指标将计空路由。

## 后续 blind 约束

本次只选择实验配置，不接入生产。blind 测试须在配置提交后运行，每个系统对同一 24 条数据各重复 3 次；看到 blind 后不允许调整阈值、Gold 或提问。独立 Single blind 只验证未变的 V2 Choice，不参与上述搜索。

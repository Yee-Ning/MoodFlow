# Jev Multi vs Hybrid Router head-to-head V3

## 冻结口径

`multi_blind_test_v3.jsonl` SHA256 `137e9e3c479b8f1d3a309fbf1e03142c79b55dc4933d34f55bcb600c78dedc94`，24 条：15 条完整支持的多意图、5 条看似多意图的 Single guard、4 条带部分不支持请求或模糊实体。dev 的 41 条与 blind 无重复文本；11 条 Gold V2 历史种子仅在 dev。标签按 Capability Contract v1.0 在运行前冻结，新增 query 是人工撰写的真实使用风格，**并非生产日志或独立人工盲审**。

Hybrid control 是当前生产三层 Router 原样运行；Jev 实验为一次 System One 请求内的四个独立 Noul yes 概率。**在 blind 前已提交的冻结配置**为 CurrentSong ≥.30、ToolCalling ≥.25、Worldline ≥.45、GeneralChat ≥.50（commit `3243c92`）；blind 后未改阈值、Gold、Contract 或提问。每套系统对同一 24 条逐条重跑 3 次，调用先后交替。Jev 实际模型 `jev-1.13.0`，alias `jev-latest`，5 秒超时、零重试；生产仍处于 Shadow Mode。原始逐次结果保存在 `jev_multi_blind_v3_results.jsonl`，汇总在 `jev_multi_head_to_head_v3_metrics.json`。

**比较对象是 route set。** Hybrid 同时输出任务、输入片段和依赖关系；Jev V3 实验只输出路由集合，尚不能替代 MainGraph 所需的任务拆分、顺序依赖与澄清流程。部分不支持/模糊实体案例仅按可承接的 route set 计分，Exact Match 不代表整句请求已完成。Single guard 只用来测试多意图入口的误路由；正式 Single 仍走冻结的 Choice V2。

## Blind 质量：每轮 24 条，三轮 route set 相同

| 指标 | Hybrid control | Jev Multi 冻结策略 |
| --- | ---: | ---: |
| Exact Match | **9/24 = 37.50%** | **19/24 = 79.17%** |
| Micro Precision / Recall / F1 | .7500 / .7143 / **.7317** | .9302 / .9524 / **.9412** |
| Macro F1 | .7198 | .9376 |
| Over-routing | 9/24 = 37.50% | 3/24 = 12.50% |
| Under-routing | 11/24 = 45.83% | 2/24 = 8.33% |
| Route-count error | 11/24 = 45.83% | 5/24 = 20.83% |
| 平均绝对 route 数误差 | .5000 | .2083 |

| Route（blind support） | Hybrid P / R / F1 | Jev P / R / F1 |
| --- | ---: | ---: |
| CurrentSong（10） | 1.000 / .500 / .667 | .909 / 1.000 / .952 |
| ToolCalling（12） | .857 / 1.000 / .923 | 1.000 / 1.000 / 1.000 |
| Worldline（9） | .750 / .667 / .706 | .889 / .889 / .889 |
| GeneralChat（11） | .538 / .636 / .583 | .909 / .909 / .909 |

按样本类型切片（下表每种 case 在三轮中各重复三次，百分比与单轮相同）：

| Cohort | Hybrid Exact / Micro F1 / Over / Under | Jev Exact / Micro F1 / Over / Under |
| --- | --- | --- |
| 真 Multi（15 条） | 8/15，.8065，26.67%，46.67% | 12/15，.9539，6.67%，13.33% |
| Single guard（5 条） | 0/5，.4444，40.00%，60.00% | 4/5，.9091，20.00%，0.00% |
| 部分 unsupported / 模糊实体（4 条） | 1/4，.5455，75.00%，25.00% | 3/4，.8889，25.00%，0.00% |

## 重复稳定性

| 三轮指标统计（mean / min / max / population std） | Hybrid | Jev |
| --- | ---: | ---: |
| Exact Match | .3750 / .3750 / .3750 / .0000 | .7917 / .7917 / .7917 / .0000 |
| Micro F1 | .7317 / .7317 / .7317 / .0000 | .9412 / .9412 / .9412 / .0000 |
| Macro F1 | .7198 / .7198 / .7198 / .0000 | .9376 / .9376 / .9376 / .0000 |
| Over rate | .3750 / .3750 / .3750 / .0000 | .1250 / .1250 / .1250 / .0000 |
| Under rate | .4583 / .4583 / .4583 / .0000 | .0833 / .0833 / .0833 / .0000 |
| 每轮平均 wall latency，ms | 5622.34 / 5515.28 / 5738.01 / 91.13 | 668.16 / 653.94 / 676.48 / 10.10 |

两套系统在 24 个 case 中的跨轮 route-set flip 均为 **0**。Jev 的原始 route 概率并非完全相同，观察到单 route 最大三轮振幅为 **.09**；一些分数在阈值附近（例如 `b-3-01` 的 GeneralChat 最低 .50），三轮无翻转不能保证长期确定性。Hybrid 同样需要区分“输出稳定”和“输出正确”。

## 性能、调用与成本代理

| 三轮共 72 次 query | Hybrid | Jev |
| --- | ---: | ---: |
| Wall latency average / P50，ms | 5622.34 / 5169.10 | 668.16 / 597.57 |
| Wall latency P95 / P99，ms | **10885.71 / 11793.09** | **987.59 / 1253.21** |
| LLM/API calls 总量 / 每 query | 78 / 1.0833 | 72 / 1.0000 |
| 本地分类模型调用 | 159 | 0 |
| Input / output tokens | 107,847 / 3,984 | 92,523 / 5,328 |
| 总 tokens / 每 query | 111,831 / 1553.21 | 97,851 / 1359.04 |
| parse/schema failure | 0 | 0 |
| timeout/API failure | 0 | 0 |
| Router exception | 0 | 0 |

以**两家服务 input/output token 同价**且忽略 Hybrid 本地模型计算为规范化代理，Jev/Hybrid token 成本比为 **.875**。Jev 的 output tokens 较多，实际货币成本仍取决于各服务当期输入/输出单价、计费 tokenizer 与本地计算资源；本轮没有可比的正式账单，不能把 .875 当作真实支出比。Hybrid 全路径 wall latency 包含规则、CPU 分类模型、可能的 LLM 与构建任务；Jev 时间只覆盖 route-set 提议，因此性能数据也不等价于完整 MainGraph 端到端响应时间。

Hybrid 的 72 次调用中，48 次完整输出经历 LLM 升级，12 次需要澄清且没有可派发任务；这两类合计 60 次内部升级/回退或澄清。Jev 实验没有失败后的生产 fallback（本轮 API 失败为 0）；未来若考虑接入，必须另行设计失败回退和任务构建，本阶段不实现。

## 可归因错误

Jev 五个固定错误 case，每个在三轮都重复：

| case_id | Gold → Jev | 类型 | 分数/边界 |
| --- | --- | --- | --- |
| `b-tw-02` | T+W → C+T+W | over | 播放具名歌曲被误读为“分析当前歌”，C=.32，刚越过 .30。 |
| `b-3-02` | T+W+G → T+W | under | 独立 Art Pop 概念解释未入 G，G=.41 < .50。 |
| `b-3-03` | W+C+T → C+T | under | Fleetwood Mac 关系网未入 W，W=.41 < .45。 |
| `b-s-01` | C → C+G | over | 否定切歌后的单一当前歌分析又入 G，G=.55。 |
| `b-p-03` | T → T+W | over | 重名艺人未解析仍入 W，W=.47 > .45。 |

Hybrid 在部分 CurrentSong/Worldline 组合中把有据关系写成 GeneralChat，或在否定句、复杂请求上给出澄清/空 route set；逐轮对照与两边的 missing/extra routes 见 `jev_multi_failure_cases_v3.jsonl`。这些错误未用于重调阈值。Worldline blind support 仅 9，估计区间较宽。

## Architecture decision table：供人工选型

| 决策维度 | 现有 Hybrid Router | Jev Multi 实验 | 仍需判断 |
| --- | --- | --- | --- |
| 本次 route-set 质量 | 9/24 Exact，Micro F1 .7317；under 45.83% | 19/24 Exact，Micro F1 .9412；under 8.33% | 自编 blind 仅 24 条，需生产风格、人工盲审集复核。 |
| Over 与边界 | over 37.50%，guard 0/5 | over 12.50%，guard 4/5；仍有模糊实体误选 | 是否接受 Jev 对含糊实体与否定动作的剩余误路由。 |
| 速度 | P95/P99 10.89/11.79 秒 | route-set P95/P99 .99/1.25 秒 | Jev 后续任务拆分、执行和 fallback 的成本尚未计入。 |
| 调用与成本 | 78 次远程调用 +159 次本地模型调用；111,831 tokens | 72 次远程调用；97,851 tokens | 实际供应商计费不可从 token 比单独推出。 |
| 稳定性 | 0/24 route flip；稳定错误较多 | 0/24 route flip；概率有 ≤.09 振幅且稳定错误仍在 | 三轮样本不足以证明长期可靠性。 |
| 生产完整性 | 已有任务片段、顺序依赖、澄清、MainGraph 接口 | 只有实验 route set，无任务拆分与生产回退 | 若考虑 Jev，需另做安全接入设计和端到端验收。 |

**本报告不宣布架构胜者。** 当前数字支持继续研究 Jev Multi 的 route-set 能力，但未证明它可独立替换 Hybrid 生产路由。生产架构由人工结合完整任务构建、真实流量、服务可靠性和成本决定；本阶段不切换 Shadow、不改 MainGraph、Subgraph 或生产数据。

## 验证与限制记录

- `python -m pytest tests -q`：**1242 passed、14 skipped、228 subtests passed**。
- `python -m pytest tests/test_jev_multi_benchmark_v3.py tests/test_jev_multi_dataset_v3.py -q`：**9 passed**。
- 本阶段修改文件的 `ruff check`、`compileall`：通过；仓库范围 `ruff check app evals tests` 有 **41 处旧文件问题**，本次文件无告警，未在本轮修改无关模块。
- 数据集、Contract、dev 逐条结果和冻结配置的 SHA256 均由 runner 校验；blind 72 行与 Single blind 20 行已逐条落盘。机读汇总、失败集按冻结结果重算。没有在看到 blind 结果后重新发起调参或改写 Gold。
- 未构建或部署生产镜像，未验证 MainGraph 完整执行、真实播放器完成、在线音乐服务和 Worldline 图生成。本轮仅是架构选型实验；新增样本缺独立人工盲审，样本量不足以推断生产流量置信区间。

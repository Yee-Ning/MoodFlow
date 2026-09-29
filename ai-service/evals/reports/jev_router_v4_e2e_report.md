# Jev Router V4：完整决策 E2E Shadow 评测

## 范围、Gold 与复测标识

`router_e2e_blind_v4.jsonl` 是运行前提交的 24 条人工编写请求，SHA256 `216197f910ef0efc7acc44e790744ee0770bbac20f9d9f2e1420f9780a4fdf7c`，与 V3 dev、multi blind、single blind 没有重复 query。包含 7 条可执行 Single、12 条多任务、5 条需澄清，涵盖播放器动作、当前歌、Worldline、知识解释、三意图、否定、顺序、歧义和不支持请求。每条冻结 route set、精确任务片段、任务顺序、依赖索引与 clarify。Gold 由本任务人工编写和程序校验，**未经独立人工盲审，也不是生产日志**。

比较对象是相同 query、相同 `RoutingContext` 下的完整 Router `RouteDecision`；没有运行任何 Subgraph 或播放器操作。Hybrid 是当前生产 Router 原样输出，Jev 是 V4 candidate。逐条轮换先运行的一方；Router wall time 包含分句、Jev/Hybrid 调用、任务构建和必要回退，不包含 Subgraph、浏览器或网络动作执行。`jev_router_v4_e2e_first_pass_results.jsonl` 是代码修复前的唯一真正未见样本首轮；它保持原样。`jev_router_v4_e2e_results.jsonl` 是修复安全边界并补齐原 0.8 Single 门槛后的复测，**不能再视作独立 blind**。本报告优先用首轮判断泛化风险。

## 首轮 blind：完整决策与路由

| 指标，24 条 | Hybrid control | Jev V4 candidate |
| --- | ---: | ---: |
| Route Exact | 11/24，45.83% | 23/24，95.83% |
| Route Micro F1 / Macro F1 | .7500 / .7268 | .9842 / .9773 |
| Over / Under route | 8 / 6 | 1 / 0 |
| **完整决策 Exact**（route、任务、片段、顺序、依赖、clarify 全对） | 5/24，20.83% | **20/24，83.33%** |
| Task count accuracy | 45.83% | 91.67% |
| Task route / fragment accuracy | 60.61% / 42.42% | 96.97% / 87.88% |
| Dependency / ordering accuracy | 66.67% / 41.67% | 93.94% / 91.67% |
| Clarify precision / recall | 0 / 0 | 100% / 80% |

上述任务 route、片段、依赖以 Gold 任务数为分母并按原文位置对齐；任务数错时缺少的任务计为错误。Ordering 是每条请求的任务 route 顺序完全一致；完整决策 Exact 还要求片段和依赖精确一致。Route 集合正确但任务拆错仍为失败。四路首轮 Jev F1：CurrentSong 1.000（support 6）、ToolCalling 1.000（11）、Worldline .909（5）、GeneralChat 1.000（9）。12 条多任务的 route set 首轮 12/12，完整任务 9/12；这与 V3 的 24 条 route-set-only blind 不是同一抽样，不能把两个百分比直接相减当改进量。

## 首轮 blind：可靠性与 Router 时间

| 指标 | Hybrid | Jev V4 |
| --- | ---: | ---: |
| Router 平均 / P50 | 3826 / 4931 ms | 1542 / 645 ms |
| **Router P95 / P99** | 9487 / 10697 ms | **8569 / 11302 ms** |
| 远程 API 总调用 / 每 query | 18 / .750 | 39 / 1.625 |
| Input / output tokens | 24,853 / 875 | 52,412 / 2,879 |
| parse/schema failure | 0 | 0 |
| timeout | 0 | 1 |
| 其他 API failure | 0 | 0 |
| Hybrid fallback | 不适用 | 2/24，8.33% |

Jev 正常请求没有串行运行完整 Hybrid；一次 Noul 超时触发 Hybrid 回退，单条延迟 11.74 秒，导致首轮 Jev P99 高于 Hybrid。另一回退是 Noul 与片段 Choice route 冲突。多路正常路径并发发出两次 Jev API 调用，因此 token 和调用数高于 V3 只测 route set 的 1 次调用；超时请求中部分已成功调用的 token 可能因 SDK 失败路径没有 usage 回传而低估。24 条样本的 P95/P99 对单次超时非常敏感，不能宣称稳定的生产尾延迟优势。

## 首轮错误与修复后复测

| case | 首轮问题 | 后续状态 |
| --- | --- | --- |
| `v4-10` | “再梳理”未被识别为顺序词，Worldline 片段保留“再”且漏依赖。 | 通用逗号+“再”解析修复；复测正确。 |
| `v4-21` | 无实体锚点的“他”被直接派到 Worldline，clarify 漏检，存在错误探索风险。 | 通用代词安全检查修复；复测安全澄清。历史 Single 回归曾因“其他”误澄清，随后修正。 |
| `v4-22` | Jev API 超时触发 Hybrid 回退；Hybrid 任务片段多了前置“再”，完整决策失败。 | 回退如设计运行；复测未出现超时，不能证明超时率为零。 |
| `v4-24` | Noul 同时选 CurrentSong .84、ToolCalling .90，而两个片段的 Choice 指向 ToolCalling；冲突回退 Hybrid，后者合并两个应顺序执行的动作。 | **仍未解决**；route set 对，但任务 Exact 错。 |

结构化失败行见 `jev_router_v4_failure_cases.jsonl`，含首轮 4 条与最终复测 1 条。数据与阈值在看到结果后均未改；代码的安全/分句修复是首轮错误驱动，所以复测只能验证修复，不能作为新的 blind。

最终代码复测：Jev Route Exact 24/24、完整决策 23/24、任务数 .9583、任务 route .9697、片段 .9394、依赖 .9697、顺序 .9583、clarify P/R 1/1；Hybrid Route Exact 11/24、完整决策 5/24。Jev fallback 1/24（任务冲突），Router 平均/P50/P95/P99 为 **779/575/1009/3734 ms**；同轮 Hybrid 为 1275/1442/3315/4227 ms。Jev 38 次 API（1.583/query）、54,170 input 与 3,004 output tokens；Hybrid 18 次 API（.750/query）、24,853 input 与 911 output tokens。复测的好成绩受到已见样本修复影响，正式切换依据仍需新独立样本与真实匿名 shadow 流量。

冻结 V3 Single blind 的候选回归最终为 **20/20 route Exact、0 fallback**；修复前曾为 19/20，因为“其他”被误作“他”。V2 Choice criteria、V3 Noul criteria/阈值及 Gold 均未调整。V4 多任务首轮 route 12/12，优于 V3 route-set benchmark 的 19/24 只可看作不同样本上的观察，不能推断真实提升。

## 生产切换结论

候选可以产出通过现有 MainGraph schema 校验的完整 `RouteDecision`，并能表达多任务与依赖；但它尚未证明可安全接管生产。阻塞项是：`v4-24` 的同 route 多动作回退合并、无依赖任务当前仍串行执行、未在 Jev 派发模式下完成 Subgraph/浏览器 E2E、缺少独立人工盲审的新集与真实 shadow 流量、尾延迟受超时和回退波动影响。保持 Shadow Mode 与 Hybrid，不宣布架构胜者。

## 七项明确结论

1. **MainGraph-compatible？** 是。候选输出的 `RouteDecision` / `RouteTask` 均通过现有 Pydantic 校验，MainGraph shadow 测试确认候选不会派发；尚未做 Jev 真实 Subgraph 执行验收。
2. **Multi task splitting 正确？** 首轮多任务完整 9/12，修复后同集 11/12；`v4-24` 两个 ToolCalling 动作仍合并，不能称全面正确。
3. **Dependency/order 可靠？** 首轮依赖 .9394、顺序 .9167，复测 .9697/.9583；一条顺序词解析错误已修，复杂省略和当前 MainGraph 串行调度仍待验。
4. **Clarify 安全？** 首轮 P/R 1.0/.8，一条未锚定“他”漏澄清；修复后同集 1.0/1.0。具名重名实体仍依赖下游解析，没有真实流量安全证明。
5. **Hybrid fallback 正常？** 是，首轮一次超时、一次任务冲突均回退；单元测试覆盖无效/低置信度路径。回退后的任务拆分可能仍错，如 `v4-24`。
6. **完整 Jev Router P95/P99？** 首轮 **8.569/11.302 秒**；修复后同集最终代码 **1.009/3.734 秒**。后者非 blind，尾部受超时和回退显著影响。
7. **生产切换 blocker？** 剩余任务合并错误、缺独立盲审及真实 shadow、未验证 Jev 派发的 Subgraph/浏览器链路、未证明稳定尾延迟与成本、无依赖任务目前仍串行。

## 验证

- `python -m pytest tests -q`：**1258 passed、14 skipped、228 subtests passed**（最终 0.8 门槛补齐后）。
- `python -m pytest tests/test_jev_router_v4.py tests/test_jev_router_e2e_v4.py -q`：16 passed。
- 本次修改文件 `ruff check` 与 `compileall`：通过；仓库范围 `ruff check app evals tests` 仍有 41 处与本次文件无关的既有问题。
- 未执行候选的真实 Subgraph、服务 API 或浏览器播放器副作用；shadow 候选只产出决策。

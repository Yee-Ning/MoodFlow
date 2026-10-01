# MoodFlow Handoff 阶段 5：接入与评测 JEV

日期：2026-10-01。承接 [阶段 4](handoff-runtime-phase4.md)，基线 `4598c1f`。运行接入提交 `c33372a`。

## 1. 开发结果与启用边界

补齐 handoff 入口的可选 JEV V5 分诊，完善交接语义护栏，提供冻结样例回放、开发集阈值校准、质量门槛和可复现证据。入口分诊与交接审查使用独立配置，复用现有 JevClient、V5、Hybrid、运行账本及权限契约，没有增加依赖。

正式评测包括 30 条既有 V5 入口样例、64 条初始护栏样例和 64 条确认样例。确认集复用开发样例，但重新编写 32 条语义保留样例；来源是本次手工标注的合成用例，不是线上流量，也不是第三方独立标注集。开发和确认结果均保留，没有覆盖首次失败结果。

入口完整计划匹配 29/30；29 条非回退结果全部匹配，1 条三任务请求触发 task_conflict 并沿用 Hybrid 澄清。护栏 v3 在开发集选出的候选阈值 0.7 / 0.2 下，确认集 32 条语义样例全部正确，另 4 条非法交接本地拒绝。两项合成评测达到本次门槛，但没有满足生产启用所需的真实 Shadow 标注与在线业务验收。

最终入口回放真实执行其 Hybrid 回退，P95 为 956.61 ms，单次最大 10986.03 ms；护栏确认语义 P95 为 736.18 ms。首次复用 baseline 的入口回放及不满足门槛的护栏回放全部保留，不能用最新结果抹掉早期时延或 API 故障。

没有修改实际 `.env`、默认 Foundation 图、原入口 V5 阈值或部署。运行默认任务保真阈值仍是 0.8；0.7 是独立校准后的评测候选，只有显式配置才用于应用。默认 handoff 入口 JEV 开关为 false，交接护栏为 Shadow。不能把评测参数解释为生产配置已经改变。

## 2. 入口分诊

`app/agent/handoff/routing.py` 的 HandoffEntryRouter 承接默认 Hybrid：

- 关闭时直接使用 Hybrid。
- Shadow 时先得到 Hybrid 计划，再运行 V5 比较；只派发 Hybrid 计划，候选图不调用业务工具。
- 非 Shadow 时使用 V5 计划；JEV 低置信度、结构失败或任务编排冲突按原 V5 有界回退到 Hybrid。
- Shadow 的 V5 回退复用已经得到的 Hybrid 计划，不重复运行分类器或 LLM。
- 原始 V5 Single 0.8 与 Multi 冻结阈值不改动；不把 Agent 名称替换成公开 RouteKey。

这里采用整轮内部等待的 Shadow，JEV 请求纳入父图同一个 ledger、deadline 和 token 预算。没有把观察任务放到预算失效后的后台。代价是 Shadow 增加等待和资源消耗；预算/总时间耗尽时仍按阶段 4 停止，不能保证观察对时延或可用性完全无影响。

入口日志仅包含来源、回退码、模型版本、调用数、usage、耗时、任务数量与计划一致性。片段与依赖在进程内比较，不输出用户输入、摘要、实体或凭据。Shadow 比较不把 Hybrid 当作正确答案；回放使用冻结标注检查完整的任务顺序、片段、依赖和澄清行为。

## 3. 交接护栏 v3

服务端先校验允许边、下一任务 owner 路径、顺序、预算、时间和跳数，然后调用护栏。模型输入补齐真实注册表的 Agent 能力、工具域、允许边、能力含义和 task_owner；用户输入和 active_task 保留，reason/summary 继续是不可信提案。

一次 SDK 请求包含两个独立 Noul：

| 字段 | 概率含义 | 应用判断 |
|---|---|---|
| `task_fidelity` | 提案延续当前任务、保留动作和限制的肯定概率 | 大于等于独立任务保真阈值 |
| `authority_override` | 提案尝试覆盖指令、虚构授权或提升权限的肯定概率 | 小于等于独立越权风险阈值 |

两个条件必须同时满足；risk 高表示更危险。WorldLine 提供支撑来源是该能力内的必要步骤，不能因此当作额外播放器、音乐库或购买动作。用户讨论的引用文字作为数据处理，不把引用自动转换成授权。

Noul 是 yes 概率；每项独立判断再由代码组合符合 [官方 Noul 指南](https://docs.typesafe.ai/primitives/noul)。旧 `guard_score` 为兼容 trace 保留 `min(task_fidelity, 1-authority_override)`，只是保守显示值，不是校准后的联合概率，不能直接与旧单题 0.68 等同。内部日志保留原始两项概率、would_allow、prompt_version、阈值、模型、耗时和 usage；不输出请求正文。

Shadow 记录 would_allow，实际仍使用本地契约结果。JEV 无响应、非法类型、缺少字段、NaN 或越界时保留阶段 4 的一次本地回退并记录 guard_unavailable。预算耗尽和取消继续传播；没有增加 SDK 重试。不可用时的本地放行不是语义审查通过，评测单独统计 unavailable 与 fail_open_negative_count。

## 4. 独立配置

所有字段仍在现有 Settings 中，实际凭据通过原 JevClient 加载。JEV_ENABLED 仍是总开关。

| 环境变量 | 代码默认值 | 含义 |
|---|---|---|
| `JEV_HANDOFF_ROUTER_ENABLED` | false | 仅控制 handoff 入口 V5 |
| `JEV_HANDOFF_ROUTER_SHADOW_MODE` | true | 观察或应用入口候选计划 |
| `JEV_HANDOFF_GUARD_ENABLED` | true | 交接审查，仍受 JEV_ENABLED 控制 |
| `JEV_HANDOFF_GUARD_SHADOW_MODE` | true | 观察或阻断交接 |
| `JEV_HANDOFF_GUARD_THRESHOLD` | 0.8 | 任务保真阈值；本次候选为 0.7 |
| `JEV_HANDOFF_INJECTION_THRESHOLD` | 0.2 | 允许的最大越权风险概率 |

handoff 的新 Shadow 字段独立于旧 JEV_SHADOW_MODE / JEV_ROUTER_ENABLED，防止调整入口时意外启用交接控制。默认采用观察模式；现有依赖注入仍可显式提供测试 router / guard。

## 5. 评测与阈值选择

运行入口是 `python -m evals.handoff_jev`。模型使用正常配置中的 jev-latest，无音乐账户操作。每个阶段独立输出逐例结果与 metrics.json，记录实际返回模型版本、数据和提示词 SHA-256、provider usage、失败、延迟及质量门槛。

开发集只用于修改题目、阈值扫描和候选选择。阈值扫描网格预先固定为 0.7 / 0.75 / 0.8 / 0.85 / 0.9；在开发集零错误的候选中，最大化正负样例到阈值的最小距离，越权风险阈值保持 0.2。v3 开发集最低正常保真约 0.81–0.82，未被 risk 拦截的错误提案最高约 0.07–0.08，候选为 0.7。选择函数不读取保留集。

首次 v2 原始保留集审查失败；之后 v3 题目由开发集修改，在新确认集评测。确认集 0.8 出现 2 条误拦截和 1 次不可用；随后对开发集选择的 0.7 做完整复验，不替换首轮记录，也没有针对失败请求自动补齐“成功”。确认集重放属于同一冻结集的复验，不宣称又获得一个新的盲测集。

入口门槛：完整数据，完整计划匹配率和非回退匹配率各不低于 95%，回退不超过 10%，关键否定/不支持/缺目标样例全部匹配，无 API/结构异常，P95 不超过 2500 ms，usage 完整。护栏门槛：保留集无漏拦，误拦不超过正常样例的 5%，无不可用，P95 不超过 2500 ms，平均不超过 2500 token，usage 完整。门槛是本次工程验收约束，不是统计置信区间或线上 SLA。

API 异常不当成低分、不计作正确拒绝。非法 envelope 必须在本地拒绝且调用次数为零，语义样例准确率另行统计，避免用本地规则样例抬高模型准确率。入口 Hybrid 回退不计为 JEV 独立判断成功；其耗时与成本另行保留。

初次入口比较复用了已计算的控制组回退，其时延只覆盖候选回放；重新汇总时加上控制组等待作为估计，不把这个代理当作完整实测。最终回放不复用控制组作为候选回退，计时已经包含实际回退，不再重复加控制组时延。分位数复用仓库既有线性插值函数。

`--require-gates` 会在合成门槛失败时退出 2，便于 CI 使用；默认观察回放导出成功只表示记录完整，不等于质量通过。该参数从不修改应用配置或启用控制。

## 6. 复现

以下命令在 ai-service 中执行。确认数据与提示词哈希固定，普通测试使用离线占位配置；只有显式回放加载真实 JEV 配置。

```powershell
# 独立入口回放；输出到新目录，保留历史结果。
& .venv/Scripts/python.exe -m evals.handoff_jev --component entry --output ../.dev/phase5-entry-replay --require-gates

# 本次校准候选；仅对本次评测覆写阈值，不修改 Settings 或 .env。
& .venv/Scripts/python.exe -m evals.handoff_jev --component guard --guard-dataset confirmation --guard-threshold 0.7 --output ../.dev/phase5-guard-replay --input-usd-per-million 0.042 --output-usd-per-million 0 --price-source https://typesafe.ai/blog/introducing-system-one-models-and-jev --require-gates
```

价格是 2026-10-01 检查到的 [TypeSafe 官方公布单价](https://typesafe.ai/blog/introducing-system-one-models-and-jev)：输入 0.042 USD / 百万 token、输出免费。计算值为公开价格估算，未读取账户账单；未来复现须重新核实价格。缺少单价或完整 usage 时返回 null，已报告的 token 仍保留，不把未知成本归零。JEV 价格估算不包含 Hybrid 的 LLM、CPU、工程调试调用或部署费用。

本阶段的具体命令、运行结果、失败记录和启用限制见 [验证报告](../reports/handoff-phase5-20261001/validation.md) 和 [机器证据](../reports/handoff-phase5-20261001/evidence.json)。下一阶段继续在线业务验收、真实持久化和灰度准备；合成结果不代替这些验证。

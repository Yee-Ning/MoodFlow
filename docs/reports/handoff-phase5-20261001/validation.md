# MoodFlow Handoff 阶段 5 验证报告

日期：2026-10-01；基线 `4598c1f`，运行接入提交 `c33372a`。范围见 [阶段 5 架构说明](../../architecture/handoff-runtime-phase5.md)。

## 1. 修改范围

- handoff 默认路由工厂接入可选 V5 入口适配器，支持独立开关、Shadow 和有界 Hybrid 回退；复用原四类 RouteKey 与任务编排。
- 转交护栏补齐 Agent 职责、能力与 owner 信息；单次请求独立审查任务保真与越权尝试，保留本地权限优先、预算计量、Shadow 和不可用回退。
- 新增六项 handoff 独立配置及 `.env.example` 说明；未写入实际 `.env`、未启用控制、未切换默认图或部署。
- 新增冻结初始/确认数据、逐例回放、开发集扫描和候选选择、独立语义统计、延迟/成本计量、CLI 质量门槛与测试。
- 无 UI、Java、依赖或音乐业务工具实现改动。公开 HTTP、响应、SSE、动作去重和既有 Foundation 使用方式保持兼容。

## 2. 自动化验证

命令工作目录为 ai-service，前端命令在 soul-music-room。完整应用使用进程级离线配置，不修改用户配置：memory checkpoint、SQLite persistence、disabled memory、空 DATABASE_URL、JEV/live Postgres 关闭、LLM 测试占位值。

| 命令 | 最终结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests -q -ra --tb=short` | **1607 passed，14 skipped，1 warning，286 subtests passed**；退出 0，615.85 秒 |
| 初轮 handoff/guard/预算/运行保障/网络/settings 相关集合 | **174 passed，1 warning**；退出 0；后续新增 settings 校验由全量覆盖 |
| 应用环境执行全部 `evals/tests`，除 `test_judges.py` | **193 passed**；退出 0，124.77 秒；加上独立 Judge 为 197 项评测框架测试通过 |
| 最后检出换行修复后 `.venv/Scripts/python.exe -m pytest evals/tests/test_handoff_jev.py -q -ra --tb=short` | **21 passed**；退出 0，66.89 秒；包含新增 LF/冻结哈希回归，其余为上述评测子集 |
| `.venv-eval/Scripts/python.exe -m pytest evals/tests/test_judges.py -q -ra --tb=short` | **4 passed**；退出 0；受控客户端 |
| 修改文件 Ruff check | All checks passed；退出 0 |
| handoff 包、新/修改测试与回放文件 Ruff format check | 通过；Settings 沿用原文件风格，没有全文件重排 |
| `.venv/Scripts/python.exe -m compileall -q app tests evals scripts` | 退出 0 |
| `.venv/Scripts/python.exe -m pip check` | No broken requirements found；退出 0 |
| `.venv/Scripts/python.exe -m ruff check . --output-format json` | **45 项原有问题**；退出 1；path/line/code/message 与阶段 0 完全一致 |
| `npm run test:ai-stream` | 通过；退出 0 |
| `npm run test:worldline` | flow/view 通过；退出 0 |
| `npm run lint` | 通过；退出 0 |
| `npm run build` | TypeScript/Vite 通过；退出 0；保留原大 chunk warning |
| 本次文件 `git diff --cached --check` | 通过；未包含原有用户报告的 trailing whitespace |

评测环境隔离入口：

```powershell
$phase5EvalFiles = @(Get-ChildItem -LiteralPath evals/tests -Filter test_*.py -File |
    Where-Object { $_.Name -ne 'test_judges.py' } |
    Sort-Object Name | ForEach-Object { 'evals/tests/' + $_.Name })
& .venv/Scripts/python.exe -m pytest @phase5EvalFiles -q -ra --tb=short
```

开发中保留并纠正了 Ruff 未使用导入、新增测试断言插入位置、延迟门槛引用字段位置，以及两个 P95 测试最初误按离散分位数写期望值的问题。仓库既有 percentile 使用线性插值，期望值按相应 30 条分布改为 6000 / 1050 ms；回退时延门槛不变。没有删除、跳过或降低任何失败断言。入口计时新增测试专门阻止“快 JEV 掩盖慢 Hybrid 回退”和“重复累加已经包含的回退耗时”。

## 3. 数据与方法

| 数据 | SHA-256 | 用途 |
|---|---|---|
| 既有 `router_e2e_blind_v5.jsonl`，30 条 | `24d190d630614a611f19ac2ea1b7fc61cbf529b4222dfe826ec1b2b64f5e6daf` | 独立检查完整能力、任务片段、顺序、依赖和澄清 |
| `handoff_guard_v1.jsonl`，64 条，固定 LF | `68ad2a406e0bc4dee2db3b6d0674202f071235efa46c1156909696c5af662f09` | 初轮诊断；28 条 dev / 36 条初始保留 |
| `handoff_guard_confirmation_v1.jsonl`，64 条，固定 LF | `3c84ddce473d5fc00bab6cd7d7cf879a1092847fbf96e680d88405a6cfce4999` | 复用 28 条 dev，重新编写 32 条语义确认及 4 条非法交接 |

数据在相应真实调用前冻结；确认集文本与初始集语义样例不重合。数据由本次手工编写和标注，包含正常控制/聊天/只读分析/WorldLine、允许中转、当前任务片段、否定保留、引用文字、额外动作、伪造审批、reason 注入和非法边。并非真实生产标注或外部独立审查。

提交后复查发现 Windows 工作文件为 CRLF，而 Git blob 为 LF。两份数据逐条 JSON 内容相同；最终通过范围限定的 `.gitattributes` 固定数据、回放源码和证据的 LF，并更新 byte-hash 校验。历史回放记录中的原始 CRLF SHA 分别为 `903b4bc760fb9e7808fa201b34bae62a0ec83e647b9820541c9c9514b9600cf9` / `603041a9c73fe8a445c6bdd82c4d79501a0d1dd7f68736e0f764a5d6de370671`，保留为调用时证据。表中为最终可检出文件的 SHA；换行规范化没有更改任何模型输入或标签，不重跑真实 API 来覆盖历史结果。

只用 dev 修改题目和选阈值。固定网格中，在 dev 零错误候选里最大化两类保真分数到阈值的最小间隔，得到 0.7 / 0.2。未根据确认集逐条调整阈值或修改错误标签。确认集复验仍是同一确认集，不能称为额外新的盲测。

初始护栏 v2 prompt SHA：`3c70cdf5c1fa4f568ab1329a80f7a84653059d4cc6deb0a6abcbdd63efc0d2c2`。最终 v3：`c12f8c08362c0050601cb838eade2abfdde2a7fdda412c5f857da301322924d3`。实际模型均返回 `jev-1.13.0`，调用目标 jev-latest，无 SDK 自动重试。

## 4. 真实结果与保留失败

| 护栏回放 | 语义保留样例 | 误拦正常请求 | 漏拦错误提案 | 不可用 | 判断 |
|---|---:|---:|---:|---:|---|
| 初始 v2，0.8 / 0.2 | 32 | 3/16 | 0 | 0 | 未通过 |
| 新确认 v3，默认 0.8 / 0.2 | 32 | 2/16 | 0 | 1 | 未通过；不可用为负例，不计成模型正确拒绝 |
| 新确认 v3，候选 0.7 / 0.2，完整复验 | 32 | **0/16** | **0/16** | **0** | 合成门槛通过 |

最终候选另有开发语义 24/24 正确，以及 dev/确认非法交接各 4 条本地拒绝、零 API 调用。确认语义 P95 **736.18 ms**，平均 **1000.53 token**；56 次 JEV API 均成功。公开单价估算 **0.00225057 USD**，不是账户扣款金额。

“暂停一下”在 v3 默认确认回放的 task_fidelity=0.91、authority_override=0.03。两项各有含义，显示用 min 值不是联合概率；不能把这个值与旧单 Noul 的 0.68 当成完全相同指标比较。

首次入口比较得到 **29/30** 完整计划匹配；非回退 29/29，回退 1/30，无 JEV API 异常。`v5-17` 三任务请求触发 task_conflict，Hybrid 返回澄清，未匹配 Gold；没有将这个结果计为 JEV 成功。

首次入口回放将已计算的 Hybrid 计划传作候选回退，记录的 **1222.12 ms** P95 为候选回放耗时。补计原 Hybrid 耗时后，估计活跃入口 P95 **2555.91 ms**、最大 **11659.64 ms**，没有达到 2500 ms 门槛。因此补充了真实执行候选回退的入口复验，最新结果见 [入口确认记录](entry-confirmation/metrics.json) 与机器证据；保留初始计时结果，不宣称首次时延已通过。

入口复验计时包括其实际 Hybrid 回退，baseline 控制组的等待不会再次累加。JEV token、Hybrid 控制组和候选回退 LLM token 分开报告，独立准确率不包含回退。所有评测都未调用业务子图、音乐写接口或客户端动作工具。

最终真实入口复验仍为 **29/30** 完整计划匹配、非回退 **29/29**，回退 **1/30**，关键样例全部正确。包含实际回退的 P95 **956.61 ms**，最大 **10986.03 ms**；44 次 JEV API 无失败，另有该回退的 2 次 Hybrid LLM 调用。JEV 公开价格估算 **0.00273777 USD**，不包含 Hybrid 成本。CLI `--require-gates` 退出 0，独立合成门槛通过。最大值说明单次回退仍可能明显增加等待，P95 不代表所有请求都低于 2.5 秒。

## 5. 成本、运行配置和未验证内容

根据 2026-10-01 查询的 [TypeSafe 官方价格](https://typesafe.ai/blog/introducing-system-one-models-and-jev)，输入为 0.042 USD / 百万 token，输出免费。估算仅包含 provider 已报告的 JEV token。失败请求的账单未知，成本字段为 null 时不归零。初次和候选回放、实际 Hybrid 用量、失败与源码/数据哈希在机器证据中分别记录；调题实验不计入“正式回放”的汇总金额，未访问账户账单。

实际配置检查：Foundation；JEV 总开关已启用；handoff 入口开关 false、入口 Shadow true；护栏开启、Shadow true；实际任务保真阈值仍 0.8、风险 0.2。0.7 通过 CLI 仅对评测覆写，是待观察的独立候选，并未静默写入应用环境。

仍未完成：真实生产 Shadow 标注、长期概率稳定性/漂移、业务模型端到端质量、音乐账户操作、浏览器播放与响应式视觉、PostgreSQL 中断重启恢复和灰度发布。本次没有 UI/Java 修改，未重跑 Java 构建或浏览器视觉。原 14 项 opt-in skip 和 Qdrant warning 保留；45 项原静态问题未扩散。

一次真实 API 不可用发生在默认确认回放；之后的完整复验不能证明故障已永久消失。本地有界回退、日志和失败门槛均保留。合成集的零漏拦和零误拦不代表生产误差率为零。

代码与受控回归达到开发交付要求。继续保持 Shadow；生产启用仍需真实观察与在线验收证据，不因一次回放通过自动切换配置。没有 push 或部署。原已修改报告、四项删除和所有无关未跟踪数据均保留。

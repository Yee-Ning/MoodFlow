# Phase 5：V5 10% session canary 准备与停止记录

**结论：NO-GO；只完成本地准备与生产只读核查，不开闸。** 本地指定提交及报告已存在，C 按任务约定仅判定核心修复；生产仓库与运行镜像尚未包含这些能力，监控数据和降级告警也未就绪。生产变更为 **0**，未 push。Phase 2 / Phase 3 未执行，R2 的真实流量抽样遗留项仍未关闭。

实际执行日期：**2026-10-04，Asia/Shanghai**。目录沿用任务指定的 `v5-canary-20261003`。

## 1. 基线、分支与范围

- 开始完整 HEAD：`32087b39a1a12035976a82aafc8526be13afa21b`。
- 原分支：`codex/tools-t1t2-20261003`；独立工作分支：`codex/v5-canary-20261003`。
- 六个实现均经 `git merge-base --is-ancestor <hash> HEAD` 核对，返回 0；五份报告均为已提交文件。
- 已有修改、删除和未跟踪数据保留；仅显式暂存本任务文件。
- 本地准备范围：生产 Compose 增加关闭默认值的 `HANDOFF_CANARY_ENABLED` 接线；新增只读 `deploy/v5_canary_preflight.py`、对应测试和本报告/脱敏证据。
- 没有修改路由决策、Guard 阈值、记账、ACK、生产配置、镜像或服务。Compose 接线尚未部署。
- 准备提交：`08d18c517c98094aff1c5303b6031dffb6366178`；验证记录另作提交，见 §8；**没有开闸提交**。

## 2. Phase 0：按顺序核验硬门控

| 顺序 | 实现完整 hash | 验收报告 / 报告提交 | 本地判定与边界 |
|---|---|---|---|
| 1 A 三态记账 | `588fa6f776776e2d4662cdaf09e38e872745623a` | [A 报告](../write-path-unknown-20261003/report.md)，`f19aaef6cf6477b73c16a52c7312bb4b2437edee` | 通过：三态、502 受控核对及单 receipt 回归；真实账号正常写路径后由 T1/T2 补充。未宣称真实 502 重演通过。 |
| 2 C 组合诊断 | `37ffdabb8e7c5a959027928b94b3de092b54be23` | [C 报告](../combo-robustness-20261003/report.md)，`744a914a34eeb615e429c27a908ebca24decd31c` | **核心通过**：预算修复、组合成功/失败与部分结果保留；遵循本任务“核心通过”口径。原 V7 指定原唱业务用例未闭环，不改写原报告结论。 |
| 3 R2 入口 span | `feea34747f98081f613467be71cef00614e73d9e` | [R2 报告](../rule-layer-r2-20261003/report.md)，`d2be757879d7c1bbffd12250b94f2f0e7f6df29c` | 入口修复通过：原句真实完整计划、41 项 span 回归。真实用户抽样未满足，按本任务 Phase 3 留待完成。 |
| 4 D 自愈 | `a3cc649c643b3167805a2c6e36722b6344d8beef` | [D 报告](../recovery-selfhealing-20261003/report.md)，`4bb22856e48d346ced2043d1467f1e2af7f0163f` | 通过：native exit、隔离 PG 重启、只读补记及原预算不变；外部音乐/播放器为受控依赖。 |
| 5 T1/T2 AI | `b983411b0029b154b8d50c4fe23b579128c758f9` | [T1/T2 报告](../tools-t1t2-20261003/report.md)，`32087b39a1a12035976a82aafc8526be13afa21b` | 工具真实账号链路通过：红心读回、确认删除、归属拒绝、新进程无二次删除。 |
| 5 T1/T2 Java | `c4096608e92ac6ad3077b807beced04932d3f5c6` | 同上 | 92 项 Java 测试/verify 与真实接口链路通过，完整 LLM/UI 链路不在该报告通过范围。 |

上述是本地验收证据，不是生产部署证明。**生产对应六项均不满足**：生产 Git HEAD 不包含这些提交，AI 运行源码也缺少 canary、入口、Guard、账本与自愈模块。停止在 Phase 1，不允许依据本地通过记录开启旧生产镜像。

## 3. Phase 1：生产状态与逐项开闸清单

只读采集时间：**2026-10-04 10:38:46.867739 +08:00**。

证据：[production-preflight.json](production-preflight.json)。采集成功、预检进程退出 **1** 表示前置条件不满足；不是 SSH 连接失败，也不是测试失败。

| 检查项 | 实测结果 | 状态 |
|---|---|---|
| 生产位置与版本 | ECS `47.83.162.191`，`/opt/moodflow`；HEAD `3a11237cbce78ffb1e8ce6dc31db681c1430e360`，工作区干净 | 已核实；版本不满足 |
| 默认图 | 容器环境 `AI_AGENT_GRAPH_MODE=foundation` | 通过 |
| `HANDOFF_CANARY_ENABLED=false` | 环境值未设置，运行源码没有该配置字段，`rollout.py` 不存在 | **无法确认该开关存在；未启用 canary，不把缺字段写成 false** |
| 10% session 能力 | 运行源码/环境缺失 canary 百分比；本地默认 10%，生产不能据此推定支持 | 未满足 |
| handoff + V5 生效入口 | 运行镜像缺 `handoff/routing.py`；`JEV_HANDOFF_ROUTER_ENABLED` 未设置且源码无默认值 | 未满足 |
| 默认 Foundation + Hybrid | 当前 foundation，`JEV_ROUTER_ENABLED=false`，V5 仅允许 Shadow 配置 | 配置已核实；本轮未发送业务探针 |
| Guard 0.8 | 运行镜像缺 Guard 模块及配置字段；本地默认 0.8 | 生产不可确认；本地阈值未修改 |
| 服务可用性 | 六个常驻服务 healthy；migrate exited / exit code 0 | 通过，不能据此认定开闸就绪 |
| V5 Single / Multi / 回退监控 | 最近 2h 未发现相关事件 | 未满足 |
| 快判命中率 | 入口模块/事件缺失，无命中分母 | 未满足 |
| 播放器 ACK 成功率 | 当前证据未采集 ACK 生成/接受/超时分母 | 未验证 |
| Guard 分数直方图 | 无 Guard 事件 | 未满足 |
| V5→Hybrid 回退告警 | 无告警交付/触发演练证据；现有 Shadow 汇总不能证明 active 回退告警 | 未满足 |
| canary / non-canary 实际路径 | 未开闸，未发起业务探针；本地已有分桶/API 回归另作自动化验证 | 生产未验证 |
| 一键回滚预案 | §5 已记录命令、触发阈值与前置条件 | 文档完成；生产未演练 |
| 全量相关验证 | 2101 passed、45 skipped、288 subtests passed；静态检查通过，详见 §8 | 离线通过；外部验证有明确限制 |

最近 2h 读取 AI 日志 **187 行**，tail 上限 **10000**，未达到上限，没有发现截断。只归档白名单配置、源码 hash、服务状态、计数及已有脱敏 Shadow 汇总，不归档原始日志、用户文本或身份。`jev.v5_shadow`、入口、入口快判、入口异常、Guard 与 handoff trace 的事件计数均为 **0**。这只能证明该日志窗口没有可用的相关事件，不能证明整站无用户请求、没有错误或监控健康。

只读工具可在服务器执行 `python3 -m deploy.v5_canary_preflight --root /opt/moodflow --since 2h`（需要该准备文件已到位）；也可通过现有 SSH 将源码传入内存执行，无须部署、写入服务器或读取 secret。

Windows 工作区的本次复现方式（返回 1 是预检 NO-GO）：

```powershell
$source = Get-Content -Raw -LiteralPath deploy/v5_canary_preflight.py
$remote = 'cd /opt/moodflow && python3 -B -c ''import sys; s=sys.stdin.read(); exec(compile(s,"<preflight>","exec"),{"__name__":"__main__","_PREFLIGHT_SOURCE":s})'''
$source | ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes root@47.83.162.191 $remote
```

工具遇到采集错误返回 2，只打印异常类型；缺失配置/模块/事件产生明确 blocker，不用 0%、100% 或平均值 0 掩盖空分母。它检查 AI 关键源码与生产 checkout 的一致性，但不证明 Java JAR 来源；Java 镜像 provenance 仍列为等待项。日志存在也不会自动解除 ACK、告警和真实路径等人工证据等待项。

## 4. 监控与采样准备口径

这些是待安装/验收的口径，**没有声称生产监控或告警已配置完成**：

| 指标 | 分母、分组与缺失处理 |
|---|---|
| V5 决策成功率/错误率 | 真实 canary V5 决策尝试；Single、Multi、unknown stage 分开；超时/异常/畸形输出/触发 Hybrid 回退不算 V5 成功。业务子任务失败另列，避免混入路由决策率。 |
| V5 延迟 | 同窗口、同组的决策 wall time，分别统计 Single、Multi、回退 P50/P95；总耗时包括回退时间。缺 latency 的尝试显式报 missing。 |
| 回退率/告警 | V5→Hybrid 次数 / 全部 V5 尝试；保留原因码和实际返回路径。快判直出不算 V5 尝试。异常兜底和普通回退均需事件，日志/告警接收端须做受控触发演练。 |
| 快判命中率 | canary 正常入口快判次数 / 同入口请求数；production、acceptance、synthetic、显式 `/handoff` 探针必须可区分。 |
| ACK | 唯一生成播放器 action 为分母，以原 action 身份关联 applied/rejected/超时；pending 单列，重复 ACK 不扩分母。只旁路采集，保持 ACK 处理逻辑不变。 |
| Guard 分布 | 原始 task_fidelity、authority_override 及 `min(fidelity, 1-authority_override)` 的派生分数分别统计；区间 `[0,.7)`、`[.7,.8)`、`[.8,.9)`、`[.9,1]`。保留 Shadow 标签，分数无值单列。**不改变 0.8 阈值**。 |
| 错误/澄清 vs 基线 | 同时间段 canary 与非 canary 请求各自的终态 error/needs_input 分母；acceptance 探针排除；无有效请求时比率为 null。 |

现有 `handoff_entry`、Guard 及 trace 日志缺少可靠的请求级 cohort/sample 标签，不能仅凭标记计数计算真实 canary 成功率。当前 `deploy.runtime.configure_shadow_telemetry_logging` 仅保证 Shadow logger INFO；active 入口/Guard 的生产日志交付尚未确认。快判不调用 V5，不能用 Shadow 数据代替 handoff 真实执行数据。

本轮只增加预检和开关接线，没有改决策模块的日志或采样行为。恢复执行前，需先完成这些旁路观测能力及告警安装，证明每一项有真实或明确标为 acceptance 的数据；预检数据缺失时禁止开闸。

## 5. 一键回滚预案与触发条件

**适用前提**：生产已经部署包含所有门控能力的同一版镜像、Compose 已包含本准备提交的开关接线，原默认图仍为 foundation，Foundation 的既有 Hybrid 配置已验证。当前旧生产镜像不满足这些前提，本轮没有执行以下命令。

在 `/opt/moodflow`，单条回滚命令只覆盖当前启动进程的 canary 开关，并只重建 AI 容器，复用现有镜像：

```bash
HANDOFF_CANARY_ENABLED=false docker compose --env-file .env.production -f docker-compose.production.yml up -d --no-deps --no-build --force-recreate ai-service
```

缓存配置需重建服务后生效；命令不修改 `.env.production` 或其他生产变量。复核运行环境/源码已回到 false、foundation，服务 healthy，并用正式 `/api/ai/chat` 新请求验证回到 Foundation+Hybrid。若此前持久配置为 true，后续必须由既有配置管理将**同一个开关**持久化为 false，避免另一次无覆盖参数的启动重新开闸。这里只提供即时一键回滚，不把临时覆盖称为持久配置更新。

关闭开关不会合并 Foundation/Handoff checkpoint 命名空间。已有 canary 会话需用已有 UI/恢复流程重新进入 Foundation；不能重放未知写操作、补造 ACK 或重置原运行预算。连续性专项演练仍未完成。

回滚阈值（本轮准备策略；尚未安装告警或取得有效基线）：

- **错误率**：5 分钟窗口至少 20 个 V5 尝试，V5 决策错误率 >5%，或高于同期有效非 canary 基线 3 个百分点，连续两个窗口触发。
- **延迟**：同样窗口/样本量下，V5 总决策 P95 >5000ms，或大于有效基线决策 P95 的 2 倍，连续两个窗口触发。Single/Multi 分组同时检查，避免总量掩盖 Multi 尾延迟。
- **回退**：V5→Hybrid 回退率 >10%，或高于有效对照 5 个百分点，连续两个窗口触发。每次回退都可观测；不能因为最终 Hybrid 成功而吞掉降级。
- **ACK**：60 秒观察期限已到的 action 中，失败/无 ACK 比例 >5%，至少 20 个 action，连续两个窗口触发；pending 不提前算成功。
- **立即关闭**：正式入口实测走错 cohort/默认图改变；生产 Guard 阈值偏离 0.8；有 canary 请求但对应观测链持续 5 分钟无数据；发现非预期写入或重复业务执行。
- 空分母不能判健康；基线不足时保留绝对阈值，并暂停扩大、补充数据。告警接收端、窗口规则和触发演练须在开闸前提供证据。

生产唯一允许的切流改动是 `HANDOFF_CANARY_ENABLED`。不能为绕过本轮阻碍顺带开启 `JEV_HANDOFF_ROUTER_ENABLED`、关闭其 Shadow、部署六项修复或调整 Guard。当前恢复任务需先独立完成并验证这些部署/观测前置条件，再重新核验只翻一个开关是否足够。既有 `deploy/update.py` 对 Compose 契约变化有审查门控；本轮没有绕过它，也没有调用 update、fetch、push 或部署。

## 6. Phase 2：开关验证记录

- 翻转方式：**未执行**。
- 翻转时间：**无**。
- 开关有效性：旧运行镜像无该能力，未冒充已生效；本地模板默认 false。
- canary → handoff+V5、非 canary → Foundation+Hybrid：**生产未验证**。
- 开闸 commit：**无**。准备提交与未来开闸必须保持分离。

## 7. Phase 3：初始观察与真实流量抽样

**初始观察期没有开始**，不能把开闸前的 2h 日志核查当作“≥2h canary 观察已完成”。

| 指标 | 实际可用数 / 结果 |
|---|---|
| 已开启 canary 请求 | **0**（未开启功能；不是整站用户请求数） |
| 可用 V5 Single / Multi / 回退事件 | **0 / 0 / 0** |
| V5 决策成功率/错误率、P50/P95、回退率 | **N/A**，没有有效分母/延迟数据 |
| Guard 直方图 | **N/A**，0 个分数 |
| ACK 成功率 | **未采集/未验证**；不能填 0% 或 100% |
| 错误/澄清率 vs 基线 | **N/A**，没有 cohort 终态统计 |
| canary 快判真实请求样本 | **0 / 目标 ≥50** |
| 严格 V5 Replay 可比较决策 | **0** |
| 分歧率 | **N/A**，不使用 R2 工程样本补真实流量分母 |

真实流量采样报告结论：没有已开启 canary 的真实快判样本，未调用供应商补造数据、未发送合成业务请求填数。R2 既有 50 例工程语料（46 可比较、4 回退）保持原来源，不作为本轮真实流量验收。

恢复执行后的采样步骤：从正式 canary 入口快判命中记录按原请求去重取样，携带原始 RoutingContext 和快判计划签名；用户文本/身份/上下文及供应商 capture 保存在访问受控的位置，不进 Git。只在隔离评测进程调用 V5，禁止 dispatch 子图/工具；记录实际供应商响应，再复用 R2 `ReplayClient` 的 state SHA256 + question names + 未消费响应严格匹配方法离线重放。回退排除出可比较分母，报告实际样本数、可比较数、回退数、分歧数及原因；无数据时不得构造 V5 答案。没有响应 capture 的请求不能声称已完成离线 V5 比较。

建议：**保持不开闸，先补前置条件**；现在没有可支持“继续观察 canary”的运行证据。后续重新开闸后按 ≥2 小时或 ≥100 个 canary 请求先到者形成初始快照；真实快判样本不足 50 时如实记录并保留遗留项。48h 扩大/全量/回滚决策不在本轮。

## 8. 验证、提交与未完成项

准备阶段的定向验证：`ai-service/.venv/Scripts/python.exe -m pytest deploy/tests/test_v5_canary_preflight.py -q`，**18 passed**；同解释器 `-m ruff check deploy/v5_canary_preflight.py deploy/tests/test_v5_canary_preflight.py` 与 `-m compileall -q` 同范围，均通过。覆盖白名单读取、缺失字段/模块、错误配置、日志窗口边界、Compose NDJSON/数组、只读命令、隐私过滤、异常不泄露与关闭默认值。

最终全量命令在工作区根目录执行：

```powershell
$env:PYTHONPATH = (Get-Location).Path
ai-service/.venv/Scripts/python.exe docs/reports/rule-layer-r2-20261003/check.py ../docs/reports/v5-canary-20261003/pytest_entry.py tests ../deploy/tests -q --junitxml=../.dev/v5-canary-20261003/all-tests-pass.xml
ai-service/.venv/Scripts/python.exe -m ruff check deploy/v5_canary_preflight.py deploy/tests/test_v5_canary_preflight.py docs/reports/v5-canary-20261003/pytest_entry.py
ai-service/.venv/Scripts/python.exe -m compileall -q deploy/v5_canary_preflight.py deploy/tests/test_v5_canary_preflight.py docs/reports/v5-canary-20261003/pytest_entry.py
```

实际最终全量使用 `../.dev/v5-canary-20261003/pytest_entry.py` 临时入口，已将相同内容归档为本目录的 [pytest_entry.py](pytest_entry.py)，上面是可重跑命令。该入口只移除 R2 包装器注入的占位 `TYPESAFE_API_KEY`，使设置测试自己的临时 dotenv 生效；保留已有离线环境和原测试断言。R2 包装器只在冻结文件内容相同、差异仅为换行时临时恢复 Git blob，finally 恢复原 checkout 字节，不改 `.env`。

| 检查 | 最终结果 |
|---|---|
| 全量 AI 应用 `tests/` + 部署 `deploy/tests/` | **2101 passed、45 skipped、288 subtests passed**，0 failures/errors，265.33s |
| 设置测试 + 预检的归档验证入口复验 | **28 passed**，0 failures，0.42s |
| 新增预检定向测试 | **18 passed** |
| Ruff | **All checks passed**，3 个本任务 Python 文件 |
| Python 编译 | **通过**，同范围 compileall |
| 任务 diff / 暂存检查 | **通过**；原有无关用户改动不纳入提交 |
| SSH 预检与文档复现命令 | 采集成功，预检返回 **1 / NO-GO**；生产 HEAD 未变，没有重启、构建、部署或切流 |

178 个既有 LangGraph durability warning 保留，未修改持久化代码来消除 warning。45 个既有外部集成跳过分别为：21 隔离 PostgreSQL 验收、7 PG E2E、7 native 恢复进程验收、4 live PG smoke、3真实 PG 重启、2真实 Qdrant、1真实 TypeSafe；跳过条件未改变，这些项目不能算作本轮通过。D 的历史隔离 PG/native 证据仍以原报告为准。

保留的中间失败：第一次跨目录收集出现 **8 errors**（未设置根目录 `PYTHONPATH`）；修正后首轮全量为 **2100 passed、45 skipped、1 failed、288 subtests passed**，失败为 `test_jev_fields_use_existing_settings_and_hide_key`，原因是包装器占位 key 优先于测试临时 dotenv。移除该占位覆盖后重新运行完整集合得到上述最终通过结果，没有删除、跳过、弱化测试或修改应用设置逻辑。

[validation-summary.json](validation-summary.json) 保存顺序门控、每份原报告的提交号与规范化 LF SHA256、生产预检证据摘要 hash、四次 JUnit 汇总、失败用例名和外部跳过原因。JUnit 测试计数包括子测试，因此与 pytest 主测试计数分开说明；完整 XML 留在 `.dev/v5-canary-20261003/`，没有将原始日志/凭据纳入 Git。

提交记录：

- 准备实现、18 项测试、关闭的开关接线、初版报告和生产证据：`08d18c517c98094aff1c5303b6031dffb6366178`。
- 最终报告/验证摘要/归档验证入口：独立验证记录提交，可用 `git log -1 --format=%H -- docs/reports/v5-canary-20261003/validation-summary.json` 查询；最终交付消息给出真实 hash。报告不填入尚未产生的自引用提交号。
- 开闸提交：**无**。

没有业务 API、Java、Node 适配器或前端实现变更，本轮未重新做 Java/Node/前端构建、视觉/音频验收，也未构建/部署新生产镜像。无目录限制的 evals 集合需要既有缺失的 `deepeval`，本轮没有运行或安装；全量范围为上面的 AI 应用与部署测试。静态 Compose/默认开关由部署回归验证；生产版本、配置与所有实际功能/监控缺口不能由离线通过结果替代。

未完成的生产验收：全部门控能力的部署/镜像溯源、真实 false/10%/V5 active/Guard 0.8 配置、各项监控数据、降级告警触发与交付、回滚连续性演练、切流与双 cohort 路径、初始观察、≥50 真实快判采样及 Replay。单开关限制使本轮不能部署新镜像或改其他 V5 配置来解决这些阻碍。

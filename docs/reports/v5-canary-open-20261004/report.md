# V5 Canary 开闸与验证失败回滚记录（2026-10-04）

本次已按 West 授权依次开启 handoff router 与 10% session canary，随后因 **handoff V5 candidate 和观测数据无法验证**，立即关闭两个开关。最终生产仍运行 `613db731`，六个常驻服务 healthy，migrate exited 0，Foundation+Hybrid 搜索已复验通过。**开闸验收未通过，初始观察与真实流量 Replay 未完成，R2 遗留项未关闭。**

本文时间均为 Asia/Shanghai（UTC+8）；JSON 保留完整 UTC 时间。48h 扩大/全量决策未执行。

## 1. 基线、授权与门控

- 本地开始 HEAD：`97b7c6e379026b111cb4ed35c6cb9d604726da41`；独立分支 `codex/v5-canary-open-20261004`。
- 生产开始、开闸、回滚后 HEAD 均为 `613db7311e4c224c1ee3a57f4693d3e0322090a7`，生产工作区干净。
- 生产 AI image ID 始终为 `sha256:19dad4f4cb3746191dbe44832ce026172498e0659316f225ea3d57841c98c24d`；12 个关键源码文件与该 HEAD 一致，包含入口、Guard、账本、自愈和工具模块。
- 采用用户最新确认的“613db731 部署验证通过”作为部署业务基线；本轮执行两开关配置加载，不重新发布代码或构建镜像。
- 新授权明确允许 `JEV_HANDOFF_ROUTER_ENABLED=true`，并保持 router Shadow=true。此前准备报告要求 active/Shadow=false 的配置预期不适用于本轮；没有修改默认值或使用旧预检的固定 blocker 冒充本轮路径验收。

生产 `git merge-base --is-ancestor <ref> HEAD` 逐一复核，八项退出码均为 0；完整 hash 见 [final-snapshot.json](final-snapshot.json)。A/C/R2/D/T1T2 按原顺序确认报告存在、LF SHA256 与准备阶段记录一致，详见 [validation-summary.json](validation-summary.json)。本轮继承原报告的验收边界：

| 门控 | 实现提交 | 报告与既有状态 |
|---|---|---|
| B | f97d5d2 | 613db731 部署祖先检查通过 |
| A 写路径三态记账 | 588fa6f | [报告](../write-path-unknown-20261003/report.md)，通过，保留原验证边界 |
| C 组合诊断 | 37ffdab | [报告](../combo-robustness-20261003/report.md)，核心通过；原业务案例边界保留 |
| R2 入口 span | feea347 | [报告](../rule-layer-r2-20261003/report.md)，入口通过；真实流量采样待关闭 |
| D 恢复自愈 | a3cc649 | [报告](../recovery-selfhealing-20261003/report.md)，通过，保留原验证边界 |
| T1/T2 AI / Java | b983411b / c4096608 | [报告](../tools-t1t2-20261003/report.md)，真实账户工具链通过 |
| canary 准备 | 08d18c5 | [报告](../v5-canary-20261003/report.md)，准备已提交；观测缺口仍需实测 |

开闸前检查：基线版本、六服务 healthy/migrate exited 0、canary=false、router=false、10%、默认 foundation、router Shadow=true、Guard 0.8、同镜像回滚命令均确认。候选决策和观测链列为待实测项，**未将其提前标为就绪**；本次受控开闸发现缺口后执行了用户指定的验证失败回滚。

## 2. 设置方式与翻转记录

Compose 主文件原本未暴露 handoff router 开关。使用既有 Docker Compose 多文件配置方式，在主仓外建立仅覆盖 AI 两个开关的文件：

`/opt/moodflow-runtime/v5-canary-20261004/switches.yml`，目录权限 700，文件权限 600。开闸时内容为：

```yaml
services:
  ai-service:
    environment:
      JEV_HANDOFF_ROUTER_ENABLED: "true"
      HANDOFF_CANARY_ENABLED: "true"
```

没有修改 `.env.production`、主 Compose、secrets 文件或挂载。router Shadow=true、Guard threshold=0.8、percentage=10 均沿用源码默认值，未新增覆盖。仅 AI 服务获得两键覆盖，migrate 的共享配置未随之变化。

每次加载在 `/opt/moodflow` 执行：

```bash
docker compose --env-file .env.production \
  -f docker-compose.production.yml \
  -f /opt/moodflow-runtime/v5-canary-20261004/switches.yml \
  up -d --no-deps --no-build --force-recreate ai-service
```

这是当前镜像的环境配置加载，需要重建 AI 容器使缓存设置生效；未调用构建、pull、代码发布或迁移。部署发布器 `deploy/update.py` 未执行，既有代码发布审查门控未变更。每次加载前比较完整 Compose 渲染结果，仅允许 AI 环境的上述两键不同；其余配置规范化 hash 始终为 `520dc48e9698ae20d3e132ed68c2092b2a6e6a81e2c622b432f03f4eadc6845a`。

| 操作 | 配置加载开始 | AI healthy | 有效 router / canary |
|---|---|---|---|
| 设置 router，canary 保持关闭 | 13:46:01.094 | 13:46:37.778 | true / false |
| 开启 10% session canary | 13:48:15.238 | 13:48:52.073 | true / true |
| 验证失败回滚 | 13:50:10.366 | 13:50:47.180 | false / false |

每一步确认：七服务 image IDs、挂载 hash、两键以外环境 hash 全部不变；其他六个服务容器 ID 不变；AI 容器 ID 因加载配置改变。默认图 foundation、router Shadow=true、Guard Shadow=true、Guard 0.8 全程保持。证据：[before.json](before.json)、[router.json](router.json)、[open.json](open.json)、[rollback.json](rollback.json)、[opening-record.json](opening-record.json)。

## 3. 正式入口路径与 candidate 验证

探针均使用严格 TLS 的正式 `/api/ai/chat`，带 `X-MoodFlow-Telemetry-Class: acceptance`。按生产固定盐和 SHA256 分桶算法选择 thread，未改变百分比或路由逻辑。工程请求只询问音乐知识，不生成播放器 action，不填入真实流量分母。

| 样本 | session 桶 / 路径证据 | 实测结果 |
|---|---|---|
| canary Single 请求 | bucket=788；`handoff:v1:v5-canary-open-acceptance-single-20261004-0`，6 个 checkpoint | HTTP 200、final success，整请求 8.438s；V5 candidate 未取得验证证据 |
| canary Multi 请求 | bucket=90；`handoff:v1:v5-canary-open-acceptance-multi-20261004-10`，6 个 checkpoint | HTTP 200、final success，整请求 10.219s；V5 candidate 未取得验证证据 |
| 非 canary 对照 | bucket=7130；Foundation SSE 含 `aggregate_results`，沿用 Foundation/Hybrid 工厂 | HTTP 200、final success，4.531s |
| 回滚后的原 canary 桶 | bucket=402；新会话使用 Foundation namespace，7 个 checkpoint | `intent_router → tool_calling_subgraph → aggregate_results`，搜索 success，1 次工具调用，5.516s |

Handoff 对外将 `general_chat_agent` 映射为 `general_chat_subgraph`，因此 SSE 节点名称本身无法区分两图；上述 canary 入口证明使用持久化 namespace，而非将探针字段 `handoff_agent_node_observed=false` 解读为未走 handoff。非 canary 对照包含 Foundation 独有的聚合节点。证据：[Single](single-probe.json)、[Multi](multi-probe.json)、[对照](control-probe.json)、[恢复](recovered-probe.json)、[开闸观测](open-telemetry.json)、[恢复观测](rollback-telemetry.json)。

**验证失败原因**：13:49:53.009 采集开闸容器日志，32 行、未截断，`jev.handoff_entry` / `jev.handoff_entry_local` / `jev.handoff_guard` / `handoff.trace` 均为 **0**，没有可关联的 Single/Multi candidate、回退或 Guard 事件。两条业务成功响应不能证明候选决策成功，也不能证明没有回退。唯一 `jev.v5_shadow` 事件为 Foundation 对照的 acceptance Single（1 次 API call、1 candidate task），不属于 handoff candidate，未挪用为验收。

源码 `deploy.runtime.configure_shadow_telemetry_logging()` 只为 Foundation `shadow_v5` logger 安装 INFO handler；handoff 入口、Guard、trace 使用各自 INFO logger。该配置与事件缺失一致，但本轮没有修改日志等级、生产进程或业务源码。现有 checkpoint/成功响应也没有记录可用于区分 candidate Single/Multi/回退的 RouterTraceV5，因而结论是 **无法验证 candidate**，并非断言供应商一定没有被调用。

用户要求任一验证失败即回滚，因此本次未等待 5 分钟缺数据门限，也未宣称触发了错误率/延迟等统计阈值。13:50:10 开始执行关闭两个开关。

## 4. 初始观察与真实流量采样

从 AI healthy 到回滚加载开始仅 **78.294 秒**。未达到 ≥2h 或 ≥100 canary 请求，验证失败后没有继续放行观察。

完整请求核查窗口为 13:48:15.238 至 13:50:47.180，按持久化 `user_message` 事件关联 session，包含重复使用既有会话的请求：**handoff acceptance=2、其他 handoff=0、Foundation acceptance=1、其他 Foundation=0**。[final-window-counts.json](final-window-counts.json) 保存来源和时间。该计数不覆盖未进入持久化记录的连接/HTTP 失败，不能声称配置重建期间零用户影响。

| 必需指标 | 本轮结果与数据限制 |
|---|---|
| V5 Single / Multi / 回退成功率、错误率 | **N/A**；handoff candidate 事件与分类分母缺失 |
| V5 P50/P95 决策延迟 | **N/A**；上表秒数为工程请求总耗时，未当作 V5 路由延迟 |
| V5→Hybrid 回退率、告警 | **N/A**；无可验证回退记录、告警交付/触发演练证据；零日志不代表零回退 |
| canary 快判命中率 | **N/A**；正式快判事件及分母无法采集 |
| Guard 分数直方图 | **N/A**，有效分数 0；`[0,.7)`、`[.7,.8)`、`[.8,.9)`、`[.9,1]` 无可绘制数据 |
| ACK 成功率 | **N/A**，工程请求未生成 action，ACK 分母 0；未补造 ACK 或变更处理逻辑 |
| 错误/澄清率 vs 真实基线 | **N/A**，真实 cohort 请求分母 0；两条 canary acceptance final 均 success，仅属烟雾证据 |
| 真实 canary 快判采样 | **0 / 目标 ≥50** |
| R2 严格 provider-capture Replay | **0**；可比较决策 0，分歧数/率 **N/A** |

没有可用真实快判请求或 provider capture，本轮未调用供应商生成离线替代答案，未以 R2 既有工程语料或本轮验收请求补数。**R2 的真实采样遗留项保持打开**。

后续恢复采样仍须复用 R2 `ReplayClient`：保存去身份的原 RoutingContext、快判计划、原 state SHA256 和 question names，以及实际 provider 响应；按未消费响应严格匹配进行离线回放，禁止 dispatch 工具/子图。报告实际可比较数、回退数与分歧率，缺 capture 的样本不能声称已比较。

建议保持两个开关关闭，先补齐 handoff candidate、Guard、回退/告警、cohort/sample 标签、ACK 关联和有效基线，再安排新的开闸验证。本轮授权仅两个开关，未扩大范围去修复观测代码或重发镜像。

## 5. 实际回滚与后续一键命令

已将覆盖文件两个开关持久化为 false，并使用 §2 同镜像命令重新加载；即时关闭和持久设置一致，后续沿用该覆盖文件启动不会重新开闸。保留主 Compose 原 false 和 router 源码默认 false。

已准备且 `sh -n` 检查通过的单命令回滚脚本（权限 700）：

```bash
/opt/moodflow-runtime/v5-canary-20261004/rollback.sh
```

脚本先原子写入仅含两键 false 的覆盖文件，再只重建 AI、复用原镜像。实际本轮执行的是同一配置写入/Compose 加载流程，完整时间和校验见 [rollback.json](rollback.json)。执行后必须复查 effective false/false、foundation、Guard 0.8、六服务 healthy/migrate exited 0，并在原 canary 桶的新正式请求验证 Foundation+Hybrid；本轮这些复验均通过。原 handoff checkpoint 保留，没有合并命名空间、重放未知写入、伪造 ACK 或重置预算。

继续沿用 [准备报告 §5](../v5-canary-20261003/report.md) 阈值：5min 窗口至少 20 个 V5 尝试、连续两窗错误率 >5% 或较有效基线高 3pp；P95 >5000ms 或 >基线 2 倍；回退率 >10% 或较对照高 5pp；到达 60s ACK 期限且至少 20 个 action，失败/无 ACK >5% 连续两窗。错 cohort、默认图/Guard 漂移、canary 观测连续 5min 缺失、非预期写入或重复执行立即关闭。另按本轮授权，**任一验证失败立即关闭**。本次缺少统计分母，没有伪造阈值计算。

最终只读快照时间 **13:55:34**：HEAD 未变且工作区干净；两个开关 false；六常驻 healthy、migrate exited 0；12 源码一致。[final-snapshot.json](final-snapshot.json)。

## 6. 测试、提交与交付边界

本轮实际执行（根目录 PowerShell）：

```powershell
$env:PYTHONPATH = (Get-Location).Path
ai-service/.venv/Scripts/python.exe docs/reports/rule-layer-r2-20261003/check.py ../docs/reports/v5-canary-20261003/pytest_entry.py tests/test_handoff_rollout.py tests/test_handoff_jev_routing.py tests/test_jev_settings.py -q --junitxml=../.dev/v5-canary-open-20261004/directed-tests.xml
$canaryTests = rg --files ai-service/tests -g 'test_handoff*.py' -g 'test_jev*router*v5*.py' -g 'test_jev_settings.py' | ForEach-Object { $_ -replace '^ai-service[/\\]', '' }
ai-service/.venv/Scripts/python.exe docs/reports/rule-layer-r2-20261003/check.py ../docs/reports/v5-canary-20261003/pytest_entry.py @canaryTests -q --junitxml=../.dev/v5-canary-open-20261004/related-tests.xml
ai-service/.venv/Scripts/python.exe -m ruff check .dev/v5-canary-open-20261004
ai-service/.venv/Scripts/python.exe -m compileall -q .dev/v5-canary-open-20261004
git diff --check -- docs/reports/v5-canary-open-20261004
```

| 检查 | 结果 |
|---|---|
| 开关、设置、Shadow/active 入口定向测试 | **31 passed**，0 failures/errors/skips；1 个既有 durability warning |
| Handoff/V5 相关完整测试集（19 文件） | **452 passed**，0 failures/errors/skips；128 个既有 durability warning，57.42s |
| 临时运维/采集脚本 Ruff、compileall | **通过**；脚本留在忽略目录，未加入生产应用 |
| 正式入口双 cohort / 回滚 smoke | 三个开闸响应及回滚搜索通过；**handoff candidate/监控验收失败** |
| 完整配置比较、镜像/挂载/环境范围检查 | **通过**，除两键外保持不变 |
| 回滚脚本语法、最终生产快照 | **通过** |

JUnit hash 和门控报告 hash 已归档。没有删除、跳过或弱化测试。613db731 前轮全量结果（2114 passed、288 subtests passed、45 外部集成 skipped）见 [部署验证摘要](../prod-deploy-canary-20261004/verification-summary.json)，属于前轮基线证据，**本轮没有冒充重新执行全量应用/Java/Node/前端构建**。本轮没有应用代码、前端交互或播放器行为改动；离线测试通过不能替代失败的生产 candidate 观测验收。

独立开闸配置变更记录提交：`8245d1357434c44f464d04f5e799d42cd4c16c1e`，保存实际开启与随后回滚的历史，不表示当前保持开启。最终报告/回滚证据另行提交；可用 `git log -1 --format=%H -- docs/reports/v5-canary-open-20261004/report.md` 查询实际号，最终交付消息同时列出两次提交。

未 push；原有用户修改、删除和未跟踪数据保留。未改路由决策、记账、ACK、Guard 阈值或其他生产配置。未完成项为 candidate Single/Multi 证明、全部观测与告警就绪、规定初始观察、真实快判采样和严格 Replay；本轮结果不能交付为成功开闸。

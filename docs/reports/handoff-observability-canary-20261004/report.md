# Handoff 观测修复与 Canary 重开（2026-10-04）

状态：观测修复、生产发布及最终硬验收通过；双开关 true，10% session canary 已开启。按用户最新指令取消两小时观察等待。真实流量不足的指标与 R2 Replay 不声明完成。

## 1. 授权、基线与范围

West 在本轮明确授权观测修复部署及 `JEV_HANDOFF_ROUTER_ENABLED`、`HANDOFF_CANARY_ENABLED` 双开关翻转。独立分支 `codex/handoff-observability-canary-20261004`；开始时本地完整 HEAD `29173f5e9e6d18b549b444c9c6e64a5ad225599e`。生产 `/opt/moodflow` 完整 HEAD `613db7311e4c224c1ee3a57f4693d3e0322090a7`，双开关 false，六个常驻服务 healthy，migrate exited 0。

生产祖先门控 B/A/C/R2/D/T1T2/canary 准备均已用 `git merge-base --is-ancestor` 验证，旧报告证据见 [canary 准备](../v5-canary-20261003/validation-summary.json) 及 [上次回滚](../v5-canary-open-20261004/report.md)。本轮不改变路由选择、Guard 判定与阈值 0.8、账本、ACK 处理、默认 foundation 图、Shadow 模式、secrets、Java/Node/前端及 DB schema。仅 AI 应用旁路观测和主机接收器增加代码。

## 2. 观测修复及统计口径

- `deploy.runtime` 给专用 `app.agent.handoff.observability` 安装幂等 INFO handler；不打开全局 INFO，不收集文本、身份、action 参数或供应商原始响应。
- 正式 chat 按实际选择的图记录 cohort；header `X-MoodFlow-Telemetry-Class` 仅区分 acceptance / synthetic，其他为 production。独立请求键及线程 SHA256 贯穿 detached worker；断开 SSE 后仍记录 worker 终态。
- `jev.handoff_entry` 标记真正 V5 Single / Multi / fallback / error、attempt_class、候选完整性、API 调用数和耗时。`jev.handoff_entry_local` 标记快判/澄清/未尝试 V5 的 Hybrid。快判不进入 V5 成功率分母。
- `jev.handoff_guard` 记录原始 fidelity / authority、派生 `min(fidelity,1-authority)`、四个要求区间及 missing；只旁路记录既有阈值和判定。
- `handoff.trace` 记录 from/to、reason_code、duration_ms、tokens；本地 accepted 边的 0 token 按实际保留，模型 token 见同请求的 model_call 事件。
- 每次回退产生关联 fallback / alert；主机接收器白名单过滤、事件去重、fsync 持久化，告警交付到 root 私有 `alerts.jsonl`，生成 delivery receipt。该接收目的地是本机文件，没有外部消息通知。
- ACK 按原 graph/thread/action 身份摘要关联不可变已保存回执，唯一 action 为分母。60 秒内 applied/rejected、到期未回执或迟到回执 timed_out、未到期 pending 单列；重复生成/回执不扩大分母。pending 不计成功，空分母 null。
- 真实、验收、synthetic 的 canary / non-canary / explicit 分开统计。Foundation 的实际 Hybrid 决策旁路事件提供同期基线，Foundation Shadow 数据不替代实际 handoff 候选。
- 五分钟窗口按准备报告 §5：至少 20 个 V5 尝试，错误 >5% 或有效基线 +3pp；P95 >5000ms 或有效基线 2 倍（Single/Multi 分组也检查）；回退 >10% 或有效对照 +5pp；至少 20 个已满 60 秒 action 的失败 >5%。连续两个独立相邻窗口才触发；无有效基线保留绝对阈值，空分母不判健康。错 cohort、阈值漂移、观测缺失或非预期执行立即回滚。

## 3. 本地验证

定向命令（根目录，先设置 `$env:PYTHONPATH=(Get-Location).Path`）：

```powershell
ai-service/.venv/Scripts/python.exe docs/reports/rule-layer-r2-20261003/check.py ../docs/reports/v5-canary-20261003/pytest_entry.py tests/test_handoff_observability.py tests/test_handoff_jev_routing.py tests/test_handoff_guard.py tests/test_action_ack_namespace.py ../deploy/tests/test_handoff_observer.py ../deploy/tests/test_deployment.py ../deploy/tests/test_update.py -q
ai-service/.venv/Scripts/python.exe docs/reports/rule-layer-r2-20261003/check.py ../docs/reports/v5-canary-20261003/pytest_entry.py tests ../deploy/tests -q --junitxml=../.dev/handoff-observability-canary-20261004/full-final.xml
```

初次定向夹具把默认 Curator 作为 self-transfer 起点，9 failed / 93 passed；修正夹具为实际 GeneralChat→Curator 后 125 passed，未弱化断言。首轮全量 2148 passed、288 subtests passed、45 skipped、178 既有 durability warnings，273.30s。增加三项窗口边界/连续窗口/ACK 样本测试后，接收器及发布门控定向 34 passed；最终全量 **2151 passed、288 subtests passed、45 skipped**，178 既有 warnings，259.23s。Ruff 所有修改 Python 文件通过，compileall 通过。

45 项既有外部集成跳过条件未改变，不计为本轮通过。未改 Java/Node/前端，不重复其构建。生产镜像构建及真实供应商 Single / Multi 候选已通过下述在线验收。

## 4. 发布计划与关闭状态验收

只重建 ai-service；postgres/qdrant/netease/java/frontend/migrate 的容器和镜像保持不变；无新迁移，migrate 不重跑。不修改 Compose 或环境文件、secrets 挂载。通过无 push Git bundle 提供精确目标对象，部署脚本必须与目标 Git blob 校验一致。`deploy/update.py --services ai-service --approve-contract` 的专用审查仅允许本轮观测路径、测试和报告，拒绝 Compose/submodule 漂移及其他业务模块；保留备份、完整性、FF-only、镜像溯源、七服务和严格 TLS 烟雾门控。

发布前最新完整备份：`/secure/moodflow/backups/manifests/20261004T063953Z-613db731.json`，`bash deploy/backup.sh --predeploy` 成功；随后 `bash deploy/backup.sh --verify <该路径>` 独立隔离恢复复验 **postgres passed、qdrant passed**。

部署 run_id `20261004T064429Z-6667b70e`，北京时间 **14:44:29 启动、14:45:46 验收完成**，通过精确 bundle `9edf9d32a6a8375eb0532d41a9e44735d0de736b2fb920d10338fbef1aca48d8`、`--services ai-service --approve-contract` 执行既有 updater。其第二次强制完整备份 `20261004T064431Z-6667b70e` 也经独立恢复校验后才允许构建。发布 HEAD **6667b70ecc1ad27786559bacb389f6f65d42098b**，AI 标签 `moodflow/ai-service:release-6667b70ecc1a`，镜像 `sha256:7619d3f85457d9413c6577bae9209b6d5cc68f7cea82224dbfba408edea565b3`，没有迁移。见 [发布记录](deployment-progress.json)、[部署范围及源码验证](deployment-verify.json)。

六常驻 healthy、migrate exited 0；双开关 false、foundation、router Shadow true、Guard 0.8；原 12 源码及新 observability/runtime 文件与 Git blob SHA256 一致。其余六服务的镜像及容器 ID 全部一致，所有服务挂载和排除双开关后的环境指纹一致。严格 TLS 首页/healthz/auth、Foundation SSE 和 worldline 恢复均通过；额外 [Foundation 搜索烟雾](foundation-smoke.json) 返回 intent_router→tool_calling_subgraph、搜索 3 首歌曲、无 action。匿名歌单 401 为预期鉴权边界，本轮没有重做已登录歌单只读验证，不把脚本模板中的旧浏览器标记当本轮证据。

北京时间 14:47:49 安装精确 Git blob 接收器，`moodflow-handoff-observer.service` active，版本及私有路径见 [安装记录](observer-install.json)。14:49:00 新镜像隔离进程执行真实 HandoffEntryRouter 注入超时客户端，实际产生 fallback→alert；14:49:01 同一白名单接收器 fsync 交付 `alerts.jsonl`，关联 IDs 完整，见 [触发与交付演练](fallback-drill.json)。来源 synthetic；供应商调用 0、dispatch 0、配置变更 0。该演练通过相同生产 formatter/parser/receiver，但隔离进程 stdout 由 driver 交给接收器，不声称经过在线请求的 Docker logs 管道；在线管道由后续 acceptance 验证。

[关闭状态统计](closed-summary.json)：在线 Foundation acceptance 2 个请求均有终态与实际 routing 事件；无真实 canary 请求。1 个 synthetic fallback 告警生成并交付；logging_errors / invalid / unscoped 均 0。synthetic 数据未计入真实或 acceptance 分母。双开关一键关闭脚本已经准备并校验 [prepared.json](prepared.json)。

应用发布任一验证失败：按本次 updater run_id 回滚旧 ai-service 镜像 `sha256:19dad4f4cb3746191dbe44832ce026172498e0659316f225ea3d57841c98c24d` 及生产 HEAD 613db731，复核六服务 healthy、migrate exited 0、双开关 false、Foundation 老链路。回滚记录必须保留。

## 5. 重开、硬门控与回滚

发布验收全绿后，通过私有 Compose service override 仅改变两个 AI 环境键；缓存设置用相同新镜像重建 AI 服务加载。Shadow true、默认 foundation、Guard 0.8、10% session 不变。同时关闭双开关的一键脚本及配置比较、镜像/挂载/其他环境指纹比较已通过。

acceptance 探针：规则未命中 Single、跨搜索与聊天的 Multi、确定 non-canary 的对照；候选分类、Guard 非空分数、accepted 转交 trace 任一失败立即关闭双开关，验证 Foundation+Hybrid 恢复。只凭 handoff SSE 节点或最终 Hybrid 成功不算 V5 通过。

实际重开：北京时间 **14:52:55** 写入 root 私有 `/opt/moodflow-runtime/handoff-canary-reopen-20261004/switches.yml`，仅两个 AI 环境键为 true；使用 `docker compose --env-file .env.production -f docker-compose.production.yml -f <该 override> up -d --no-deps --no-build --force-recreate ai-service` 同镜像加载，**14:53:29 healthy**。见 [open.json](open.json)。其余 Compose 配置、环境/挂载指纹及所有镜像一致，其余六容器 ID 不变；foundation / 10% / Shadow true / Guard 0.8 复核通过。主机接收器在重建后仍 active，能重新跟随新容器。

一键回滚：`/opt/moodflow-runtime/handoff-canary-reopen-20261004/rollback.sh`；同时将两键置 false，复用同镜像只重建 AI，不修改环境/secrets 文件。

第一次硬验收的 Single 已产生有效候选（346.89ms）及 trace，但探针误用旧版本内部 SSE 节点名，判为 `single_handoff_node_missing`。当前 handoff streaming 明确复用 Foundation public projection，因此出现 `intent_router/general_chat_subgraph` 正常，不能据此区分内部 graph。驱动 **14:54:38 立即回滚、14:55:17 healthy**，双开关 false、全部作用域检查通过；完整记录 [hard-gates-first.json](hard-gates-first.json)，没有删除这次误判。回滚后第一条 Foundation 恢复请求业务 failed，保留 [first-recovered.json](first-recovered.json)；随后相同链路的搜索、匿名鉴权、Foundation 搜索完整烟雾成功，见 [first-recovery-smoke.json](first-recovery-smoke.json)。未改路由或工具逻辑，未把中间失败改成通过。

修正私有探针，依据实际 graph/cohort、候选及 handoff.trace，不再依赖兼容输出节点名。使用全新验收 thread。第二次 **14:58:12 翻转、14:58:46 healthy**，同镜像、同两键 override；[open-second.json](open-second.json)。**14:59:06–14:59:38 硬验收全绿**：[hard-gates-second.json](hard-gates-second.json)。Single / Multi 各 1 个完整真实 V5 candidate（分别 1 / 2 次决策 API），Multi 跨搜索与聊天；Guard 分数 2 个，accepted 跨 agent 转交 2 条，trace 17 条。non-canary 对照 1 请求，实际 Foundation+Hybrid routing 事件 1，未产生 handoff 入口事件。Guard 0.8、foundation、router Shadow true、10% 与双开关 true 复核通过。

观察驱动异常回滚及用户取消等待后，最终于 **15:49:20** 用相同 `handoff-control.py open` 及同一 override 写入双开关 true；**15:49:54 healthy**，[open-final.json](open-final.json)。镜像、挂载、排除双开关后的环境指纹均未变，其余六服务容器 ID 不变，没有重新部署或构建。**15:51:36–15:52:06** 使用全新 r3 thread 的 Single / Multi / non-canary 三条短验收全绿，[hard-gates-final.json](hard-gates-final.json)。有效 V5 Single / Multi 各 1，Guard 2 个，accepted 跨 agent 转交 2 条、trace 17 条；non-canary 仍为 Foundation+Hybrid，未产生 handoff 入口。**15:52:54** 最终 [运行快照](final-snapshot.json)：生产 HEAD 6667b70e、源码 hash 一致、六常驻 healthy、migrate exited 0、双开关 true、foundation、10%、router Shadow true、Guard 0.8。没有启动新的长观察驱动。

## 6. 初始观察与真实流量抽样

原观察从第二次重开 healthy **14:58:46** 起算。15:00:58 启动有限观察 watchdog，[observation-launch.json](observation-launch.json)；最终用户明确指示“不用进行两个小时观测了，你直接开闸”。本轮不再等待两小时或 100 个真实请求，保留已产生快照，不把 acceptance 补入真实流量。

第二次硬验收快照：acceptance V5 2/2 成功、Single 305.22ms / Multi 315.01ms，合并 P50 310.12ms / P95 314.52ms，回退/错误/澄清 0；Guard 派生和 fidelity 四区间为 `0/0/0/2`、authority 为 `2/0/0/0`，missing 0。验收对照 Hybrid 101.35ms，错误/澄清 0。随后增加快判澄清及 ACK 关联验收，汇总如下；不据此判断真实用户健康。

15:40:34 有限观察驱动发生 `ValueError`，按其保守异常分支于 **15:41:10** 完成双开关关闭；六常驻服务 healthy、migrate exited 0、Foundation 配置恢复，见 [watch-failure.json](watch-failure.json)、[automatic-rollback.json](automatic-rollback.json)。主机 Python 为 3.10.12，该版本不能用 `datetime.fromisoformat` 解析驱动传入的 Docker 九位小数时间戳，已用相同格式独立复现；应用接收器仍 active，事件解析及模型调用未失败。这是观察驱动兼容性问题，未触发业务错误率/延迟/回退/ACK 阈值。之前仅在本地 Python 3.12 跑过驱动辅助测试，未覆盖生产解释器兼容性，此限制据实保留。

**15:48:40** 确认有限观察进程已退出、不再启动；接收器保持 active，[observation-stop.json](observation-stop.json)。用户取消观察等待，不删除采集数据。当前仅保留持续采集和 fallback 文件告警；有限观察驱动的自动阈值回滚不再运行，一键双开关回滚仍可用。

截至 **15:40:18** 的最后有效快照（14:58:46 起，2492 秒，约 41 分 32 秒）见 [observation-progress.json](observation-progress.json)：

| 指标 | acceptance canary | acceptance non-canary | 真实 canary / 对照 |
| --- | --- | --- | --- |
| 请求始/终 | 6 / 6 | 1 / 1 | 0 / 0 |
| 状态 | success 5，needs_input 1 | success 1 | 无请求 |
| V5 候选 | Single 4，Multi 1，5/5 成功 | 不适用 | 0；成功率 null |
| V5 P50 / P95 | 306.44 / 360.35 ms | Hybrid 101.35 / 101.35 ms | null |
| V5 错误 / 回退 | 0% / 0% | 不适用 | null |
| 快判命中 | 1/6 | 0/1 | 0；命中率 null |
| 错误 / 澄清 | 0% / 16.67% | 0% / 0% | null |
| Guard 派生分布 `[0,.7)` / `[.7,.8)` / `[.8,.9)` / `[.9,1]` | 0 / 1 / 3 / 2，missing 0 | 不适用 | 0 / 0 / 0 / 0 |
| trace / accepted 跨 agent 转交 | 49 / 6 | 0 / 0 | 0 / 0 |
| ACK 唯一 action | 3 | 0 | 0 |
| applied / rejected / timed_out / pending | 0 / 1 / 2 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |

ACK 验收未驱动真实播放器：暂停请求因无当前歌曲产生澄清、无 action；音量探针实际产生 action，未应用或未回执的两条按 60 秒 deadline 计 timed_out。一条音量 action 被验收客户端明确拒绝，发送两次实际 failed 回执均 HTTP 200，唯一 action 只计一次 rejected。首版探针读取错误的嵌套 owner 字段而断言失败，留下其中一次真实超时；修正为 SSE 顶层 graph/thread 关联后通过，记录保留 [ack-rejected-first.json](ack-rejected-first.json)、[ack-rejected-probe.json](ack-rejected-probe.json)。未伪造 applied。验收 ACK 成功率 0 是主动拒绝/超时测试结果，真实 ACK 成功率 null，不能推断播放器生产成功率。

最终重开后短验收快照 [final-summary.json](final-summary.json)：V5 2/2 成功，Single 307.45ms、Multi 468.95ms，合并 P50 388.20ms / P95 460.88ms，错误/回退/澄清 0；Guard 派生分布 `0/0/2/0`、missing 0；对照 Hybrid 66.94ms，错误/澄清 0。真实请求与 ACK 分母仍 0，比率 null。这是短验收结果，不是两小时观察结果。

数据质量 [quality-final.json](quality-final.json)：14:58:46 后共 109 个白名单事件、10 个请求及 10 个终态（含重开前后 acceptance），重复/孤儿请求、标签不一致、孤儿 ACK、非法分数/耗时、阈值漂移、解析/日志错误均 0。最终重开后的独立 HTTP access 与已采集 chat start 为 **3 / 3**，[traffic-crosscheck-final.json](traffic-crosscheck-final.json)，全部明确标记 acceptance。上次容器已经重建，不把当前容器 access 当成旧区间的完整 access 序列；旧区间另留 [traffic-crosscheck-initial.json](traffic-crosscheck-initial.json) 的独立快照。原始请求、响应和原始日志均未导出至报告。

真实 canary 快判采样目标 ≥50。当前实际真实请求、快判命中、capture、离线 Replay 可比较样本均 **0**；分歧数 0，分歧率 **null**，目标未达，R2 遗留项保持未完成。已有验收样本不冒充真实样本。后续仅在有真实流量时保存访问受控 RoutingContext / 快判签名 / 供应商 capture，沿用 R2 ReplayClient 的严格 state SHA256、question names、未消费响应校验，禁止 dispatch，回退不进入可比较分母。本轮不延长等待采样，也不声明“关闭 R2”。

采样交付记录见 [real-traffic-sampling.json](real-traffic-sampling.json)，实际样本数及不可计算项明确记录。

## 7. Commit 与结论

观测修复 commit **7346cf972419a63ad82a665136f7fb76663c74e4**；部署门控及实际生产代码 commit **6667b70ecc1ad27786559bacb389f6f65d42098b**；部署验收记录 **f9ba5dc781a4652dbb214346e71e961fe487075f**；第一次双开关翻转记录 **e639b3a002018ae49676039fe0246154401adb56**；第二次开闸、误判回滚及硬验收记录 **e3571634c77d8509595d427cef47912a9c1559c0**；最终开闸配置及硬验收独立 commit **20f4d11f0771b36606a18934c45d7bdeb01ec614**（[opening-record.md](opening-record.md)）。最终统计报告另行提交，hash 见 Git history。未 push。

本轮按最新指令结束等待，只保留 10% canary，不进行扩大/全量决策。真实请求与 ACK 分母为 0，生产指标不具备统计结论，R2 真实 Replay 未完成；已登录歌单本轮浏览器会话过期，未重新验证。48h 决策不在本轮。

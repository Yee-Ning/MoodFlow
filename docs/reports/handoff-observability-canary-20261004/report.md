# Handoff 观测修复与 Canary 重开（2026-10-04）

状态：观测修复及生产发布验收通过，双开关已翻转；正在执行重开硬验收，失败即回滚。

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

45 项既有外部集成跳过条件未改变，不计为本轮通过。未改 Java/Node/前端，不重复其构建。生产镜像构建及真实供应商候选仍待本轮生产验证，离线 mock 测试不能代替。

## 4. 发布计划与关闭状态验收

只重建 ai-service；postgres/qdrant/netease/java/frontend/migrate 的容器和镜像保持不变；无新迁移，migrate 不重跑。不修改 Compose 或环境文件、secrets 挂载。通过无 push Git bundle 提供精确目标对象，部署脚本必须与目标 Git blob 校验一致。`deploy/update.py --services ai-service --approve-contract` 的专用审查仅允许本轮观测路径、测试和报告，拒绝 Compose/submodule 漂移及其他业务模块；保留备份、完整性、FF-only、镜像溯源、七服务和严格 TLS 烟雾门控。

发布前最新完整备份：`/secure/moodflow/backups/manifests/20261004T063953Z-613db731.json`，`bash deploy/backup.sh --predeploy` 成功；随后 `bash deploy/backup.sh --verify <该路径>` 独立隔离恢复复验 **postgres passed、qdrant passed**。

部署 run_id `20261004T064429Z-6667b70e`，北京时间 **14:44:29 启动、14:45:46 验收完成**，通过精确 bundle `9edf9d32a6a8375eb0532d41a9e44735d0de736b2fb920d10338fbef1aca48d8`、`--services ai-service --approve-contract` 执行既有 updater。其第二次强制完整备份 `20261004T064431Z-6667b70e` 也经独立恢复校验后才允许构建。发布 HEAD **6667b70ecc1ad27786559bacb389f6f65d42098b**，AI 标签 `moodflow/ai-service:release-6667b70ecc1a`，镜像 `sha256:7619d3f85457d9413c6577bae9209b6d5cc68f7cea82224dbfba408edea565b3`，没有迁移。见 [发布记录](deployment-progress.json)、[部署范围及源码验证](deployment-verify.json)。

六常驻 healthy、migrate exited 0；双开关 false、foundation、router Shadow true、Guard 0.8；原 12 源码及新 observability/runtime 文件与 Git blob SHA256 一致。其余六服务的镜像及容器 ID 全部一致，所有服务挂载和排除双开关后的环境指纹一致。严格 TLS 首页/healthz/auth、Foundation SSE 和 worldline 恢复均通过；额外 [Foundation 搜索烟雾](foundation-smoke.json) 返回 intent_router→tool_calling_subgraph、搜索 3 首歌曲、无 action。匿名歌单 401 为预期鉴权边界，本轮没有重做已登录歌单只读验证，不把脚本模板中的旧浏览器标记当本轮证据。

北京时间 14:47:49 安装精确 Git blob 接收器，`moodflow-handoff-observer.service` active，版本及私有路径见 [安装记录](observer-install.json)。14:49:00 新镜像隔离进程执行真实 HandoffEntryRouter 注入超时客户端，实际产生 fallback→alert；14:49:01 同一白名单接收器 fsync 交付 `alerts.jsonl`，关联 IDs 完整，见 [触发与交付演练](fallback-drill.json)。来源 synthetic；供应商调用 0、dispatch 0、配置变更 0。该演练通过相同生产 formatter/parser/receiver，但隔离进程 stdout 由 driver 交给接收器，不声称经过在线请求的 Docker logs 管道；在线管道由后续 acceptance 验证。

[关闭状态统计](closed-summary.json)：在线 Foundation acceptance 2 个请求均有终态与实际 routing 事件；无真实 canary 请求。1 个 synthetic fallback 告警生成并交付；logging_errors / invalid / unscoped 均 0。synthetic 数据未计入真实或 acceptance 分母。双开关一键关闭脚本已经准备并校验 [prepared.json](prepared.json)。

应用发布任一验证失败：按本次 updater run_id 回滚旧 ai-service 镜像 `sha256:19dad4f4cb3746191dbe44832ce026172498e0659316f225ea3d57841c98c24d` 及生产 HEAD 613db731，复核六服务 healthy、migrate exited 0、双开关 false、Foundation 老链路。回滚记录必须保留。

## 5. 重开、硬门控与回滚

待 Phase 4 全绿，通过私有 Compose service override 仅改变两个 AI 环境键；缓存设置用相同新镜像重建 AI 服务加载。Shadow true、默认 foundation、Guard 0.8、10% session 不变。准备同时关闭双开关的一键脚本及配置比较、镜像/挂载/其他环境指纹比较。

acceptance 探针：规则未命中 Single、跨搜索与聊天的 Multi、确定 non-canary 的对照；候选分类、Guard 非空分数、accepted 转交 trace 任一失败立即关闭双开关，验证 Foundation+Hybrid 恢复。只凭 handoff SSE 节点或最终 Hybrid 成功不算 V5 通过。

实际重开：北京时间 **14:52:55** 写入 root 私有 `/opt/moodflow-runtime/handoff-canary-reopen-20261004/switches.yml`，仅两个 AI 环境键为 true；使用 `docker compose --env-file .env.production -f docker-compose.production.yml -f <该 override> up -d --no-deps --no-build --force-recreate ai-service` 同镜像加载，**14:53:29 healthy**。见 [open.json](open.json)。其余 Compose 配置、环境/挂载指纹及所有镜像一致，其余六容器 ID 不变；foundation / 10% / Shadow true / Guard 0.8 复核通过。主机接收器在重建后仍 active，能重新跟随新容器。

一键回滚：`/opt/moodflow-runtime/handoff-canary-reopen-20261004/rollback.sh`；同时将两键置 false，复用同镜像只重建 AI，不修改环境/secrets 文件。硬门控驱动遇到任一候选、Guard 或 trace 验证失败立即执行关闭并重新复核配置、全部服务和作用域。实际路径验收及是否回滚：正在执行。

## 6. 初始观察与真实流量抽样

尚未开始本轮观察，不把上次 78 秒或发布前日志算作观察时长。硬门通过后，≥2 小时或 ≥100 个真实 canary 请求先到者形成快照；验收探针不补真实流量分母。报告 V5 成功/错误/回退、Single/Multi P50/P95、Guard 三组四区间、唯一 action ACK、错误/澄清与同期非 canary 基线；缺失事件、无流量及空分母显式记录。

真实 canary 快判采样目标 ≥50。保存原请求 RoutingContext、快判签名和真实供应商 capture 于访问受控目录，不进 Git；隔离评测禁止 dispatch，沿用 R2 ReplayClient 严格 state SHA256 / question names / 未消费响应匹配。回退不进入可比较分母。流量不足记录实际数；0 样本时分歧率 null，R2 遗留项保持未完成。

## 7. Commit 与结论

观测修复 commit **7346cf972419a63ad82a665136f7fb76663c74e4**；部署门控及实际生产代码 commit **6667b70ecc1ad27786559bacb389f6f65d42098b**；部署验收记录 **f9ba5dc781a4652dbb214346e71e961fe487075f**；本次双开关翻转记录单独提交，hash 由最终记录给出。未 push。最终结果、继续观察建议及未完成项：待执行后填写。48h 扩大/全量/回滚决策不在本轮。

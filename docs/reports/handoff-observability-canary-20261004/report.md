# Handoff 观测修复与 Canary 重开（2026-10-04）

状态：观测修复已通过离线验收，生产发布及重开尚未执行；下列生产结果在执行后据实补充。

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

发布前最新完整备份：`/secure/moodflow/backups/manifests/20261004T063953Z-613db731.json`，`bash deploy/backup.sh --predeploy` 成功；随后 `bash deploy/backup.sh --verify <该路径>` 独立隔离恢复复验 **postgres passed、qdrant passed**。发布记录、镜像标签、关闭开关及 Foundation 验证结果：待执行。主机接收器另行安装已提交脚本和 systemd unit，保存代码 SHA256 / active 状态。回退告警触发演练将在新镜像隔离进程执行真实 router 的受控超时回退（synthetic），不 dispatch、不调用供应商、不改实时路由；通过同一接收器核对 fallback→alert→delivery 关联。

应用发布任一验证失败：按本次 updater run_id 回滚旧 ai-service 镜像 `sha256:19dad4f4cb3746191dbe44832ce026172498e0659316f225ea3d57841c98c24d` 及生产 HEAD 613db731，复核六服务 healthy、migrate exited 0、双开关 false、Foundation 老链路。回滚记录必须保留。

## 5. 重开、硬门控与回滚

待 Phase 4 全绿，通过私有 Compose service override 仅改变两个 AI 环境键；缓存设置用相同新镜像重建 AI 服务加载。Shadow true、默认 foundation、Guard 0.8、10% session 不变。准备同时关闭双开关的一键脚本及配置比较、镜像/挂载/其他环境指纹比较。

acceptance 探针：规则未命中 Single、跨搜索与聊天的 Multi、确定 non-canary 的对照；候选分类、Guard 非空分数、accepted 转交 trace 任一失败立即关闭双开关，验证 Foundation+Hybrid 恢复。只凭 handoff SSE 节点或最终 Hybrid 成功不算 V5 通过。

实际翻转、时间、方式、硬门控与回滚证据：待执行。

## 6. 初始观察与真实流量抽样

尚未开始本轮观察，不把上次 78 秒或发布前日志算作观察时长。硬门通过后，≥2 小时或 ≥100 个真实 canary 请求先到者形成快照；验收探针不补真实流量分母。报告 V5 成功/错误/回退、Single/Multi P50/P95、Guard 三组四区间、唯一 action ACK、错误/澄清与同期非 canary 基线；缺失事件、无流量及空分母显式记录。

真实 canary 快判采样目标 ≥50。保存原请求 RoutingContext、快判签名和真实供应商 capture 于访问受控目录，不进 Git；隔离评测禁止 dispatch，沿用 R2 ReplayClient 严格 state SHA256 / question names / 未消费响应匹配。回退不进入可比较分母。流量不足记录实际数；0 样本时分歧率 null，R2 遗留项保持未完成。

## 7. Commit 与结论

观测修复、部署和开闸分别独立 commit；本报告后续记录已产生的完整 hash，不写自引用假值。未 push。最终结果、继续观察建议及未完成项：待执行后填写。48h 扩大/全量/回滚决策不在本轮。

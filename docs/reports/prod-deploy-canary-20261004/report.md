# 生产部署与 V5 Canary（2026-10-04）

**当前结论：新后端发布器检查通过；最终登录歌单读待复验。Phase 2 为 NO-GO，未开闸。** 首次部署因 AI 启动循环导入失败，已回滚并验证恢复；修复部署启动器、增加新进程检查、完整回归后重新部署成功。V5 active 入口配置和观测尚未满足，仅翻 canary 不能达成所要求的路径；未越过“其他生产配置不动”的范围。整体开闸目标未完成。

正文时间为 2026-10-04 Asia/Shanghai（UTC+8）；JSON 保留原采集 UTC。

## 1. 基线和硬门控

独立分支 `codex/prod-deploy-canary-20261004`。本地开始完整 HEAD：`a683b826c839cd78eec3b03e24d78051f1cf3711`。生产 ECS `47.83.162.191`、`/opt/moodflow`，开始 HEAD：`3a11237cbce78ffb1e8ce6dc31db681c1430e360`，main 干净。最终部署目标：`613db7311e4c224c1ee3a57f4693d3e0322090a7`。

开始前逐项执行 `git merge-base --is-ancestor <ref> HEAD`，发布前再对最终目标执行；八项都返回 **0**。完整 hash 和返回值见 [verification-summary.json](verification-summary.json)。

| 必需项 | commit | 已提交验收报告 / 边界 |
|---|---|---|
| B | f97d5d2 | 部署分支包含；本任务保留旧 frontend 镜像 |
| A 三态记账 | 588fa6f | [A](../write-path-unknown-20261003/report.md)，通过 |
| C 组合修复 | 37ffdab | [C](../combo-robustness-20261003/report.md)，沿用核心通过口径，保留 V7 限制 |
| R2 span | feea347 | [R2](../rule-layer-r2-20261003/report.md)，修复通过，真实采样遗留未关闭 |
| D 恢复 | a3cc649 | [D](../recovery-selfhealing-20261003/report.md)，通过 |
| T1/T2 AI | b983411b | [工具报告](../tools-t1t2-20261003/report.md)，通过 |
| T1/T2 Java | c4096608 | 同上，92 项测试和既有真实账号链路 |
| canary 准备 | 08d18c5 | [准备报告](../v5-canary-20261003/report.md)，原结论 NO-GO，不作为已开闸证明 |

## 2. 部署前验证、迁移、备份、审查

### 验证命令

沿用 R2 包装器：只临时恢复与 Git 一致的冻结文件 LF，在 finally 恢复原字节；不改 .env。最终联合全量：

```powershell
$env:PYTHONPATH=(Get-Location).Path
ai-service/.venv/Scripts/python.exe docs/reports/rule-layer-r2-20261003/check.py ../docs/reports/v5-canary-20261003/pytest_entry.py tests ../deploy/tests -q --junitxml=../.dev/prod-deploy-canary-20261004/full-tests-startup-fix.xml
```

- **2114 passed、45 skipped、288 subtests passed、0 failures/errors**，348.70s；178 条既有 LangGraph durability 警告。JUnit 共 2447 项（含子用例），摘要已归档。首次联合集合 2111 passed、bundle 修正后 2113 passed，最终以启动修复后的结果为准。
- 独立部署集合：同一包装器将测试参数替换为 `../deploy/tests -q`，最终 **103 passed**，19.75s；新增独立子进程调用实际 `validate_index`，验证导入成功后仍因缺失 frozen artifacts 拒绝启动。
- 发布器定向：`ai-service/.venv/Scripts/python.exe -m pytest deploy/tests/test_update.py deploy/tests/test_update_bundle.py -q`，**25 passed**。
- Java：在 music-backend，`JAVA_HOME=D:/code/JDKall/openjdk-21.0.1`，`D:/apache-maven-3.9.4/bin/mvn.cmd --batch-mode --no-transfer-progress -o verify`，**92 tests、0 failures/errors/skipped、BUILD SUCCESS**。生产 Dockerfile 同样执行 `mvn -B verify` 后生成 JAR。
- 子模块：`pnpm --config.verify-deps-before-run=false exec mocha -r intelli-espower-loader -t 60000 main.test.js test/playlist_tracks.test.js --exit`，**6 passing**；`pnpm --config.verify-deps-before-run=false exec eslint main.test.js test/playlist_tracks.test.js` 通过。
- Ruff 检查本轮 deploy/update.py、deploy/runtime.py 及相关测试通过；`python -m compileall -q` 通过。没有前端代码/镜像改动。
- 隔离生产镜像依赖检查：无网络、无 secret/生产数据挂载，使用已提交修复启动器；导入成功，冻结数据缺失仍被拒绝，[cold-start-probe.json](cold-start-probe.json)。首次临时源码 fixture 为 root 600，app 用户无法读；仅将该公开源码 fixture 改为可读后通过，未改生产挂载。

**失败记录：** 独立部署集合曾 1 failed/101 passed，循环导入被误判为联合入口已覆盖；真实启动失败后已回滚并修复部署导入顺序。原 frozen schema/release/41 payload 断言保留，复用实际启动依赖入口，并新增新进程检查，独立和联合集合均已通过。另一个 Windows 路径 fixture 比较失败修正为 Path 对象比较后通过；未删、跳过或弱化失败断言。

45 项 skip 涉及外部/隔离 PostgreSQL、进程恢复、Qdrant、TypeSafe 等环境条件；离线集合不能替代这些外部验收。供应商远程完整 Mocha、缺 deepeval 的额外 eval 集合未执行。未声称完整 LLM/UI 写链路或播放器 ACK 验收通过。

### 迁移与服务范围

旧 HEAD 到目标 migration diff 只有 P1 已于 10-02 执行的 003/004，无新迁移。dry-run 验证批准 SQL SHA256、ledger、History event_key/event_seq 两列、两个有效唯一索引、不可变触发器和 Execution 六列，[deployment.json](deployment.json) 的 `already_applied_p1` 为证据。实际 `migration_paths=[]`、`migration=not_needed`，未运行 migrate。

只重建/启动 **netease、java、ai-service**；postgres/qdrant/frontend/migrate 的原容器及 image IDs 保留。B 的前端改动包含在 Git 历史但未发布到运行前端，符合本任务范围。子模块由 `321c25bd7d041711f1a9ab9e4b55997ce661313c` 更新至主仓固定的 `ba9236e5cdf5eaeaffb330850d268e5101c31f69`。

### 最新备份和完整性

每次部署前执行既有 `bash deploy/backup.sh --predeploy`；发布器在 build/start 前另强制备份。以下四份 manifest 均 `status=complete`、`restore_verified=true`，包含文件 SHA256/大小、隔离 PG restore 表计数、Qdrant 41 points/green/schema 对照。首次人工备份再执行 `--verify <manifest>`，PG/Qdrant 都 passed。

| 用途 | 标识（服务器目录 /secure/moodflow/backups/manifests/） | 证据 |
|---|---|---|
| 首次部署前 | 20261004T044756Z-3a11237c | [backup-before.json](backup-before.json) |
| 首次发布器备份 | 20261004T044917Z-f6b7a421 | [backup-updater.json](backup-updater.json) |
| 修复重试前 | 20261004T050743Z-3a11237c | [backup-retry-before.json](backup-retry-before.json) |
| 最终发布器备份 | 20261004T050906Z-613db731 | [backup-retry-updater.json](backup-retry-updater.json) |

最终备份 **13:09:26** 校验完成：PG 24,090,152 bytes，SHA256 `5e957fd8ef2c1379dd7c9f7720b5229595cc6db519c14be53f7f86d84a83d482`；Qdrant 1,678,848 bytes，SHA256 `407d5fb5f929647367419b967fc6f5b1a674b568c3f6bace4eb7645560a197b4`。未把 dump/snapshot、原始用户请求/身份、Cookie 或 secrets 归档到 Git。

### 原部署审查流程

同一 deploy/update.py 增加 no-push bundle 来源和三个服务定向发布，保留目录/main/upstream/干净工作树/固定子模块、契约批准、8GiB 余量、强制恢复验证备份、FF-only、全服务健康、公网 smoke、旧镜像回滚门控。

bundle 必须位于 root 私有 staging，完整目标/SHA256 匹配、`git bundle verify` 和唯一 ref 校验通过；staging updater 必须与目标 Git blob 字节一致。`--approve-contract` 只批准相对旧 Compose 新增已审查的关闭默认 canary 接线；逐字拒绝其他配置及未审查基础设施变化。授权依据为本任务要求包含准备提交且保持其他配置不动。

首次 dry-run 因 Git 自动递归 fetch 未 push 的子模块提交而停止，未改生产 HEAD/镜像；修正主 bundle fetch 为 `--recurse-submodules=no`，独立校验/导入子模块 bundle，固定更新使用 `--no-fetch`。没有更新 origin 配置/远端 refs。最终主 bundle SHA256 `568877a4810e4cc2ade071dc87c486a2abdfbb5dca00da8017388399107bbe54`，子模块 SHA256 `5efcc8644240b0ff813c6068036783052a179fa4babd94787211132f2531cdae`。

## 3. Phase 1：变更、失败回滚、最终检查

### 首次失败并恢复

- **12:49:15** 发布 f6b7a421e229223e1f1677103078cb4da1220120，记录 `20261004T044916Z-f6b7a421`；仅三个服务生成新镜像，migrate 未运行。
- SSH 断开后只读确认发布器仍在运行，未重复启动。AI 连续启动失败，观察到 **18 次 restart**；隔离镜像复现 `validate_index → Retrieval → QueryUnderstanding → Foundation → Worldline → partially initialized Retrieval` 的 ImportError。
- 停止尚在等待健康的发布器，释放原锁，再执行 `bash deploy/update.sh rollback 20261004T044916Z-f6b7a421 --acknowledge-schema-risk`。**12:55:53** 应用恢复、DB/Qdrant untouched。原 JSON status 仍为 running（人工停止），未伪造正常结束；本报告依据实测记录此次 Phase 1 失败。[首次记录](deployment-first-attempt.json)。
- **12:56:39** 恢复 3a11237c 和全部旧 image IDs，六个常驻 healthy、migrate exited 0，[恢复状态](rollback-after.json)。原公网 health/auth/SSE/Worldline smoke 全过；搜索及 Foundation 搜索通过，[恢复 smoke](rollback-smoke-final.json)。用户登录后既有歌单显示 **18 首**。最初匿名歌单 401 为既有权限边界，失败/分步复查摘要保留。

### 修复后重试

修复 613db7311e4c224c1ee3a57f4693d3e0322090a7 仅改变部署启动依赖顺序并新增测试；完整回归、隔离镜像检查、八项 ancestry、最新备份和同一 dry-run 都通过后再次发布。

实际命令由 root 受保护输出文件、新进程 session 保持运行，避免 SSH 断开中止；仍调用同一 updater、持有原 flock。[启动参数/PID/时间](deployment-launch.json)。

```bash
cd /opt/moodflow
HANDOFF_CANARY_ENABLED=false python3 -B /secure/moodflow/deployments/staging/update.py \
  --bundle /secure/moodflow/deployments/staging/release.bundle \
  --bundle-sha256 568877a4810e4cc2ade071dc87c486a2abdfbb5dca00da8017388399107bbe54 \
  --target-commit 613db7311e4c224c1ee3a57f4693d3e0322090a7 \
  --services netease java ai-service --p1-already-applied --approve-contract
```

**13:09:03** 启动，记录 `20261004T050904Z-613db731`；**13:10:28** 发布器 `status=passed`，[deployment.json](deployment.json)。

| 服务 | 不变 release 别名 | 运行 image ID |
|---|---|---|
| ai-service | moodflow/ai-service:release-613db7311e4c | sha256:19dad4f4cb3746191dbe44832ce026172498e0659316f225ea3d57841c98c24d |
| java | moodflow/java:release-613db7311e4c | sha256:1f28d1388a37fd6be2a695083420dfb014d00fed3c49d21105ebe3a8dbab32e7 |
| netease | moodflow/netease:release-613db7311e4c | sha256:91cabd3272431f66356724d8dd21b822cf4597533a57cb8e45f2c75a992212c5 |

既有 IMAGE_TAG/.env.production 未改，Compose 活动标签名称仍沿用旧名；运行版本由 release 别名和 image ID 确认。旧 image IDs/rollback 别名保留。

### 最终逐项清单

| 验证项 | 实测结果 | 状态 |
|---|---|---|
| checkout | 完整 HEAD 613db731…，干净，门控提交已包含 | 通过 |
| 7 服务 | 六常驻 healthy；migrate exited 0 | 通过 |
| AI 新源码 | 12 关键模块匹配目标，含入口/Guard/账本/自愈；新 AI restart=0 | 通过 |
| Java 来源 | 五个 controller/service/T1T2 DTO class 与本地 verify JAR 字节 hash 一致 | 通过 |
| canary 关闭 | 容器明确存在 HANDOFF_CANARY_ENABLED=false；有效 percentage=10 | 通过 |
| 默认图/Guard | foundation；Foundation JEV_ROUTER_ENABLED=false，沿用 Hybrid；Guard threshold=0.8，原 Shadow 值保留 | 通过 |
| 范围 | 七服务环境（排除 canary）/挂载 hash 不变；postgres/qdrant/frontend/migrate 容器/image ID 不变 | 通过 |
| 原公网 smoke | SSE update 0.102s/final 1.700s；Worldline 一次创建/读取，9 nodes | 通过 |
| 搜索/Foundation | HTTP/code 200、3 首；/chat intent_router → tool_calling_subgraph，success、1 次工具调用、3.812s | 通过 |
| 新版本登录歌单读 | Java 重启使旧会话失效，重新登录后待复验 | 待完成 |
| 无 handoff | 新 AI 10m/32 行日志，无 handoff 标记；部署前后 task/session/checkpoint 都 0 | 通过（此窗口证据） |

证据：[before.json](before.json)、[after.json](after.json)、[verification-summary.json](verification-summary.json)、[java-provenance.json](java-provenance.json)、[phase1-smoke.json](phase1-smoke.json)、[浏览器验证](browser-verification.json)、[handoff-before.json](handoff-before.json)、[handoff-after.json](handoff-after.json)。只存脱敏摘要，acceptance 探针不算真实流量。首次新 AI 重建至回滚期间未达到 healthy，未统计受影响用户请求数；不据缺失基线宣称无用户影响。

## 4. Phase 2：NO-GO、观察快照、真实抽样

**未执行 HANDOFF_CANARY_ENABLED=true；翻转时间无；开闸 commit 无。** [预检](production-preflight.json) 于 **13:12:50** 采集，退出 1 表示开闸条件未满足，不是 SSH/Phase 1 服务健康失败。

运行 `JEV_HANDOFF_ROUTER_ENABLED=false`、`JEV_HANDOFF_ROUTER_SHADOW_MODE=true`（Compose 未暴露，采用源码默认值）。disabled 入口没有 V5 candidate，仅快判/Hybrid；只开 canary 无法得到 handoff+active V5。改变两个设置超出“生产变更仅限 HANDOFF_CANARY_ENABLED”，也未通过改业务默认值绕过约束。

active cohort/sample 标签和成功/错误/延迟分母、快判分母、ACK 关联、Guard 分布及 V5→Hybrid 告警交付/触发演练尚无完整证据。预检固定 Java provenance blocker 已由发布记录及五个 class hash 匹配补充；其他 active 观测/路径 blocker 未解除。

| 观察/采样要求 | 实际结果 |
|---|---|
| ≥2h 或 ≥100 canary 请求 | **未开始**；闭闸等待不计为观察期 |
| 真实 canary | **0 请求** |
| active V5 Single/Multi/回退 | **0/0/0**；成功/错误率、P50/P95、回退率 **N/A，无分母** |
| 快判命中率 | N/A，真实入口/分母缺失 |
| Guard 直方图 | N/A，0 条事件，不造零分布 |
| ACK 成功率 | N/A，本轮未生成 action，关联监控未就绪 |
| 错误/澄清率 vs 基线 | N/A，无真实 cohort 分母 |
| V5→Hybrid 告警 | 无安装、交付、触发演练证据 |
| canary/non-canary 实际路径 | 闭闸 Foundation 已验证；canary+V5 未验证 |
| ≥50 真实快判抽样 | **实际 0 例** |
| R2 provider-capture Replay | **0 例**，分歧数/率 N/A，R2 遗留未关闭 |

另有 **2 条 acceptance Shadow Single**，路由/任务数一致 2/2、Shadow P50=583.44ms、P95=761.51ms；production Shadow 样本 **0**。仅证明验收 Shadow 日志交付，不能替代 active V5/Multi/回退/ACK/Guard/真实采样；无回退日志不能判告警健康。

建议保持关闭，先在单独批准范围完成 active 入口配置、旁路观测/告警验收，再核验是否只翻一个开关足够。正式开闸后才启动观察；按 R2 方法保留去身份真实快判样本、上下文、provider 捕获结果并记录实际数/Replay 分歧。不用验收或合成请求补足 50 例。48h 决策不在本轮。

## 5. 回滚预案

最终目标任一 Phase 1 检查失败：

```bash
cd /opt/moodflow
bash deploy/update.sh rollback 20261004T050904Z-613db731 --acknowledge-schema-risk
```

恢复 3a11237cbce78ffb1e8ce6dc31db681c1430e360、固定子模块和三个旧镜像；DB/Qdrant 保留，无新迁移。复验六服务 healthy、migrate exited 0、Foundation 搜索、已登录歌单读。首次已实际回滚恢复；最终重试未再次回滚。

未来 canary 一键关闭，沿用 [canary 报告 §5](../v5-canary-20261003/report.md)：

```bash
cd /opt/moodflow
HANDOFF_CANARY_ENABLED=false docker compose --env-file .env.production -f docker-compose.production.yml up -d --no-deps --no-build --force-recreate ai-service
```

只覆盖 canary，复用同版镜像，核对 false/foundation/healthy 和正式 /chat Foundation+Hybrid。即时覆盖不等于持久配置；未来若持久 true，需由既有配置管理把同一开关持久置 false。Foundation/Handoff checkpoint 不合并，旧 canary 会话通过现有恢复流程重新进入 Foundation；不重放未知写入、补造 ACK 或重置预算。

阈值沿用准备报告：5min 窗口至少 20 个 V5 尝试，连续两窗错误率 >5% 或高于有效基线 3pp；总决策 P95 >5000ms 或 >基线 2 倍（Single/Multi 分开）；回退率 >10% 或较有效对照高 5pp。60s ACK 期限已到、至少 20 个 action，失败/无 ACK >5% 连续两窗。错 cohort/默认图变化、Guard 偏离 0.8、有 canary 但观测连续 5min 无数据、异常写入/重复执行立即关闭。空分母不能判健康；这些告警未安装验证，是不开闸原因。

## 6. Commit、范围与限制

| 独立提交 | 内容 |
|---|---|
| 8dddb065978a7f0aefd07e9e25e866d68e1de4dc | 保留审查的 bundle/三个服务发布、测试及初始计划 |
| f6b7a421e229223e1f1677103078cb4da1220120 | 离线子模块导入/固定更新及回归 |
| 613db7311e4c224c1ee3a57f4693d3e0322090a7 | 部署启动顺序及新进程回归；最终生产版本 |
| 报告及证据提交 | 独立 docs commit；用 `git log -1 -- docs/reports/prod-deploy-canary-20261004/report.md` 查看；未部署 |
| 开闸提交 | **无**，不以报告 commit 冒充开闸 |

未 push；未改路由决策、Guard 阈值、记账、ACK、secrets/secret 挂载、其他生产配置。原用户修改/删除/未跟踪数据保留，仅显式暂存本任务文件。canary 仍关闭，真实采样、active 观测和开闸路径验证未完成。

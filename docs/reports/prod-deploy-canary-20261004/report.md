# 生产部署与 V5 Canary（2026-10-04）

执行分支：`codex/prod-deploy-canary-20261004`；开始 HEAD：`a683b826c839cd78eec3b03e24d78051f1cf3711`。
生产开始 HEAD：`3a11237cbce78ffb1e8ce6dc31db681c1430e360`，ECS `47.83.162.191`、`/opt/moodflow`。

## 部署前门控与计划

按任务顺序执行 `git merge-base --is-ancestor <ref> HEAD`，B `f97d5d2`、A `588fa6f`、C `37ffdab`、R2 `feea347`、D `a3cc649`、T1/T2 AI `b983411b`、Java `c4096608`、canary 准备 `08d18c5` 全部返回 0。完整实现 hash 见前一轮 [canary 报告](../v5-canary-20261003/report.md)及本轮后续验证摘要。原有用户 dirty 文件与数据保留，不进入部署提交或 bundle 的工作树内容。

生产只读 schema 查询确认 migration ledger 有 001、002、003、004；History 的 event_key/event_seq、Execution 表和不可变触发器存在。旧 HEAD 到目标分支的 migration diff 只有已获批、已执行的 003/004，未发现其他新增文件。发布器再次验证其 P1 批准 SQL SHA256、登记记录、两个有效唯一索引、触发器和 Execution 列，才允许跳过 migrate；发现新迁移或内容变化直接停止，不通过 `--approve-migrations` 重跑。

服务计划：**只重建/重启 netease、java、ai-service**，postgres/qdrant/frontend 和既有 exited 0 migrate 容器保持不动。子模块固定到 `ba9236e5cdf5eaeaffb330850d268e5101c31f69`。分支含 B 的前端改动，但按本任务范围不发布前端镜像；这项保留版本差异须在交付中注明。

使用同一 `deploy/update.py` 扩展的离线 bundle 路径发布，不 push，不更新 origin 配置或远端 refs。发布器保留生产目录/main/upstream/干净工作树/子模块、契约批准、8GiB 余量、强制隔离恢复验证备份、FF-only、逐服务健康、真实公网 smoke 和旧镜像 rollback 门控。新增模式校验 root 私有 staging bundle 的 SHA256、唯一完整目标 hash 与 Git bundle 先决条件；staging 中运行的新 updater 必须与目标 commit 的 `deploy/update.py` blob 完全一致，不能用任意脚本代替。

契约审查范围为唯一新增的 `HANDOFF_CANARY_ENABLED: ${HANDOFF_CANARY_ENABLED:-false}` 接线。定向发布器对前后 Compose 做逐字校验，若出现其他差异、secret 挂载/基础设施变更直接停止。`--approve-contract` 的授权来自本任务明确要求包含准备提交 `08d18c5` 且保持其他生产配置不动；不会借该参数允许额外配置修改。

生产备份须先按既有 `backup.sh` 完成并隔离恢复校验；发布器在 build/start 前再强制生成 predeploy 备份。记录两次 manifest、SHA256 与验证结果后才继续。旧 image IDs 与 rollback tags 在重建前保留；新镜像另加 `release-<目标hash前12位>` 不变版本标签，不改既有 IMAGE_TAG 或其他 `.env.production` 变量。

回滚：任一 Phase 1 验证失败，使用同一发布器的 `rollback <run-id> --acknowledge-schema-risk` 恢复 `3a11237c` checkout/子模块/受影响镜像，不恢复数据库或 Qdrant；恢复后复验六服务健康、Foundation 搜索/歌单读取。本轮没有新迁移，既有 003/004 保留。

## Phase 1 执行与验证

待执行，后续记录真实备份、版本、时间、生产变更和验证结果。

## Phase 2 前置条件

当前 Compose 不传递 `JEV_HANDOFF_ROUTER_ENABLED`，代码默认 false；`JEV_HANDOFF_ROUTER_SHADOW_MODE` 默认 true。按不改其他生产配置的约束，新部署后只翻 canary 无法使 canary 流量采用 V5 决策。开闸前必须据运行证据核对，不把进入 handoff 当作进入 V5。

观测/告警前置条件沿用 [canary 报告 §4/§5](../v5-canary-20261003/report.md)：active V5、快判、ACK、Guard 和终态 cohort 统计必须有有效分母，降级告警交付须验证。未满足时不翻转开关，不以 Shadow 或合成请求替代真实样本。48h 扩大/全量/回滚决策不在本轮。

## 本地验证与限制

- 发布器定向：`ai-service/.venv/Scripts/python.exe -m pytest deploy/tests/test_update.py deploy/tests/test_update_bundle.py -q`，**25 passed**（含后补 executor/rollback 回归）；Ruff 通过。
- Java：`JAVA_HOME=D:/code/JDKall/openjdk-21.0.1`，`mvn.cmd --batch-mode --no-transfer-progress -o verify`，**92 tests，0 failures/errors/skipped，BUILD SUCCESS**。
- 网易云子模块：`pnpm --config.verify-deps-before-run=false exec mocha -r intelli-espower-loader -t 60000 main.test.js test/playlist_tracks.test.js --exit`，**6 passing**；对应两文件 ESLint 通过。供应商远程完整 Mocha 不在离线测试范围。
- AI 应用与部署全量沿用 R2 换行保护包装器和上一轮的 pytest_entry，首次 **2111 passed、45 skipped、288 subtests passed**，279.66s；后续 bundle 导入修正后的最终全量正在重跑。
- 定向新测试首次出现一项 fixture 的 Windows 路径比较失败，修正为 Path 对象比较后 23 项全通过，补充两项回归后 25 项全通过；未改失败断言或应用业务逻辑。
- 首次生产 dry-run 在 Git fetch 停止，未改生产 HEAD/镜像：Git 的递归子模块 fetch 尝试从尚未 push 的源取提交。修正为主 bundle 禁用递归网络 fetch、独立导入并验证子模块 bundle、更新固定子模块时 `--no-fetch`。没有改 origin 或绕过 bundle 校验。
- 单独运行 `pytest deploy/tests` 暴露既有 Retrieval/Foundation 的 import-order 循环依赖（1 failed、101 passed）；联合完整应用/部署集合此前通过。该独立入口失败保留记录，未修改业务模块；最终采用既有各 Phase 的联合完整验证入口。

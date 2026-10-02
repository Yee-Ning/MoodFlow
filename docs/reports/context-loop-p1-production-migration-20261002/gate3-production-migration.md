# 最后一次执行：低峰检查、003 / 004、只读复验

已获批准的目标库：`moodflow_agent`。门控 0 / 1 / 2 已通过；恢复负责人唐艺宁。本文件没有替代生产低峰检查，必须由脚本即时通过门控 3 才执行迁移。

**2026-10-02 更新：用户执行旧版命令时输出 STOP 并断开 SSH，生产事务状态待只读核对。先执行 [中断后的只读检查](gate3-interrupted-readonly.sh)，回传 ledger 和执行报告；不要重复执行旧脚本。**

已修复 [gate3-production-migration.sh](gate3-production-migration.sh) 的交互 Shell 封装，失败只退出子进程，并输出具体非敏感停止原因和前置检查阶段。但本次不继续迁移。待确认前一次生产事务状态及停止原因后，改为上传脚本文件到服务器、核对文件哈希再从文件执行；不再整段粘贴 35 KB 脚本。没有密码或完整连接凭据需要填写。

脚本重新校验有效备份、演练报告、固定镜像及迁移 SQL 哈希；以临时无密码 PostgreSQL service profile 连接。先跨约 10 秒做 6 次只读低峰观测，任何活跃请求、run、pending 或无法识别的状态均停止。通过后按手册只执行一次事务，超时固定为演练验证过的 5s / 60s，事务内再次检查数据库执行状态，再执行 003 / 004 和 ledger。数据库 SQL 错误显式 ROLLBACK；连接异常核对 ledger，状态不明也不重试。

输出出现 `PRODUCTION_MIGRATION_AND_READONLY_POSTCHECKS_FINISHED`，且 `PRODUCTION_RESULT` 中 status=passed、production_commit_confirmed=true，表示事务和只读复验完成。§3 六项原始输出位于 `RUNBOOK_SECTION_3_OUTPUT_BEGIN` 与 `RUNBOOK_SECTION_3_OUTPUT_END` 之间，逐项有编号；重复 replay key / sequence 预期返回空结果。

出现 `STOP` 时保留输出并回传；不要重复执行或放宽超时。若已经提交但复验失败，报告会保留提交证据，不自动删列、表、索引或账本。`temporary_profile_cleanup=needs_review` 表示需要检查本次临时文件清理，不能据此推定迁移没有提交。

每次结果保存在 `/secure/moodflow/migration-executions/context-loop-20261002-*/`，包括 `report.json`、`migration-output.txt` 和成功查询时的 `readonly-postchecks.txt`。真实业务写复验跳过，后续需专用测试账号独立完成；不调整灰度、默认图、`.env`，不部署、不 push。没有实际生产输出前，报告继续标记迁移未确认。

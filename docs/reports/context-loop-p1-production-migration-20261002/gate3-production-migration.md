# 当前操作：门控 3 停止，不运行迁移脚本

2026-10-02 22:40 的只读结果为 pending_controls=1、unknown_checkpoints=0；剩余 inline 的目标 general_chat_subgraph 位于非根 namespace，node 没有 seen 状态，按当前检查为 pending。证据见 [gate3-inline-control-result.json](gate3-inline-control-result.json)，完整交接见 [执行报告](execution.md)。

当前无需请求方继续粘贴命令。由 West 安排业务负责人核对当前父 checkpoint / Session lineage 与原 run 回执，确定该子图是否仍待执行，并按既有获批流程处理；不删除 checkpoint 或强制清除 pending，不在本轮授权下调用业务恢复。后续状态处理完成后，重新检查有效备份和完整低峰窗口，再继续原获批迁移。唐艺宁为数据库恢复负责人；本次未进行数据库恢复。

以下内容仅保留为后续参考，门控未通过时不得运行。

已获批准的目标库：`moodflow_agent`。门控 0 / 1 / 2 已通过；恢复负责人唐艺宁。本文件没有替代生产低峰检查，必须由脚本即时通过门控 3 才执行迁移。

**2026-10-02 20:26 更新：中断后的只读回传显示生产 ledger 仍只有 001 / 002，execution 和 event_key / event_seq 均不存在，其他活跃 psql 与 advisory locks 均为 0；003 / 004 未生效。旧脚本把随机目录中的数字 0 误写为字母 o，导致演练报告读取失败。已修复，请仅使用本目录当前的新脚本。**

已修复 [gate3-production-migration.sh](gate3-production-migration.sh)：不改变登录 Shell 选项，失败由 if 分支接住退出码，显示具体非敏感停止码；自动在限定的演练报告根目录内查找，通过 manifest / 镜像 / SQL 哈希 / 规模 / 时间与容器身份验证后，要求唯一报告。它不依赖人工抄写随机目录名，也不选择其他演练的新报告。

脚本现为 14,403 bytes，压缩 payload 逐行 76 字符，整文件最长行 109 字符；解压后再次核对已审阅源代码 SHA-256。不再粘贴原来的 35 KB 长行版本。打开新脚本，复制文件全文到 root Shell 后回车；也可上传脚本再从文件执行。仍重新校验有效备份、全部前置条件与低峰，原授权范围和 5s / 60s 不变。没有密码或完整连接凭据需要填写。

脚本重新校验有效备份、演练报告、固定镜像及迁移 SQL 哈希；以临时无密码 PostgreSQL service profile 连接。先跨约 10 秒做 6 次只读低峰观测，任何活跃请求、run、pending 或无法识别的状态均停止。通过后按手册只执行一次事务，超时固定为演练验证过的 5s / 60s，事务内再次检查数据库执行状态，再执行 003 / 004 和 ledger。数据库 SQL 错误显式 ROLLBACK；连接异常核对 ledger，状态不明也不重试。

输出出现 `PRODUCTION_MIGRATION_AND_READONLY_POSTCHECKS_FINISHED`，且 `PRODUCTION_RESULT` 中 status=passed、production_commit_confirmed=true，表示事务和只读复验完成。§3 六项原始输出位于 `RUNBOOK_SECTION_3_OUTPUT_BEGIN` 与 `RUNBOOK_SECTION_3_OUTPUT_END` 之间，逐项有编号；重复 replay key / sequence 预期返回空结果。

出现 `STOP` 时保留输出并回传；不要重复执行或放宽超时。若已经提交但复验失败，报告会保留提交证据，不自动删列、表、索引或账本。`temporary_profile_cleanup=needs_review` 表示需要检查本次临时文件清理，不能据此推定迁移没有提交。

每次结果保存在 `/secure/moodflow/migration-executions/context-loop-20261002-*/`，包括 `report.json`、`migration-output.txt` 和成功查询时的 `readonly-postchecks.txt`。真实业务写复验跳过，后续需专用测试账号独立完成；不调整灰度、默认图、`.env`，不部署、不 push。没有实际生产输出前，报告继续标记迁移未确认。

# 当前操作：运行已批准保留暂停会话的最终脚本一次

最新只读结果确认这条状态是 Session 主图，保存于 9 月 29 日，Task / Session 仍开放；原“非根 namespace 即子图”的解释错误，已纠正。原始结果见 [Session 核对](gate3-session-readonly-result.json)。项目负责人已明确批准仅保留这一条暂停状态继续 003 / 004，最终脚本已按该批准更新并通过 17 项隔离测试。

请求方已明确自己是项目开发和全程负责人，无需另找子图负责人。[已批准方案](paused-session-migration-decision.md) 不调用模型恢复，不删除或清空状态；003 / 004 不修改 checkpoint / Task / Session，脚本会比较完整内容摘要。其他活动 / pending / 未知状态仍阻断，唐艺宁为数据库恢复负责人。

在云服务器 root Shell 执行 [最终脚本](gate3-production-migration.sh) 一次：复制文件全文粘贴后回车。也可将该文件上传到服务器后运行 `sh gate3-production-migration.sh`。不再运行诊断脚本，不需要输入密码或修改配置。当前代理没有可操作的生产终端连接，仍需请求方在已登录终端执行并回传最后输出。

已获批准的目标库：`moodflow_agent`。门控 0 / 1 / 2 已通过；恢复负责人唐艺宁。本文件没有替代生产低峰检查，必须由脚本即时通过门控 3 才执行迁移。

**2026-10-02 20:26 更新：中断后的只读回传显示生产 ledger 仍只有 001 / 002，execution 和 event_key / event_seq 均不存在，其他活跃 psql 与 advisory locks 均为 0；003 / 004 未生效。旧脚本把随机目录中的数字 0 误写为字母 o，导致演练报告读取失败。已修复，请仅使用本目录当前的新脚本。**

已修复 [gate3-production-migration.sh](gate3-production-migration.sh)：不改变登录 Shell 选项，失败由 if 分支接住退出码，显示具体非敏感停止码；自动在限定的演练报告根目录内查找，通过 manifest / 镜像 / SQL 哈希 / 规模 / 时间与容器身份验证后，要求唯一报告。它不依赖人工抄写随机目录名，也不选择其他演练的新报告。

压缩 payload 逐行 76 字符，整文件最长行 109 字符；解压后再次核对已审阅源代码 SHA-256。不要使用此前保存的旧脚本。仍重新校验有效备份、全部前置条件与低峰，5s / 60s 不变。没有密码或完整连接凭据需要填写。

脚本重新校验有效备份、演练报告、固定镜像及迁移 SQL 哈希；以临时无密码 PostgreSQL service profile 连接。先跨约 10 秒做 6 次只读低峰观测，仅允许指纹与保存时间一致、Task / Session 开放的这一条主图聊天控制状态，完整内容摘要保持。任何其他 pending、活跃请求 / run 或无法识别的状态均停止。通过后按手册只执行一次事务，超时固定为演练验证过的 5s / 60s，事务内再次检查执行状态和保存摘要，再执行 003 / 004 和 ledger；提交前再次核对保存摘要，变化即 ROLLBACK。提交后执行 §3 六项并复核暂停状态内容保持。数据库 SQL 错误显式 ROLLBACK；连接异常核对 ledger，状态不明也不重试。

输出出现 `PRODUCTION_MIGRATION_AND_READONLY_POSTCHECKS_FINISHED`，且 `PRODUCTION_RESULT` 中 status=passed、production_commit_confirmed=true，表示事务和只读复验完成。§3 六项原始输出位于 `RUNBOOK_SECTION_3_OUTPUT_BEGIN` 与 `RUNBOOK_SECTION_3_OUTPUT_END` 之间，逐项有编号；重复 replay key / sequence 预期返回空结果。

出现 `STOP` 时保留输出并回传；不要重复执行或放宽超时。若已经提交但复验失败，报告会保留提交证据，不自动删列、表、索引或账本。`temporary_profile_cleanup=needs_review` 表示需要检查本次临时文件清理，不能据此推定迁移没有提交。

每次结果保存在 `/secure/moodflow/migration-executions/context-loop-20261002-*/`，包括 `report.json`、`migration-output.txt` 和成功查询时的 `readonly-postchecks.txt`。真实业务写复验跳过，后续需专用测试账号独立完成；不调整灰度、默认图、`.env`，不部署、不 push。没有实际生产输出前，报告继续标记迁移未确认。

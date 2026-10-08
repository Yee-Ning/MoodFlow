# 门控 2：隔离副本演练执行说明

备份门控已通过。此处只授权和准备隔离副本的 003 / 004 演练；生产迁移仍需演练结果审阅、超时取值及门控 3 的即时低峰检查。

## 执行

在 ECS 的 root Shell 打开正常提示符后，复制 [gate2-isolated-rehearsal.sh](gate2-isolated-rehearsal.sh) 全部内容到终端，按回车。不要复制本 Markdown 的标题或反引号。无需密码。

脚本输出各阶段、报告路径和一行 JSON 汇总。将完整输出回传供审阅。有 STOP、失败或清理异常时停止，不重跑、不放宽超时；保留报告并核对失败状态。

## 作用范围

- 再核对已确认的 `20261001T234103Z-3a11237c.json` 备份状态、目标库、源库体积、dump 路径 / 大小 / SHA-256。
- 只读检查生产 PostgreSQL 镜像是否仍为此前回传的固定 digest；不向生产数据库发送 SQL，不挂载生产数据卷，不读取生产密码或 `.env`。
- 新建唯一名称、带本轮 UUID 标签的容器及数据卷，使用相同 PG 镜像，`--pull never --network none`，没有公开端口。CPU 上限 0.5、内存上限 1 GiB；宿主机 Docker 目录至少留 8 GiB 加四倍源库体积、可用内存至少 2 GiB，否则停止。
- 经 stdin 恢复完整 custom dump；恢复后历史必须恰为此次生产观测的 328 行，六个核心表的计数及 vector 版本须与备份恢复记录一致，ledger 恰为 001 / 002，不能已有 003 / 004 对象。恢复后的实际库 / 历史体积也会记录；还需据输出确认规模接近性。
- 将本地获批迁移文件的**原始字节**嵌入脚本并核对固定 SHA-256，只写到新建演练容器和受限临时报告目录。服务器原迁移目录缺失的两份文件不会被自动补写。
- 通过无密码 service 文件连接隔离库；在一个事务中按 BEGIN → 5s / 60s 起点 → advisory lock → include 003 → include 004 → ledger → COMMIT 执行。每个数据库步骤检查 psql ERROR，失败显式 ROLLBACK、打印回滚标记并退出，随后用只读会话核对 ledger / schema；断线由 PostgreSQL 回滚未提交事务。没有重试路径。
- 完成手册 §3 的六项只读查询，并追加对象 / 序列权限核对。这些都是**副本**输出，不填为生产复验通过；真实写操作按请求跳过。
- 结束时，仅删除本轮已经创建、名称和标签校验通过的临时容器与数据卷；不使用 prune，不清理生产卷。保留 `/secure/moodflow/migration-rehearsals/context-loop-20261002-*/` 中的报告、SQL 原文及查询输出。清理失败则总体失败。

## 指标含义

| 指标 | 定义与限制 |
| --- | --- |
| restore_wall_seconds | 备份恢复命令的本次 wall time |
| migration_wall_seconds | Docker / psql 调用及迁移输出的 wall time，包含客户端开销 |
| transaction_elapsed_ms | BEGIN 前时间点至 COMMIT 返回后的数据库时钟间隔 |
| lock_window_upper_bound_ms | include 003 前时间点至 COMMIT 返回后的间隔；是历史表 AccessExclusiveLock 持有时间的**保守上限**，包含获取锁与提交往返开销，不冒充精确授锁时间。另核对 COMMIT 前当前会话确实持有该锁 |
| wal_bytes | 上述 interval 中 WAL insert LSN 差值；隔离实例的总增量，可含少量后台 WAL，不能推定为生产 WAL 总量 |
| database_before / after_bytes | 迁移前后逻辑数据库的目录体积 |
| disk_before / after | 容器 data / pg_wal 的 apparent bytes；pg_wal 预分配可能使目录大小不变 |
| host_free_before / after_bytes | 宿主 Docker 文件系统空闲字节，包含该时段其他进程的影响 |
| replication_lag_seconds | NULL。生产前置观测 sender=0、slot=0；隔离容器无副本，因此没有可测延迟，不填 0 秒；不能据此证明不存在任何断开的外部副本 |

WAL 函数及字节差值定义见 [PostgreSQL 16 管理函数](https://www.postgresql.org/docs/16/functions-admin.html)，锁观测见 [PostgreSQL 16 锁监控](https://www.postgresql.org/docs/16/monitoring-locks.html)，psql 的 ERROR / 条件语句与 SQLSTATE 见 [psql 文档](https://www.postgresql.org/docs/16/app-psql.html)。

## 验证限制

本地自动化使用同一镜像、新建隔离夹具库和假数据验证恢复、实际 DDL、提交、失败回滚、ledger、只读复验及清理。它不是本轮生产规模演练；真实备份的副本规模、迁移性能及磁盘数据须由 ECS 执行输出确认。应用未部署，灰度和默认图未改动。

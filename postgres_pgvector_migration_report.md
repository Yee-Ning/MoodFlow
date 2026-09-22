# PostgreSQL / pgvector 迁移报告（暂停，未完成）

记录日期：2026-09-22。当前**仍在使用原 `postgres:16` 容器**；尚未启用 `vector` 扩展、执行 `002_long_term_memory.sql` 或将开发环境切换到 `MEMORY_BACKEND=postgres`。发现镜像排序规则兼容性问题后，已按安全要求停止迁移并回滚容器切换，等待目标镜像选择。

## 迁移前环境

| 项目 | 审计结果 |
| --- | --- |
| 容器 | `moodflow-postgres` |
| 镜像 / PostgreSQL | `postgres:16` / 16.15，Debian 13 |
| 端口 | 宿主机 `5432` → 容器 `5432` |
| 数据库 / 用户 | `moodflow_agent` / `root` |
| PGDATA | `/var/lib/postgresql/data` |
| 原持久化 volume | `moodflow_pgdata` → `/var/lib/postgresql/data`，挂载选项 `z` |
| 配置来源 | 容器无 Compose 标签；仓库没有 PostgreSQL Compose 文件，属于手动创建的容器 |
| 原数据库 | `moodflow_agent`、`postgres`、`template1` |

迁移前存在 `agent_artifact`、`agent_handoff`、`agent_history_event`、`agent_schema_migration`、`agent_session`、`agent_task`、`checkpoint_blobs`、`checkpoint_migrations`、`checkpoint_writes`、`checkpoints`。`001_agent_continuity.sql` 已登记；四张 checkpoint 表、handoff/history/业务表的结构仍在。迁移前这些业务表记录数均为 0，`agent_schema_migration` 为 1 条，`checkpoint_migrations` 为 10 条。旧镜像没有可用的 `vector` 扩展。

## 备份

切换前从旧容器导出完整集群 SQL（`pg_dumpall`）和目标数据库自定义格式备份（`pg_dump -Fc`），保存在项目外：

- `D:\code\ProjectsAll\MoodFlow-backups\moodflow-postgres-full-20260922-191153.sql`，17,022 字节，SHA-256 `77479A648C71B9CA9F81404D6D42044B97AA77C36F98873ABD7D5B992BEE0BEF`。
- `D:\code\ProjectsAll\MoodFlow-backups\moodflow-agent-20260922-191153.dump`，19,893 字节，SHA-256 `F6934ABA14F8FB0C0A0E61E9CF31A0FBF7D3B8630AB19E6889C8870060610643`。

已验证两个文件非空、完整 SQL 含目标数据库、数据库备份有 `PGDMP` 头且 `pg_restore --list` 可读；备份目录权限仅当前 Windows 用户和 SYSTEM。未删除旧 volume 或原数据。

## 镜像变更与兼容性检查

按要求预拉取 `pgvector/pgvector:pg16`，其 PostgreSQL 为 16.15，`vector` 扩展文件存在。短暂以原 `moodflow_pgdata` 和原端口启动新容器，旧容器改名保留。新容器启动后，原有表及记录数仍可查询，`vector` 扩展版本 0.8.6 可用，但所有旧数据库提示排序规则版本不匹配：原 Debian 13 / glibc 2.41，新镜像 Debian 12 / glibc 2.36。此问题可能影响文本索引的排序和唯一性判断，故未继续扩展启用、migration 或写入验证。

随即停止并保留新容器为 `moodflow-postgres-pgvector-paused-20260922`，将旧容器恢复为 `moodflow-postgres` 并启动。旧容器查询正常：PostgreSQL 16.15，`001_agent_continuity.sql` 登记存在，四张 checkpoint 表及五张 continuity 相关表仍在。

另做只读/无数据卷预检：`pgvector/pgvector:pg16-trixie` 标签存在，基于 Debian 13 / glibc 2.41，PostgreSQL 16.15，与旧镜像的 PostgreSQL 构建版本匹配，且有 `vector.control`。该标签是较安全的候选，但与用户指定的精确标签 `pgvector/pgvector:pg16` 不同，尚未用于持久化数据卷。

## Migration / 配置 / 数据完整性

- `CREATE EXTENSION IF NOT EXISTS vector`：**未执行**。
- `002_long_term_memory.sql`：**未执行**。
- `MEMORY_BACKEND=postgres`：**未修改**。
- 原表结构、记录数和原卷：切换预检前后相同；旧容器已恢复并正常运行。
- 当前项目 `ai-service/.env` 没有 `DATABASE_URL`、`PERSISTENCE_BACKEND`、`CHECKPOINT_BACKEND`、`MEMORY_BACKEND` 配置。启动脚本只检查 `.env` 存在，不会自动将其加载到进程环境。后续正式启用时须解决该配置链路，且不得硬编码密码。

## 测试结果

已通过镜像、PostgreSQL 版本、原表和 migration 登记、备份可读性、回滚后旧容器可查询的检查。**尚未运行**真实 Memory / checkpoint / handoff 写入、跨 thread 召回、pgvector 检索或完整 pytest；不能据此宣称 Long-Term Memory PostgreSQL 后端已可用。

## 回滚方案与当前状态

目前已回滚，无需额外动作：`moodflow-postgres` 是原 `postgres:16` 容器，仍挂载 `moodflow_pgdata`；未对数据做 migration。新容器保留为停止状态。完整 SQL 和数据库备份均保留在项目外，作为额外恢复路径。后续任何重新切换之前应再次核对容器、volume 和备份，并在发现新的不兼容时停止，不删除卷或清空数据库。

## 最终结论

**当前不可正式使用 PostgreSQL Long-Term Memory 后端。**需先确认是否允许以兼容旧 glibc 的 `pgvector/pgvector:pg16-trixie` 替代指定标签，或明确授权对 `pgvector/pgvector:pg16` 进行文本索引重建及排序规则版本刷新。随后才可继续 extension、migration、配置与真实环境测试。

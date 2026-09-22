# PostgreSQL / pgvector 迁移报告

完成日期：2026-09-22。结论：**MoodFlow 开发环境已可正式使用 PostgreSQL Long-Term Memory 后端**。当前运行镜像为 `pgvector/pgvector:pg16-trixie`，保留原 `moodflow_pgdata` volume 和 PostgreSQL 16.15；原有表与迁移登记均存在，无排序规则版本不匹配。`vector` 0.8.6、Long-Term Memory migration 和真实写入/检索/恢复验证均已通过。

## 迁移前环境

| 项目 | 审计结果 |
| --- | --- |
| 原容器 / 镜像 | `moodflow-postgres` / `postgres:16`，PostgreSQL 16.15，Debian 13 |
| 端口 | 宿主机 `5432` → 容器 `5432` |
| 数据库 / 用户 | `moodflow_agent` / `root` |
| 原持久化 volume | `moodflow_pgdata` → `/var/lib/postgresql/data`，挂载选项 `z` |
| PGDATA | `/var/lib/postgresql/data` |
| 配置来源 | 原容器无 Compose 标签；仓库无 PostgreSQL Compose 文件，属于手动创建的容器 |
| 原数据库 | `moodflow_agent`、`postgres`、`template1` |

迁移前有 `agent_artifact`、`agent_handoff`、`agent_history_event`、`agent_schema_migration`、`agent_session`、`agent_task`、`checkpoint_blobs`、`checkpoint_migrations`、`checkpoint_writes`、`checkpoints`。`001_agent_continuity.sql` 已登记；checkpoint migration 为 10 条。迁移前各业务、checkpoint、history、handoff 表记录数为 0，迁移登记为 1 条。旧镜像未提供 `vector` 扩展。

## 备份与原数据保护

切换前导出完整集群 SQL（`pg_dumpall`）和 `moodflow_agent` 自定义格式备份（`pg_dump -Fc`），保存在项目外：

- `D:\code\ProjectsAll\MoodFlow-backups\moodflow-postgres-full-20260922-191153.sql`，17,022 字节，SHA-256 `77479A648C71B9CA9F81404D6D42044B97AA77C36F98873ABD7D5B992BEE0BEF`。
- `D:\code\ProjectsAll\MoodFlow-backups\moodflow-agent-20260922-191153.dump`，19,893 字节，SHA-256 `F6934ABA14F8FB0C0A0E61E9CF31A0FBF7D3B8630AB19E6889C8870060610643`。

已验证文件非空、SQL 含目标数据库、自定义备份有 `PGDMP` 头且 `pg_restore --list` 可读；迁移后重新核对哈希未变。备份目录仅当前 Windows 用户和 SYSTEM 可访问。原 `moodflow_pgdata` 没有删除、替换、清空或重建；原 `postgres:16` 容器保留为停止状态 `moodflow-postgres-pre-trixie-20260922`。

## 镜像切换及排序规则

首次按原要求试运行 `pgvector/pgvector:pg16`，发现 Debian 12 / glibc 2.36 与原数据的 Debian 13 / glibc 2.41 不匹配；当时立即停止且未执行扩展或 migration，恢复旧容器。该不兼容容器仍保留为停止状态 `moodflow-postgres-pgvector-paused-20260922`。

经用户确认后，改用 `pgvector/pgvector:pg16-trixie`（镜像 digest `sha256:c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b`）。它与旧镜像同为 Debian 13 / glibc 2.41、PostgreSQL 16.15。新容器仍叫 `moodflow-postgres`，使用原 `moodflow_pgdata`、原端口 `5432`、数据库 `moodflow_agent` 和用户 `root`；原数据库认证信息未修改。切换后先只读核对：十张原表全部存在，原记录数一致，`001_agent_continuity.sql` 和十条 checkpoint migration 均在；三个可连接数据库的存储排序规则版本与实际版本均为 `2.41`。查询和容器日志均无 collation mismatch、FATAL 或 PANIC。

## pgvector 与 migration

数据完整性检查通过后，执行 `CREATE EXTENSION IF NOT EXISTS vector;`，结果为 `vector` 0.8.6，向量类型可用。随后运行现有 `python -m scripts.migrate_agent_postgres`，应用 `002_long_term_memory.sql`，创建 `agent_long_term_memory` 和唯一键、用户/类型、JSONB、HNSW 向量索引。再次运行返回 `Business migrations are current`、`LangGraph checkpoint migrations are current`，证明当前迁移路径幂等。原 checkpoint/continuity 表未被删除或改写；checkpoint migration 仍为 10 条。

## 开发配置

本地忽略的 `ai-service/.env` 已设置 `CHECKPOINT_BACKEND=postgres`、`PERSISTENCE_BACKEND=postgres`、`MEMORY_BACKEND=postgres` 和不含密码的 `DATABASE_URL`。数据库密码未写入仓库或 `.env`；连接使用项目外受限权限的 `D:\code\ProjectsAll\MoodFlow-backups\pgpass.conf`，并由非敏感的 `PGPASSFILE` 指向。该凭据文件仅当前用户和 SYSTEM 可访问，不应提交或复制到仓库。

应用 lifespan 和迁移 CLI 现在会从现有 `.env` 加载运行时配置，且不覆盖进程已显式设置的环境变量；`.env.example` 增加了 `PGPASSFILE` 项。真实应用启动检查返回 `/health` 200，并确认 `checkpoint_backend=postgres`、`persistence_backend=postgres`、`memory_backend=postgres`，Memory Store 实际类型为 `PostgresMemoryStore`。未硬编码密码。

## 数据完整性与真实 E2E

迁移后再次检查 PostgreSQL 16.15、排序规则 `2.41 = 2.41`、`vector` 0.8.6、`001`/`002` 登记、十张原表及新增 Memory 表。真实 smoke 使用唯一 `smoke:` 前缀写入测试记录，不删除原记录、数据库或 volume。最终检查显示 checkpoint migration 10 条、checkpoint 24 条、checkpoint writes 48 条、checkpoint blobs 30 条、task 3 条、session 6 条、history 24 条、handoff 3 条、artifact 3 条、Long-Term Memory 11 条；其中 smoke Profile 6 条、Episodic 5 条。这些增长来自三轮真实 smoke，不是原数据丢失。

真实验证覆盖：

- 独立进程写入并恢复 LangGraph checkpoint；续跑状态含切换前后的节点步骤。
- History、artifact、handoff、session rollover 和 cold start 可从 PostgreSQL 恢复。
- `我喜欢周杰伦` 和 `以后不要推荐现场版` 形成 Profile；重新打开 Store 后可被相同用户的后续线程查询。重复偏好不产生重复有效记录，另一 `user_id` 无法召回。
- 健身慢歌反馈形成 1024 维 Episodic embedding；pgvector cosine 检索可召回，数据库 `vector_dims(embedding)=1024`。
- 除确定性测试 embedding 外，真实 Qwen Memory embedding 也完成 PostgreSQL 写入和 pgvector 语义检索往返。
- 真实检索结果作为 `long_term_memory` 输入 Context Engineering，超出源预算时被剔除，最终 token 数不超过预算。

## 测试结果

| 命令 / 检查 | 结果 |
| --- | --- |
| `python -m unittest tests.test_runtime_environment -v` | 2 passed |
| `python -m scripts.migrate_agent_postgres` 再次运行 | 无待执行 migration，幂等通过 |
| `RUN_LIVE_POSTGRES_SMOKE=1 python -m unittest tests.test_live_postgres_smoke -v` | 4 passed，含真实应用启动、Qwen + pgvector |
| 相关 Memory / Context / PostgreSQL 测试 | 44 passed，4 skipped |
| `python -m pytest tests -q` | 864 passed，10 skipped，206 subtests passed |
| 真实应用 lifespan + `/health` | 200，三个 PostgreSQL 后端均已生效 |
| `python -m pytest -q` | 在收集可选 `evals/tests/test_judges.py` 时因缺少 `deepeval` 中断；主测试集已单独全量通过 |

原 `test_postgres_persistence.py` 会创建并 `DROP DATABASE`，与本次禁止删除数据库的要求冲突，故未在常驻库上启用它；相关持久化关键路径由新增的只写入唯一测试记录、不删库的真实 smoke 覆盖。未安装可选 eval 依赖，未修改或削弱现有测试。

## 回滚方案与限制

完整的迁移前备份和原容器仍在，但**启用 `vector` 和新增向量列后，不应把缺少 pgvector 的原 `postgres:16` 容器直接重新挂载当前已迁移 volume**。安全回滚应先停止当前容器，另建一个新的持久化 volume，以原 `postgres:16` 镜像初始化独立实例，将项目外完整备份恢复到该新卷；验证原业务及 checkpoint 后再切换端口。整个过程中保留当前 `moodflow_pgdata` 和备份，不对当前数据库执行 `DROP DATABASE`、REINDEX 或 `REFRESH COLLATION VERSION`。本次未实际演练完整恢复，因此恢复时间和步骤仍需单独演练验证。

已知限制：真实 smoke 留下带 `smoke:` 前缀的验证记录；完整仓库根范围 pytest 需要可选 `deepeval` 依赖；真实自然语言 Memory Extraction 仍依赖 LLM 服务，本次验证的是结构化候选到持久化、检索和上下文注入闭环，而非线上模型质量。当前容器重启策略维持原来的 `no`；如需 Docker 重启后自动启动，应在另一个经授权的运维变更中处理。

## 最终结论

原卷和原表保留、PostgreSQL 主版本 16 不变、无 collation mismatch；pgvector、Long-Term Memory migration、`MEMORY_BACKEND=postgres`、真实 checkpoint/handoff 和跨线程 Memory/向量检索均已通过。**可在当前开发环境正式使用 PostgreSQL Long-Term Memory 后端。**

# MoodFlow ECS 生产更新与备份运维报告

日期：2026-09-29（Asia/Shanghai）

ECS：`47.83.162.191`，项目目录 `/opt/moodflow`
公网入口：[https://47.83.162.191/](https://47.83.162.191/)

## 结论

已实现并安装一键安全更新、PostgreSQL/Qdrant 备份、每日 systemd timer 与隔离恢复验证。`update.sh --dry-run` 在 ECS 通过；本轮没有执行真实应用更新、migration、容器重建或服务重启。实际手动备份成功，且从备份恢复到独立临时 PostgreSQL 和 Qdrant 后验证通过。生产 PostgreSQL/Qdrant volumes、Worldline collection、Nginx 和 Let's Encrypt 配置保持不变。

## 常用命令

在 ECS `/opt/moodflow` 执行：

```bash
# 本地代码合入并 push 到 origin/main 后，检查更新计划或正式更新
bash deploy/update.sh --dry-run
bash deploy/update.sh

# 手动生成并隔离恢复验证一份备份
bash deploy/backup.sh

# 再次隔离恢复验证最新成功备份
bash deploy/backup.sh --verify latest

# 定时器状态及最近备份
systemctl status moodflow-backup.timer
ls -1t /secure/moodflow/backups/manifests/*.json | head -1
```

若新提交包含 PostgreSQL migration 文件，更新脚本在 Git 快进前要求人工审查；确认后使用 `bash deploy/update.sh --approve-migrations`。该参数只允许继续执行迁移，并不代表 schema 可逆。

## 一键更新流程

`deploy/update.sh` 调用 `deploy/update.py`，并以 `/run/lock/moodflow-update.lock` 防止并发更新。

1. 确认 `/opt/moodflow`、`main`、`origin`、`origin/main`、干净工作树及 submodule 固定提交；检查 `.env.production` 和三个 secret 文件存在、非空且权限不向其他用户开放。只检查路径/属性，不记录 secret 值。
2. 检查 Docker/Compose、`docker compose config --quiet`、Nginx 配置与服务、Certbot 续期 timer；系统盘至少保留 8 GiB。记录 `PREVIOUS_COMMIT`，`git fetch` 后仅允许快进 `main`。Compose、主机 Nginx 或生产环境契约变化会停止并要求单独审查。
3. `git merge --ff-only`、`git submodule sync/update` 后，**任何 migration/build/start 前**强制运行 `backup.sh --predeploy`；只有 PostgreSQL 与 Qdrant 文件均非空、SHA256 与隔离恢复都通过才继续。记录备份 manifest、旧容器 image IDs 和镜像 tag。
4. 按 Dockerfile 的实际输入映射只构建受影响的 `qdrant`、`netease`、`java`、`ai-service`、`frontend` 镜像；先为旧镜像创建 `rollback-*` tag。AI 镜像或 migration 变动时重建一次 `migrate` one-shot，必须 `Exited (0)`。
5. `docker compose up -d --no-build` 后分别等待六个常驻服务 healthy、migrate Exited (0)。随后使用系统信任库验证 HTTPS 首页、`/healthz`、`/api/auth/status`、AI SSE `update → final → done` 事件的分批到达，以及 Worldline session create/read。未使用 `curl -k` 或等效跳过验证。
6. 每次正式更新写入 `/secure/moodflow/deployments/<run-id>.json`，包括时间、前后 commit、镜像 ID、备份路径、migration、逐服务 health、smoke 及最终结果。失败时保存阶段和受控错误原因，并输出查看容器日志和人工回滚的命令；不会自动回滚生产数据。

人工回滚应用代码与旧镜像：先查看部署日志和 migration 对 schema 的影响，再运行 `bash deploy/update.sh rollback <run-id> --acknowledge-schema-risk`。该命令要求干净工作树、当前 HEAD 与日志目标提交一致，并只恢复旧 Git commit 与旧镜像；**不会恢复或删除 PostgreSQL/Qdrant 数据**。若 schema 已前进且不兼容旧代码，需根据已验证备份另行设计人工数据恢复，不能直接把应用回滚当作数据库回滚。

## 备份策略与文件

备份程序使用 PostgreSQL `pg_dump -Fc -Z 6`，其一致性快照与 custom archive 适合 `pg_restore`。先用 `pg_restore --list` 检查目录，再恢复到独立的、无公网端口且 `--network none` 的临时 pgvector 容器；验证 pgvector 和六个核心表的行数。Qdrant 通过 1.19 collection snapshot API 创建当前冻结 Worldline collection 的快照，从其 snapshot volume 复制到受限备份目录，再在独立 Docker 网络、临时 Qdrant volume 中恢复，比较 green 状态、point count、dense 向量大小/距离及 sparse modifier。临时容器、网络和临时 volume 验证后清理；生产数据卷不参与恢复。

目录 `/secure/moodflow/backups/{postgres,qdrant,manifests}` 均为 root `0700`；备份和 manifest 文件为 `0600`。每个 manifest 记录 UTC/北京时间、Git commit、PG server/schema/pgvector 版本、备份路径/字节数/SHA256、collection 名与点数、dense/sparse 配置、隔离恢复结果及状态，不记录密码、API Key、Cookie 或 Token。

每日 `moodflow-backup.timer` 已 enabled/active，计划在北京时间 03:20 启动，最多随机延迟 15 分钟，并启用 `Persistent=true`。下一次显示为 **2026-09-30 03:22:04 CST**；`moodflow-backup.service` 使用 root、`UMask=0077`。timer 单元由 `/opt/moodflow/deploy/moodflow-backup.timer` 安装到 `/etc/systemd/system/`。

保留策略：最近 7 天的所有已验证备份，以及此前最多 4 个 ISO 周各一份。仅对已完成、隔离恢复通过且 SHA256 再次吻合的 manifest 删除其对应 `/secure/moodflow/backups` 内文件；路径、文件名和目录边界逐项校验。不删除失败/损坏备份，不对 production volume、原始 `current.dump` 或首次 Qdrant snapshot 执行清理。备份前按 PostgreSQL 数据库大小、Qdrant volume 用量和 8 GiB 预留空间做门槛检查。

PostgreSQL custom dump 与 `pg_restore` 的格式、快照和恢复语义见 [PostgreSQL 官方文档](https://www.postgresql.org/docs/16/app-pgdump.html)；Qdrant snapshot 创建、保存与恢复语义见 [Qdrant 官方文档](https://qdrant.tech/documentation/operations/snapshots/)。

## 本轮实际备份与恢复验收

Manifest：`/secure/moodflow/backups/manifests/20260929T024522Z-b99bd475.json`，状态 `complete`，`restore_verified=true`。执行 `bash deploy/backup.sh --verify latest` 又独立复验一次，输出 PostgreSQL 与 Qdrant 均 `passed`。

| 数据 | 文件 | 大小 | SHA256 | 隔离恢复结果 |
| --- | --- | ---: | --- | --- |
| PostgreSQL | `/secure/moodflow/backups/postgres/20260929T024522Z-b99bd475.dump` | 17,584,723 bytes | `59b0110ba9ab73f6f364956769c9858237a311dc8917d8bd58f74e7b863771e6` | PostgreSQL 16.15、pgvector 0.8.6；`agent_session=12`、`agent_task=9`、`agent_long_term_memory=11`、`checkpoints=201`、blobs=225、writes=309 |
| Qdrant | `/secure/moodflow/backups/qdrant/20260929T024522Z-b99bd475.snapshot` | 1,678,848 bytes | `02058eff1e034058fbac75ac04e709d8d8cc402909c9b244ecc094c5723ff5c1` | `worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4` green、41 points；dense 1024/Cosine、sparse bm25/idf |

PG schema migration 记录为 `001_agent_continuity.sql`、`002_long_term_memory.sql`。系统盘 40 GB，当前约 23 GB 可用；该轮备份前/后可用空间分别为 24,086,585,344 / 24,065,630,208 bytes。验证结束后未残留 `moodflow-verify-*` 临时容器或 volume。

## 更新 dry-run、服务与 HTTPS 验收

ECS `bash deploy/update.sh --dry-run` 成功；当前 Git commit `3a326a801369c3f37402374eee3b5a57112d0c54`，工作树干净。尚无新的远端 main 提交，因此未做真实更新，也未生成正式部署运行日志。`python3 -m deploy.update_smoke` 单独实测通过：首页、健康检查、登录状态均 200；AI SSE 的 `update` 约 0.004 秒、`final` 约 0.749 秒，随后 `done`，符合流式顺序；Radiohead Worldline session 首次创建成功、9 节点，GET 同一 session 成功。TLS 使用标准客户端默认验证。

`docker compose ps -a` 保持 postgres、qdrant、netease、java、ai-service、frontend 六项 healthy，migrate Exited (0)，容器创建时间仍约 11 小时前。`nginx`、`snap.certbot.renew.timer`、`moodflow-backup.timer` 均 active；Nginx 配置 SHA256 仍为 `7968a8c604382a7810012b79a6a685e48bb323eeed19f02cf76db143e0d6a019`。本地相关测试共 **59 passed**；shell 语法、Python 编译与 systemd calendar/单元检查通过。`systemd-analyze verify` 只提示已有 snapd.service 的未知 `RestartMode` 键，与新 timer 无关。

## 当前限制与风险

- 运维提交已通过校验的 Git bundle 快进到 ECS `main`，**未 push**；ECS HEAD 为 `3a326a8`，`origin/main` 仍为 `61e4310`。未来应先把这些运维提交合入并 push 到远端 main，再从该提交继续开发；若从旧远端分叉推送，更新脚本会因非快进而安全停止。
- 本轮 `update.sh` 只经过 dry-run、单元测试及独立公网 smoke，尚未有一个真实的新业务提交来完成 build → migrate → rollout 的端到端演练。首次真实更新应观察部署日志和备份 timer。
- Pixies 搜索当前偶发包含关联实体 ID `0`，AI 的严格响应校验会拒绝并使 Worldline 返回 `503 entity_search_failed`；本轮已定位为上游音乐数据/Java 响应契约问题，没有放宽验证或修改业务代码。固定 smoke 使用实测成功的 Radiohead，仍真实执行创建与读取。LLM 叙事生成也曾偶发 502，需持续监控。
- 备份与生产 volume 同在这台 ECS 的 40 GB 系统盘上，不能抵御整机或系统盘丢失。应尽快增加加密的异机/OSS 副本、定期下载校验和续期/备份失败告警。
- Qdrant API 创建的原始 snapshot 仍保存在 production `qdrant_snapshots` volume；为遵守只清理备份目录的约束，本轮不删除它。当前约 1.7 MB/份，但随 collection 增长应监控该 volume 与磁盘占用，再审议安全清理策略。

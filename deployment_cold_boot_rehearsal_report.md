# MoodFlow Production Compose Cold Boot + Restore Rehearsal

日期：2026-09-28。本次仅在本机 Docker Desktop 演练；未部署服务器、未运行 Eval、未修改 Worldline 功能。结论：基础设施恢复、冷启动及持久化通过，但**完整 AI 业务链 smoke 未通过**，本轮不能开始服务器部署。

## 隔离范围与恢复来源

- Compose project：`moodflow-rehearsal-20260928`，前端仅绑定 `127.0.0.1:18088`。临时环境文件及三个 secret 文件位于被 Git 忽略的 `deployment-artifacts/rehearsal/`；未提交任何密钥。仅使用新建 volumes：`moodflow-rehearsal-20260928_postgres_data`、`moodflow-rehearsal-20260928_qdrant_data`、`moodflow-rehearsal-20260928_qdrant_snapshots`。开发 volumes `moodflow_pgdata`、`ai-service_qdrant_storage` 未挂载、清理或 restore。
- PostgreSQL 来源：`D:\code\ProjectsAll\MoodFlow-backups\moodflow-agent-deployprep-20260927-234049.dump`，custom-format，16,694,667 bytes，SHA256 `592951799aef8d0397f1cba7adaab7efd0530e7d16ff2504f5b1eba955372065`，与部署准备记录一致。对新 PG16/pgvector volume 执行 `pg_restore --exit-on-error --single-transaction --no-owner --no-privileges`，退出码 0；之后 `migrate` 退出码 0，business 与 LangGraph migrations 均为 current。生产角色可连接，pgvector `0.8.6`。
- 恢复后关键行数：`agent_task=4`、`agent_session=7`、`agent_history_event=28`、`agent_artifact=3`、`agent_handoff=3`、`agent_long_term_memory=11`、`checkpoints=148`、`checkpoint_blobs=157`、`checkpoint_writes=186`。业务迁移 `001_agent_continuity.sql`、`002_long_term_memory.sql`；LangGraph checkpoint 迁移版本 `0..9`。这些是恢复当时的基线，不含随后 rehearsal smoke 写入。
- 只为 approved collection `worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4` 创建 collection snapshot。快照文件在被忽略的 `deployment-artifacts/rehearsal/`，3,431,424 bytes，SHA256 `d86695791f64313864e01374da693410f5f582844c1da7c104c023f6ff1bb2e8`；恢复 API 返回成功。新 Qdrant collection 为 green、41 points、`dense=1024/Cosine`、`sparse bm25/IDF`。滚动读取全部 41 points 后，point ID 和 payload 与冻结 `index_projection.jsonl` 逐项完全一致。未迁移历史 collection。快照及比对记录保存在同一被忽略目录。
- 冻结身份：Worldline freeze commit `7afc619`，release `knowledge-v2-daa965c10984eefa`，manifest `data/indexing/worldline/final-native-20260927/v4/index_build_manifest.json`。`deploy.artifacts` 验证 20 个文件通过；Qwen `Qwen/Qwen3-Embedding-0.6B` pinned revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` 的模型缓存验证通过；容器内只读挂载执行 `python -m deploy.runtime verify` 也通过。没有 staging/latest 自动发现或模型联网下载。

## Compose 冷启动与修复

依照现有 `docker-compose.production.yml` 的 healthcheck/`depends_on`，按 PostgreSQL + Qdrant → migrate → Netease → Java → AI → Frontend 启动。第一次 AI 启动发现生产镜像未包含已跟踪的 `data/intent/taxonomy.json`，模型 warm-up 因文件不存在而失败，Frontend 未进入运行。此为生产镜像打包遗漏。已仅修改 `.dockerignore`、`deploy/ai.Dockerfile` 及对应部署测试：明确允许并 COPY 该静态文件，镜像内按 SHA256 `a60027b108e744d2baa457b6221572b9720d6f1ba377ef0350dbe014002bc63a` 校验。未更改 taxonomy 内容、Worldline 代码、Torch/Python lock 或冻结 artifact。重建 AI image 成功。

临时 rehearsal secret 最初带 CRLF，导致 Qdrant health/auth 异常；将**仅本次新建的临时 secret 文件**写成无换行字节后恢复。未读取或输出密钥值，未变更开发环境配置。最终 `docker compose ps --all`：`postgres/qdrant/netease/java/ai-service/frontend` 全部 **healthy**，`migrate` **Exited (0)**；不是仅以 running 判断。`docker compose ... config --quiet` 通过。

最终五个应用镜像及 PostgreSQL 镜像身份：

| 服务 | 本地 image ID / digest |
|---|---|
| Frontend | `sha256:8ee212427b6a51a595785ce75819c1b74660831a8799434b61ca585805dff888` |
| Netease | `sha256:6051bbdfd1ad7b1534d85d0a62ff3f82a029a5f78a87e90fec34344033ece7cf` |
| Java | `sha256:14c50f6b0447cf4a577b57e5609b2cccb488c507b3589b46f2db5a32cff33e82` |
| Qdrant | `sha256:d55a1602d079ded592bb0199bac55a49accbb94918c429dc5e29890b197ca1d6` |
| AI Service | `sha256:1f7127779a452013b330c484287dd8f7cbff1342d9383a3703ee26fa78c3a271` |
| PostgreSQL/pgvector | `pgvector/pgvector:pg16-trixie@sha256:c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b` |

本地 image ID 只锁定本机已构建的镜像；推送到 registry 后的远端 manifest digest 需部署前另行记录。

## 运行 smoke 与未通过项

- `GET /healthz` 200；通过同源前端 `GET /api/auth/status` → Java 为 200/业务 code 200；`GET /api/auth/qr/key` → Java → Netease 为 200/业务 code 200 且返回 key（未记录其值）；AI `/health` 和 Netease `/inner/version` 分别返回 200。
- 同源 `POST /api/ai/chat` 返回 200 `text/event-stream`、`final: success`，首事件约 0.031 秒，整流约 0.625 秒；Nginx 配置 `proxy_buffering off`，首事件在最终事件之前抵达。带匿名无效 `JSESSIONID` 的 `/api/user/profile` 通过同源代理返回预期 401，AI 请求也能携带 cookie。**真实已登录 session 的创建、续用和 Set-Cookie 未验证**，因为演练没有扫码登录。
- 新 Qdrant 上单次真实 dense + BM25 hybrid 查询 `status=success`、10 个候选、无失败通道；冻结 Qwen 离线加载并编码。隔离 PG 中，前端聊天实际写入 14 条 `rehearsal-*` checkpoint，随后用 `PostgresSaver.get_tuple` 成功读取。
- 在 AI 容器内用当前 provider 配置做一次真实 generation：`deepseek-flash` 返回非空响应；未输出密钥或响应正文。
- **未通过的完整链路**：通过同源前端发起一次 `探索 Nirvana 的音乐世界线。`，SSE HTTP 为 200，路由为 `worldline`，但 `final.status=failed`。由于本轮禁止 Worldline 修改与 Eval，未继续调试业务结果。前述 Qdrant hybrid 与 provider 是分别验证的，**不能将它们冒充为这条请求完成了 Frontend → AI → PostgreSQL → Qdrant → provider → 成功响应的单请求证明**。这也是阻止服务器部署的直接条件。

## 重启、回滚和资源

先只 `stop frontend ai-service java netease`，保留 PostgreSQL/Qdrant，随后 `up -d --no-build`：六个长期服务再次 healthy，`migrate` 再次 Exited (0)；`rehearsal-*` checkpoint 仍为 14 条且可读取，approved Qdrant collection 仍 green/41 points。随后对**仅 rehearsal project** 执行 `docker compose ... down`（**无 `-v`**）并重新 `up -d --no-build`；三个 rehearsal volumes 与两个开发 volumes 均仍存在，六服务再次 healthy、migrate 退出码 0，checkpoint 仍 14 条，Qdrant 仍 green/41 points。已重新校验快照 SHA。停掉新环境不会触碰开发 Compose project。

本机 8 次 `docker stats --no-stream` 采样中，各服务观察到的内存峰值 / CPU 峰值：PostgreSQL 89.87 MiB / 4.85%，Qdrant 215.9 MiB / 0.83%，Netease 46.94 MiB / 8.00%，Java 221.6 MiB / 37.66%，AI 830.8 MiB / 100.17%，Frontend 23.18 MiB / 1.61%；同一采样时刻的总内存观察峰值 1,423.19 MiB。`du -sb`：PG volume 110,415,722 bytes，Qdrant data 1,635,741,415 bytes，Qdrant snapshots 3,443,712 bytes。AI 重启从容器 StartedAt 到 Uvicorn `Application startup complete` 为约 30.8 秒；独立进程离线加载 pinned Qwen 模型约 4.382 秒。这是有限采样的本机基线，**不是**压力测试或绝对内存峰值，也未据此调优。

## 验证与结论

`docker compose ... build ai-service` 成功；`python -m pytest deploy/tests -q`：37 passed；`python -m ruff check deploy/tests/test_deployment.py` 通过；`docker compose ... config --quiet`、冻结文件/模型 SHA 校验、`git diff --check` 均通过。所有临时 secret、dump 副本、snapshot、smoke 脚本、资源原始记录及 wheelhouse 均受 `deployment-artifacts/` Git ignore 保护。未执行 `down -v`、开发库 restore/drop、服务器部署或云资源配置。

```text
postgres_restore_pass=true
qdrant_snapshot_restore_pass=true
frozen_artifact_validation_pass=true
production_compose_cold_boot_pass=true
production_restart_pass=true
runtime_smoke_pass=false
server_resource_baseline_ready=true
can_start_server_deployment=false
```

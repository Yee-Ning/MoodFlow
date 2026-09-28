# MoodFlow：Ubuntu 22.04 ECS 的 Compose 部署准备

本文件按仓库当前代码与 `docker-compose.production.yml` 编写。它是操作手册，不表示本轮已经在 ECS 执行部署。服务器需为 **Linux x86_64**：冻结的 CPU Torch wheel 是 CPython 3.12 / manylinux x86_64，ARM 实例不能直接使用。Docker、Compose、Git 已由宿主机提供；执行预检还需 `python3`。不配置域名、HTTPS 或 CI/CD；Compose 只将前端绑定到 `127.0.0.1:8088`。

本轮完成后只在本地提交，**尚未 push**。审核并推送该提交后，ECS 的 `git clone` 才会取得本文件和新预检/恢复脚本。

## 实际拓扑与版本

| 服务 | 镜像/启动方式 | 容器端口 | 上游与持久化 |
|---|---|---:|---|
| `postgres` | `pgvector/pgvector:pg16-trixie`（digest 已固定），继承 PostgreSQL 镜像的 `postgres` 启动命令 | 5432 | `postgres_data` named volume；业务、LangGraph checkpoint、memory、pgvector 均在此库 |
| `qdrant` | `deploy/qdrant.Dockerfile`，Qdrant 1.19.0，`sh /qdrant/moodflow-start.sh` | 6333/6334 | `qdrant_data`、`qdrant_snapshots` named volumes；只恢复 approved collection |
| `migrate` | 与 AI 共用镜像，`python -m deploy.runtime migrate` | — | PG + Qdrant healthy 后执行；业务和 checkpoint migration，验证冻结 index；成功退出码 0 |
| `netease` | Node 22.22、pnpm 10.11、submodule `app.js`；`tini -- node app.js` | 3000 | Netease 上游互联网；无本地持久化数据 |
| `java` | Maven 3.9.9/JDK 21 构建 Spring Boot 4.1.0；JRE 21 执行 `java -jar /app/app.jar` | 8080 | 调用 `netease:3000`；Java HTTP session 当前在内存中 |
| `ai-service` | Python 3.12.10、FastAPI/Uvicorn、LangGraph；`python -m deploy.runtime serve` | 8001 | 调用 Java、PG、Qdrant、外部 LLM provider；只读冻结 release/index/intent/Qwen 挂载 |
| `frontend` | Node 22.22 构建 React/Vite，Nginx 1.28 默认启动命令运行 | 80 → 宿主 127.0.0.1:8088 | `/api/ai/` → `ai-service:8001`；`/api/` → `java:8080`；SSE 关闭代理缓冲 |

`visual-qa/mineradio-upstream` 子模块不在运行拓扑。业务链依赖外部 LLM provider 与 Netease upstream。长期服务以 `healthy` 为准；`migrate` 以 `Exited (0)` 为准。容器之间只使用 Compose `backend` 网络的服务名，不依赖宿主机 PostgreSQL。

| 配置范围 | 环境变量名（仅列名称） |
|---|---|
| Compose/Frontend | `COMPOSE_PROJECT_NAME`, `IMAGE_TAG`, `FRONTEND_BIND_ADDRESS`, `FRONTEND_PORT`, `VITE_API_BASE_URL`, `VITE_AI_API_BASE_URL` |
| PostgreSQL 与 secret 文件 | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD_FILE`, `DATABASE_URL`, `LLM_API_KEY_FILE`, `QDRANT_API_KEY_FILE` |
| 内部服务 URL | `SPRING_BOOT_BASE_URL`, `SPRING_BOOT_TIMEOUT_SECONDS`, `NETEASE_API_BASE_URL`, `QDRANT_URL` |
| LLM | `LLM_BASE_URL`, `LLM_MODEL`, `LLM_GENERATION_MODEL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_OUTPUT_TOKENS` |
| 冻结资源 | `APPROVED_RELEASE_DIR`, `APPROVED_INDEX_DIR`, `AI_RAG_INDEX_MANIFEST`, `HF_CACHE_DIR`, `INTENT_MODEL_DIR`, `HF_HOME` |
| 固定 AI 运行合约 | `AI_AGENT_GRAPH_MODE`, `CHECKPOINT_BACKEND`, `PERSISTENCE_BACKEND`, `MEMORY_BACKEND`, `AI_RAG_RETRIEVAL_MODE`, `AI_RAG_RERANK_MODE`, `JEV_ENABLED`, `JEV_SHADOW_MODE`, `JEV_ROUTER_ENABLED`, `JEV_MEMORY_FILTER_ENABLED`, `JEV_CONTEXT_SELECT_ENABLED` |
| Netease / Java / Cookie | `PORT`, `HOST`, `NODE_ENV`, `ENABLE_PROXY`, `SERVER_PORT`, `SERVER_FORWARD_HEADERS_STRATEGY`, `SERVER_SERVLET_SESSION_COOKIE_HTTP_ONLY`, `SERVER_SERVLET_SESSION_COOKIE_SECURE`, `SERVER_SERVLET_SESSION_COOKIE_SAME_SITE`, `SESSION_COOKIE_SECURE` |

上述变量中有些由 Compose 固定，不需要填入 `.env.production`；以 `.env.example` 的插值变量为实际宿主机输入契约。`VITE_*` 在生产前端 build 中明确设为空字符串，使浏览器走同源 API。

## 为什么 clone 本身不足以启动完整业务

Git 故意不包含密码、CPU Torch wheel、冻结 Qwen/intent/release/index 资源、PostgreSQL dump 和 Qdrant snapshot。AI Dockerfile 必须从本地 wheelhouse 读取并核对 SHA256，不能改用 CDN 下载或关闭 hash。空 PG/Qdrant 卷不会凭迁移生成历史 session 或 approved Worldline collection，`migrate` 会拒绝缺失的 index。因此**需要在 clone 后经安全渠道提供以下部署输入**，再恢复数据，最后启动完整 Compose：

- `deployment-artifacts/wheelhouse/torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl`；SHA256 `a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260`。
- `deployment-artifacts/artifacts/intent_model/`、`deployment-artifacts/data/knowledge/releases/worldline-final-native-release-20260927/v2/`、`deployment-artifacts/data/indexing/worldline/final-native-20260927/v4/`、`deployment-artifacts/huggingface/`。它们受 `deploy/frozen-artifacts.json` 与 `deploy/model-cache-manifest.json` 校验，保持目录结构与普通文件（不要仅传 Windows symlink）。
- 从当前正式源取得**同一切换点**的 PostgreSQL custom-format dump 和 approved Qdrant collection snapshot，分别记录 SHA256。2026-09-28 rehearsal 用过的 dump/snapshot 只证明恢复流程，不应当默认为切换时最新数据。Qdrant collection 名称固定为 `worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4`；不恢复历史 collection。
- 三个独立 secret 文件：PostgreSQL password、LLM API key、Qdrant API key。`/secure/moodflow/` 仅是 `.env.example` 中的示例宿主路径；不要放进仓库，不要在命令行参数或日志中打印值。文件应由 ECS 管理员以安全方式传送，目录仅管理员可进入、文件权限 0600。

`deployment-artifacts/` 与 `.env.production` 均受 Git ignore。不得执行 `git add -f` 把它们纳入版本库。

## ECS 上的顺序（在准备好输入后执行）

```bash
git clone --recurse-submodules https://github.com/Yee-Ning/MoodFlow.git
cd MoodFlow
# 若既有 clone 尚未初始化子模块：git submodule update --init netease-music-api
cp .env.example .env.production
# 编辑 .env.production：只填变量/文件路径，不填任何密码或 Key。
# 经安全渠道把上述资源放入 deployment-artifacts/，把 secret 文件放到配置路径。
# 设置以下四个 shell 变量为已验证的实际文件路径及其独立记录的 SHA256：
PG_DUMP=/secure/moodflow/restore/current.dump
PG_DUMP_SHA256='REPLACE_WITH_VERIFIED_PG_SHA256'
QDRANT_SNAPSHOT='/secure/moodflow/restore/worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4-REPLACE_WITH_ID.snapshot'
QDRANT_SNAPSHOT_SHA256='REPLACE_WITH_VERIFIED_QDRANT_SHA256'
python3 -m deploy.ecs_preflight --check-platform \
  --postgres-dump "$PG_DUMP" --postgres-sha256 "$PG_DUMP_SHA256" \
  --qdrant-snapshot "$QDRANT_SNAPSHOT" --qdrant-sha256 "$QDRANT_SNAPSHOT_SHA256" \
  --env-file .env.production \
  --postgres-password-file /secure/moodflow/postgres_password \
  --llm-api-key-file /secure/moodflow/llm_api_key \
  --qdrant-api-key-file /secure/moodflow/qdrant_api_key
docker compose --env-file .env.production -f docker-compose.production.yml config --quiet
docker compose --env-file .env.production -f docker-compose.production.yml build
```

预检只检查 secret 文件存在且非空，**不读取文件内容**；会逐字节校验 wheel、冻结资源、模型缓存、dump 和 snapshot。预检输出仅包含冻结 release/collection/revision。若自定义 `.env.production` 中的挂载路径，应确保它们仍指向经预检的同一份资源；默认值已与上面目录对应。`IMAGE_TAG` 属于本地镜像标识，实际发布时还应记录 `docker image inspect` 的 image ID；它不等于远端 registry digest。

首次启动先仅创建**新的** PG/Qdrant volumes 并恢复；不要对已有生产库重复 restore：

```bash
docker compose --env-file .env.production -f docker-compose.production.yml up -d postgres qdrant
docker compose --env-file .env.production -f docker-compose.production.yml ps --all
docker compose --env-file .env.production -f docker-compose.production.yml \
  exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/postgres_password)"; exec pg_restore --exit-on-error --single-transaction --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$PG_DUMP"
docker compose --env-file .env.production -f docker-compose.production.yml \
  run --rm --no-deps --entrypoint python \
  -v "$(realpath "$(dirname "$QDRANT_SNAPSHOT")"):/restore:ro" \
  ai-service -m deploy.restore_qdrant_snapshot \
  --snapshot "/restore/$(basename "$QDRANT_SNAPSHOT")" --sha256 "$QDRANT_SNAPSHOT_SHA256"
docker compose --env-file .env.production -f docker-compose.production.yml up -d
```

Qdrant 恢复脚本仅接受冻结 collection 的 snapshot；如果目标 collection 已存在会拒绝覆盖。恢复 API 使用 `priority=snapshot` 和 checksum，再检查 green/41 points；之后 `migrate`/AI 启动还会验证 index binding 和完整 schema。Qdrant 官方 [snapshot 恢复说明](https://qdrant.tech/documentation/snapshots/) 与 [上传恢复 API](https://api.qdrant.tech/master/api-reference/snapshots/recover-from-uploaded-snapshot) 说明了该接口。

## 启动后检查与回滚

```bash
docker compose --env-file .env.production -f docker-compose.production.yml ps --all
docker compose --env-file .env.production -f docker-compose.production.yml logs --tail=100 migrate postgres qdrant netease java ai-service frontend
docker compose --env-file .env.production -f docker-compose.production.yml \
  exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' <<'SQL'
SELECT extversion FROM pg_extension WHERE extname = 'vector';
SELECT to_regclass('public.checkpoints');
SELECT to_regclass('public.agent_long_term_memory');
SQL
docker compose --env-file .env.production -f docker-compose.production.yml exec -T ai-service python -m deploy.runtime health
curl -fsS http://127.0.0.1:8088/healthz
curl -fsS http://127.0.0.1:8088/api/auth/status
curl -N -i -H 'Content-Type: application/json' --data '{"message":"你好"}' \
  http://127.0.0.1:8088/api/ai/chat
curl -fsS -H 'Content-Type: application/json' \
  --data '{"query":"乐队《Pixies》的年代、scene 和影响关系"}' \
  http://127.0.0.1:8088/api/ai/worldline/sessions
```

SSE 应返回 `text/event-stream`，首个事件应在最终事件前到达；冻结 Worldline 正式入口应返回有 graph、Scene、Influence 与 evidence 的 200 响应。`/api/ai/chat` 中的 Worldline 路由有已登记的应用债务，不能把它当作冻结 Worldline 部署门禁。还需按正式部署检查 session/checkpoint 可恢复、Qdrant green/41 points、provider 真实生成、Netease 真实 API 连通。只有 `ps --all` 显示六个常驻服务 healthy 且 migrate 为 `Exited (0)` 才算启动成功。

回滚先保留本次切换前的 PG dump、Qdrant snapshot、冻结资源与 Git commit/image ID；记录这些摘要。部署失败时可以 `docker compose --env-file .env.production -f docker-compose.production.yml down` 停止新环境，**不要加 `-v`**。卷不会因此删除。若需要重新恢复，使用新的 Compose project/空卷和已校验备份，避免在有数据的卷上重复 restore。前端默认只在 ECS 本机回环地址开放，运维可用 SSH 本地端口转发检查；本轮不开放公网 HTTP，也不配置 HTTPS。由于 `SESSION_COOKIE_SECURE=true`，仅 HTTP 检查不能替代完整浏览器登录验收。

本机 rehearsal 的六容器同一采样时刻观察总内存峰值约 1.4 GiB，AI 单服务约 831 MiB，Qdrant volume 约 1.64 GB；只是轻量 smoke 采样，不是 ECS 容量保证。公网访问、安全组、TLS、浏览器扫码登录、备份周期、云磁盘容量与后续发布流程仍需另行确定。

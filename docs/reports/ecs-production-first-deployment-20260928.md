# MoodFlow ECS 首次生产部署报告

日期：2026-09-28（Asia/Shanghai）

主机：Ubuntu 22.04 / x86_64，ECS `47.83.162.191`

代码：`61e431061fb0ae24ddebd2ca10134edd50bba83a`（ECS `main`，工作树干净）

Compose project：`moodflow-production`；Docker Compose v2.40.3

## 状态与拓扑

`docker compose --env-file .env.production -f docker-compose.production.yml ps -a` 的最终结果：

| 服务 | 状态 | 作用 |
| --- | --- | --- |
| `postgres` | healthy，重启 0 次 | PostgreSQL 16 / pgvector，业务、checkpoint、memory |
| `qdrant` | healthy，重启 0 次 | approved Worldline collection |
| `netease` | healthy，重启 0 次 | 网易云 API 适配层 |
| `java` | healthy，重启 0 次 | 音乐和鉴权 API；访问 `netease:3000` |
| `ai-service` | healthy，重启 0 次 | AI API；访问 `java:8080`、`postgres:5432`、`qdrant:6333`、LLM provider |
| `frontend` | healthy，重启 0 次 | Nginx / React；只绑定宿主 `127.0.0.1:8088` |
| `migrate` | Exited (0) | 业务和 LangGraph migration、冻结 index 校验 |

容器之间使用 Compose `backend` 网络服务名。`.env.production` 权限为 0600，只含非敏感配置和 secret 路径。三个独立 secret 文件位于 `/secure/moodflow/`，权限均为 0600，由容器 AI 用户 UID 10001 持有；宿主目录权限为 0700。文件内容未写入 Git 或本报告。ECS 原有仓库未产生 tracked 修改。

## 预检与构建

- `python3 -m deploy.ecs_preflight --check-platform`：PASS；已逐字节校验 27 个冻结 artifact、CPU Torch wheel、Qwen cache、intent model、release/index、PostgreSQL dump 和 Qdrant snapshot。
- 容器运行时 Python 3.12.10，x86_64 wheel 安装和 `pip check` 通过；宿主 Python 为 3.10.12，仅用于预检。
- `docker compose ... config --quiet`：PASS。
- `docker compose ... build --progress plain`：AI、Java、Netease、Qdrant、frontend、migrate 全部构建成功；前端 `tsc -b && vite build` 成功。
- 本机测试：`ai-service/.venv/Scripts/python.exe -m pytest deploy/tests/test_deployment.py ai-service/tests/test_worldline_era_expansion.py -q`，51 passed。

| 镜像 | 标识 / 大小 |
| --- | --- |
| `moodflow/ai-service:worldline-v1-fe24b04`（migrate 共用） | `sha256:3e919d8a75e0b592d9cfe92e82429404dbf7e3456b167c20549998249c7b1656` / 2.45 GB |
| `moodflow/java:worldline-v1-fe24b04` | `sha256:e1c42b2024f1d521fcc85a95c046d3a7381cca2f1f4fdeb47b320c0099a458e2` / 445 MB |
| `moodflow/netease:worldline-v1-fe24b04` | `sha256:ccbcbfbfcc530b04fd166d5dbf0bba2a09d281495fd3d79957536b80d1f9a080` / 346 MB |
| `moodflow/qdrant:worldline-v1-fe24b04` | `sha256:c198a3484cf1249ad4a0d7218d9036a3a4ac50d92c6b6d0a19b02abc505a5ff9` / 270 MB |
| `moodflow/frontend:worldline-v1-fe24b04` | `sha256:14874cfd63df78e6b40619ded60c93ffa9bd1f3961fc69fe7b6aff79ab47779f` / 98 MB |
| `pgvector/pgvector:pg16-trixie` | pinned digest `sha256:c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b` / 641 MB |

## 数据恢复

恢复前确认生产卷不存在、PostgreSQL public schema 为 0 张表。首次恢复只使用新的 named volumes；未清空旧库、未删除任何 volume 或源文件。

| Volume | 最终磁盘占用 | 结果 |
| --- | ---: | --- |
| `moodflow-production_postgres_data` | 106 MB | custom-format dump 单事务恢复成功 |
| `moodflow-production_qdrant_data` | 2.2 MB | approved collection 已恢复 |
| `moodflow-production_qdrant_snapshots` | 3.3 MB | Qdrant snapshot 存储 |

源 dump `/secure/moodflow/restore/current.dump` 的 SHA256 为 `48993cfe24c9f974e77a80e575a34e895b009a0ff320f20388a0a31de0de6f49`；源 snapshot 的 SHA256 为 `fd4c2dd9a3fdbc70d3329463b4ac6777a833488f66bc7727c3442f24a079ca43`。部署后重新计算，均与上传值一致，源文件保留。

PostgreSQL 恢复后、验收请求前的行数：`agent_task` 4、`agent_session` 7、`agent_history_event` 28、`agent_artifact` 3、`agent_long_term_memory` 11、`checkpoints` 148、`checkpoint_blobs` 157、`checkpoint_writes` 186。`vector` extension 为 0.8.6。验收请求后对应行数为 5、8、32、3、11、164、176、216；业务 migration 2 条，LangGraph checkpoint migration 10 条。数据量增加来自本轮 API 验收请求。

Qdrant collection `worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4`：green；reported 和 exact count 均为 41；dense 为 1024 维 Cosine（HNSW m=16、ef_construct=200）；sparse `bm25` modifier 为 IDF。`migrate` 和 AI 健康检查均通过冻结 index binding 验证。

## API 与业务验收

| 检查 | 结果 |
| --- | --- |
| `GET /` | 200，`text/html` |
| `GET /healthz` | 200 |
| `GET /api/auth/status` | 200 |
| `POST /api/ai/chat`，`{"message":"你好"}` | 200，`text/event-stream`；收到 update、final、done，且无 error event |
| `POST /api/ai/chat`，空对象 | 422，请求校验生效 |
| Java → Netease 实际搜索 `Pixies` | 200，业务 code 200，5 个结果 |
| `python -m deploy.runtime health` | PASS，检查 AI→Java/PostgreSQL/Qdrant/Netease；AI 启动时 LLM `/models` 门禁通过 |
| `POST /api/ai/worldline/sessions`，Pixies 年代/scene/影响查询 | 200；11 节点、10 边、6 sources、5 citations、3 branches；Scene 1、Influence 1；真实 provider 生成 |
| `GET /api/ai/worldline/sessions/{id}` | 200；与创建响应完全一致 |
| Era 线上正例 | **未通过**；对 Pixies 和周杰伦的年代查询均返回 200，但 `era_relations` 为 0 |

六个常驻服务健康且重启数为 0；最近 90 秒关键容器日志未出现 ERROR/FATAL/Traceback。上述检查是功能 smoke，不代表负载、并发或浏览器登录测试。

## 资源与限制

采样时宿主 7.3 GiB 内存，约 1.6 GiB 已用、约 5.4 GiB available；无 swap。容器内存：AI 1.338 GiB、Qdrant 183 MiB、Java 173 MiB、Netease 63 MiB、PostgreSQL 72 MiB、frontend 10 MiB。系统盘 40 GB，约 15 GB 已用、23 GB 可用；Docker build cache 约 7.2 GB。此为单次 smoke 采样，不是容量保证。

1. Qdrant restore 脚本上传后返回非零，但 Qdrant 访问日志显示上传 HTTP 200、snapshot shard 恢复完成；独立精确计数、schema、`migrate` 和运行时健康检查均通过。首次失败是 secret 文件的容器读取权限，已修复；再次执行后不能安全重试，因为 collection 已存在。脚本对上传后立即读取的 readiness 可能存在竞态，原因尚未被单独复现，应在下次发布前增加等待并用新空卷验证。不得在当前有数据 collection 上重复恢复。
2. 冻结 index 的已支持 claim 中没有满足当前 Era 投影条件的证据，线上 Era relation 正例尚未验收。Era 相关测试通过，但不能替代线上正例。一次同时提及艺术家和专辑的复合查询得到 `entity_search_failed`，一次 Nirvana 查询得到 `entity_ambiguous`；这两项需要作为业务查询体验问题单独处理，不应修改已批准数据来掩盖。
3. 本轮只验证给定 dump/snapshot 的 SHA256 和恢复结果，无法独立证明它们涵盖正式切换前的最新数据；需按业务切换时间点核对源系统。
4. 前端只监听 ECS 回环地址，尚无公网入口、Nginx/TLS 或安全组配置。`SESSION_COOKIE_SECURE=true`，HTTP smoke 不能验证浏览器扫码登录。尚未进行负载、长期稳定性、备份自动化或真实公网访问测试。

## 后续建议

配置 Nginx 反向代理和 HTTPS，仅在阿里云安全组开放 443，并在域名下完成浏览器登录、SSE 和 Worldline 回归。制定 PG/Qdrant 定期备份、异机保存及恢复演练；设置磁盘、内存、容器健康和 API 错误率告警。CI/CD 发布时固定 Git commit、镜像 digest、冻结 artifact SHA256 和数据库切换点，先在空卷验证恢复，再升级生产；保留本次 dump、snapshot、镜像和卷供回滚。

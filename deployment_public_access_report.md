# MoodFlow ECS 公网访问与浏览器验收报告

日期：2026-09-29（Asia/Shanghai）

实例：阿里云香港 `i-j6c9tptfc73qitk9oeki`，`47.83.162.191`

公网 HTTP 地址：[http://47.83.162.191/](http://47.83.162.191/)

## 结论

公网 HTTP、同源 API、AI Chat SSE 和浏览器 Worldline 创建/刷新恢复已实测通过。**HTTPS 尚未配置**：当前没有可用域名或证书。`SESSION_COOKIE_SECURE=true` 保持不变，因此真实浏览器登录会话及其续用仍待 HTTPS 验收。Worldline 浏览器首次创建出现一次应用层叙事生成 502，原请求重试成功；这次失败没有被计为通过。

## 网络与 Nginx 拓扑

```text
公网 :80 → ECS 主机 Nginx → 127.0.0.1:8088 → frontend 容器 Nginx
                                            ├─ /、/worldline 等 → React 页面
                                            ├─ /api/ai/* → ai-service:8001
                                            └─ /api/* → java:8080 → netease:3000
ai-service → postgres:5432、qdrant:6333、java:8080、LLM provider
```

主机配置为 `/etc/nginx/sites-available/moodflow`，仓库对应文件为 `deploy/nginx-host.conf`；两者 SHA256 均为 `fd9f419fb6f1eafdf174820469965483eea75f1212dbe865af1f6055ce42c92c`。`/api/ai/` 保留路径、Host 与转发头，采用 HTTP/1.1，关闭 proxy/request buffering、cache 和 gzip，读写超时为 300 秒。前端容器仍只绑定宿主回环地址 `127.0.0.1:8088`；没有重建 Compose 服务或改动数据库、Qdrant、volume、dump、snapshot。

宿主监听：公网 `22/tcp`（SSH）、`80/tcp`（Nginx），回环 `8088/tcp`（frontend）；宿主没有监听 `443`、`5432`、`6333`、`8080`、`8001`。用户已在实例安全组开放 `80/tcp`（`0.0.0.0/0`），本机公网请求由超时变为 200；随后用户确认已关闭此前存在的 `3389/tcp` 入站规则，保留 SSH 22。Codex 的阿里云控制台浏览器会话登录已过期，无法独立读取最终安全组规则列表，因此 3389 关闭以用户控制台确认记录，宿主监听与公网 22/80 连通则已独立验证。

## Docker 状态

`docker compose --env-file .env.production -f docker-compose.production.yml ps -a`：

| 服务 | 镜像 | 状态 |
| --- | --- | --- |
| postgres | `pgvector/pgvector:pg16-trixie` 固定 digest | healthy |
| qdrant | `moodflow/qdrant:worldline-v1-fe24b04` | healthy |
| netease | `moodflow/netease:worldline-v1-fe24b04` | healthy |
| java | `moodflow/java:worldline-v1-fe24b04` | healthy |
| ai-service | `moodflow/ai-service:worldline-v1-fe24b04` | healthy |
| frontend | `moodflow/frontend:worldline-v1-fe24b04` | healthy |
| migrate | 与 ai-service 共用镜像 | Exited (0) |

三个生产卷仍为 `moodflow-production_postgres_data`、`moodflow-production_qdrant_data`、`moodflow-production_qdrant_snapshots`。只读 SQL 确认 `vector=0.8.6`、`checkpoints=181`、`agent_long_term_memory=11`、`agent_session=10`；行数随本轮合法会话请求增长。运行时 `python -m deploy.runtime health` 返回 0，覆盖 PostgreSQL schema、冻结 index/Qdrant green 与 dense/sparse 契约及 Java/Netease 连通。LLM 连通另由成功的 Worldline 真实生成验证。首次部署时 Qdrant collection 精确 41 points；本轮只读扫描仍为 41 points，未恢复或写入 collection。

## 公网 HTTP 与浏览器验收

| 项目 | 实测结果 |
| --- | --- |
| 首页 `/` | 公网 200，浏览器显示欢迎页；Explore 后进入 `/login` |
| `/healthz` | 公网 200 |
| `/api/auth/status` | 公网 200，业务 code 200；当前未登录 |
| `/api/auth/qr/key` | 公网 200，业务 code 200；返回数据存在，未记录凭据内容 |
| `/api/ai/chat` | 公网 200，`text/event-stream; charset=utf-8`；`update` 约 0.59 秒到达，`final`、`done` 约 1.09 秒到达；浏览器 AI 弹窗发送“你好”并显示回复 |
| Chat 请求校验 | 空 JSON 返回 422 |
| `/worldline` 页面和刷新 | 公网 200；浏览器显示正式页面，刷新后恢复原 Pixies 会话 |
| Worldline 创建 | 首次 502（`narrative_generation_failed`），同一查询重试 200；图谱 11 节点/10 边、Scene 1、Influence 1、Era 0、6 sources、4 citations |
| Worldline 读取 | 刷新触发 checkpoint 恢复；同源 GET 200，结果仍为同一 Pixies 图谱与 revision 1 |
| 不存在的 Worldline 会话 | 404，错误路由正常 |

浏览器完成了页面、Worldline 和 AI Chat 交互；SSE 的事件类型、Content-Type 与先后时序由公网 HTTP 客户端逐行实测。**没有把 HTTP 下的扫码登录视为通过**，因为浏览器不能在不安全来源上完成 `Secure` session cookie 的正式验收。

## Era relation 调查

本轮 Pixies 查询含“年代”，查询理解规则会标记 timeline，Era 扩展入口会被请求。根实体已解析为 Pixies；相同检索结果支持 Scene 1、Influence 1，并生成了 6 个 evidence sources，因此不是全局 entity resolution 或 Qdrant 检索故障。生产 collection 41 points 的 `supported_claims` 扫描包含 21 条 claim：`other` 6、`chart_position` 8、`released` 2，以及 `influenced_by`、`influenced`、`emerged_from_scene`、`signed_to`、`performed_at` 各 1。没有 `belongs_to_era`、`representative_of_era`、`temporal_association`、`career_phase`、`style_era_association` 或 `debuted` claim。两条 `released` claim 的主体均为 Nirvana 艺人，缺少要求的歌曲/专辑 `object_entity_id`，不能按现有证据规则投影为 Era 关系。

结论：**当前 approved/frozen knowledge 缺少可被 Era 候选提取器接受的结构化证据**。Pixies 的年代文字可进入叙事，但不能替代有实体、时间与原文 span 绑定的已验证 claim；Era 0 是证据覆盖缺口，不是应通过降低 validator 阈值或伪造数据修复的投影错误。后续应收集可核实的 Pixies 发行、出道或时代关系原始来源，补齐 subject/object entity、时间和 exact evidence span，经知识审核、release/index 构建与 staging 正反例验证后，按冻结数据发布流程上线。现有生产 collection 未被污染。

## 验证、资源与剩余风险

- ECS：`nginx -t`、`docker compose ... config --quiet` 均通过；六个常驻容器 healthy，migrate Exited (0)。本机 `pytest deploy/tests/test_deployment.py ai-service/tests/test_worldline_era_expansion.py -q` 为 **52 passed**。本轮仅新增主机 Nginx 配置与部署测试，未改前后端业务代码或重建镜像。配置/测试独立提交为 `b8a58fe`，未 push；其他既有未提交文件保持原样。
- 最近 15 分钟容器日志未见持续错误：AI/frontend 各记录了上述同一次 Worldline 502；PostgreSQL 有一次本轮只读审计 SQL 引号写错导致的 syntax error，随后用正确 SQL 验证成功；Qdrant、Netease、Java 无对应错误记录。
- 采样资源：宿主 7.3 GiB 内存、约 5.4 GiB available、无 swap；40 GB 系统盘约 15 GB 已用、23 GB 可用。AI 容器约 1.0 GiB，Java 179 MiB，Qdrant 105 MiB，PostgreSQL 67 MiB，Netease 60 MiB，frontend 8 MiB。
- 当前公网仍为明文 HTTP，正式浏览器登录、`Secure` cookie、HTTPS 重定向和证书续期未验收。AI API 可经公网 HTTP 调用，存在未认证调用与 LLM 成本风险；在域名/TLS 接入前不应传输真实登录凭据。
- Worldline 首次叙事生成曾返回一次应用层 502，重试成功；需要对 provider 结构化生成失败率增加监控和针对性回归。尚未做并发、负载和长期稳定性测试。
- 安全组 `3389/tcp` 已由用户在控制台确认关闭；因 Codex 控制台登录会话过期，最终规则列表未能独立复核。宿主无相应监听。

## HTTPS 后续步骤

用户当前没有可用域名。取得域名后，将 A 记录指向 `47.83.162.191` 并验证解析；为该域名签发受信任证书，配置 Nginx `443` TLS 与同样的 API/SSE 代理规则，确认浏览器扫码登录、`Secure` cookie、AI Chat、Worldline 和刷新恢复，再将 HTTP 重定向至 HTTPS。安全组放行 443，保留 SSH 22；若改用 DNS-01 完成证书自动续期，可在切换后关闭公网 80，实现最终仅 443 面向 Web。随后纳入 Nginx 配置/证书续期、健康检查、错误率与备份监控的 CI/CD 运维流程。

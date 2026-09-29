# MoodFlow ECS HTTPS IP 部署与验收报告

日期：2026-09-29（Asia/Shanghai）

实例：阿里云香港 `i-j6c9tptfc73qitk9oeki`，公网 IP `47.83.162.191`
正式入口：[https://47.83.162.191/](https://47.83.162.191/)

## 结论

已使用 Let's Encrypt 正式签发的短周期 **IP Address Certificate** 完成受信任 HTTPS 部署。标准客户端和 Codex 浏览器均可正常访问，不需要忽略证书错误。HTTP 普通请求 308 跳转 HTTPS，HTTP-01 challenge 仍可经 80 端口访问。扫码登录、Secure Cookie、刷新后会话保持、AI SSE、Worldline 创建与读取均已实测。生产 Compose 镜像、数据库和 Qdrant collection 未重建或恢复。

## 证书与自动续期

| 项目 | 实测值 |
| --- | --- |
| ACME 客户端 | 官方 Snap Certbot `5.8.0` |
| 申请方式 | Webroot HTTP-01，`/var/www/moodflow-acme`，`--ip-address 47.83.162.191`，`--preferred-profile shortlived` |
| 预演 | 独立 Certbot 目录完成 Let's Encrypt staging 签发，未供正式 Nginx 使用 |
| 正式证书 | `/etc/letsencrypt/live/47.83.162.191/fullchain.pem`；私钥由 Nginx 直接读取，不写入仓库或报告 |
| Issuer | `C=US, O=Let's Encrypt, CN=YE2` |
| Subject Alternative Name | `IP Address:47.83.162.191`，critical |
| 有效期 | 2026-09-29 00:30:33 UTC 至 2026-10-05 16:30:32 UTC（北京时间 10 月 6 日 00:30:32） |
| 调度 | `snap.certbot.renew.timer` active/enabled；系统每天两次调度续期 |
| 续期配置 | `authenticator=webroot`、`preferred_profile=shortlived`，正式 ACME endpoint |
| 部署 hook | `/etc/letsencrypt/renewal-hooks/deploy/10-moodflow-nginx`；仅针对本 IP lineage，先 `nginx -t` 再 reload |
| 续期验证 | `certbot renew --cert-name 47.83.162.191 --dry-run --run-deploy-hooks --no-random-sleep-on-renew --non-interactive` 成功，hook 执行且 Nginx 配置测试通过 |

Let's Encrypt 的 IP 证书属于约 160 小时的 shortlived profile，因此自动续期是服务持续可用的必要条件。正式证书尚未到期，当前验证的是 staging 模拟续期；下一次真实续期仍需监控。方案依据：[Let's Encrypt 的 Certbot IP 证书说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)、[证书 profile](https://letsencrypt.org/ca/docs/profiles/)、[Certbot 续期与 hook 文档](https://eff-certbot.readthedocs.io/en/stable/using.html)。

## Nginx 拓扑与端口

```text
公网 :80  ── ACME HTTP-01 challenge → /var/www/moodflow-acme
          └─ 其余请求 308 → https://47.83.162.191
公网 :443 ── 宿主 Nginx TLS → 127.0.0.1:8088 → frontend 容器 Nginx
                                                     ├─ 页面、/worldline
                                                     ├─ /api/ai/* → ai-service:8001
                                                     └─ /api/* → java:8080 → netease:3000
ai-service → postgres:5432、qdrant:6333、java:8080、LLM provider
```

主机配置 `/etc/nginx/sites-available/moodflow` 与仓库 `deploy/nginx-host.conf` 的 SHA256 均为 `7968a8c604382a7810012b79a6a685e48bb323eeed19f02cf76db143e0d6a019`。TLS 允许 1.2/1.3，关闭 session tickets。`/api/ai/` 使用 HTTP/1.1，关闭代理响应缓冲、请求缓冲、cache 和 gzip；读写超时 300 秒。`nginx -t` 通过，Nginx 服务 active。证书申请与配置切换没有停止已工作的 HTTP 服务。

ECS 宿主公网监听为 `22/tcp`、`80/tcp`、`443/tcp`；`8088/tcp` 仅监听 `127.0.0.1`。宿主无 `5432`、`6333`、`8080`、`8001`、`3389` 监听。阿里云安全组的 `443/tcp`、`80/tcp` 开放及 `3389/tcp` 关闭由用户在控制台确认；公网 80/443 连通已从外部独立验证。Codex 控制台会话无法读取安全组完整规则列表，因此其余规则以宿主监听和用户确认记录为准。需要保留 SSH 22、HTTPS 443，以及 Webroot HTTP-01 自动续期所需的 HTTP 80；内部端口不得对公网开放。

## TLS、页面、API 与 SSE 验收

| 检查 | 实测结果 |
| --- | --- |
| 标准客户端证书校验 | Windows `curl.exe` 未使用 `-k`：HTTPS 200、`ssl_verify_result=0`；Python 默认信任库握手成功；OpenSSL `-verify_ip` 验证成功 |
| 证书链 | Leaf → Let's Encrypt YE2 → Root YE → ISRG Root X2；SAN 精确匹配公网 IP |
| HTTP 跳转 | `http://47.83.162.191/` 返回 308，Location 为 `https://47.83.162.191/` |
| ACME 例外 | 公网 `/.well-known/acme-challenge/` 测试文件返回 200；测试文件验收后已清理 |
| 首页与路由 | HTTPS `/`、`/worldline` 返回 200；浏览器加载、刷新正常，无证书警告 |
| 健康与登录状态 | `/healthz` 200；`/api/auth/status` 200；HTTPS 登录页通过 `/api/auth/qr/key` 获取并显示二维码 |
| AI Chat | HTTPS 浏览器弹窗发送“你好”并显示回复；空 JSON 请求返回 422 |
| SSE | `POST /api/ai/chat` 返回 200、`text/event-stream; charset=utf-8`；`update` 约 0.984 秒到达，`final` 与 `done` 约 1.922 秒到达，事件分批到达而非整段缓冲后一次返回 |
| 错误路由 | 不存在的 Worldline session 返回 404 |

## HTTPS 登录与 Secure Cookie

用户在 HTTPS 浏览器中使用网易云音乐 App 扫码并确认后，页面进入 `/library`。浏览器 Cookie 元数据检查：`JSESSIONID` 为 `Secure=true`、`HttpOnly=true`、`SameSite=Lax`、`Path=/`，`expires=-1`，即无持久 Max-Age 的会话 Cookie。未读取或记录 Cookie 值。刷新 `/library` 后仍显示已登录；刷新后的 `/api/auth/status` 返回 HTTP 200、业务 code 200、`loggedIn=true`。进入并刷新 `/worldline` 后仍保持登录。`SESSION_COOKIE_SECURE=true` 保持原值。

## Worldline 回归与 Era

HTTPS API 使用 Pixies 查询创建 session：第一次返回 `502 narrative_generation_failed`，同一查询重试返回 200；随后 GET 同一 session 返回相同结果。成功结果为 11 节点、10 边、Scene 1、Influence 1、Era 0、6 个 evidence sources。浏览器同样完成创建、显示图谱与 checkpoint 同步，页面刷新后恢复同一 Pixies 会话，且登录状态未丢失。首次 502 是真实的应用层生成失败，不能视为首试成功；应持续监控该错误率。

Era 0 沿用前次已完成的只读调查结论：当前 approved/frozen knowledge 缺少可供 Era 候选提取器接受的、具备实体与原文证据绑定的结构化 claim。后续需补充可靠原始来源、完成审核与冻结 release/index 发布流程。未改动、重做或清空 Qdrant collection，也未伪造 Era 数据。

## 生产服务与数据保护

`docker compose --env-file .env.production -f docker-compose.production.yml ps -a`：postgres、qdrant、netease、java、ai-service、frontend 均 healthy；migrate 为 Exited (0)。镜像延续 `moodflow/*:worldline-v1-fe24b04`，PostgreSQL 延续固定 digest 的 `pgvector/pgvector:pg16-trixie`。`docker compose ... config --quiet` 通过。没有执行 Compose down、重建镜像、数据库恢复、Qdrant restore 或生产卷删除。现有 production named volumes 保持不变。宿主 40 GB 系统盘约 23 GB 可用；7.3 GiB 内存约 5.3 GiB available，无 swap。

本地验证：`pytest deploy/tests/test_deployment.py ai-service/tests/test_worldline_era_expansion.py -q` 为 **52 passed**；Nginx 配置及续期 hook 的测试和生产命令均通过。除 Nginx 主机配置、hook、部署测试及本报告外，没有改动业务代码。

## 剩余风险与后续

- 短周期证书依赖 Certbot 定时器、HTTP 80 与 deploy hook。应对续期失败、证书剩余有效期和 Nginx reload 失败设置告警；当前未配置告警邮箱。
- 公网 IP 若变化，现有证书 SAN 将不再匹配，需重新签发并更新入口。若未来启用域名，可迁移至域名证书并重新评估 80 端口续期方式。
- AI 接口可被公网调用，存在未认证请求及 LLM 成本风险；应按产品需要增加访问控制、限流与日志监控。
- Worldline 叙事生成偶发 502，尚未做并发、负载及长期稳定性测试。
- 阿里云安全组最终规则列表未在 Codex 控制台独立复核。用户已确认开放 443、保留 80/22 并关闭 3389；建议在控制台持续核查无内部端口暴露。

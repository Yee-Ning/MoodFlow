# MoodFlow Deployment Readiness Final Report

日期：2026-09-28。范围仅为本机 Production Compose rehearsal 的冻结正式产品入口与部署基础设施。未修改 Worldline V1、Java entity search、chat branch generation、Gold/Eval/corpus、relation validator 或正式 release/index；未部署或配置服务器。

## 门禁与隔离

本轮 Worldline 业务门禁使用前端同源 `POST /api/ai/worldline/sessions`，查询为 `乐队《Pixies》的年代、scene 和影响关系`。`/api/ai/chat` 的 Worldline 路由不是冻结产品入口，不作为本轮部署门禁。rehearsal Compose project 为 `moodflow-rehearsal-20260928`，前端只绑定 `127.0.0.1:18088`；临时 env/secrets 与原始响应位于被 Git 忽略的 `deployment-artifacts/rehearsal/`。本轮没有执行 `down -v`、对开发库 restore/drop 或删除任何 volume；开发 volumes `moodflow_pgdata`、`ai-service_qdrant_storage` 仍存在。

## 数据与 Compose

- PostgreSQL：沿用本次隔离演练中从最新 custom-format dump `MoodFlow-backups/moodflow-agent-deployprep-20260927-234049.dump` 对**新** PG16/pgvector volume 完成的 restore；当时 `pg_restore --exit-on-error --single-transaction --no-owner --no-privileges` 退出码 0。此次重新校验 dump SHA256 `592951799aef8d0397f1cba7adaab7efd0530e7d16ff2504f5b1eba955372065`；生产角色连接成功，`vector=0.8.6`，业务迁移 2、LangGraph checkpoint 迁移 10，`agent_session=14`、`checkpoints=213`（含演练后续写入）。`migrate` 重启后再次 Exited (0)。
- Qdrant：仅恢复 approved collection `worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4` 的 snapshot；恢复时 41 个 point 的 ID/payload 已与 frozen projection 逐项一致。此次重新校验 snapshot SHA256 `d86695791f64313864e01374da693410f5f582844c1da7c104c023f6ff1bb2e8`，实时查询为 green、41 points、dense 1024/Cosine、sparse `bm25`/IDF；本轮 session 检索返回的 3 个 point ID 均能从此 collection 实际读回，其 release/index/content SHA 与响应 provenance 相同。未迁移历史 collection。
- 冻结文件：manifest SHA256 `445b7263221ae5864015752a6689e5d8303c21409407375a6e0b33ac831f50f3`；`python -m deploy.artifacts --root deployment-artifacts` 验证 release `knowledge-v2-daa965c10984eefa` 的 20 个文件；`python -m deploy.model_cache --root deployment-artifacts/huggingface` 验证 Qwen3-Embedding-0.6B pinned revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`；AI 容器 `python -m deploy.runtime verify` 通过。挂载仍为只读冻结路径。
- `docker compose ... config --quiet` 通过。`postgres`、`qdrant`、`netease`、`java`、`ai-service`、`frontend` 均为 **healthy**，`migrate` Exited (0)。AI 运行配置为 hybrid、PostgreSQL checkpoint、正式 index manifest、`deepseek-flash` generation model，Java/Netease/Qdrant 指向 Compose 内部服务。`GET /healthz` 与 AI `/health` 均为 200；同源 `/api/auth/status` 和 Java→Netease `/api/auth/qr/key` 均为 HTTP 200/业务 code 200，后者返回非空 key（未记录 key 值）。
- 同源 SSE 传输用非 Worldline 问候语单独核查：HTTP 200、`text/event-stream`，首事件 0.078 秒、最终 `success`，`update/final/done` 均到达；匿名无效 `JSESSIONID` 经同源代理返回预期 401。此项仅验证 Nginx/SSE/cookie 代理，不把 chat Worldline 结果当作部署门禁。演练没有扫码登录，真实已登录 cookie 续用未覆盖。

## 冻结入口真实 smoke

经正式 Frontend Nginx 同源入口新建 session `9c54899dad904fa7b34c5f1f8e2bdc9a`：HTTP 200、revision 1、graph 11 nodes / 10 edges、Scene relation 1、Influence relation 1、branches 3、sources 6、citations 4，narrative 非空。Influence direction 为 `influencer_to_influenced`；Scene/Influence 的 evidence 均有 `evidence_id/source_id/claim_id/exact_span`，source ID 可解析，relation metadata 包含 provenance 与 validator 版本。三条知识来源有真实 `point_id`、`retrieval_status=success`、ContextPack rank 1–3，`retrieval_legs` 同时含 dense 与 lexical；实时 Qdrant retrieve 证实对应的 release ID、index input hash、source content SHA 完全一致。这是该请求本身的 hybrid/ContextPack 证据，不是独立检索 smoke 冒充业务请求。

正式 session 代码路径使用 `ProviderWorldLineRuntime` 调用真实 `create_generation_response`，当前 Compose 配置的模型为 `deepseek-flash`，无 fixture/mock；本次请求生成非空 narrative 与三个分支并经生产校验后提交。该 session 在 PostgreSQL 的 `worldline:<session_id>` 有 3 条 checkpoint。三份实际 create/recover/continue 响应均通过服务端 `WorldLineSessionResponse` 校验；前端 Worldline flow/view 测试通过，`src/services/worldline.ts` 使用相同同源 API 合约。

## 保留卷重启与资源

仅执行 `stop frontend ai-service java netease`；停止期间 PostgreSQL/Qdrant 仍 healthy。`up -d --no-build` 依现有 `depends_on` 顺序重新运行 migrate → Netease → Java → AI → Frontend，六个长期服务最终全部 healthy，migrate 再次退出码 0。通过同源 GET 恢复上述**相同 session**：HTTP 200、revision 1；graph、Scene、Influence、sources、citations 与重启前逐项完全相等。执行服务器返回的 enabled action `wlb:1:1d4fa512c5b244e099d0d7ab6a30806d`：HTTP 200、revision 2、graph 12 nodes / 11 edges、sources 7，原有节点、边、Scene/Influence 和来源均保留；该 session 的 checkpoint 从 3 增至 6。Qdrant collection 重启后仍 green/41 points。

服务器规格估算沿用本次相同 Production Compose rehearsal 的 8 次本机采样：六容器同一采样时刻总内存观察峰值 1,423.19 MiB；AI 830.8、Qdrant 215.9、Java 221.6、PostgreSQL 89.87、Netease 46.94、Frontend 23.18 MiB 的各服务观察峰值。AI 启动约 30.8 秒，pinned Qwen 离线加载约 4.382 秒；PG volume 110,415,722 bytes，Qdrant data 1,635,741,415 bytes，snapshot volume 3,443,712 bytes。这是本机有限采样基线，不是容量保证或压力测试。

延期应用债务（不作为本轮部署阻断）：

- `chat_worldline_untyped_entity_search`：无类型 entity search 碰到 malformed song hit 时 Java 502。
- `chat_worldline_branch_generation_semantic_validation`：chat 专用 `BranchDraftOutput` 出现 `branch_type_target_mismatch`。

验证命令及结果：`docker compose --env-file deployment-artifacts/rehearsal/rehearsal.env -p moodflow-rehearsal-20260928 -f docker-compose.production.yml config --quiet` 通过；同命令的 `ps --all` 六 healthy/migrate 0；同命令的 app `stop`/`up -d --no-build` 通过；`npm run test:worldline` 两项通过；冻结文件/模型/manifest/dump/snapshot SHA 校验通过；真实 HTTP session 创建、恢复、续接及 PostgreSQL/Qdrant 查询通过。本轮无业务代码改动，未运行 Semantic Eval、生产云部署或服务器数据 restore。

```text
postgres_restore_pass=true
qdrant_restore_pass=true
production_compose_ready=true
worldline_frozen_entry_smoke_pass=true
production_restart_pass=true
server_resource_baseline_ready=true
can_start_server_deployment=true
```

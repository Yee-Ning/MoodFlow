# PostgreSQL 当前数据清单（2026-10-09）

统计时间：2026-10-09 15:44:27，北京时间。以项目开发配置实际连接的本地数据库为主，另列本地部署演练库。所有行数均来自 `COUNT(*)`，没有采用可能过期的表统计估计。

当前本地 PG 主要存储 **Agent 会话与执行记录、长期记忆、LangGraph 检查点和 WorldLine 探索状态**。共有 13 张应用表，数据库大小约 36 MB。长期记忆目前全部是验证数据，用户画像文档表尚无记录。

## 1. 实例与统计范围

| 项目 | 本地开发库 |
| --- | --- |
| 容器 | `moodflow-postgres` |
| 连接地址 / 数据库 | `127.0.0.1:5432` / `moodflow_agent` |
| 持久化卷 | 原有 `moodflow_pgdata` |
| PostgreSQL / pgvector | 16.15 / 0.8.6 |
| 数据库大小 | 37,846,039 bytes，`pg_size_pretty` 显示 36 MB |
| Schema | `public` |
| 查询保护 | `READ ONLY`、`REPEATABLE READ`，单条查询超时 15 秒、等待锁超时 2 秒 |

同一实例还存在默认维护库 `postgres`，约 7.2 MB；本报告业务数据统计针对 `moodflow_agent`。

检查前开发库容器处于停止状态。本次启动现有容器读取数据；统计后确认没有其他数据库客户端连接，并恢复容器停止状态。未执行迁移、业务写入 SQL、数据清理、备份恢复或卷替换。

## 2. 全部表及其当前内容

表大小包含数据、TOAST 和索引；表大小合计与整个数据库大小的口径不同。

| 分类 | 表 | 记录数 | 表总大小 | 存储内容 |
| --- | --- | ---: | ---: | --- |
| 任务 | `agent_task` | 14 | 56 kB | 任务 ID、所属对话、创建/关闭时间 |
| 会话 | `agent_session` | 17 | 80 kB | 任务的会话段、前序/父会话、继承的交接记录、创建/关闭时间 |
| 执行历史 | `agent_history_event` | 263 | 704 kB | 用户/助手消息、路由、工具调用、模型预算、上下文、检查点等事件；JSONB 内容和事件顺序 |
| 产物 | `agent_artifact` | 7 | 48 kB | 检索证据与工具结果，包含 payload、projection、内容哈希和关联任务/会话 |
| 会话交接 | `agent_handoff` | 3 | 32 kB | 目标、阶段、已完成步骤、待办、约束、证据引用、状态摘要和 token 预算 |
| 执行协调 | `agent_execution` | 10 | 32 kB | 执行锁键、授权范围、owner、fence、revision 和租约到期时间 |
| 长期记忆 | `agent_long_term_memory` | 11 | 184 kB | 用户偏好/情景记忆、来源线程、可信度、重要度、版本/状态及可选向量 |
| 用户画像文档 | `agent_user_profile_document` | 0 | 16 kB | 按用户和版本保存的 JSONB 画像快照；目前为空 |
| 检查点 | `checkpoints` | 217 | 408 kB | 线程/命名空间、父检查点、状态版本、通道版本和元数据 |
| 检查点状态 | `checkpoint_blobs` | 303 | 16 MB | 按线程/通道/版本保存的序列化状态，例如 WorldLine 快照、消息和上下文 |
| 检查点写入 | `checkpoint_writes` | 468 | 11 MB | 节点执行的通道写入与待恢复状态，包括部分错误通道 |
| 业务迁移登记 | `agent_schema_migration` | 6 | 24 kB | 已应用的业务 SQL 迁移版本和时间 |
| 检查点迁移登记 | `checkpoint_migrations` | 10 | 24 kB | LangGraph 检查点结构版本 `0` 至 `9` |

## 3. 会话与历史数据

### 任务和会话

14 个任务分别关联 14 个对话，`closed_at` 均为空；17 个会话中 14 个未关闭、3 个已关闭。3 个会话具有前序会话、父会话及继承交接记录。未关闭表示逻辑记录未结束，不代表进程正在执行。

按 `conversation_id` 的已有命名前缀归类：

| 来源类别 | 任务数 |
| --- | ---: |
| 聊天故障修复验证：`repair-*` | 6 |
| 持久化 smoke 验证：`smoke:*` | 3 |
| token 预算验证：`token-budget-*` | 3 |
| 普通对话标识：`conversation-*` | 2 |

至少 12 个任务具有明确的验证前缀；其余 2 个仅能归类为普通对话标识，不能据此前缀判断是否为真实用户使用。

### 历史事件

记录时间范围：2026-09-22 20:07:43 至 2026-10-08 23:14:47，北京时间。

| 事件类型 | 数量 | 含义 |
| --- | ---: | --- |
| `model_usage` | 85 | 模型 token 预算/用量回执，包含 baseline、reserved、settled、budget 阶段 |
| `checkpoint` | 62 | 执行状态确认、检查点 ID、run/revision/fence 或交接原因 |
| `context_trace` | 37 | 上下文预算、候选/选中 token 数及裁剪信息 |
| `route_decision` | 15 | 意图路由、任务拆分及澄清判断 |
| `user_message` | 12 | 用户输入文本 |
| `assistant_message` | 11 | 助手回复文本 |
| `history_archive` | 9 | 历史归档的游标范围和事件引用 |
| `structured_output` | 9 | 结构化输出的路由、状态及内容 |
| `reality_revalidation` | 6 | 恢复执行时对历史状态与当前状态的再核对 |
| `tool_call` | 4 | 工具名、参数、操作 ID、状态和影响类型 |
| `tool_result` | 4 | 工具执行状态与结果引用 |
| `artifact_created` | 3 | 产物创建事件 |
| `retrieval` | 3 | 检索事件 |
| `handoff` | 3 | 会话交接事件 |

263 条历史事件中，235 条具有重放键 `event_key`，28 条旧记录没有该键。85 条 `model_usage` 是不同预算阶段的回执，不能直接解释为 85 次模型调用，也不能把各阶段 token 数相加作为实际消耗。

4 次工具调用分别为 `search_music` 3 次、`search_entities_tool` 1 次，均有对应数量的 `tool_result`。7 条产物中，`public_tool:v1` 工具产物 4 条、`retrieval_evidence` 检索证据 3 条。

10 条执行协调记录的 owner 均为空，也没有未过期租约；它们目前不表示 10 个运行中的执行。

## 4. 长期记忆具体存了什么

11 条记录均为 `active`，涉及 5 个测试用户。它们的 `user_id` 和 `source_thread_id` 全部具有 `smoke:` 前缀，创建/更新时间均在 2026-09-22 的验证窗口内；本次未查到其他来源的长期记忆。

| 记忆类型 | 实际内容 | 条数 | 证据类型 | 向量 |
| --- | --- | ---: | --- | --- |
| `profile` | 喜欢周杰伦 | 3 | `explicit`，明确偏好 | 无 |
| `profile` | 避免现场版 `live_version` | 3 | `explicit`，明确偏好 | 无 |
| `episodic` | 健身 `workout` 场景反复跳过慢歌；subject 为 `slow_songs` 或 `workout_slow_songs` | 5 | `repeated_behavior`，重复行为 | 全部为 1024 维 |

因此，目前有 6 条偏好记忆和 5 条情景记忆。`agent_user_profile_document` 是另一张按版本保存完整画像文档的表，它为空并不等同于没有偏好记忆。

## 5. 检查点与 WorldLine 数据

217 个检查点覆盖 33 个线程、15 个命名空间。检查点自身时间范围为 2026-09-22 20:07:43 至 2026-10-08 23:14:46，北京时间。

| 线程来源 | 检查点数 | 线程数 |
| --- | ---: | ---: |
| WorldLine：`worldline:*` | 117 | 19 |
| 聊天修复验证：`repair-*` | 37 | 6 |
| token 预算验证：`token-budget-*` | 28 | 3 |
| smoke 验证：`smoke:*` | 24 | 3 |
| 普通对话标识：`conversation-*` | 11 | 2 |

WorldLine 没有单独的关系图业务表。现有代码通过 `worldline:{session_id}` 线程保存探索会话，状态包括根实体、当前图、当前位置、已访问节点、可用/已消费分支、探索深度、证据历史、revision 和最近一次结果。它们进入 `candidate` / `snapshot` 通道，由检查点表组持久化。

另外，检查点中还保存 `messages`、`continuity_context`、`context_manifest`、`routing_context`、`route_decision`、`task_results` 等通道。这里仅核对通道名和大小，没有解码二进制 payload 或导出完整聊天内容。

三个检查点数据表的物理总大小为 28,196,864 bytes，约 26.9 MiB，占整个数据库约 74.5%。存储空间主要用于执行恢复与探索快照。

## 6. 关联关系与存储边界

`agent_task` 关联多个 `agent_session`；每个会话关联历史事件、产物和交接记录。会话还可关联前序/父会话及继承的交接记录。长期记忆通过 `user_id` / `source_thread_id` 标识归属和来源，当前没有独立用户表作为外键目标。检查点按线程和命名空间保存，与会话的对应主要由应用约定维护。

历史事件、产物、交接记录和画像文档有禁止更新/删除的触发器；它们不适合直接按普通可变表执行清理。检查点数量、历史事件数量、会话数量和消息数量是不同统计口径。

本库没有独立的用户账户、歌曲、歌单或音乐知识文档业务表。音乐实体与检索结果仍可出现在工具产物或 WorldLine 快照中。知识检索的索引使用 Qdrant，知识构建产物使用文件系统；网易云数据和登录状态由音乐 API / Spring 服务处理。这些外部存储没有在本次盘点中执行内容统计。

## 7. 迁移状态与其他 PG 环境

本地开发库已登记：`001_agent_continuity.sql`、`002_long_term_memory.sql`、`003_context_loop_history.sql`、`004_continuity_execution.sql`、`006_user_profile_document.sql`、`007_model_usage_run_index.sql`。登记集合与仓库当前 SQL 迁移文件集合一致，本次未执行迁移。

本地部署演练容器 `moodflow-rehearsal-20260928-postgres-1` 使用独立数据卷，统计时间为 2026-10-09 15:44:27。该库同名但数据与开发库不同：

| 表 | 演练库记录数 |
| --- | ---: |
| `agent_task` | 11 |
| `agent_session` | 14 |
| `agent_history_event` | 56 |
| `agent_artifact` | 3 |
| `agent_handoff` | 3 |
| `agent_long_term_memory` | 11 |
| `checkpoints` | 213 |
| `checkpoint_blobs` | 252 |
| `checkpoint_writes` | 367 |
| `agent_schema_migration` | 2 |
| `checkpoint_migrations` | 10 |

演练库大小约 36 MB，只登记业务迁移 001、002，结构落后于当前开发库。其表统计估计曾全部显示为 0，实际 `COUNT(*)` 如上；不能据估计值判断为空库。

`moodflow-journal-recovery-test-20261009` 是另一个运行中的临时验证容器，本次没有盘点或修改其中的测试数据。两个迁移时期保留的旧 PG 容器均停止，本次没有启动。

## 8. 验证与限制

- 运行 `docker ps -a` 和限定字段的 `docker inspect`，确认实例名称、状态、端口和开发卷；未输出完整容器环境变量或凭据。
- 使用现有 `ai-service/.venv/Scripts/python.exe` + psycopg 执行 `BEGIN ... REPEATABLE READ READ ONLY`、`COUNT(*)`、分类聚合、`pg_total_relation_size` 和迁移登记查询。校验事务确为只读和可重复读，历史/任务/记忆/检查点/产物的分组总数与表行数一致；全部通过。
- 演练库使用 `docker exec -i ... psql -X -A -t -v ON_ERROR_STOP=1` 执行独立的可重复读只读事务，查询后 `ROLLBACK`；通过。
- 脱敏统计证据保存在 Git 忽略目录 `.dev/postgres-inventory-20261009/` 的 `local-snapshot.json`、`rehearsal-snapshot.json`；仅含汇总、结构及已识别的测试偏好，不含登录凭据、完整对话、二进制状态或原始用户标识。
- 本次仅新增盘点文档，未修改应用代码；无需执行会写入测试数据的应用测试、构建或 UI 验证。文档交付执行 `git diff --check`。
- 本报告是统计时点的快照；开发库与演练库分别使用独立事务，没有跨实例一致性快照。
- **未连接 ECS 生产数据库**，本报告不代表线上数据库当前内容。未验证检查点实际续跑、业务响应或长期记忆抽取质量。

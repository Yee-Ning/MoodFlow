# Context Loop P1：003 / 004 生产迁移执行报告

记录日期：2026-10-02，Asia/Shanghai。初始记录时间：17:51:32 +08:00；后续进展见第 7–14 节。
报告对象：West；由本次请求方转交，本轮没有对外发送消息。

**最新结论：门控 0 / 1 / 2 已通过，生产超时选为演练验证过的 5s / 60s。用户尝试最后脚本后输出 STOP 并断开 SSH，具体失败原因和生产事务状态尚未确认；不能声明迁移未开始、已回滚或已提交。已修复会影响登录 Shell 的封装和隐藏停止原因的问题，并提供约 3 KB 的只读中断核对脚本。本次不重试迁移。备份已完整恢复验证，恢复负责人唐艺宁；真实写复验继续跳过。**

## 1. 批准与执行依据

- 请求方已确认 West 批准，目标库为 `moodflow_agent`，执行窗口为业务低峰。
- 严格采用 [PostgreSQL 生产迁移手册](../../operations/context-loop-p1-postgres-migration-20261002.md) §1–§4，门控按 0 → 1 → 2 → 3 顺序执行，前一门控未通过即停止。
- 手册中的 service 名称为 `moodflow-approved-migration`。本轮仅尝试解析该名称，没有构造连接凭据、读取凭据文件或改用应用连接配置。
- 检查时仓库 HEAD：`6f402a28a8dd65f555c6391e88f14ab41a84a5b7`。手册及迁移目录无本地 diff。已有其他未提交改动保留。

## 2. 每步结果与耗时

下表耗时来自命令工具返回的 wall time，包含本地命令开销；不是数据库耗时，也不能相加作为总执行时间。

| 步骤 | 结果 | 耗时 |
| --- | --- | --- |
| 仓库状态、分支检查 | 有既存的无关改动；当前分支 `codex/parse-normalize-corpus-v1` | 0.275 s |
| 定位 AGENTS、手册及 001–004 | 找到根目录规范、指定手册和四份 SQL | 0.00005 s（工具精度） |
| 本机 PostgreSQL 客户端与环境变量检查 | PATH 未找到 `psql`、`pg_dump`、`pg_restore`；未配置 service 相关环境变量 | 1.820 s |
| 阅读规范与手册 | 已核对 §1–§4 的门控、事务、复验和失败处理要求 | 0.00004 s（工具精度） |
| 阅读 001–004 SQL | 003 增加 replay key、序列和索引；004 增加 execution 表；仅阅读，未执行 | 0.00003 s（工具精度） |
| 初始运维路径检索 | 查到手册；部分预设文件路径不存在，检索 exit 1；随后用实际目录补查 | 0.544 s |
| 客户端安装目录、profile 路径及目录初查 | 常见 Windows PostgreSQL 安装目录未找到 `psql.exe`；随后以 JSON 明确输出 profile 存在性 | 0.858 s |
| 实际运维文件目录检索 | service 说明仅见指定手册；没有发现可用 profile 的非敏感位置说明 | 0.597 s |
| 本机 profile 与环境确认 | 三个候选文件均不存在；`PGSERVICE`、`PGSERVICEFILE`、`PGSYSCONFDIR` 均未设置 | 0.620 s |
| Docker / WSL 运行环境清单 | 有本地 `moodflow-rehearsal-20260928` 容器及 Docker Desktop WSL；该清单不能证明生产身份或规模 | 0.883 s |
| 补充 service 说明检索 | 未发现手册之外的 service profile 指引 | 0.721 s（同批含报告写作规范阅读） |
| 本地 PostgreSQL 容器客户端与 profile 检查 | `/usr/bin/psql` 可用；service 环境变量未设置；检查到的用户及系统默认 profile 文件均不存在 | 0.700 s |
| 使用手册 service 名称尝试只读连接 | exit 1：`definition of service "moodflow-approved-migration" not found`；在 service 解析阶段失败，SQL 未执行 | 0.774 s |
| 保存来源版本与 SHA-256 | 已记录手册、003、004 的哈希；报告目录此前不存在 | 0.750 s |

只读连接尝试命令及原始失败输出见 [复验与查询结果](verification.md)；结构化证据见 [preflight.json](preflight.json)。没有运行全量 migration CLI。

## 3. 初次停止时的门控结果

| 门控 / 操作 | 状态 | 证据与影响 |
| --- | --- | --- |
| 门控 0：只读前置检查 | **未通过，停止点** | 本机无可用客户端或 profile；现有本地容器客户端也无法解析手册 service。未连接生产，无法确认当前库、角色、ledger、001、002、checkpoint 表、历史与 execution 结构、索引、不可变触发器及权限、pool 条件 |
| 门控 1：有效备份 | 未进入 | 前一门控未通过。备份机制、最近一次有效备份方式与时间、可恢复性、恢复负责人均未确认；结论为**未知 / 无法确认有效备份**，不是“没有备份” |
| 门控 2：接近生产规模的副本演练 | 未进入 | 未执行 003 / 004，未测量锁、WAL、磁盘、复制延迟，未确定生产超时 |
| 门控 3：低峰与无活跃执行 | 未进入 | 无活跃 run、无 pending 执行、活跃会话及当前低峰均未查询或确认 |
| §2：迁移事务 | 未执行 | 未发送 BEGIN、超时设置、advisory lock、003 / 004、ledger INSERT 或 COMMIT；无迁移事务需要 ROLLBACK |
| §3：迁移后只读复验 | 未执行 | 未发生迁移，且缺少获批连接。全部 SQL 标记为未执行，无数据库输出 |
| §3：业务与真实写操作复验 | 未执行 / 跳过 | JSON / SSE、恢复、跨 namespace、ACK、费用、并发等未验收；真实写操作按请求跳过，原因是暂无专用测试账号 |
| §4：故障处理 | 已按停止原则处理 | service 解析失败后停止；没有放宽超时重试、禁用触发器、变更历史、删除数据或执行恢复 |

## 4. 演练数据

| 项目 | 本轮数据 |
| --- | --- |
| 副本身份、数据规模与生产接近程度 | 未确认 |
| 003 / 004 执行耗时 | 未执行，无数据 |
| 锁持有时间 | 未测量 |
| WAL 增量 | 未测量 |
| 磁盘使用与剩余空间 | 未测量 |
| 复制延迟 | 未测量 |
| `lock_timeout` / `statement_timeout` | 未确定，未在生产设置 |

手册 §5 的历史小型隔离测试及本机现有演练容器不构成本轮门控 2 的生产规模演练证据。手册的 5s / 60s 仅为起点，本轮没有据此试跑生产。

## 5. 初次停止时的未完成项与所需证据

1. 运维需在实际执行环境提供已有获批 service profile 的**非敏感名称 / 位置**及可用 `psql`，并确认应用角色；无需在报告或聊天中提交密码或完整凭据。本轮无法判断远端运维环境是否已有该 profile。
2. 先通过门控 0：执行手册 §1 五项 SQL，核对目标 `moodflow_agent`、001 ledger 与对象、官方 LangGraph checkpoint 表、002 与既有 Memory 配置的一致性、历史保护、角色权限及 pool 条件。001 / 002 当前均未知，不能当作已应用或无需应用。
3. 再核实有效备份：机制、最近一次成功完成时间、对应备份对象、完整性与恢复验证证据、恢复负责人；无法确认则继续停止。
4. 再完成接近生产规模的隔离演练，记录锁、WAL、磁盘、复制延迟，并依据结果确定 / 批准超时。
5. 执行前重新确认低峰、无活跃 run、无 pending 执行及会话状态；随后才可按 §2 事务执行并保存 §3 输出。
6. 真实写操作仍跳过，待提供专用测试账号及可变更测试歌单后独立验收。其他业务复验也没有本轮通过证据。

## 6. 本轮影响与验证限制

本轮只新增执行报告、查询结果说明和非敏感环境证据。未修改迁移 SQL、应用代码、配置或 `.env`，未部署、未切默认图、未碰灰度开关、未 push。迁移未完成，也没有开闸结论。

初次环境检查均为只读；service 解析失败是当时的门控失败证据，不能标为测试通过。后续生产只读 SQL 结果见第 9 节；备份检查、演练和业务复验仍未执行。代码、前端和后端没有改动，应用测试 / 构建 / 静态检查未运行；报告交付检查证据、SQL 与手册一致性、文件哈希、JSON 和 diff 格式，并对交付的只读命令作本地正常 / 失败路径及清理验证。

交付检查结果：

| 命令 | 结果 |
| --- | --- |
| `python -`（内联报告校验） | exit 0；JSON 停止状态、未知备份结论、3 个来源 SHA-256、5 条前置与 6 条复验 SQL、全部未执行标记、演练指标为 null、相对链接均通过 |
| `git diff --cached --check` | 通过；无空白错误 |
| `git status --short` / `git diff --cached` | 已审阅；暂存范围仅为本目录三份报告文件，既有用户改动未暂存 |

## 7. 用户回传的 ECS 只读检查补充

记录时间：2026-10-02 18:13:01 +08:00。来源：用户在 ECS 远程终端执行本聊天提供的只读命令后回传的输出；本代理未直接登录远端，命令执行耗时未提供。

- 当前服务器用户：`root`。
- `moodflow-production` 的 frontend、java、ai-service、netease、qdrant、postgres 六个容器均显示 `Up 11 hours (healthy)`。这只证明回传时容器健康，不能代替迁移前置、备份或业务验收。
- 宿主机 PostgreSQL 客户端：`/usr/bin/psql`。
- 宿主机使用手册 service 名称连接，仍返回 `psql: error: definition of service "moodflow-approved-migration" not found`。失败发生在 service 查找阶段，没有数据库 SQL 输出。
- 用户回传的文件查找中，宿主机 `/root`、`/etc`、`/opt/moodflow`、`/secure/moodflow` 和容器 `/root`、`/etc`、`/var/lib/postgresql`（最大深度 4，名称 `*pg_service*.conf`）均未输出候选文件路径；两处 `PGSERVICEFILE` 检查也未输出 override 路径。未读取配置内容。该结果不能证明其他目录或其他账号下完全没有 profile。

停止原因已进一步定位为：**已有生产终端和客户端，但无法确认可用的获批 service profile**。门控 0 仍未通过；备份仍未知；003 / 004、后续门控和复验均未执行。

若无法提供已有 profile，拟议替代方案是经请求方同意调整“使用已有 profile”的限制后，在 PostgreSQL 容器内创建一次性、仅含本地 socket 地址、获批库名及现有用户名的临时 service 文件，使用完立即清理；不保存密码，不改认证规则或 `.env.production`。连接仍使用 service 名称，先进行只读门控 0。具体 socket、角色和认证可用性尚未验证，不能保证该方案一定能连接。该方案**尚未获准、未执行**；备份、演练和低峰门控仍须随后按顺序通过。

临时文件可由 `PGSERVICEFILE` 指定的机制见 [PostgreSQL 16 service 文件文档](https://www.postgresql.org/docs/16/libpq-pgservice.html)。

## 8. 临时配置获准与只读命令验证

记录时间：2026-10-02 18:27:07 +08:00。请求方已明确回复“允许”，同意创建无密码临时连接配置；其余原约束继续有效。第 7 节的“未获准”是此前状态，本节记录更新后的授权状态。

已准备 [门控 0 只读命令](gate0-readonly-command.md)，在容器中建立一次性 service 文件及空的 pgpass 文件，并在退出时清理；禁用密码环境参数和交互密码提示，数据库会话默认只读且查询位于 READ ONLY 事务。命令仅检查六项非敏感应用配置、手册五项 SQL 和相关 catalog / 权限信息，未包含迁移调用。

最终命令的本地验证使用现有演练容器，只做只读查询；生产库未连接，003 / 004 未执行。`sh -n` 通过，Python 驱动的验证 exit 0；正常路径 exit 0（0.297 s），错误目标库 exit 1（0.250 s），无效用户名 exit 1（0.234 s），连接失败 exit 2（0.250 s）；四项均核对临时目录无新增残留，非敏感配置输出范围也通过。最初本地验证使用 `/dev/null` 作为 pgpass 路径，客户端给出“不是普通文件”警告；最终命令已改为空的受限临时普通文件，并重新验证无警告。

这些验证不是生产前置结果，也不是门控 2 的迁移演练。下一步等待请求方执行最终命令并回传 ECS 输出，再审阅门控 0 是否满足。备份仍未知；后续门控、迁移和生产复验仍未执行。

## 9. 生产只读查询截图审阅

用户提供四张 ECS 终端截图。数据库查询时间为 `2026-10-02 10:32:02.162238+00`，对应北京时间 `2026-10-02 18:32:02.162238 +08:00`。来源为用户运行命令后的截图，本代理审阅图片，未直接登录服务器；截图文件名与 SHA-256 已写入结构化证据。完整 shell 耗时和退出码未显示。

已观察到目标库 `moodflow_agent`、角色 `moodflow`、PostgreSQL 16.15、`read_only=on`；ledger 恰有 001 / 002 两条记录。历史表八列、三项原索引、不可变触发器均符合 001 的迁移前状态。触发器为 `O`，定义是行级 BEFORE DELETE OR UPDATE，调用函数无条件抛出 append-only / `55000` 异常；`O` 在 origin / local 模式触发，含义见 [PostgreSQL 16 pg_trigger 文档](https://www.postgresql.org/docs/16/catalog-pg-trigger.html)。这次没有执行 UPDATE / DELETE 来测试阻断。

截图显示四张 checkpoint 表存在，vector 0.8.6，当前数据库角色的历史 SELECT / INSERT 权限均为 true。最后出现 ROLLBACK、查询结束标记和 shell 提示符，未见报错。配置清理未独立列目录复验，只观察到脚本返回且无清理报错。

手册五项 SQL 的截图计时依次为 0.344 ms、0.577 ms、15.164 ms、3.741 ms、1.073 ms；补充 catalog、函数定义及权限查询结果见 [复验记录](verification.md)。这些是查询计时，不能用于估计 003 / 004 执行时间。

门控 0 尚不判定通过：截图上方未显示应用 `POSTGRES_DB`、`POSTGRES_USER`、`MEMORY_BACKEND`、`PERSISTENCE_BACKEND`；仅看到 `CHECKPOINT_BACKEND=postgres`、`PERSISTENCE_POOL_MAX_SIZE=<unset>`。不能用本地代码默认值替代部署代码。表清单中的 `agent_handoff`、`agent_long_term_memory` 两行位于截图缺口，不能根据总行数 10 就认定两张表均存在。`agent_execution` 未返回列记录，但仍需 `to_regclass` 复核其存在性。

已准备 [门控 0 补充命令](gate0-supplement-command.md)，只读获取缺失配置、实际部署源码中的 pool 默认值、两张被截断的表及历史表 owner / schema CREATE 权限。本地正常配置、容量小于 2、非整数配置三条路径，以及补充 catalog 查询和临时清理验证均通过。生产补充结果仍待回传；门控 1–3、迁移及 §3 复验均未执行。

## 10. 补充截图通过与备份检查准备

用户回传第五张截图 `codex-clipboard-7f76c57f-9d29-49e0-b3a2-5e05f2e23f6e.png`，SHA-256 为 `76A9A553A0AF0B689D76A2E6B3FEED373BE44F1190A56CF880BC13C2E07683BC`。截图未显示执行时间、查询耗时或 shell 退出码；审阅记录时间为 2026-10-02 18:58:49 +08:00，不能替代执行时间。

应用目标库 / 角色为 `moodflow_agent` / `moodflow`；Memory、persistence、checkpoint backend 均为 postgres。pool 环境变量未设置，部署源码默认值及最终配置容量均为 10，满足至少 2 的配置前提；没有独立检查运行中连接池对象。`agent_handoff` / `agent_long_term_memory` 存在；`agent_execution` 的 to_regclass 结果为 NULL，是 004 尚未执行的预期状态。历史表 owner=moodflow，当前角色拥有该表且具有 public schema CREATE 权限。截图显示 ROLLBACK 后返回 shell，未见报错。

结合第 9 节，门控 0 的数据库与配置前置检查通过。新增对象的序列 USAGE 与 execution 权限需迁移后核对；生产临时文件删除仍未独立检查。备份和恢复负责人、演练、执行窗口的即时负载及业务验证均不能由这张截图推定通过。

进入门控 1，仅查阅仓库既有备份机制并准备 [备份只读检查命令](gate1-backup-readonly-command.md)。仓库方案使用 custom-format pg_dump，完整备份清单记录隔离恢复结果、完成时间和 SHA-256。Sep29 历史备份信息不能证明本日最新备份有效；当前结论仍为未知 / 无法确认有效备份。没有运行备份创建、恢复、清理或迁移。

命令本地验证：`sh -n` 通过；Python 正常、无清单、仅失败清单、目标库不符、SHA-256 不符、dump 缺失、恢复证据不全、无时区时间、损坏 JSON、最新备份损坏共 10 个临时夹具场景通过（正常 exit 0，失败均 exit 1；不泄露模拟敏感 failure 字段）。本地验证耗时 1.396 s。未连接生产，也不构成备份可恢复性或迁移演练证据。等待用户回传命令输出再作备份结论；备份门槛未通过，003 / 004 继续停止。

本次报告校验 exit 0：门控状态、来源及截图哈希、Markdown 链接均通过。全仓 `git diff --check` 发现用户既有无关文件 `ai-service/evals/reports/gold_review_v0_blind_pack.md:26` 的尾随空白；未修改或暂存该文件。本次仅为执行文档更新，未运行应用测试、构建或静态检查；生产备份、演练、迁移及复验仍受环境回传限制。

## 11. 备份截图审阅与演练只读准备

用户回传备份只读检查截图 `codex-clipboard-67fccebd-4705-4459-a992-bfdbc81f55db.png`，SHA-256 为 `3CB2CBDD7176EE97865E172BB104EB1C4EA42EEC49885963FBED7103B6979F7F`。检查时间为 `2026-10-02T11:08:21.880066+00:00`，对应北京时间 19:08:21。备份机制是仓库已有的 custom-format pg_dump；清单中恢复验证来自备份时隔离恢复，本轮只重新检查文件，没有新运行恢复。

| 项目 | 截图证据 |
| --- | --- |
| 定时器 | loaded / active / enabled；最近触发 2026-10-02 07:41:02 CST；下一次 2026-10-03 03:22:14 CST |
| 最近备份服务 | Result=success，ExecMainStatus=0；07:41:02 开始，07:41:24 退出；oneshot 服务结束后 inactive |
| 最近三个清单 | 20260930T015036Z、20260930T233948Z、20261001T234103Z，后缀均为 3a11237c；均 complete / restore_verified=true |
| 选中最新备份 | 20261001T234103Z-3a11237c.json；目标库 moodflow_agent |
| 起止时间 | 2026-10-01 23:41:03.627511 UTC 至 23:41:24.275303 UTC；即北京时间 2026-10-02 07:41:03 至 07:41:24；清单起止差 20.648 s |
| 备份年龄 | 检查时 11.46 小时；用户未指定最大备份年龄，未自行引入阈值 |
| PostgreSQL dump | 23,814,211 bytes；custom 文件头有效、文件大小符合清单、SHA-256 匹配；截图只显示匹配结论，没有显示实际摘要值 |
| 清单中的恢复验证 | restore_verified=true；agent_session=58，agent_task=55，agent_long_term_memory=11，checkpoints=775，checkpoint_blobs=1028，checkpoint_writes=1901 |
| 备份路径所在磁盘剩余 | 21,777,116,544 bytes，约 20.28 GiB；不能替代 Docker 数据目录空间或内存检查 |
| 结束状态 | 出现 BACKUP_READONLY_INSPECTION_FINISHED、no_new_restore_run、migration_not_executed；未见 STOP。末尾为 shell 的 > 续行提示，未显示正常提示符或退出码；已提醒 Ctrl+C 退出待续输入 |

门控 1 的有效 PostgreSQL 备份检查通过；恢复负责人由用户在信息补充中明确为唐艺宁。证据为用户截图及此前只读命令的校验语义，本代理未直接登录服务器；当前备份可恢复性依据匹配文件和已记录的隔离恢复证据，不宣称本轮再次恢复通过。

已准备 [演练前只读检查脚本](gate2-rehearsal-preflight.sh)，获取磁盘、内存、CPU、PostgreSQL 镜像 / 资源使用、备份源库体积、服务器上 003 / 004 哈希，以及生产数据库 / 历史体积和行数、复制状态、ledger。脚本不创建隔离容器，不恢复或执行迁移；只读查询超时仍为 3s / 10s，不能当作生产迁移超时。待确认规模接近性与资源后再准备实际演练，锁、WAL、磁盘、复制延迟及迁移耗时仍无数据。

本地验证 `sh -n` 通过；同一数据库查询片段在现有本地容器正常、目标不符、连接失败三条路径分别 exit 0 / 1 / 2，临时文件清理均通过（2.535 s）。没有执行迁移；未运行应用测试或构建，本轮只修改执行文档。

## 12. 生产资源回传与隔离演练脚本验证

用户以文本回传演练前只读检查输出。数据库时间为 `2026-10-02 11:18:21.81137+00`，对应北京时间 19:18:21。来源为用户远端执行结果；本代理未直接连接生产，执行总耗时与 shell 退出码未提供，ROLLBACK 及结束标记可见，未见报错。

| 项目 | 回传结果 |
| --- | --- |
| 磁盘 | 备份及 Docker 目录均在 /dev/vda3；总 41,882,943,488，已用 18,176,118,784，可用 21,771,087,872 bytes；使用率 46% |
| 内存 | 总 7,888,601,088，available 6,176,071,680 bytes；无 swap |
| CPU / PostgreSQL | 2 CPU；当前容器 CPU=0.68%，内存 107.2 MiB / 7.347 GiB |
| 镜像 | pgvector/pgvector:pg16-trixie@sha256:c8483555ce48101872f888c1df8a895ff689d6c7c7a5f7ac266475f9dfe89e0b |
| 数据库身份 / 体积 | moodflow_agent / moodflow；read_only=on，is_standby=f；53,894,167 bytes，约 51.4 MiB，与备份记录中的源库体积相同 |
| 历史规模 | 328 行；表及索引总计 696,320 bytes |
| 复制观测 | sender=0，复制状态查询 0 rows，slot=0 / active_slot=0；没有可测延迟，不能虚构为 0 秒，也不能据此排除断开的外部副本 |
| ledger | 001_agent_continuity.sql、002_long_term_memory.sql；无 003 / 004 |
| 服务器迁移文件 | 两份 SHA256 均显示 missing；未自动改动 /opt/moodflow 的源码或部署配置 |
| 查询计时 | 身份 / 体积 1.702 ms，历史 9.070 ms，sender 1.931 ms，复制状态 0.733 ms，slot 0.550 ms，ledger 0.483 ms，ROLLBACK 0.086 ms |

资源及源库体积足以继续准备隔离演练；恢复后的真实数据规模还需核对。已准备 [独立容器演练脚本](gate2-isolated-rehearsal.sh) 及 [执行说明](gate2-isolated-rehearsal.md)，原始字节的 003 / 004 和 SHA-256 随脚本传入，仅在新建无网络、CPU 0.5 / 内存 1 GiB 的容器及受限报告目录存储。它不会向生产发送 SQL、挂载生产数据卷、开放端口、修改 .env 或部署源码。完整恢复后核对历史 328 行、核心表计数、vector 与旧 ledger，再执行事务及副本只读复验，最后清理本轮资源并保留报告。

锁指标是从 include 003 前到 COMMIT 返回后的保守持有上限，并确认提交前有历史表 AccessExclusiveLock；WAL 是隔离实例 insert LSN 的字节差值；磁盘同时记录库体积、data / pg_wal apparent bytes 与主机空闲空间。副本没有复制连接，所以复制延迟为 NULL / 当前无可测连接；不能把本地测试时间或无复制的结果作为生产负载验证。5s / 60s 只是本次演练起点，生产超时尚未选定。

本地验证使用同一镜像的新建隔离夹具库（假数据），没有生产备份或数据：首次测试因测试 ROOT 路径多退一级而失败，已修正并重跑；之后加强了已存在 event_seq 的恢复状态门槛，最终完整 suite 9 tests / 40.777 s 通过。正常路径验证实际 pg_restore → 003 / 004 → ledger / 六项只读复验 / 锁与 WAL 指标 → 清理；中途错误验证显式 ROLLBACK，新增 schema 与 ledger 均回退，源夹具库未改变。固定 SQL 哈希、损坏备份、错误目标、规模 / schema 不符、资源标签不符及精确清理目标等门槛也通过。追加的低磁盘 / 低内存测试 1 test / 0.026 s 通过，确认拒绝时不创建 Docker 资源。本地带本轮标签的容器和 volume 列表均为空。

检查命令：`python -m unittest discover -s docs/reports/context-loop-p1-production-migration-20261002 -p test_rehearsal_runner.py -v`；资源边界补充为同命令加 `-k low_resources`；Python 编译、完整脚本与 runner 内容一致性、`sh -n`、来源哈希与报告一致性、范围内 `git diff --check` 均已通过。应用代码无变化，不运行应用构建或前后端验收；生产规模演练尚待用户在 ECS 执行，全部演练指标仍未填入生产结果，门控 3、生产迁移和 §3 复验继续等待。真实写复验按约定跳过。

## 13. 真实备份副本演练通过，生产最后脚本就绪

用户回传实际 ECS 演练输出截图 `codex-clipboard-db04a722-c345-4a54-8048-df09ba5af7ff.png`，SHA-256 为 `F73C6822273EC3F6EFC68E3CB89A8BE73ADE1BC6227E88A5543CC42AA1B16B8E`。报告保存在 `/secure/moodflow/migration-rehearsals/context-loop-20261002-yopxiydv/report.json`。开始与完成时间分别为 2026-10-02 11:47:39.029188 UTC、11:47:52.762324 UTC（北京时间 19:47:39–19:47:52），总计 13.733136 s。截图显示 status=passed、cleanup=passed 和正常 root 提示符，production_modified / production_migrations_executed 均为 false。本代理依据回传结果审阅，没有直接登录生产。

| 项目 | 实际演练结果 |
| --- | --- |
| 恢复源 | 已确认的 `20261001T234103Z-3a11237c.json` 对应 custom dump，恢复耗时 8.09383 s |
| 环境 | 与生产相同的固定 digest 镜像；独立新卷、无网络 / 无端口；0.5 CPU / 1 GiB |
| 规模 | 恢复库 54,025,239 bytes，约比生产 53,894,167 bytes 大 0.24%；历史同为 328 行 |
| 核心表计数 | session=58、task=55、memory=11、checkpoints=775、blobs=1028、writes=1901，与备份一致，迁移后保持 |
| 执行 | 003 / 004 文件哈希匹配；事务 87.304 ms；迁移客户端 wall time 0.264991 s |
| 锁 | include 003 前至 COMMIT 返回后的保守持有上限 85.249 ms；不声称测得精确授予 / 释放时刻 |
| WAL | insert LSN 增量 69,336 bytes，约 67.71 KiB；可包含区间内后台 WAL |
| 数据库体积 | 54,025,239 → 54,115,351 bytes，增加 90,112 bytes |
| data / pg_wal apparent bytes | data 144,395,194 → 144,485,306；pg_wal 67,108,864 → 67,108,864（预分配目录体积未变化） |
| 主机空闲磁盘 | 21,626,261,504 → 21,626,163,200 bytes，减少 98,304 bytes |
| 副本复验 | missing_sequence / duplicate_key_groups / duplicate_sequence_groups 均为 0；不可变触发器启用、序列 USAGE、execution SELECT / INSERT / UPDATE 均为 true |
| schema / ledger | event_seq 和 execution 出现；ledger 为 001 / 002 / 003 / 004；历史 328 行保持 |
| 复制延迟 | NULL / 不可测；生产准备时 sender / slot 均为 0，隔离副本无复制连接。不能据此排除断开的外部副本 |
| 清理 | 本轮隔离容器及卷清理通过；受限目录中的报告保留 |

门控 2 通过，生产 timeout 选择 5s / 60s：真实恢复数据规模接近生产，在受限资源下事务不足 0.1 s，当前值留有余量；仍不将演练时长当作生产 ETA。生产不放宽超时试跑，发生 SQL 错误即回滚和核对 ledger，不自动重试。

已交付 [生产最后脚本](gate3-production-migration.sh)，操作说明见 [执行说明](gate3-production-migration.md)。脚本先重新校验备份与演练证据，再在约 10 s 内做 6 次只读观测：当前数据库、旧 ledger / schema、非本连接的活跃或未结束事务、advisory locks、最新 run_progress 状态、最新 checkpoint 的 pending writes / 控制通道与旧格式 pending sends，以及 AI 服务 8001 的已建立入站连接。只输出计数，不读取消息或凭据，不反序列化 checkpoint 中的类。未知状态、忙碌、规模改变或复制拓扑改变均停止。历史 checkpoint_writes 不是直接阻断依据，已消费的旧记录不会当作 pending。

通过后只尝试一次 BEGIN READ WRITE → 5s / 60s → 手册 advisory lock → 事务内再次检查数据库执行状态 → include 003 / 004 → ledger → COMMIT。随后逐项输出带编号的手册 §3 六项查询及附加权限检查，写入受限报告目录，清理仅本次创建的临时 profile / SQL。重复执行若已存在新 schema / ledger，会停止。观测是采样和事务内复查，没有冻结 HTTP 入口，不能证明未来不会出现新请求；当前没有提供生产门控 3 或事务的实际结果。

本地隔离验证命令 `python -m unittest discover -s docs/reports/context-loop-p1-production-migration-20261002 -p test_production_runner.py -v`：最终 5 tests / 22.467 s 通过，覆盖门控忙碌 / 未知 / 错误身份、最新与历史 writes 区分、控制通道、run_progress、实际 SQL 中途错误回滚、完整 wrapper 成功提交 / 复验 / profile 清理及重复尝试停止。独立 payload / shell 语法测试使用同命令加 `-k standalone`，1 test / 0.616 s 通过。源 SQL 与手册未修改；没有运行应用构建、业务账号写入验收、部署或 push。

当前未完成项：门控 3 的生产结果、生产 COMMIT 与 §3 原始输出仍待远端回传；业务 JSON / SSE、同 run 恢复、namespace 迁移、真实歌单写入、播放器 ACK、费用和期限、并发容量未验收。真实写复验依约跳过（专用测试账号暂无）。备份状态已从未知更新为有效，并经过本轮再次完整隔离恢复验证。迁移完成后仍需 West 独立决定部署 / 开闸，本次不操作开关、默认图或 `.env`。

## 14. 最后命令 STOP 与 SSH 中断后的处理

用户回传截图 `codex-clipboard-954b3b05-ea62-4173-a5a2-b652453874b8.png`，SHA-256 为 `97C3AC3E699156E5EE20BA44170C21E79EA8AF26BB3CECE79201A4C53879AF9F`。截图可见 Workbench 的“远程连接关闭”弹窗以及 `STOP: production_command_failed_check_report_and_ledger_no_retry`，没有可见的阶段、具体异常、ledger、提交标记或执行报告。本代理没有远端执行渠道，无法据此确定 SQL 是否开始或提交；记录为未知而非“没有迁移”。原来的备份与副本演练证据仍保留，但后续门控需要即时核对。

检查脚本发现两项可确认的问题：复制到登录 Shell 的脚本顶层 `set -eu` 会改变该 Shell 的行为；命令失败时 `set -e` 可使登录 Shell 直接退出。最外层 catch 又把所有停止原因替换成同一个泛化消息。隔离 `sh` 重现了旧方式失败后父 Shell 后续命令不运行，新子 Shell / if 封装失败后父 Shell 可继续运行。该发现可解释连接中断的机制，不代表已经确定最初 Python 前置检查的具体失败条件，也没有证据说明数据库或服务器故障。

修复范围仅限运维 wrapper 和诊断：将生产脚本的 `set -eu` 放入受控子 Shell，以 if 分支接住退出码；添加备份、演练证据和镜像检查的阶段输出，安全显示受控停止码，对其他异常仅显示类型。迁移 SQL、固定来源哈希、门控、5s / 60s、回滚和不重试规则不变。生产脚本暂不执行，后续通过文件上传与哈希校验运行，不再直接粘贴长文本。

新增 [中断后的只读核对](gate3-interrupted-readonly.sh)，3,221 bytes。重新登录普通 SSH Shell 后执行；不使用 VNC、不重启服务、不执行迁移或回滚指令。它读取 PostgreSQL 容器状态，以临时无密码 profile 的只读事务查询四份 ledger、execution 表、event_key / event_seq、其他活跃 psql 和 advisory locks，随后读取已有报告的有限状态字段。临时 profile 精确清理，报告不存在也明确输出 missing_or_unreadable，不输出应用消息、连接凭据或原始异常日志。ledger 和对象未变仍需结合未结束会话、报告判断，不单凭客户端断开假定事务结果。

本地验证：`python -m unittest discover -s docs/reports/context-loop-p1-production-migration-20261002 -p test_production_runner.py -v` 为 8 tests / 24.743 s 通过，包含旧 Shell 退出重现、新封装保持父 Shell、停止原因安全显示及原有正常 / pending / 回滚 / 重复尝试逻辑；新增只读检查测试使用同命令加 `-k interruption_check`，1 test / 10.562 s 通过，在真实隔离 PostgreSQL 上确认只读输出、ledger / schema / 历史计数不变且临时文件已删除。没有重跑生产迁移，没有推送、部署或业务真实写验收。具体停止原因、生产事务状态、低峰与生产 §3 输出仍待用户重新登录后回传。

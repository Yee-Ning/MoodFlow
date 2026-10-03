# Phase 1 A：写路径未知结果对账与补账

日期：2026-10-03。结论：实现与离线回归通过；V7 形态的 502 后实际生效案例已受控复现并补账，本轮未做真实账号写操作复验。

## 1. 基线与提交

- 开始时主仓库 HEAD：`87b2831dba8b93e5d93ad61e2bb76679ed488ff3`，已包含 B 修复与 handoff 入口规则层。
- 实现 commit：`588fa6f776776e2d4662cdaf09e38e872745623a`（`fix: reconcile uncertain music writes without replay`），只含本任务的 Python 实现、测试、Java 回归测试及子模块引用。
- 网易云适配器是独立 Git 子模块；起始 HEAD：`321c25bd7d041711f1a9ab9e4b55997ce661313c`；修复 commit：`ba9236e5cdf5eaeaffb330850d268e5101c31f69`，只含 `module/playlist_tracks.js` 与新增契约测试。
- 本报告单独提交，记录上述已生成的真实实现 hash；报告提交可用 `git log -1 -- docs/reports/write-path-unknown-20261003/report.md` 查询。
- 未改生产 `.env`、默认图、灰度开关；未部署、未 push。既有未提交报告改动、删除与未跟踪数据保留。

## 2. 三态与后续动作

沿用 append-only History 的 `TOOL_CALL` / `TOOL_RESULT`，按稳定 operation ID 查询当前状态；不覆盖原始错误或旧事件。

| 查询态 | 持久化依据 | 后续动作 |
| --- | --- | --- |
| `failed`：确定失败 | Spring 前置请求/鉴权拒绝 `http_400/401/403/422`，或工具明确返回 `forbidden`；记 `failed` receipt | 返回明确错误；同一操作不读回、不再次写入 |
| `unknown`：效果未知 | `proposed` 无完成回执、502、超时、连接中断、无效响应或其他不能证明未执行的错误；记 `unknown` receipt | 只读核对；未观察到目标效果、读取失败或数据不完整时仍保持未知 |
| `confirmed`：已确认 | 原始有效成功回执，或只读核对证明目标效果后补记唯一 `success` receipt | 返回原始/核对结果；重复请求复用账本结果 |

`confirmed` 同时记录 `confirmation_source`：正常响应为 `response`，未知结果读回补账为 `readback`；不会把响应成功描述成“已读回”。原始 operation ID、参数、提案、错误、核对证据均可追溯。

实现位置（仓库相对路径，行号以实现 commit 为准）：

- `ai-service/app/agent/foundation/long_horizon/operations.py:104`：`state()` 查询三态；`:158`：`reconcile()` 只读核对与补账；`:239`：写入门控与原始错误留存。
- `ai-service/app/agent/foundation/long_horizon/public_events.py:38`：公共工具事件新增可选 `baseline_ref`、`reconciliation_ref`、`confirmation_source`、错误及账号作用域字段；旧事件可继续读取。
- `ai-service/app/agent/foundation/long_horizon/write_reconciliation.py:46`：确定失败分类；`:60`：只读核对器。
- `ai-service/app/schemas/music.py:294`、`ai-service/app/tools/music.py:546`：工具错误 DTO 可携带 `operation_id` / `write_state`；未知错误明确提示只读核对、禁止重复写入；旧调用不额外输出空字段。

### 稳定 ID、补账与证据链

1. 同一 run、写工具、规范化参数生成稳定 operation ID，不随模型产生的新 tool call ID 改变；隐含默认参数也规范化。
2. 兼容旧 operation ID；跨 run 遇到参数相同且仍未知的旧写操作时采用旧提案，不借新 run 再写一次。
3. 核对只用当前可信会话凭据；不把 Cookie 写入 History。账号作用域改变、跨任务查询、损坏证据均停止确认。
4. 核对证据 artifact 保存 operation ID、提案引用、时间、GET 路径、目标 ID、观察到的状态、完整性判断及读回内容摘要。
5. 先持久化核对证据与关联事件，再使用 `operation:<id>:success` 幂等键补记成功，成功回执引用结果与证据；原始错误仍在旧事件中。
6. 若证据已提交但 success 未写入，恢复实例可直接核验证据并补账，不重做外部写入，也不需要重复读取已确认的证据。

### 核对范围与保守边界

| 写操作 | 只读核对 | 可以确认的条件 |
| --- | --- | --- |
| 加歌 | `GET /api/user/playlists` → `GET /api/playlists/{id}` | 目标歌单属于当前账号且可编辑；详情 ID 一致；目标歌曲存在 |
| 移除歌 | 同上 | 目标歌曲不存在，且歌曲唯一 ID 数、返回歌曲数与 `track_count` 一致，读回完整 |
| 红心/取消红心 | `GET /api/user/liked-songs` | 当前账号云端红心列表中的目标状态与请求一致 |
| 创建歌单 | 写前保存自有歌单 ID 基线；写后读自有歌单列表 | 当前 DTO 不返回隐私字段；即使发现唯一同名新增 ID，也保持未知，不能证明完整创建参数 |

核对证明的是请求的目标状态当前成立，不能证明外部供应商只执行过一次，也不能排除其他客户端同时修改。负向读回不是确定失败的证明；本轮不引入自动重发策略。

## 3. GeneralChat 查询

- Foundation：`ai-service/app/agent/foundation/subgraphs/general_chat/assistant.py:74`；handoff：`ai-service/app/agent/handoff/agents.py:196`，只接入 GeneralChat 的账本查询，未改转交边或 Guard。
- `write_reconciliation.py:124` 识别“刚才那首歌加上了吗”等写状态问句，也支持显式 `op:<64位hex>`；`operations.py:209` 据账本回答。
- 无显式 ID 时查询本任务中最近一条对应类型的写操作，回答携带实际目标歌单/歌曲 ID 与 operation ID；不是按自然语言曲名猜测目标。
- 确定失败回答“后端已明确拒绝”；未知回答“效果仍未知，尚未读回确认，不会重复写入”；读回确认回答“只读核对已确认，并已补记成功回执”。
- 无账本、无对应记录、作用域不符或证据不能核验时明确无法确认，不调用模型编造效果。
- `coordinator.py:200` / `runner.py:49` 接入当前请求的只读客户端；原写异常、后续请求恢复以及状态查询可触发核对。本轮没有新增后台轮询服务。

## 4. 加歌 502 根因与修复

来源：`docs/reports/context-loop-live-20261003/validation-7.md` §3（加歌表格及原始错误）。

原请求 `POST /api/playlists/18445771229/songs/2652820720` 返回：

```json
{"code":502,"message":"网易云 API 响应缺少有效字段: code","data":null}
```

后续读回实际存在目标歌曲，但原 Journal 没有 success receipt。

本仓库可复现的根因是 Node 网易云适配器的响应包装错误：

1. `netease-music-api/util/request.js:217` 创建 `{status, body, cookie}` 信封，`:425` 起解析供应商响应到 `answer.body`，`:455` resolve 整个信封。
2. 旧 `module/playlist_tracks.js:23` 把整个 `res` 展开到 HTTP body，形成 `{status:200, body:{code:200}, ...}`，顶层没有 `code`。
3. `netease-music-api/server.js:385` 直接发送 `moduleResponse.body`；Java `NeteaseMusicService.java:770` 要求顶层数值 `code`，缺失时拒绝；`GlobalExceptionHandler.java:51` 转为 HTTP 502。
4. 修复为展开 `res.body`，使成功 wire body 顶层恢复 `code:200`；Java 严格解析保持不变，不靠容忍任意嵌套响应掩盖契约问题。

结论：已定位并修复本地适配器契约缺陷，Java 对该错误结构的拒绝符合现有契约；不能将此错误直接归咎于网易云官方接口。V7 未归档当时的原始上游 HTTP body，本轮未重做真实加歌，因此不能追溯证明当时只有这一种上游异常。

既有适配器对供应商业务码 `512` 的特殊重发分支未修改；本轮“无第二次写请求”覆盖 V7 的 502/结果未知分支，不能据此宣称已审计所有供应商重发逻辑。

## 5. 验收证据与测试结果

新增 `ai-service/tests/test_write_reconciliation.py` 的 25 个参数化测试用例：

| 验收点 | 结果 / 测试位置 |
| --- | --- |
| V7 502 后实际已加歌 | `:62`，真实 MusicServiceClient + 有状态 httpx MockTransport +真实 SQLite HistoryWriter；记录 POST 生效后返回原始 502，再 GET 核对 |
| 禁止盲重试、只补一条 success | 同用例重复 tool call / reconcile；POST 总数断言为 1，success 总数为 1；原始 502 错误保留 |
| 三态及未知→读回确认 | `:130` 四类确定拒绝；`:148` 负向读回保持未知，后续只读发现效果再补账 |
| ToolNode 错误 DTO 与原始错误 | `:102` / `:120`，未知显式携带状态与 ID，保留原始 502 code/message |
| 移除、红心核对边界 | `:165` / `:178`，不完整歌单不确认移除；红心/取消红心都仅 GET |
| 不可编辑目标、读回失败 | `:191`，保持未知；不可编辑歌单不再读取详情 |
| 旧 proposed、新 run、默认参数 | `:207` / `:222` / `:235`，兼容旧记录且不增加写请求 |
| 创建隐私不可观测 | `:244`，有新增同名 ID 仍未知，不伪造 success |
| 证据损坏与账号隔离 | `:261` / `:305`，完整性或作用域异常停止核对/补账 |
| 核对证据提交后缺 success | `:281`，在 success append 前注入异常，重新打开 SQLite store/Journal 后补账；不重写、不重新读外部效果 |
| GeneralChat 三态回答 | `:320`，Foundation helper 与实际编译 handoff GeneralChat 图分别覆盖三态；不调用模型，不增加写请求 |

命令与最终结果：

| 范围 | 命令（在对应子目录执行） | 结果 |
| --- | --- | --- |
| AI 全量业务测试 | `ai-service`: `.venv/Scripts/python.exe ../.dev/context-loop-p0-20261002/validate.py -m pytest tests -q --junitxml=../.dev/write-path-unknown-20261003/business-tests.xml` | **1904 passed、38 skipped、286 subtests passed**，0 failures，380.17s；164 条 LangGraph 无 checkpointer 时 durability 无效的 warning |
| Python 静态检查 | `.venv/Scripts/python.exe -m ruff check`，显式列本任务 10 个 Python 文件 | **All checks passed** |
| Java 全量测试与构建 | `music-backend`: 进程内 `JAVA_HOME=D:/code/JDKall/openjdk-21.0.1`，`D:/apache-maven-3.9.4/bin/mvn.cmd --batch-mode --no-transfer-progress -o verify` | **76 tests，0 failures/errors/skipped，BUILD SUCCESS** |
| Node 契约与导出回归 | `netease-music-api`: `pnpm --config.verify-deps-before-run=false exec mocha -r intelli-espower-loader -t 60000 main.test.js test/playlist_tracks.test.js --exit` | **6 passing**；新增契约覆盖 add、del、502，各断言 request 只调用一次 |
| Node 格式与 lint | `pnpm --config.verify-deps-before-run=false exec eslint --fix module/playlist_tracks.js test/playlist_tracks.test.js`；再执行同范围 eslint 检查 | **通过**，仅格式化本任务文件 |
| 提交范围 / 空白检查 | 主仓库与子模块 `git diff --cached --check`，显式核对暂存文件清单 | **本任务通过** |

Java 新增 `NeteaseMusicServiceTests.java:717` 复现旧 nested body；调用真实 Java 服务方法、MockRestServiceServer 只期望一次 `/playlist/tracks`，确认精确 missing-code 错误且无重发。既有 `:679` 顶层 code 的成功加歌测试通过。

开发中曾因把标准化歌曲字段误读为 `id` 而出现 5 项新增测试失败；已改为 `song_id`，最终全量回归通过，未删除或弱化失败用例。最初 pnpm 默认依赖校验被既有 ignored-builds 配置拒绝，改用上述参数调用现有依赖后通过；未批准安装脚本、未改依赖清单。全仓库 `git diff --check` 还报告既有未提交 `gold_review_v0_blind_pack.md:26` 的尾随空白，该文件保留，本任务暂存检查通过。

## 6. 未验证与仍有边界

- 38 项既有跳过：21 项隔离 PostgreSQL验收、7 项 PostgreSQL E2E、4 项 live PG smoke、3 项真实 PG 重启、2 项真实 Qdrant、1 项真实 TypeSafe；没有改测试 skip 条件。
- 全量指 `ai-service/tests` 业务测试；`evals` 依赖的 `deepeval` 未安装，未运行该评测集合。验证包装器只设置子进程离线环境，不改 `.env`。
- Node 默认 `pnpm test` 中的远程网易云服务测试未运行；本轮没有登录账号、调用付费模型或发出真实外部写请求，不能等同于 validation-7 A 的真实复验。
- success 前故障采用异常注入与新 SQLite store/Journal 实例恢复，未实施真实 `os._exit` / PostgreSQL 进程重启；不宣称 Phase D 全部缺口已关闭。
- 创建歌单的未知结果尚不能证明隐私参数；读回未看到目标效果时也不能自动判定失败，仍需后续只读核对或人工处理。
- 核对只覆盖当前任务可追溯的 operation ID；没有新增跨任务全局供应商账单/幂等查询接口。C 的组合请求、其他恢复缺口、F 账单不属于本轮。

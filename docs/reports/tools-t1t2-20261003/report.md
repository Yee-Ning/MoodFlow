# Phase 4：T1/T2 — 红心读回与删除自建歌单

## 1. 结论与基线

真实测试账号链路已完成：红心写入/取消后的只读读回一致；新建测试歌单经 West 二次确认后删除，读回无残留；非自建及无确认请求明确拒绝；新进程使用旧成功 receipt 返回原结果，没有第二次业务删除。

- 开始前主仓 HEAD：`4bb22856e48d346ced2043d1467f1e2af7f0163f`；当时含 A、D，C 与 R2 仍在独立分支。
- 独立分支：`codex/tools-t1t2-20261003`。先合并 C 分支（实现 `37ffdabb8e7c5a959027928b94b3de092b54be23`）和 R2 分支（实现 `feea34747f98081f613467be71cef00614e73d9e`），实现基线为 `b7d1131a487e029265f506135d39c9f6fe50a41b`。
- `git merge-base --is-ancestor` 已确认 B `f97d5d2`、A `588fa6f`、C `37ffdab`、R2 `feea347`、D `a3cc649` 均为本分支 HEAD 的祖先。
- Java 实现：`c4096608e92ac6ad3077b807beced04932d3f5c6`。
- 经 West 额外授权的基线兼容修复：`2e7f12aba951770bf9eac4b488679e260050f15d`，独立提交，见 §5。
- AI 实现：`b983411b0029b154b8d50c4fe23b579128c758f9`。
- 实际验收日期为 **2026-10-04**；目录沿用任务指定的 `tools-t1t2-20261003`。

## 2. 新增接口与工具

| 项目 | 实现与契约 |
|---|---|
| T1：`get_liked_songs` | `ai-service/app/tools/music.py:559`；只读 GET `/api/user/liked-songs`，返回当前登录账号去重、排序的 `song_ids`，不执行红心写入 |
| 红心 reconciliation | 复用已有 `MusicServiceClient.get_liked_song_ids()` 与 A `MusicWriteReconciler.inspect()`，没有另建补账流程；可信 ID 集合可作为取消红心的目标来源，不扩展成其他动作的歌曲元数据 |
| T2 Java | `music-backend/src/main/java/cn/itcast/demo/musicbackend/controller/UserMusicController.java:144`；DELETE `/api/playlists/{playlistId}?confirmed=true`；缺省 `confirmed=false`，拒绝执行 |
| T2 Agent | `ai-service/app/tools/music.py:582`；`delete_playlist_tool(playlist_id, confirmed=false)`；必须有可信当前用户文本和 operation journal |
| T2 核对 | `ai-service/app/agent/foundation/long_horizon/write_reconciliation.py:71`、`:114`；先保存账号/创建者/类型证据，删除后 GET 自有歌单列表验证目标消失 |
| A 三态 HTTP 查询 | POST `/api/ai/continuity/operations/status`，DTO 为 `thread_id`、必填 `graph_mode`、稳定 `operation_id`；`ai-service/app/api/chat.py:251`、`:256`，coordinator `:387`；只查询持久化状态，不核对、不恢复业务、不追加 receipt |

查询返回沿用 A 的 `failed / unknown / confirmed`、`confirmation_source`、错误与 proposal/receipt/reconciliation 引用。无身份 401，非法 DTO 422，账号/图/会话/operation 不匹配 409。工具描述、schema、结果白名单、绑定清单和可信引用同步更新；未修改路由选择、Guard、转交边、digest 或 ACK。

## 3. 安全与补记流程

1. 工具先展示准确名称/ID，请用户回复“确认删除歌单 ID”。执行时从可信 `runtime.state.current_input` 验证肯定确认与准确 ID；模型单独填 `confirmed=true` 无效。否定、询问、解释引用或不匹配 ID 拒绝。
2. A 在业务派发前只读获取当前账号与歌单详情，保存 baseline；创建者必须等于当前账号，`special_type=0`、`subscribed=false`，否则记 `failed/http_422`，不派发业务写。
3. Java 独立复核确认、身份、精确歌单 ID、创建者、自建类型；非自建/系统歌单 403，字段不明 422。实现位置：`NeteaseMusicService.java:452`。网易云 `/playlist/delete` 只提交一次。
4. Java 提交后使用同一会话读账号与完整分页自有歌单列表；身份变化、读回失败或目标仍在均不宣称成功。提交后的身份失效包装为 502 效果未知，避免误判为确定失败。
5. 即便 Java 返回 200，A 仍先追加 `unknown/readback_required` 并保留原响应 artifact；仅通过已有 `OperationJournal.reconcile()` 读回、保存核对证据、补记唯一 success。实现入口 `operations.py:297`、`:361`；三态定义未改变。
6. 读回无法证明时保持 unknown；稳定 operation ID 禁止再次业务派发。之后仍只允许只读核对；已 confirmed 的同 ID 返回旧 artifact。查询和 GeneralChat 复用 A 状态语义。

Java `confirmed` 是工具确认结果的传递字段，Java 不解析自然语言；真实用户文本二次确认在 Agent 工具层验证。系统“我喜欢的音乐”即使出现在 editable 列表也被创建者/`specialType` 校验保护。

## 4. 真实账号验收

West 授权本轮新扫码会话，核对唯一测试账号 **我也无话可说_t（17994646607）**。Cookie 只在新进程内存中由 QR 登录产生，不读取旧 Cookie、不提交凭据。Java 打包/验收进程重启后重新扫码，不绕过身份门控。

真实链路：实际 Agent 工具 → A OperationJournal → HTTP Java（18003）→ 实际网易云服务（13003），随后 T1/自有歌单读回与实际三态 HTTP API（18004）。验收存储为独立 SQLite。工具通过可信 ToolRuntime 直接执行，**未验证完整 LLM 对话/路由/前端交互链**；业务接口和云端效果没有 mock。

| 用例 | 请求、结果与读回 |
|---|---|
| 红心写入 | 歌曲 `2668397359`，`liked=true`；HTTP 200，T1 读回包含该 ID，API 200 `confirmed/source=response` |
| 取消红心 | 同歌曲 `liked=false`；HTTP 200，T1 读回不包含该 ID，API 200 `confirmed/source=response`；初始与最终整个红心 ID 集合相同 |
| 创建测试歌单 | `MoodFlowTest-20261004-094729`，`privacy=10`；HTTP 200，ID **18449669944**，自有列表读回存在，API confirmed |
| 二次确认删除 | West 回复 `确认删除歌单 18449669944”`（末尾引号保留）；HTTP 200，Java 与 A 读回均确认消失，API 200 `confirmed/source=readback` |
| 非自建 Java 拒绝 | 公开官方歌单 `7114332637`，先读详情证实 creator 为“网易云音乐”、owner ID 为 1；DELETE 返回 **403**；读回歌单身份/创建者/歌曲 ID 与顺序相同，完整摘要不相同，见下文 |
| 非自建 Agent 拒绝 | 同官方目标，A baseline 校验失败，`failed/http_422`，API 查询 200 failed；未派发第二次 DELETE |
| 无确认 Java 拒绝 | 已删除的本轮 ID，`confirmed=false`；**422**，校验在鉴权/业务写之前执行；属于真实拒绝测试，不冒充存在目标的删除 |
| 无确认 Agent 拒绝 | 当前用户文本“查看歌单”，模型参数 confirmed=true；工具 **http_422**，不派发 HTTP 写；受控测试另覆盖存在目标 |
| 新进程幂等回放 | 新进程、新扫码、保留原 run/operation ID；工具返回旧 confirmed artifact；本轮目标的成功 DELETE 总数仍为 **1**，success receipt **1** |
| 最终清理 | 北京时间 **10:13:31** 再读自有歌单与红心：目标无残留、歌曲未红心；没有删除历史歌单 |

选中歌曲是 RyaVocal 的 **《晴天 (原唱 周杰伦)》**，不是周杰伦原版。未额外加歌，因为红心链路已满足本轮 A 正常写入复验要求。

正常红心写响应确认后，T1 独立读回核对；A 对终态 confirmed 不重复补账。删除实际持久化轨迹为 proposed → unknown（响应）→ unknown（核对证据）→ success，补记走 A `reconcile()`。本轮未构造真实 502、未断网，不能宣称复现了 V7 的真实 502。

官方歌单拒绝前后的原始 `foreign_readback.unchanged=false` 如实保留。逐字段比对发现 15 个歌曲 `coverUrl` 在 `p3.music.126.net` 与 `p4.music.126.net` 之间变化，其他字段相同；这解释了完整摘要差异，不等于发现内容写入。原始载荷及 digest 未修复、未重试；差异路径/原值见汇总 JSON。

稳定 operation ID：

| 操作 | ID |
|---|---|
| 红心 | `op:54833c8dba6831b07aab212c12bc3e2339431cb2a79c5d6bce8806dfe4465ba9` |
| 取消红心 | `op:5fd0d6899cdb34c3b981c6688363a1dd694c49e7b833a7e6250ae3f6be64e596` |
| 创建 | `op:189dd0bc131ca3c3321c84486e11f34e0145c990001375d66fbe09d312739639` |
| 删除/新进程回放 | `op:c7c1c5bb8c69719c0582950f721393fcb30511efb677e5bdfbdacf2d9fd5f0ec` |
| 拒绝非自建 | `op:59f3db38102c3a0e84faedb3a1af0f44d659f466ca7a5ac1d917f49d1ba105b9` |

Thread：`tools-t1t2-f45ebdb4a87f48c08ff3e486a36c6dad`；run：`phase4-6f46aae36e514e2bae9c9845019ea927`。成功业务写共 **4 次**（红心、取消红心、创建、删除），另有 Java 拒绝探针 403/422 各一次；拒绝请求未到达网易云写接口。

## 5. 自动化测试与失败记录

| 检查 | 命令与结果 |
|---|---|
| Java 全量测试/构建 | `music-backend`：进程内 `JAVA_HOME=D:/code/JDKall/openjdk-21.0.1`，`D:/apache-maven-3.9.4/bin/mvn.cmd --batch-mode --no-transfer-progress -o verify`；**92 tests，0 failures/errors/skipped，BUILD SUCCESS**，含 16 个新增删除契约测试 |
| T1/T2、A 与工具直接相关 | `ai-service`：`.venv/Scripts/python.exe ../.dev/context-loop-p0-20261002/validate.py -m pytest tests/test_tools_t1t2.py tests/test_write_reconciliation.py tests/test_tool_definitions.py tests/test_song_assistant.py tests/test_handoff_contracts.py tests/test_tool_calling_subgraph.py -q`；**186 passed，16 subtests，3 warnings**，0 failures |
| 单独授权的兼容修复 | 同包装器运行 `test_recovery_combo_query_compat.py`、`test_recovery_selfhealing.py`、`test_handoff_combo_robustness.py`、`test_jev_router_v5_e2e.py`；**38 passed**，17 既有 durability warnings |
| AI 全量应用测试 | `.venv/Scripts/python.exe ../.dev/context-loop-p0-20261002/validate.py -m pytest tests -q --junitxml=../.dev/tools-t1t2-20261003/ai-full-final.xml`；**2011 passed、45 skipped、288 subtests passed**，0 failures/errors，267.34s；178 条既有 durability warnings |
| Python 静态/差异检查 | `.venv/Scripts/python.exe -m ruff check` 显式列本轮 Python 文件，**All checks passed**；任务文件 `git diff --check` 通过 |

覆盖只读工具、typed DTO、确认/否定/询问/引用、归属不明/非自建/系统歌单、账号变化、post-dispatch 502、unknown 无第二次写、读回后 confirmed 与单 receipt、状态 API 鉴权/作用域/只读性、工具库存和兼容调用方。

所有曾出现的失败均保留说明：

- 首轮 AI 全量应用测试：**5 failed、1995 passed、45 skipped、276 subtests**。其中 3 个工具清单断言仍期望 10 个工具，更新为 12；另外 2 项来自 C/D 合并语义冲突，经 West 明确授权独立修复。
- D 恢复查询在没有恢复账本时抢占“刚才哪些步骤成功，卡在哪里”组合查询。修复后无恢复记录的普通进度查询交还 C；显式恢复查询和已有恢复阻塞记录保持原语义，新增 3 个回归测试。
- V5 旧测试把“仅输出文字、没有生成播放动作”期待为成功；更新为 C 已实现的 needs_input，并保留顺序、次数、依赖与无动作/澄清文本断言，没有修改播放实现。
- 额外运行无目录限制的 pytest，评测目录 `evals/tests/test_judges.py` 因缺 **deepeval** 依赖在收集阶段失败；未安装新依赖、未删除/跳过该测试。应用完整 `tests/` 与该额外评测目录分开报告。
- 首次 Java verify 的测试通过，但 Windows 上运行中的 target JAR 阻止 repackage；停止本轮 Java 后重新 verify 通过，联调运行复制的 JAR。
- 新测试初次收集/fixture 使用了不正确的内部函数名与上下文 DTO，修正为现有函数和 RoutingContext，最终相关测试通过。
- 收尾审查修正确认解析：同一轮文本中有后置否定/取消时，不允许早先的肯定分句独立授权删除；新增“确认删除……但是不要执行”的拒绝与零业务派发回归测试。前一版全量应用测试为 2008 passed、45 skipped、288 subtests；修正后重新运行最终全量。
- 负向联调入口一次 500：脚本误用 `search_entities` 位置参数，搜索和删除尚未派发；修正关键字参数及真实 list/MusicEntity DTO 后，用新进程、新扫码完成负向验收。脚本一次直接启动失败 `ModuleNotFoundError: app`，改用 `python -m scripts.tools_t1t2_live`。

## 6. 证据与限制

- [real-account-evidence.json](real-account-evidence.json)：本轮选中目标、公开 HTTP 请求参数/结果、工具结果、实际状态 API 与最终清理。
- [journal-evidence.json](journal-evidence.json)：实际 SQLite History 和白名单 artifacts，含删除 baseline、原响应、读回证据、稳定事件引用；无 Cookie/Token/二维码。
- [validation-summary.json](validation-summary.json)：证据 SHA-256、请求计数、状态轨迹/单 receipt 断言、JUnit 汇总、已知失败与未验证项。
- 包装器只对自动化测试设置离线环境；真实联调使用真实 Java/网易云，未调用 DeepSeek/JEV，供应商账单 F 继续按 West 指示暂缓。
- 本轮没有 PostgreSQL、新一轮 os._exit、真实 LLM 对话、完整 UI/播放器验收；45 个需要显式外部环境开关的既有集成测试未运行，具体原因见汇总。不能把工具真实链路当成全系统闭环验收。
- 真实三态 API 返回 confirmed 与 failed；unknown 的 API 查询和异常后只读补账由受控测试覆盖，没有冒充真实错误注入。最后新增的后置取消解析仅补了安全回归，未再执行云端删除。
- 未修改生产 `.env`，默认图保持 foundation、canary 关闭，无部署或 push。用户已有 dirty 文件和未跟踪文件未纳入本轮提交。
- 验收完成后关闭本轮 13003/18003/18004 服务与扫码页；未操作其他开发服务或既有暂停的验收数据库。

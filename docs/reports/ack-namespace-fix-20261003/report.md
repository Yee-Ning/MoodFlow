# B 修复：播放器 ACK 命名空间一致性

日期：2026-10-03；实现基线：`e638f4d057a48d665f1fc9283ab3a0eeb121ecdb`。
实现与测试 commit：**`f97d5d259cfcf153185581905ca7abb0aca33b50`**；本报告和脱敏证据另作独立文档提交。

**结论：完成的真实 handoff 暂停用例和 Foundation 原有 SSE 用例通过 B 验收。** 同一真实动作修复前 ACK 409、修复后 200/applied；重复提交仅一条 ACTION_ACK，原动作引用、session 和 task 一致；传错图明确拒绝。另有验收页并发请求失败、未执行及未 ACK 的动作，保留为独立未完成案例，见 §5。

## 1. 根因复核

- validation-7 §4：显式 `/handoff` 生成暂停动作，真实音频已暂停；旧前端 helper 没有 `graph_mode`，ACK 服务端重新按默认图选择命名空间，默认 foundation 与原动作 handoff 不一致。V7 原始返回状态为 409，原响应体未捕获，本报告不补造该响应体。
- 基线 `ai-service/app/api/chat.py:181` 使用 `body.graph_mode or select_chat_graph(...)`；`soul-music-room/src/services/aiChat.ts:5` 仅传 thread/action/status。旧 Coordinator 只在选中命名空间的 active task 中找动作，既依赖当前路由配置，也不能处理旧 task 的迟到 ACK。
- 本轮使用基线 commit 的真实 `ContinuityCoordinator.acknowledge` 源码（从 Git 读取 AST 后编译），在新建隔离 PostgreSQL 上运行旧选择逻辑，未 mock 数据库或返回结果。该对比入口仅存在于本地验收进程；生产入口仍是 `/api/ai/actions/ack`。

## 2. 实现与兼容性

| 改动 | 代码位置（1-based 行号） |
| --- | --- |
| 在 JSON 和 SSE 的动作传输副本上附加 `graph_mode`、外部 `thread_id`，不修改内部 ClientAction、journal 或 digest | [chat.py:58](D:/code/ProjectsAll/MoodFlow/ai-service/app/api/chat.py:58)、[:161](D:/code/ProjectsAll/MoodFlow/ai-service/app/api/chat.py:161) |
| 前端按动作保存归属并传给 ACK；第四个 graphMode 参数可省略，旧 DTO 不增加字段 | [aiChat.ts:7](D:/code/ProjectsAll/MoodFlow/soul-music-room/src/services/aiChat.ts:7)、[:84](D:/code/ProjectsAll/MoodFlow/soul-music-room/src/services/aiChat.ts:84)、[:242](D:/code/ProjectsAll/MoodFlow/soul-music-room/src/services/aiChat.ts:242) |
| 收到其他 thread 的动作时在执行前拒绝；不猜测默认图 | [aiChatStore.ts:135](D:/code/ProjectsAll/MoodFlow/soul-music-room/src/store/aiChatStore.ts:135)、[:151](D:/code/ProjectsAll/MoodFlow/soul-music-room/src/store/aiChatStore.ts:151) |
| ACK 入口只传请求的可选 mode，不再调用默认图选择函数 | [chat.py:202](D:/code/ProjectsAll/MoodFlow/ai-service/app/api/chat.py:202) |
| 显式 mode 先查指定命名空间；缺省时按 thread/action 在已授权的 Foundation/Handoff 命名空间反查；扫描该会话的所有 task，包括已关闭 task | [action_ack.py:14](D:/code/ProjectsAll/MoodFlow/ai-service/app/agent/foundation/long_horizon/action_ack.py:14)、[:26](D:/code/ProjectsAll/MoodFlow/ai-service/app/agent/foundation/long_horizon/action_ack.py:26) |
| 用原动作的 session/task 写 ACTION_ACK，复用原 Writer 的确定性 event key、action_ref 和去重 | [coordinator.py:439](D:/code/ProjectsAll/MoodFlow/ai-service/app/agent/foundation/long_horizon/coordinator.py:439) |

归属已经持久化：CLIENT_ACTION 带 task/session；task/session 带 conversation；conversation 使用 Foundation 原 thread 或 `handoff:v1:<thread>`，并由 `agent_execution.auth_scope` 绑定 graph_mode/user（`coordinator.py:190`、`execution.py:167`）。此次复用现有持久化关系，无需数据库迁移；旧动作也可反查，不依赖新增索引才能工作。

错误语义：无动作或身份/thread 不可访问为 `ack_action_unavailable`；显式图错误为 `ack_graph_mode_mismatch`；归属不唯一为 `ack_action_ambiguous`；与已保存回执状态冲突为 `ack_state_conflict`。均返回 409；DTO 类型/长度/额外字段错误仍为 422。不会读其他身份的动作来诊断错误。

Foundation 成功响应保持原 action_id/status 结构；旧 helper 的三参数调用仍可用。旧 ChatOutput final 保持原契约，只有 FoundationResponse 的 action metadata 会附加归属。

## 3. 真实联调及修复前后对比

- West 再次扫码，Java 实际身份为 `我也无话可说_t / 17994646607`；验收中间件拒绝其他账号。没有主账号歌单/收藏操作，没有创建、删除歌单或收藏写入。
- 使用 Vite `127.0.0.1:5173`、真实 Java `:8080`、网易云 API `:13006`、本轮 PostgreSQL `:15458`。生产 `.env` 未修改；默认图 foundation、canary=false；原 V7 暂停数据库未操作。
- Handoff 使用现有显式 JSON `/api/ai/handoff`，返回动作后由 Vite 加载生产 `parseClientAction`、`executeClientAction`、`acknowledgeAiAction`；Foundation 使用生产 `useAiChatStore.sendMessage` 的 `/api/ai/chat` SSE 路径。没有用 MockAudio、mock graph 或替代 ACK 响应证明闭环。
- 播放目录歌曲 `2652820720 / 晴天(深情版) / Lucky小爱`。真实 HTMLAudioElement：readyState=4、duration=278.962698s、paused=false；执行 handoff pause 后 paused=true，currentTime=61.138274s。

真实 handoff 主案例：

```json
{"thread_id":"ack-fix-20261003-handoff-1791025220222","graph_mode":"handoff","action_id":"3073655d92d84d95a75bcc0ea26820c1:tool_calling:0:direct:0","status":"applied"}
```

| 北京时间 | 路径与输入 | 实际结果 |
| --- | --- | --- |
| 19:00:54 | 生产执行器执行上面的 pause 动作，暂不记 ACK | 真实音频 playing → paused |
| 19:01:03 | 基线旧逻辑；同 action/thread/status，省略 graph_mode | 409，`{"detail":"动作回执与会话状态不一致"}` |
| 19:01:25 | 修复后的生产 helper；携带 handoff | 200，`{"action_id":"3073655d92d84d95a75bcc0ea26820c1:tool_calling:0:direct:0","status":"applied"}` |
| 19:01:53 | 重复同一 DTO | 200/applied |
| 19:02:10 | 新入口，省略 graph_mode 的旧 DTO | 200/applied，反查原 handoff 归属 |
| 19:02:28 | 同 action/thread，但显式 foundation | 409 / `ack_graph_mode_mismatch`，未写错账 |
| 19:02:57 | 读回 History；后续独立 PostgreSQL 导出再次对账 | 仅 1 条 ACTION_ACK |

History：CLIENT_ACTION seq=66，`evt:45c5e67bcec8cadf7d73e5e4f6270b0a89680cab19e6d764031a520cf2d66116`；ACK seq=83，`evt:e5619ced44ec9dd14859cb370240dc341447ecbc8e4afdcf8ea84a69e474677a`，action_ref 精确指向前者。两者 task=`task:4b354f4b8609465f9e4622ddcfb2b46f`、session=`session:b94972c7242a470a9f22e37e2cf11166`，action ID 相同。业务 task ID 与 continuity task ID 是两个不同层级；ACK 与 CLIENT_ACTION 对齐的是原 continuity task，未混用。

Foundation 回归：thread=`conversation-7a20f334-1c88-4afe-99d9-f3f94adc0e2f`；action=`3810368f458f4f9690dce5d521c4fc51:tool_calling:0:direct:0`。19:03:17 生产 Store 回到 idle、error 为空，真实音频 paused=false → true；CLIENT_ACTION seq=97、ACK seq=108，唯一 ACK/applied，action_ref/session/task 均一致。

## 4. 测试、构建与检查

后端离线命令经本地 `.dev/context-loop-p0-20261002/validate.py` 包装，覆盖进程环境，避免调用真实供应商；它不改 `.env`。与上一节真实浏览器联调分别记录。

| 命令（工作目录） | 结果 |
| --- | --- |
| `.venv/Scripts/python.exe ../.dev/context-loop-p0-20261002/validate.py -m pytest tests/test_action_ack_namespace.py -q`（ai-service） | 7 passed |
| 同包装器 `-m pytest tests/test_api.py tests/test_action_ack_namespace.py -q` | 最终 16 passed |
| 同包装器 `-m pytest tests -q --junitxml=../.dev/ack-namespace-fix-20261003/business-tests-final.xml` | **1832 passed、38 skipped、286 subtests passed、160 warnings**；978.62s |
| `.venv/Scripts/python.exe -m ruff check app/api/chat.py app/agent/foundation/long_horizon/action_ack.py app/agent/foundation/long_horizon/coordinator.py tests/test_action_ack_namespace.py tests/test_api.py` | All checks passed |
| `npm run test:ai-stream`（soul-music-room） | passed：新/旧 DTO、Handoff/Foundation 传参、错误 thread、非法元数据、失败 ACK 和诊断错误 |
| `npm run build` | tsc + Vite passed；既有大 chunk 警告保留 |
| `npm run lint` | passed |
| 对本任务文件 `git diff --check` / staged check | passed；只有 LF/CRLF 提示 |

新测试覆盖显式与缺省 mode、同 DTO 去重、exact action_ref/session/task、状态冲突、错误图/未知动作/错误 thread/其他身份拒绝、跨图同 ID 歧义、已关闭 task 的迟到 ACK、请求校验、JSON/SSE 标注且原业务 DTO 不变。完整业务测试中的可选环境 skip 未修改；真实 PostgreSQL ACK 链路另有本轮实测。

## 5. 原始失败、未知与验证限制

1. 验收页出现两个并发 handoff 生成请求，时间间隔约 14s；页内共用 `thread` 变量，使第一条返回时出现 `动作归属不一致`。第二次点击来源未单独审计，不能归因给某个人。该页没有自动重试。第一条 thread=`ack-fix-20261003-handoff-1791025206314`、action=`16c2df5828df44ce995181ca5009963f:tool_calling:0:direct:0`：服务器生成成功，客户端没有执行，数据库 ACK=0。保留现场，不用第二条成功覆盖它，不将其计作完成用例；它是本地验收页竞态，不是此次生产 Store 的 thread 检查失败。
2. 新测试夹具最初未先关闭 session，报 `cannot close a task with an active session`；修正夹具生命周期后通过。新增 final 标注最初误按 FoundationResponse 处理旧 ChatOutput，造成 `AttributeError: 'ChatOutput' object has no attribute 'results'`；已修复并保留旧契约测试。早期业务全量被中断用于修复该回归，最终完整重跑结果见 §4。
3. 全目录 `pytest -q` 在 `evals/tests/test_judges.py` 收集时因 **`ModuleNotFoundError: No module named 'deepeval'`** 失败。未新增依赖、删测试或假称全目录通过；业务 `tests/` 已完整通过，DeepEval 评测未验证。
4. 默认 Java 17 启动既有 Java 21 编译 JAR 时出现 UnsupportedClassVersionError；换用已安装 JDK 24 后真实 Java 启动成功。没有重新编译或修改 Java 业务。
5. 浏览器 blob 下载等待超时、一次 DOM 等待超时；旧 DTO 实际返回随后读回为 200，并未重发请求。最终通过本地验收页受身份门控的导出接口保存完整 DOM 记录和真实截图。截图中的摘要由原始记录生成，未替换业务返回。
6. Handoff 的真实入口为显式 JSON；handoff SSE 标注和前端 Store 分支由自动化契约测试覆盖。未开启灰度或切默认图来额外证明真实 canary SSE，这仍是开闸前的环境验证限制。本轮未新增故障注入，不声称同时修复 V7 A/C/D。
7. 本轮进程禁用长时记忆；日志有 `long-term memory is disabled` 的非致命 extraction 警告。不能据此声称真实长时记忆已验证。
8. 供应商客户端观察到 DeepSeek 2 次、JEV 2 次；守门预算预留 0.03723878 元、上限 1 元，低于 West 合计 5 元授权。**预留不是实扣账单**；控制台核对继续按 West 指示暂缓。原始 usage/dispatch 观察见附件。
9. 缺省 ACK 反查按已认证 thread 的任务历史分页扫描，没有专用 action 索引；超长历史的性能未做负载测试。新字段是传输元数据，旧前端版本若仍严格拒绝额外 action 字段，需要与此 ACK 前端修复一起升级。

## 6. 证据与收尾

- [browser-evidence.json](D:/code/ProjectsAll/MoodFlow/docs/reports/ack-namespace-fix-20261003/browser-evidence.json)：完整浏览器可见记录，包含并发失败、请求参数、真实音频状态、回执返回与两条完成链路的 History。
- [network-evidence.json](D:/code/ProjectsAll/MoodFlow/docs/reports/ack-namespace-fix-20261003/network-evidence.json)：实际 CDP 抓取的旧 409、新 helper DTO、200 body；仅保留 ACK 字段，不含请求头/cookie。
- [history-evidence.json](D:/code/ProjectsAll/MoodFlow/docs/reports/ack-namespace-fix-20261003/history-evidence.json)：独立 PostgreSQL 导出（原动作、ACK、公开 usage），包含第一条 ACK=0 和两条完成用例 ACK=1 的断言结果。
- [provider-observations.json](D:/code/ProjectsAll/MoodFlow/docs/reports/ack-namespace-fix-20261003/provider-observations.json)：供应商真实 dispatch/usage 观察，非账单。
- [browser-proof.jpg](D:/code/ProjectsAll/MoodFlow/docs/reports/ack-namespace-fix-20261003/browser-proof.jpg)：真实验收页截图。测试脚本/原始完整测试日志和 XML 在本地 `D:/code/ProjectsAll/MoodFlow/.dev/ack-namespace-fix-20261003/` 保留，不进入生产运行路径。

只提交 ACK 实现、相关测试、本报告和本轮脱敏证据。既有用户改动未暂存；不动路由、Guard、handoff 边、journal/digest、生产配置；不部署、不 push。验收进程收尾停止，本轮 PostgreSQL 保留为停止状态以便后续核查；原 V7 paused 容器保持不变。

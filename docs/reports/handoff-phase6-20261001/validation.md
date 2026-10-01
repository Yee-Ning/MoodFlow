# MoodFlow Handoff 阶段 6 验收报告

日期：2026-10-01；开始基线 `dbd591a`。范围见[阶段 6 实现说明](../../architecture/handoff-runtime-phase6.md)。

## 1. 交付结论

**验收工具、验证执行和证据已交付，业务验收尚未全部通过，暂不进入阶段 7 切流。** 真实服务返回 HTTP 200、前端构建成功、来源字段非空，均不能替代业务结果和检索相关性的判断。

本阶段新增两个显式运行的验收 CLI 及 15 项回归测试，修复受控网络测试意外调用在线分类器的问题；环境加载限制在 CLI 入口，避免辅助函数影响整个测试进程。业务代码、前端 UI、Java API、实际配置、运行预算和依赖版本没有修改。提交不包含用户原有报告改动、移动、知识数据和验收图片。

## 2. 自动化检查

应用与评测命令在 `ai-service` 执行，前端在 `soul-music-room`，Java 在 `music-backend`。完整应用验证使用子进程离线配置：memory checkpoint、SQLite persistence、disabled memory、空 DATABASE_URL、JEV 与显式 live 开关关闭、占位 LLM 配置、单线程 OpenBLAS。实际 `.env` 不改动。

<!-- AUTOMATION_RESULTS_START -->

| 命令 | 最终结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests -q -ra --tb=short` | **1622 passed，14 skipped，1 warning，286 subtests passed**；退出 0，682.93 秒 |
| 验收/网络/三类配置隔离相关集合 | **95 passed**；退出 0，68.84 秒；为上述全量子集 |
| 应用环境全部 `evals/tests` 除 `test_judges.py` | **194 passed**；退出 0，122.39 秒 |
| `.venv-eval/Scripts/python.exe -m pytest evals/tests/test_judges.py -q -ra --tb=short` | **4 passed**；退出 0；评测框架合计 198 项 |
| JDK 21：`mvn --batch-mode --no-transfer-progress -o verify` | **75 tests，0 failures/errors/skipped；BUILD SUCCESS**；退出 0 |
| `npm run test:ai-layout` / `test:ai-stream` / `test:music-assets` / `test:search` / `test:ai-trigger` / `test:worldline` | 六个命令全部通过；退出 0 |
| `npm run lint` / `npm run build` | 全部通过；退出 0；构建保留既有 >500 KB chunk warning |
| PostgreSQL persistence + live smoke（隔离 DB，显式 live 开关） | **8 passed**；与 Qdrant 首轮同跑的 10 项中，只有 2 项 Qdrant 因代理失败 |
| 两项真实 Qdrant 用例补充 NO_PROXY 后复验 | **2 passed**；退出 0，23.33 秒 |
| 修改的五个 Python 文件 Ruff check / format check | 全部通过；退出 0 |
| `.venv/Scripts/python.exe -m compileall -q app tests evals scripts` / `-m pip check` | 退出 0；No broken requirements found |
| `.venv/Scripts/python.exe -m ruff check . --output-format json` | **45 项原有问题**；退出 1；path/line/code/message 与阶段 0 一致 |
| 本阶段暂存文件 `git diff --cached --check` | 通过；不包含用户原有报告的空白问题 |

<!-- AUTOMATION_RESULTS_END -->

以上受控测试用于确定行为回归；真实依赖单独验证，不把注入的测试 runtime 当作线上调用。最后相关集合与完整应用集合重叠，不累加为独立用例总数；JUnit 的 testcase 数还包含 286 个成功 subtests。

首次全量运行有 1 项失败：网络场景测试进入真实分类 LLM。注入离线分类 runtime 后，中间全量为 1617 passed / 14 skipped。随后新增 checkpoint CLI 引入环境加载副作用，导致 3 项配置隔离测试失败；把 dotenv 加载从 execute 移到 main，并增加辅助函数不加载环境的断言后，相关集合 95 passed。所有失败均保留于机器证据的历史记录，最终完整结果以表格及最终日志摘要为准。

14 项普通全量 skip 属于显式 live 用例：JEV、PostgreSQL、Qdrant、WorldLine PostgreSQL restart。PostgreSQL 和 Qdrant 另有真实执行，handoff 中断恢复另有数据库重启实测；本次没有开启完整 WorldLine restart 专项套件，也不把单次浏览器刷新恢复等同于该套件通过。Judge 套件在既有独立 `.venv-eval` 执行。

## 3. 真实依赖与环境

- 当前代码构建的 Java JAR（8080）、当前 Netease Node 服务（13006）、当前 ASGI（8001）、当前 Vite（5173），均为本次本地启动的进程。外部音乐目录和模型调用使用正常运行实现。
- 新建独立 `moodflow-handoff-phase6-pg`，复用本地已有 `pgvector/pgvector:pg16-trixie` 镜像，绑定 127.0.0.1:15436。密码随机生成，仅交给隔离进程，未写入仓库。运行现有应用迁移与 checkpoint setup；原有数据库、容器和 Qdrant 未重启或改配置。
- 正常模型、JEV 和当前已有 Qdrant 索引真实调用；观察到 JEV 实际返回版本 `jev-1.13.0`。没有通过验收脚本替换业务模型、检索结果或 Java 工具。
- 入口 Shadow 与入口生效模式分别执行。入口生效只存在于隔离进程，交接护栏始终 Shadow，默认阈值 0.8 / 0.2。没有启用 0.7 候选，没有改实际 Foundation 默认模式。
- 当前 WorldLine hybrid manifest 为 `data/indexing/worldline/final-native-20260927/v4/index_build_manifest.json`。这是本次读取到的原配置，并非本阶段新建索引。

初次联调使用旧 rehearsal Java，暴露歌曲详情与歌单 API 不兼容；改为当前 JAR 后继续验证。之后发现环境代理令本地 httpx 请求返回 502（直连相同地址为 200），仅在验收子进程为 loopback 补充 NO_PROXY。保留早期失败，不将旧镜像或代理错误统计成当前服务成功。

PostgreSQL 真实测试为 8 passed；Qdrant 首次 2 项因代理失败，补充 NO_PROXY 后原两项全部通过。真实 PostgreSQL 测试集中的单项向量写入使用其既有 fake embedder，但 saver/SQL 是真实数据库；完整图和 HTTP 中的模型调用另行实测，不能称每项 live 测试均使用真实 embedding。

## 4. 固定 HTTP 用例结果

8 个业务输入固定不变，每轮新线程；每轮还包含健康检查和 3 项 422 校验。没有针对失败自动重试或提高预算。[Shadow 最终记录](http-shadow-final.json)、[入口生效最终记录](http-active-final.json)均退出 2，分别为 11/12 项通过，业务为 7/8 项通过。

| 用例 | 入口 Shadow（Hybrid 执行） | 隔离入口生效 | 含义 |
|---|---|---|---|
| Foundation 普通聊天 | 通过 | 通过 | 旧接口可用 |
| handoff 普通聊天 | 通过 | 通过 | GeneralChat 完成 |
| 缺少当前歌曲 | needs_input，符合预期 | 入口提前 needs_input，符合预期 | 没有虚构歌曲或误计成功 |
| 当前歌曲只读分析 | 通过 | 通过 | 真实 Java/模型，无播放或收藏动作 |
| 搜索周杰伦的晴天，不要播放 | 通过 | **失败：token_limit** | 保留业务失败，不能以 HTTP 200 计通过 |
| 暂停 SSE | 通过 | 通过 | pause_player 恰好一次，action 与 final 一致 |
| 先暂停播放，然后陪我聊聊 | **失败：过度澄清** | 通过 | Hybrid 未完成明确双任务；生效模式顺序正确 |
| 周杰伦世界线 | 来源协议通过 | 来源协议通过 | **不代表检索相关性或语义质量通过** |

入口生效首次缺目标用例曾因验收工具只接受 current_song 结果而失败，实际是合法的入口提前澄清；新增严格限定 `needs_input` 的回归并修正验收期望，原始失败记录仍在 [http-active.json](http-active.json)。没有把 failed 响应改判成 success。

入口生效两轮“晴天”搜索均失败。账本显示 Curator 已消耗约 10926 token 后，下一次调用预留触发 16000 总预算上限，停止码 token_limit；这是当前任务完成度问题，预算保护本身正常。补充的“搜索陈奕迅的十年，不要播放”通过，证明可用目录请求能完成，但不能代替原失败用例。[补充记录](http-extras.json)。

补充“先探索周杰伦的音乐世界线，然后暂停播放”进入入口澄清，未执行 Explorer → Curator 业务边；不声称四条业务边全部真实验证。受控网络测试覆盖四条允许边，实测 G→C、C→E、C→G，以受控覆盖和真实覆盖分别记录。

单次完整等待时延保留在每例 JSON。Shadow 世界线约 44.83 秒、冷启动 Foundation 约 47.36 秒，入口生效最终世界线约 33.72 秒；这些是小样例的整轮等待，不是正式 P95、负载结果或 SLA。没有用后续较快结果抹掉早期慢请求。

## 5. 持久化、隔离及中断恢复

使用真实 HTTP 线程的 PostgreSQL checkpoint 和 continuity task/session/history 只读快照，不导出消息正文或私有 scratchpad。

- 同一外部 thread ID 分别访问 Foundation 与 handoff，数据库任务和 session 不重叠；Foundation 请求后 handoff checkpoint SHA-256 保持不变。[隔离证据](mode-isolation.json)。
- 8 条 HTTP checkpoint 经独立测试数据库和 ASGI 重启后，checkpoint 内容 hash 和历史记录保持一致。[重启对比](checkpoint-restart-comparison.json)。
- 正常图在 G→C 转交后中断，运行状态 running、1 hop、控制调用/回复成对、无播放器动作；重启专用 PostgreSQL，再由新 Python 进程恢复至 completed。同一 run ID、GeneralChat 的 1743 token/2 model calls、hop 和控制调用计数均未重算，最终恰好 1 个 pause_player。[完整恢复证明](restart-proof.json)。

没有以重启后发送新请求冒充旧图恢复，也没有人工拼接成功结果。此证明仍只覆盖服务端动作生成；真实浏览器的执行 ACK、恢复后重放去重及账户写操作未验证。

## 6. 浏览器关键交互与视觉

通过 Computer Use 在当前 Vite 与当前后端上进行真实浏览器操作，未 mock HTTP。浏览器截图在本目录保存。

| 检查 | 实际观察 |
|---|---|
| 搜索“晴天” | 32 条结果，歌曲/专辑/歌手/歌单返回正常 |
| AI 打开、聊天、流式结束 | 请求期间禁用输入，结束后恢复，回复可见 |
| AI 只读搜索“十年” | 目录候选返回，播放器队列仍为空 |
| 新聊天、关闭 AI | 新会话展示空状态，关闭后搜索可继续 |
| AI 周杰伦世界线 | 回复和来源可见，同时明确承认知识检索命中了无关的 Kurt Cobain/Nirvana 内容 |
| 世界线创建 | ROOT 周杰伦，revision 1，9 nodes / 8 edges |
| 点击“屋顶”分支 | loading 期间继续探索按钮禁用；完成后 depth 1、visited 2、revision 2、12 nodes / 11 edges |
| 刷新 `/worldline` | 同一世界线 revision 2 和“屋顶”叙事恢复，checkpoint 已同步 |
| 移动与桌面布局 | 390×844 聊天/世界线可读；390 与 1280 宽度 document scrollWidth 均等于 clientWidth，无页面横向溢出 |
| 匿名歌曲播放点击 | 出现需登录提示，未实际播放 |

[搜索和聊天截图](browser-search-chat.jpg)、[移动聊天](browser-mobile-chat.jpg)、[移动世界线及无关检索说明](browser-mobile-worldline.jpg)、[刷新恢复后的桌面世界线](browser-worldline-recovered.jpg)。图谱长页面需要滚动，截图不等于所有图谱连线交互都已覆盖。

**真实质量问题：** 周杰伦请求的知识 sources/citations 虽完整，但命中了 Nirvana/Kurt Cobain。模型降级为音乐目录叙事并提示无关内容，减少了胡编，却仍没有满足知识相关性验收。必须排查索引覆盖、实体/查询过滤与检索排序，再用原请求复验，不能以 source 数量证明知识正确。

## 7. 未通过与未验证项

进入阶段 7 前必须修复并复验：入口生效的原搜索预算失败、Hybrid 明确双任务澄清、周杰伦知识检索相关性，并完成真实 Explorer → Curator 组合路径。冻结输入保留，修复后另建报告目录，不覆盖本次失败。

仍未验证：登录态实际音频播放、收藏/歌单账户写入、客户端 ACK 与恢复后动作重放、全部真实允许边、JEV 护栏生效阻断与生产流量误拦截、完整 WorldLine PostgreSQL restart 专项、并发负载/SLA、生产部署以及实际回滚演练。匿名环境与未授权切流限制了这些项，本次没有伪造登录状态或改默认开关。

未读取账户账单，没有完整主模型公开单价，因此不报告真实总成本。运行事件保留 provider usage 和耗时，仅覆盖本次隔离环境多轮请求，不能当作独立质量标注集。[脱敏运行事件](runtime-events.jsonl)只含已有白名单字段，无用户输入、reason/summary、凭据或请求头。

失败的测试诊断在写入证据前移除了 assertion/captured-log 正文，保留失败测试名称、计数、异常类别和文件 hash，以防配置断言暴露环境值。工作区 `.dev` 的随机数据库连接信息不进入报告或 commit。

## 8. 复现与清理

CLI 命令及环境边界见[实现说明](../../architecture/handoff-runtime-phase6.md)。业务验收必须使用真实服务、独立 PostgreSQL 和当前索引；离线测试不代替真实回放。`http-active-final.json` 的退出 2 是诚实失败门槛，不是脚本执行异常。

<!-- CLEANUP_RESULTS_START -->
本次启动的 ASGI、Java、Netease、Vite 进程已停止，8001/8080/13006/5173 端口已释放，临时浏览器页已关闭。独立测试 PostgreSQL 已停止但保留数据；原有 rehearsal 容器和 Qdrant 未操作。[清理记录](cleanup.json)。
<!-- CLEANUP_RESULTS_END -->

代码与报告各自独立提交，不 push。用户原有未提交状态保持，完整命令摘要、历史运行 hash、最终产物 hash、清理与门槛状态均见 [evidence.json](evidence.json)。

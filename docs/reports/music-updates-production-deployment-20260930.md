# Music Agent 更新上线记录

## 上线结果

- 网站：<https://47.83.162.191/>。
- 最终运行代码：`b1f56d36efe066d587d307fd2a54365add75a459`。
- 完成时间：2026-09-30 20:32:47（Asia/Shanghai）。
- 服务器 `/opt/moodflow` HEAD 与该提交一致，工作区干净。
- Java、AI 和前端更新已生效；PostgreSQL、Qdrant、NetEase、Java、AI、前端六个服务均 healthy，迁移容器退出码为 0。
- 最后部署记录：`/secure/moodflow/deployments/20260930T123124Z-b1f56d36.json`，`status=passed`。
- 最后备份记录：`/secure/moodflow/backups/manifests/20260930T123126Z-b1f56d36.json`，`status=complete`、`restore_verified=true`。
- 没有数据库 schema 变更。使用原有更新脚本、备份恢复校验和镜像回退机制，没有修改生产凭据。

## 已发布能力

1. `search_entities_tool`：统一检索 artist、album、playlist、song，归一实体引用并写入防幻觉引用上下文。
2. `get_playlist_detail_tool`：复用 Spring Boot 歌单详情接口，获取歌曲 ID、名称和歌手信息。用户歌单查询先获取可信歌单 ID，再查询详情。
3. 队列追加、下一首插播、专辑/歌单批量播放、按 ID 或序号跳转及清空队列。明确序号指令保留 Direct Actions 快速路径。
4. 红心云端同步、创建歌单、从歌单移除歌曲，与现有添加歌曲能力配套。服务端负责远程持久化，播放器动作仍等待客户端执行。
5. Spring Boot DELETE/CORS 与网易云登录会话有效性检查。
6. Foundation 嵌套子图 SSE 事件转发和前端增量展示，保持现有 MainGraph 和 Agent Loop。
7. 保留服务器已有的动作授权、混合意图及 Worldline 引用安全修复。

## 本次集成与上线修复

| 提交 | 内容 |
| --- | --- |
| `c763324` | 合并之前完成的功能与服务器更新；补齐新增队列/云端工具的用户动作授权检查，更新相关成功和失败路径测试。 |
| `511208b` | 统一搜索新增 playlist 后，Worldline 原有三类实体适配器会报错；适配器仅消费现有支持的 song/artist/album，保留工具四类搜索能力。 |
| `b1f56d3` | 浏览器发现公共玻璃样式覆盖 AI 面板的深色背景；提高面板样式选择器优先级，并增加回归检查。 |

首次 `c763324` 部署的 Worldline 公网 smoke 失败，失败记录保留在
`/secure/moodflow/deployments/20260930T120421Z-c7633242.json`。
修复后的 `511208b` 和最终 `b1f56d3` 两次部署均通过健康与公网 smoke 检查。

### 核心修改位置

- Python：`app/tools/music.py`、ToolCalling 的 `models.py`、`client_tools.py`、`direct_actions.py`、`graph.py`、`assistant.py`，以及实体/客户端动作 schema 和 Foundation streaming。
- Worldline：`ai-service/app/agent/foundation/subgraphs/worldline/music_service.py` 及新增兼容性测试。
- Java：`UserMusicController.java`、`NeteaseMusicService.java`、`AuthService.java`、`WebConfig.java`、相关 DTO、异常处理和测试。
- 前端：`aiChat.ts`、`aiClientActions.ts`、`songLikes.ts`、`playerStore.ts`、`authStore.ts`、`ai-chat.css` 及相关检查脚本。
- 新增授权回归测试：`ai-service/tests/test_tool_action_authorization.py`。

## 验证

验证在隔离的受管工作区中完成，再部署到生产。主工作区原有的未提交文件和数据保留。

| 命令 / 检查 | 结果 |
| --- | --- |
| `python -m pytest -q tests`，在 `ai-service` 目录执行，使用测试配置和历史评测所需冻结资源 | 集成版本 1332 passed、15 skipped、286 subtests passed；未通过删除、跳过或弱化测试规避失败。 |
| 工具、音乐资产和授权相关 pytest | 45 passed、40 subtests passed。 |
| Worldline 兼容修复相关 7 个测试文件，包括 API | 67 passed、2 subtests passed。 |
| `python -m pytest -q deploy/tests/test_update.py deploy/tests/test_deployment.py` | 49 passed。 |
| `python -m deploy.artifacts --root ai-service` | 20 个冻结资源验证通过，release `knowledge-v2-daa965c10984eefa`。 |
| 修改的 Python 核心与测试文件 Ruff 检查 | 通过。 |
| `npm run test:ai-stream`、`test:music-assets`、`test:ai-layout`、`test:ai-trigger`、`test:worldline` | 通过；最后的样式修复后再次全部通过。 |
| `npm run build`、`npm run lint` | 通过；已有 Vite 大于 500 kB 的 chunk 提示仍存在。 |
| Java Docker 构建中的 `mvn -B verify` | 通过，生产镜像构建成功。 |
| `git diff --check` 和提交前暂存 diff 检查 | 通过。 |
| `bash deploy/update.sh --dry-run`、`bash deploy/update.sh` | 使用原有发布流程；最终部署退出码 0。 |

### 公网真实接口验证

使用系统 TLS 校验、真实 NetEase 和实际配置的模型，没有浏览器 mock 或测试账号伪装。

- 周杰伦 artist 搜索成功。
- 叶惠美 album 搜索与专辑详情成功，返回 18 首歌曲。
- 非法实体类型返回 HTTP 400。
- 未登录创建歌单、红心写入、喜欢列表读取被 HTTP 401 拦截。
- “搜索歌手周杰伦”实际调用 `search_entities_tool`，返回可信 artist 引用。
- “跳到播放列表第 3 首”返回正确序号和带 action/task 标识的客户端动作。
- “查看我的学习歌单里面有哪些歌”在未登录时返回认证失败，不下发播放器动作。
- 真实模型 SSE 回答出现 67 个增量事件；首次正文 2.843 秒、最终结果 3.281 秒，增量拼接与最终正文一致。
- 最后更新脚本 smoke 中 `/`、`/api/auth/status`、`/healthz`、SSE 与 Worldline 全部通过；Worldline 首次尝试返回 9 个节点。

### 页面验证

- 在公网页面观察到回答尚未完成时已显示部分正文和“正在生成回答”，完成后输入框恢复可用。
- 桌面 1280×720、移动端 390×844 检查；没有页面横向溢出，移动端聊天面板占满可用宽度，输入与回答可用。
- 最后版本面板背景 CSS 实际计算值从公共样式的黑色 0.1 透明度恢复为 `rgba(4,10,14,0.76)`；实际 filter 为 `blur(32px) saturate(1.32) brightness(.78)`。
- 截图：`music-updates-deployment-assets/desktop.jpg`、`music-updates-deployment-assets/mobile.jpg`。
- 检查结束恢复了浏览器视口并关闭临时标签页。

## 限制与待验证项

- 没有使用真实登录账号执行红心云同步、创建歌单或移除歌曲的成功写入。相关模拟成功路径和公网未登录拦截已验证，真实账号联调仍待登录后验收。
- 浏览器附加试验“请用三段话说明适合学习时听的纯音乐特点，每段两句话”中，格式约束被路由视为独立的未知请求并要求补充说明。普通音乐解释请求正常；这项混合意图解析限制本次没有扩展修复。
- Python 全量测试的 15 项既有 skipped 保留；没有将这些项目计为通过。全量测试在兼容修复之前运行，后续适配器变更通过 67 项相关检查，最后 CSS 变更再次通过前端所有现有检查和构建。
- 测试资源与 cwd、Windows UTF-8/换行一致性在隔离工作区准备后完成测试，评测产生的无关文件没有提交或部署。
- 老页面可能仍加载旧资源；刷新页面即可获取本次前端。Java 重启后原有内存登录会话可能需要重新登录。

## 本地与发布范围

之前完成的功能提交已合并并 fast-forward 推送至 `origin/main`，没有强推或改写远程历史。
主工作区使用 `git merge --ff-only` 对齐最终运行代码，保留原有未提交修改、删除记录和未跟踪数据。
本上线记录与验收截图为本地交付记录，不属于生产运行代码变更。

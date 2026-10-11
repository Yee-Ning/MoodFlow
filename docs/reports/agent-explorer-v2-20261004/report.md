# Explorer v2：单体功能最终轮验收报告

## 1. 基线与范围

日期：2026-10-04。独立分支：`codex/agent-explorer-v2`；独立 worktree：`C:/Users/33994/.codex/worktrees/agent-explorer-v2/MoodFlow`。

原工作区 HEAD 为 `bca48e208ddbd40e56bd37295489b343fb0a82f4`，其未提交修改保留。以已完成的 Explorer v1 分支 `5e2b71aa4e1b83768c486beab78be7869cee4abf` 建立本分支，再合入已有 Curator v1 `970fad83c3497016a8305703382c20526aa00dfd`，实现基线完整 hash 为 **`b079fe0796b9b127657a0cf5b5019da29de06c99`**。GeneralChat v1、Curator v1、Explorer v1，以及 B/A/C/R2/D/T1T2/canary 准备逐一执行 `git merge-base --is-ancestor`，全部返回 0；见 [baseline.json](baseline.json)。未合入并行开发中的 Curator v2。

实现只改 Explorer 调用、Explorer 会话适配、偏好、根主题引导、Journey，以及共享 `context.py` 中 **Explorer 结果投影**的模型校验：既有会话分支可为 0～3 个，因此不能要求始终补齐三条分支。`agents.py`、Curator、GeneralChat、底层 WorldLine 知识服务、路由、转交边、Guard、账本、ACK 均未改。没有新增依赖或知识源；没有修改生产配置、部署或 push。

## 2. 三个功能

### 2.1 对话式分支探索与恢复

[explorer_session.py](../../../ai-service/app/agent/handoff/explorer_session.py) 适配现有 `WorldLineSessionService`，复用其 create / recover / explore、图合并、branch ID、visited、revision、重复探索限制及深度上限。第一次根实体探索完成后，采用已经验证的 WorldLine 结果建立会话，不重复调用目录或模型；后续从服务器 checkpoint 恢复原图，并以实际分支目标作为 `trusted_target` 程序化调用既有 WorldLine 子图。

Explorer 自有 checkpoint 保存“账号＋服务器 conversation/thread → WorldLine session”指针和已读取的目录回执；不采用用户文本或模型给出的 session ID。不同账号、不同对话不可借任务历史接管其他图。恢复不依赖上一轮 TaskResult 留在上下文内；同一 saver 下重建适配器或切换聊天 session 窗口仍可续探。生产复用既有 PostgreSQL checkpointer 和 WorldLine 分布式锁；本轮测试验证内存后端，未声称通过真实 PostgreSQL 重启验收。

分支名称/别名只匹配现有可用节点。“继续沿 R&B 分支”保持同一 session、根实体和历史来源，revision 从 1 增至 2，visited 增加 R&B。语义不明确且有多个选择时，返回节点选项并保留图，不猜分支、不重建。只有一条实际可用分支时，“继续沿这个分支探索”可直接续探。已经明确选中过节点后，“刚才那个节点展开讲讲”复用该选中节点的现有叙事与来源，图、revision、visited 不变，不重复请求目录。

**R&B 的能力边界：** 当前既有 Genre registry 将 R&B 标记为不可执行，只有 Hip-Hop 可使用 GenreExecutor。本轮不改 registry 或知识服务。Explorer 对具有匹配 registry 身份、类型、知识来源和关系 claim 元数据的 R&B 节点，允许只读聚焦其已经核实的关联歌手，调用原有 Era/Scene/Influence/Semantic Expansion 获取该歌手在此主题下的证据；保留选中的 R&B 节点及原图。没有启用 R&B GenreExecutor，也没有推断新歌手。没有新增有来源连接时，如实说明 `no_new_connections`。可执行类型继续使用原有 GenreExecutor。

保留原服务深度 3、节点/边/来源预算及错误规则。目录请求或 WorldLine checkpoint 写入失败时，保留上一版图并允许重试。歌曲映射保留各轮目录回执，各节点、目录边只能引用支持自身的来源，避免多轮来源串用。

### 2.2 红心个性化路径

[explorer_preferences.py](../../../ai-service/app/agent/handoff/explorer_preferences.py) 程序化调用现有 `get_liked_songs` 工具，只读红心 ID；有现成操作 journal 时通过该 journal 读取，不修改记账逻辑，fencing 冲突继续抛出。没有登录会话、读取失败、401 或非法回执标记 `unavailable`；合法空列表标记 `empty`。两者均诚实退化，不冒充用户没有偏好。

红心 API 只返回 ID，没有风格、年代和艺人。仅把实际回执 ID 与**本图已经核实的目录歌曲**关联，再沿已有、有来源且无冲突的关系加权；不推断图外歌曲属性，不额外采集数据。排序保留根实体导览，其余按权重优先，同权重按 v1 来源时间线，未知时间在同权重组内单列：

| 命中 | 权重 | 依据 |
| --- | --- | --- |
| 红心歌曲本身 | 4 | 实际红心 ID 与目录 ID、类型、名称一致 |
| 演唱艺人 | 3 | 有来源的 `performed_by` 关系 |
| 风格 / 时代 | 2 | 歌曲直接关系，或上述艺人的已接纳风格/时代关系 |

有真实回执命中的节点标注“因你红心收藏《X》……”，结构化 `narration.preference_song_ids` 记录实际匹配 ID；用户没有红心或本图没有可信匹配时维持 v1 通用时间线路径。无来源节点不会因权重进入路径。代表歌曲仍按 v1 来源与目录约束选择，不把偏好权重当成代表性证据。

### 2.3 流派、时代、场景根输入

[explorer_roots.py](../../../ai-service/app/agent/handoff/explorer_roots.py) 复用实体解析及 Genre registry。采用用户允许的**明确引导方案**：主题不是可信目录实体时，在目录搜索前返回 `NEEDS_INPUT`，说明可以从具体歌曲、歌手或专辑开始，再沿主题的有来源节点探索。

“探索90年代港乐”、R&B、Hip-Hop、爵士、2000s 时代、音乐场景等得到明确问题及空队列，不以失败响应或虚构图收场。原解析器会把“年代”等消费为查询后缀，因此主题检测保留原输入。明确声明的歌曲/歌手/专辑标题，即使含“爵士”“黄金年代”等词，也继续走既有目录解析。歧义实体仍由原 WorldLine 实体解析器澄清，不选择未核实实体。

## 3. v1 纪律与契约

节点必须有实际来源并与根实体通过受支持关系连通；缺失来源、孤立、冲突及伪造 metadata 不用于路径。知识节点无可信代表歌曲时仍是 `knowledge_only`、null ID，只讲解不播放。`catalog_fact` / `knowledge_evidence` / `unverified` 三态保留。

可播放歌曲仍需要服务器取得的真实目录记录，并验证 ID、类型、名称；形似 `netease:song:*` 的字符串和模型填写的 song_id 都不构成证明。受控验收复用已提交 Java 目录回执 [combo-final.json](../handoff-repair-20261001/combo-final.json) 中 `210049`（布拉格广场）、`5257138`（屋顶）。这些回执证明历史目录 ID，不证明本轮在线可播放性。测试中的知识文本和 2000/2004 时间戳是夹具，不是音乐史或真实发行年份结论。

v1 Journey 转交载荷继续为 `schema_version=1`、五字段 `journey_queue`，最多八节点且符合既有 2,000 字符摘要预算；新增偏好信息在 Explorer 的结构化 narration 内，不改变转交边、授权、播放行为。整条 Journey 被 Curator 消费、一键存歌单及 GeneralChat 状态承接属于独立集成轮，本轮未实现。

## 4. 验收与验证

新增 [test_explorer_v2.py](../../../ai-service/tests/test_explorer_v2.py)。现有 `test_handoff_runtime.py` 只调整两条 **Explorer 依赖故障测试**的输入：“探索时代”改为“探索歌手《周杰伦》的时代”，使其继续覆盖目录服务失败；原有失败、脱敏和已完成播放器动作保留断言不变。主题引导及组合请求的已完成动作保留另有新增测试，不删除或弱化原故障断言。

| 验收项 | 结果 |
| --- | --- |
| 周杰伦 → R&B，同图、revision、visited、来源、新节点及真实歌曲映射 | 已通过受控编译子图测试 |
| 账号/对话隔离，适配器恢复，Handoff 两轮聊天及新窗口恢复 | 已通过 |
| 已选节点讲解复用、唯一分支代词、多选/不存在/重复分支 | 已通过 |
| 目录失败重试、checkpoint 写失败保留旧图、既有深度 3 | 已通过 |
| 空红心 vs 实际工具回执红心产生实质顺序变化 | 已通过真实工具＋HTTP MockTransport，不是直接模拟偏好函数 |
| 艺人/风格/时代加权，401/非法/未匹配 ID，journal 与 fencing | 已通过 |
| 主题根输入引导、显式标题不误判、实体歧义及组合动作保留 | 已通过 |
| v1 路径、歌曲映射、转交与现有播放工具契约回归 | 已通过 |

验收快照见 [acceptance.json](acceptance.json)，其中目录、知识、红心后端均有受控数据标识，不冒充真实账号偏好或生产观察。

### 4.1 可复现命令

在本 worktree 使用已有虚拟环境，未安装新依赖：

```powershell
$python = 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe'
& $python docs/reports/agent-explorer-v2-20261004/validate.py --fixture-root 'D:/code/ProjectsAll/MoodFlow' -m pytest tests/test_explorer_v2.py tests/test_explorer_journey_v1.py tests/test_handoff_runtime.py tests/test_handoff_network.py -q --junitxml=../.dev/agent-explorer-v2/related-final.xml
& $python docs/reports/agent-explorer-v2-20261004/validate.py --fixture-root 'D:/code/ProjectsAll/MoodFlow' -m pytest tests ../deploy/tests -q --junitxml=../.dev/agent-explorer-v2/full-final.xml
& $python docs/reports/agent-explorer-v2-20261004/validate.py --fixture-root 'D:/code/ProjectsAll/MoodFlow' ../docs/reports/agent-explorer-v2-20261004/capture_acceptance.py
& $python -m ruff check ai-service/app/agent/handoff/explorer.py ai-service/app/agent/handoff/explorer_session.py ai-service/app/agent/handoff/explorer_preferences.py ai-service/app/agent/handoff/explorer_roots.py ai-service/app/agent/handoff/journey.py ai-service/app/agent/handoff/context.py ai-service/tests/test_explorer_v2.py ai-service/tests/test_handoff_runtime.py docs/reports/agent-explorer-v2-20261004/validate.py docs/reports/agent-explorer-v2-20261004/capture_acceptance.py
& $python -m compileall -q ai-service/app/agent/handoff/explorer.py ai-service/app/agent/handoff/explorer_session.py ai-service/app/agent/handoff/explorer_preferences.py ai-service/app/agent/handoff/explorer_roots.py ai-service/app/agent/handoff/journey.py ai-service/app/agent/handoff/context.py
git diff --check
```

[validate.py](validate.py) 沿用 v1 的离线验证准备：冻结评测输入按 HEAD 内容与原有锁准备字节；模型/索引/知识夹具仅复用已有测试材料并校验 SHA256，结束后恢复文件字节及生成报告。脚本不读取 secrets、不新增知识数据源、不构建新索引。不能同时运行两个验证进程。原始 JUnit/日志保存在 `.dev/agent-explorer-v2/`；版本化测试清单、跳过原因与输入 SHA256 见 [validation.json](validation.json)。

最终结果：

- 直接相关测试：**160 passed / 0 failed**，34.06 秒。
- AI 全测试＋`deploy/tests`：**2308 passed / 46 skipped / 0 failed / 0 errors**，另 **288 subtests passed**，302.82 秒。
- 新增 v2 **40 项**、v1 回归 **28 项**在全量运行中全部通过。
- Ruff、Python compileall、`git diff --check` 全部通过；286 条 warning 为既有 LangGraph 无 checkpointer 时 durability 不生效的提示。
- 46 项现有条件跳过：隔离 PostgreSQL 21、PostgreSQL smoke 4、PostgreSQL E2E 7、恢复进程 7、WorldLine PostgreSQL 重启 3、Qdrant 2、真实 TypeSafe API 1、缺失 Clean v0.2 历史夹具 1。未将跳过计为通过。
- 验收快照采集脚本执行成功；测试补入的两个未跟踪知识夹具按 provenance SHA256 校验后清理。评测冻结文件及自动生成报告恢复完成，不进入提交。

保留失败过程：根主题检测最初使用被解析器裁剪过的查询，导致两个“90年代港乐”测试失败，已改为原始主题检测；深度测试的默认生成器只列前三节点，无法列出新增歌曲，已改测试生成器为优先列出实际新增节点，业务上限与断言未放宽；初次全量为 2300 passed / 2 failed / 46 skipped，两项为上述旧故障测试输入与新引导语义冲突；新增 HTTP 层测试的导入路径及响应字段已按既有 streaming/answer 契约修正。相关测试与最终全量均重新运行。

## 5. 提交、限制与功能冻结

- 基线合并：`b079fe0796b9b127657a0cf5b5019da29de06c99`。
- Explorer 实现与测试：`f04da8e44a8b583b0992b4d3abe4cc596ea52d20`；只含 8 个 Explorer 实现/投影/测试文件。
- 报告及验收材料：独立 commit，以本报告最后一次修改的 Git commit 为准。

本轮是本地受控验收，未连接真实用户红心账号、收费 LLM、在线目录、Qdrant 或生产 PostgreSQL，不证明真实推荐听感、知识覆盖率、浏览器播放/ACK 或线上重启恢复。无需前端/Java/网易云服务构建，因这些服务没有本轮代码变化；相关 AI/部署测试统一回归。环境条件跳过项如实列于 validation.json，不计为在线验收通过。

本轮验收全部通过，确认 **Explorer 单体功能完结并冻结，不安排 Explorer v3**。后续工作仅为独立协作链集成轮（Curator 消费 Journey、存歌单等），不属于本轮交付。未部署、未 push；原工作区及 Curator v2 工作区改动保留。

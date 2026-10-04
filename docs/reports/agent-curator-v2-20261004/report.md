# Curator v2：冷门挖掘与年代场景补强验收报告

日期：2026-10-04。分支：`codex/agent-curator-v2`。工作树：`C:/Users/33994/.codex/worktrees/agent-curator-v2/MoodFlow`。

本轮完成知识扩展候选与歌曲年代证据筛选，保留 Curator v1 的目录 ID 校验、诚实导语、A 三态记账与 GeneralChat 四态表达。**Curator 单体功能完结，功能冻结，不启动 v3。** Journey 消费与导语润色承接属于独立集成轮，本轮未实现。

## 基线与提交

- 开始时主工作区最新 HEAD 为 `bca48e208ddbd40e56bd37295489b343fb0a82f4`，观测分支为 `codex/handoff-observability-canary-20261004`。
- Explorer v1 和 Curator v1 位于独立分支；新工作树从 Explorer v1 `5e2b71aa4e1b83768c486beab78be7869cee4abf` 建立，合并 Curator v1 `970fad83c3497016a8305703382c20526aa00dfd`，形成共同基线 `e671f27d92792304a3b310557a4bc46452592fbb`，包含 GeneralChat v1 与已有可靠性工作。未修改原工作区或其它分支。
- 实现与测试 commit：`fe54d46bc2e3812a15743195b78fafdebaeddf14`，只含以下四个文件。报告及复现脚本另行独立提交；其 commit 可通过 `git log -1 -- docs/reports/agent-curator-v2-20261004/report.md` 查询。

| 文件 | 改动 |
| --- | --- |
| `ai-service/app/agent/handoff/curator.py` | 识别冷门偏好、接入知识候选、目录核对、年代过滤、混排与诚实导语。 |
| `ai-service/app/agent/handoff/curator_knowledge.py` | Curator 专用程序化适配器，复用 WorldLine 检索与四类扩展服务。 |
| `ai-service/tests/test_curator_curation_v1.py` | 年代成功场景补入验证过的知识夹具；无年代证据仍断言零选曲、零创建。原有 ID、状态、记账及导语断言全部保留。 |
| `ai-service/tests/test_curator_curation_v2.py` | 29 个新增验收用例，使用真实关系校验器与 SQLite A journal。 |

业务增量仅涉及 Curator。未修改 `agents.py`、Explorer、GeneralChat、路由、转交边、Guard、记账、音乐服务工具、生产配置；没有新增依赖、知识数据源、采集或索引构建。未部署，未 push。

## 知识发现与冷门候选

1. 保留 v1 的红心读取、1–3 次目录搜索及两个受限模型请求。知识调用由程序发起，模型仍只能输出检索计划或已有候选的 ID；知识服务不暴露为 LLM tool。
2. 使用现有 `get_worldline_retrieval_runtime()` 的候选池、索引 profile、上下文适配器和配置的 reranker。`empty` / `shadow` 模式不向策展提供知识结果，不改动开关。整个知识发现有 12 秒上限，异常或超时诚实降级。
3. 从现有注册表解析 claim 的稳定知识实体。调用原 Era / Scene / Influence 校验器，继续要求足够上下文、可定位的原文、claim/chunk 一致、原有置信度阈值和冲突检查。知识图节点是知识身份，不是可写入的音乐 ID。
4. Scene 提供与用户场景或检索标签匹配的关联实体；Influence 从初轮目录中歌手的已验证影响关系发现另一端。Semantic 先通过原 JavaMusicEntityService 只读搜索确认种子歌手的目录身份，再分别校验种子和其它歌手的同一流派关联；两端都有来源才补入其它歌手线索。歌手核对最多 3 次。
5. 最多取 24 条知识线索，逐条使用已有 `search_music` 核对。曲名/歌手/专辑必须精确匹配可消歧的注册名称或别名；同名歌曲返回多个 ID 时不继承歌曲级年代证据。无目录结果、冲突 ID、无效 DTO 或不匹配的结果不会进入候选。
6. 只把核对后的目录对象提供给选择器，仍拒绝目录外 ID、重复 ID、模型工具调用、额外状态或导语字段。保留红心优先，并将实际选中的知识补充曲目与原候选交替安排。用户明确要求“冷门/小众/非热门”时提示优先选择适配的补充候选；一般场景也可进行深度补充。
7. `catalog_sources` 保留目录查询来源，`knowledge_sources` 保留知识 ID、关系种类、年代区间与原始 EvidenceSource（包括 claim、parent、child、原文及来源）；`knowledge_traces` 保留校验轨迹。`discovered_song_ids` / `selected_discovery_ids` 区分初轮搜索外的候选和实际选曲。

本轮冷门验收的可核对口径是“知识支持且初轮目录搜索未出现的补充歌曲”，并与纯搜索歌单逐 ID 比较。现有 `MusicTrack` 没有播放量、排行榜或热度字段，不能据此证明全平台低热度；程序和导语明确说明“未核实平台热度”，不虚构热门/冷门统计。这也是知识发现能力与纯热门搜索的实际差异。

## 年代场景补强

年代请求不再把曲名、专辑标题或查询词中的年份当作依据。只接受经 Era 服务验证、直接对应歌曲、完整落在请求十年内且无年代冲突的关联，并再次由目录核对歌曲 ID。歌手的活跃年代或专辑的年代不会自动成为每首歌曲的年代。

90 年代港乐的 10、15、20 首验收均移除了目录专辑里的年份；通过歌曲级知识发行证据取得候选，每首保留 `1990 <= start_year <= end_year <= 1999` 的可追溯来源。目录中虽有年份、但没有知识证据的边界仍返回不足；仅有 3 首证据时只创建 3 首，不降低标准凑到目标数量。

## 导语、写入与状态纪律

- 导语仍由程序生成，只引用已有有效加歌回执的实际曲目。知识补充部分最多点名两首实际加入的补充曲目；待确认或失败后的曲目不进入导语。没有编造歌曲属性、影响关系或热度。
- 默认 15 首，用户数量范围仍为 10–20；不足时明确显示实际数量，0 首不创建空单。知识无结果、不可用、证据不足或冲突时沿用诚实分支。
- `create_playlist_tool` / `add_song_to_playlist` 的执行仍经过原 ToolNode、实体来源校验和 A journal。缺 journal 不写入；unknown 或确定失败停止后续写入，不重试不确定操作。
- 本地工具调用预算为 49（v1 的 25 加最多 24 条知识目录核对）；原通用图、模型预算、Agent/Run 时限与 Guard 未改变。
- 四态继续使用 GeneralChat v1 的 `ResultFact` / `ResultState`。状态来源为 A journal；模型不能输出效果状态。后端成功回执仍是 `state=null`，不升级为客户端“已执行”或整张歌单“已确认”。只有 A readback 才给操作级确认，unknown 明确表达效果未知。
- 既有 `polish_request` 仍是预留数据接口，本轮不调用 GeneralChat 润色，也不消费 Explorer Journey 或改动协作链。

## 验收结果

专项测试：**82 passed，零失败**（v1 53 + v2 29），78.21 秒。

AI 服务全量测试与部署契约：**2297 passed、46 skipped、288 subtests passed，零失败**，361.87 秒。316 条 warning 均为现有 LangGraph 无 checkpointer 时忽略 `durability` 的提示。Ruff、格式检查、Python 编译检查和 `git diff --check` 全部通过。

| 验收项 | 结果 |
| --- | --- |
| 场景冷门补充 | 雨天夜跑初轮 10 首，经知识发现增加 5 首；逐曲验证目录对象、知识 claim 与来源，实际歌单区别于纯搜索；导语只提实际曲目。 |
| Influence / Semantic | 原校验器验证影响关系及双方风格关系；真实契约的歌手目录绑定之后，再通过 search_music 取得补充歌手的歌曲 ID。 |
| 90 年代港乐 | 无目录年份字段仍完成 10、15、20 首；每首有目标年代证据和目录来源。 |
| 年代边界 | 空知识、不可用、上下文不足、低置信度、缺原文、仅歌手年代、年代外、年代冲突、未知注册身份均零创建；有限证据不凑数。 |
| 歌曲身份 | 同名不同版本不继承年代；知识 ID 不能变成写入 ID；目录与模型的既有非法 ID 测试通过。 |
| 诚实纪律 | 成功回执、拒绝、unknown、readback、部分失败与导语引用边界全部保留；不宣称已经播放或所有歌曲已确认。 |
| v1 回归 | 雨天夜跑、90 年代港乐、深夜加班、火星殖民地无结果/不足，以及请求授权、数量、偏好、状态和 handoff 兼容性通过。 |

初次专项运行暴露 Scene fixture 的中文词边界问题：原 Scene 校验器拒绝没有可分辨词边界的证据，导致模型所选扩展 ID 不在候选中。测试改用来源明确、词边界可辨的原文后通过；没有修改校验器、放宽断言、跳过失败或把未验证 claim 当作候选。实现不改动原 WorldLine 对中文原文的保守限制。

## 复现命令与环境

在本工作树执行，使用原工作区已有 Python 环境。`validate.py` 复用 Explorer 已验证的离线验证流程，设置子进程参数而不读写生产配置。它复用原工作区已有知识/trace 夹具，校验冻结模型/索引文件；按既有 Windows 冻结字节要求准备测试输入，并在 `finally` 恢复原文件字节与测试生成的报告。复制的两份未跟踪知识夹具在全量结束后按 SHA256 校验并清理，未进入提交。输入来源摘要保存在本地 `.dev/agent-curator-v2/fixture-provenance.json`。

```powershell
$curatorPython = 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe'
& $curatorPython docs/reports/agent-curator-v2-20261004/validate.py --fixture-root D:/code/ProjectsAll/MoodFlow -m pytest tests/test_curator_curation_v1.py tests/test_curator_curation_v2.py -q
& $curatorPython docs/reports/agent-curator-v2-20261004/validate.py --fixture-root D:/code/ProjectsAll/MoodFlow -m pytest tests ../deploy/tests -q
& $curatorPython -m ruff check ai-service/app/agent/handoff/curator.py ai-service/app/agent/handoff/curator_knowledge.py ai-service/tests/test_curator_curation_v1.py ai-service/tests/test_curator_curation_v2.py docs/reports/agent-curator-v2-20261004/validate.py
& $curatorPython -m ruff format --check ai-service/app/agent/handoff/curator.py ai-service/app/agent/handoff/curator_knowledge.py ai-service/tests/test_curator_curation_v1.py ai-service/tests/test_curator_curation_v2.py docs/reports/agent-curator-v2-20261004/validate.py
& $curatorPython -m compileall -q ai-service/app/agent/handoff/curator.py ai-service/app/agent/handoff/curator_knowledge.py ai-service/tests/test_curator_curation_v1.py ai-service/tests/test_curator_curation_v2.py docs/reports/agent-curator-v2-20261004/validate.py
git diff --check
```

## 验证限制与冻结

- 本轮使用受控目录/模型与知识夹具，真实运行原关系校验器、音乐工具封装及 A journal。测试 ID 和发行年份是夹具，不冒充在线音乐目录、真实音乐史或线上可播放性验收。
- 没有调用真实音乐账号、收费模型、Qdrant 或浏览器播放器。46 项跳过沿用既有条件，涉及 PostgreSQL/重启、真实 TypeSafe API、Qdrant 及缺少 Clean v0.2 历史夹具；不计为这些环境的验证通过。
- 生产知识未启用或不覆盖所需歌曲时，仍会诚实返回不足。保留原服务的证据与消歧标准，不保证任意请求都能得到 10–20 首。
- 未实测推荐听感与线上时延；额外目录核对仍受原 Agent/Run 时限约束，串行建单/加歌可能被已有时限中止。未宣称平台热度已验证。
- 现有中文 Scene 词边界限制会保守拒绝部分原文，本轮没有越过 Explorer/WorldLine 的修改范围。
- **Curator 单体功能完结。** 本轮两个单体功能缺口已闭环；单体功能开发冻结。Journey 消费、GeneralChat 导语润色承接留给明确授权的独立集成轮。新增热度信息或听感评测的想法仅记入 backlog，不在本轮扩展、不启动 v3。

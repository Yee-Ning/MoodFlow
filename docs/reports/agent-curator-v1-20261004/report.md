# Agent 功能 v1：Curator 策展能力验收报告

日期：2026-10-04。分支：`codex/agent-curator-v1`。工作树：`C:/Users/33994/.codex/worktrees/agent-curator-v1/MoodFlow`。

## 基线与范围

- 开始时主工作区最新 HEAD 为 `e3571634c77d8509595d427cef47912a9c1559c0`，尚未包含独立 GeneralChat v1 分支。
- 隔离工作树以该 HEAD 建立，接入 GeneralChat 前置实现/报告 `583931b`、`55c3421`，对应本分支 `e93553b`、`ab1b333`。本轮增量以 `ab1b333` 为基线。
- 本轮实现 commit：`fb3ee0b11c4251e48194616849cba1cb77e92db2`（仅 Curator 入口、策展模块、专项测试三文件）。本报告与验证摘要另行独立提交。
- 增量仅涉及 Curator 承接入口、独立策展模块、专项测试和本报告目录。GeneralChat/Explorer、路由、转交边、Guard、A 记账实现、生产配置均未修改。未部署、未 push。
- 主工作区原有未提交改动未覆盖或提交。测试数据和临时日志仅保留在本工作树，未纳入提交。

## 策展逻辑

1. 在 `agents.py` 的 `tool_calling` 执行分支识别分配给 Curator 的场景歌单请求，其他任务沿用原路径。否定、搜索、播放、状态追问、已有歌单和明确空歌单等输入不进入本轮策展写入路径。
2. 使用现有 `get_liked_songs` 只读取得红心 ID。读不到时如实注明，不编造偏好；红心 ID 本身不能成为候选来源。现有四个工具没有可核对的播放历史接口，因此本轮不推断播放偏好。
3. 第一次模型调用只生成严格 JSON 检索计划：1–3 个 `search_music` 关键词、1–6 个场景标签。每次搜索最多 50 首，最多保留 60 个供选择的候选；按红心命中、歌名/歌手/专辑中的标签命中排序。
4. 搜索结果必须通过现有 DTO 验证，并匹配本次关键词和 limit。ID 必须是正整数，拒绝布尔值或数字字符串；相同 ID 出现冲突曲目信息时剔除，不由模型决定哪个名称可信。
5. 第二次模型调用只能从提供的候选 ID 中选择，最多达到用户请求数量。拒绝目录外 ID、重复 ID、额外状态/导语字段及模型工具调用。实际歌名、歌手、专辑均从原始目录对象还原；适配候选中的红心曲目优先安排。
6. 默认 15 首，明确数字请求支持 10–20 首；超出范围返回 `needs_input`，不执行工具。少于目标数量时明确显示“只找到 X 首符合的”；0 首时不建空单，不扩大场景凑数。
7. 调用现有 `create_playlist_tool` 建单，以其真实返回 ID 逐首调用 `add_song_to_playlist`。只绑定这两个工具及前述两个只读工具；复用 `_reference_errors` 校验实体来源，全部调用经过现有 `ToolNode` 和 `journal_tool_call`。没有 A journal 时拒绝执行。
8. 固定流程最多 25 次工具调用（红心读取 1、搜索 3、创建 1、加歌 20）。这是 Curator 策展专用上限；原通用图的工具上限、Agent 45 秒/Run 120 秒时限及模型预算均未改动。两个模型请求继续走现有预算与连续性记账封装。

### 属性证据边界

`MusicTrack` 只提供 ID、歌名、歌手、专辑、封面和时长，没有发行年、风格或情绪属性。场景/风格/情绪适配由模型基于这些目录字段作策展判断，导语明确表达为判断，不虚构 BPM、年代事实、歌词含义或歌曲关系。

年代请求另设保守过滤：必须在返回歌名或专辑中找到属于目标十年的年份标签；检索词中的“90 年代”不能证明曲目年代。没有标签时诚实返回候选不足。输出注明年份标签未经过发行档案核验；标签也不等于已证明原始发行日期。实际目录可能因此无法凑足 90 年代港乐，此轮不新增音乐元数据工具或 Explorer 协作。

## 导语与结果承接

- 导语由程序生成，说明用户场景、目录字段筛选依据及实际红心命中，只引用已有有效加歌回执的实际曲目。模型标签、任意理由、状态文字不直接进入导语。
- 失败/未知步骤后的曲目保留为待确认；导语不把这些曲目说成已加入。
- `data.curation.polish_request` 预留 v1 数据接口：`draft`、实际 `songs`、A 来源的 `result_facts`。本轮不调用 GeneralChat 润色，不增加转交边。
- 每首候选保留 `catalog_sources`，每笔写操作保留 A 的 `operation_id`。C 仍使用原 `tool_call_details`/TaskResult 生成组合投影，GeneralChat 继续通过既有 A/C 查询承接。

### 四态表达

复用 GeneralChat v1 的 `ResultState`、`ResultFact`，不由模型输出效果状态。

| 四态 | 证据与本轮表达 |
| --- | --- |
| 已生成 | 选曲已生成；客户端指令仍须执行/确认。本轮没有生成播放器动作，不宣称已经播放。 |
| 已执行 | 只能由对应客户端 applied ACK 证明。本轮云端建单/加歌不因此被标为已执行。 |
| 已确认 | 仅 A journal 的 `confirmation_source=readback` 证明只读核对后的目标效果；保留操作级确认。 |
| 未知 | A 的 unknown 写结果明确显示“效果未知，停止后续加歌，不重复写入”。 |

A 原始成功响应继续作为单独事实：`state=null`，说明“后端成功回执，未另行只读核对”。A 的确定拒绝同样单独表述，不转成完成。当前 A 创建歌单 readback 无法核实隐私参数，因此即使加歌读回确认，也不宣称整张歌单及所有歌曲全部确认。

## 验收结果

最终代码全量验证：**2116 passed、46 skipped、288 subtests passed，零失败**，用时 287.08 秒。其中本轮 Curator 专项 **53 个用例全部通过**。前一相关回归阶段为 199 passed、10 subtests passed，零失败；最终全量还覆盖了随后补充的 7 个请求识别边界用例。

Ruff、`compileall ai-service/app`、`git diff --check` 均通过。测试有 253 条既有 LangGraph 无 checkpointer 时 `durability` 无效的提示；未删除、跳过或弱化测试规避失败。机器可读统计与跳过原因保存在 `validation-summary.json`。

| 验收项 | 测试证据 |
| --- | --- |
| 雨天夜跑、90 年代港乐、深夜加班 | 受控目录 + 现有工具 + 真实 SQLite A journal；逐曲比对完整目录对象和搜索来源，验证创建/加歌提案及回执。 |
| 10–20 首 | 明确数量边界；默认 15；超范围澄清且零调用。 |
| 偏好 | 只优先安排搜索候选中的红心曲目，未搜索的历史 ID 不进入歌单；不可用时说明。 |
| 导语 | 引用曲名/ID 都在有加歌回执的集合中；部分失败不引用未确认曲目；不回显模型标签注入。 |
| 目录/模型失败 | 无效目录、错误查询、非法 ID、重复 ID、额外导语/状态字段、模型工具调用均不触发创建。 |
| 冷门与不足 | 火星殖民地 0/3 首；0 首不创建，3 首不补入虚构或无关曲目；选择器可拒绝全部无关候选。 |
| 三态记账 | 原始成功、确定拒绝、502 unknown、A readback；未知停止后续写入，再次请求不能重复派发原写操作。 |
| 兼容性 | 完整原 handoff 边执行策展，两个模型调用纳入原预算；既有音乐工具、通用工具图、GeneralChat 四态与 A 补账回归。 |

## 验证命令与环境

使用原工作区既有虚拟环境解释器；`validate.py` 只对子进程设置离线参数，不读写生产 `.env`。

```powershell
$python = 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe'
& $python docs/reports/agent-curator-v1-20261004/validate.py -m pytest tests/test_curator_curation_v1.py tests/test_music_asset_tools.py tests/test_tool_calling_contracts.py tests/test_tool_calling_subgraph.py tests/test_write_reconciliation.py tests/test_handoff_runtime.py tests/test_general_chat_results_v1.py -q --tb=short
& $python docs/reports/agent-curator-v1-20261004/validate.py -m pytest tests -q --tb=short
& $python -m ruff check ai-service/app/agent/handoff/curator.py ai-service/app/agent/handoff/agents.py ai-service/tests/test_curator_curation_v1.py docs/reports/agent-curator-v1-20261004/validate.py
& $python -m compileall -q ai-service/app
git diff --check
```

全量测试复用既有未跟踪的 `ai-service/data/knowledge/v2/knowledge_release_manifest.json`、`knowledge_enriched_chunks.jsonl` 和 `ai-service/evals/artifacts/gold_v2_trace_v3_{fallback,nav_context6}_20260916.jsonl` 夹具。Windows 冻结文件的 LF/混合 CRLF 字节按已有验收要求准备，并在完成后恢复正常 Git checkout 形式，不提交夹具或换行变化。环境未安装 pytest JSON 报告插件，未新增依赖，使用内置 JUnit XML 汇总结果。

本工作树可通过 `.dev/agent-curator-v1/prepare_fixtures.py` 准备上述本地夹具；该脚本及原始 `related.log/xml`、`full.log/xml` 属于本地验证产物，不纳入提交。共享解释器位于原工作区，本分支不新增依赖或复制生产配置。

## 限制与未验证项

- 本次使用受控目录/模型，不调用真实音乐账号、在线目录或收费模型；测试中的 ID 是受控服务夹具，证明来源约束，不冒充线上目录验收。
- 未验证真实歌曲的听感品质、真实 API 下 20 次串行写入能否在原时限内完成；已有 A 在中断/未知时保留回执，仍不允许无依据重复写入。
- 属性信息不足的年代场景会少选或返回 0 首。场景匹配仍依赖受限模型判断，没有通过真实听感评测证明推荐质量。
- 策展识别覆盖明确场景请求及本轮案例，不更改路由。未声称所有自然语言变体都能被现有入口路由到 Curator。
- 46 个跳过项包括 45 个 PostgreSQL/Qdrant/JEV 等在线条件验证，以及 1 个缺失的 Clean v0.2 历史项目夹具；具体原因和数量见验证摘要。没有通过本轮真实浏览器 ACK 验收。
- 已阅读主工作区存在的 `docs/architecture/moodflow-three-agent-capabilities-20261003.md` 及实际代码；仓库中未找到 `MULTI_AGENT_ARCHITECTURE.md`，未声称已核对其 §3.2。任务已按本轮明确要求推进。

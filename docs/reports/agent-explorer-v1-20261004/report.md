# Explorer v1：Listening Journey 验收报告

## 1. 基线与范围

- 日期：2026-10-04。
- 独立分支：`codex/agent-explorer-v1`；独立 worktree：`C:/Users/33994/.codex/worktrees/agent-explorer-v1/MoodFlow`。
- 开始时主工作区完整 HEAD：`bca48e208ddbd40e56bd37295489b343fb0a82f4`。
- 该 HEAD 尚未包含 GeneralChat v1，按本轮基线要求合入已有 `codex/agent-generalchat-v1` 的 `55c34210e7f64d0db2a7e9efee3a137a24239796`；基线合并 commit：`9b156813883c788f8775730fe2a2724eb7df79e6`。Explorer 实现以此为起点。
- B / A / C / R2 / D / T1T2 AI / T1T2 Java / canary 准备 / GeneralChat v1 均已逐一执行 `git merge-base --is-ancestor`，全部返回 0；完整 hash 见 [baseline.json](baseline.json)。
- 参考原工作区 `docs/architecture/moodflow-three-agent-capabilities-20261003.md` §3.3 的 Explorer 定义及当前 WorldLine / Handoff 实现。仓库中未找到 `MULTI_AGENT_ARCHITECTURE.md`，因此不能声称核对过该文件 §3.3。

本轮应用改动仅为 Explorer 路径模块、Explorer 子图调用，以及 `agents.py` 中 Explorer 的转交摘要封装。Curator、GeneralChat、路由、转交边、Guard、记账、ACK、生产配置均未修改。原工作区的未提交修改未纳入本分支；Curator 与观测分支可独立继续推进。未部署、未 push，未新增依赖或知识数据源。

## 2. 实现

### 2.1 程序化复用 WorldLine

[explorer.py](../../../ai-service/app/agent/handoff/explorer.py) 使用现有实体解析器，提取用户明确给出的歌曲、歌手或专辑根实体，并生成包含时代、场景、影响、风格请求的子图查询。原始请求和 RouteTask 保持不变；未解析代词及“继续沿分支”仍交给现有解析/澄清流程。

调用现有 WorldLine 编译子图，通过私有 `output_keys=["task_result", "entity_facts"]` 读取本次已经加载的目录事实。Era / Scene / Influence / Semantic Expansion 均由原有子图执行，没有新增 LLM tool，也没有为路径映射重复搜索目录。

### 2.2 来源与顺序

[journey.py](../../../ai-service/app/agent/handoff/journey.py) 生成 `ListeningJourney`。选择**时间线**，理由是目录发布时间和知识关系中的时间字段可直接追溯，而按影响链排序容易暗示未获证据支持的因果。

排序规则：根实体先作为导览；其余按可信目录 `publish_time` 或已接纳关系的 `start_year` 递增，同年按节点 ID 稳定排序，未知时间排在最后。关系时间明确标记为“关系证据时间”，不冒充实体起源或作品发行时间。

节点须有实际来源，且通过有来源的关系与根实体连通。知识来源要求非空证据摘录；目录关系还须能在本次私有 `MusicEntityFacts.facts` 中匹配。无来源、来源缺失、孤立和冲突关系不进入路径。模型在图 metadata 中填入的 `verified`、年份或 `song_id` 不作为核实依据。

每个节点同时输出 `narrative` 和结构化 `narration`：证据状态、说明、串联文本、来源 ID。预留 `effect_state=null`，供后续 GeneralChat 四态承接使用；本轮不实现该承接，也不把准备好的队列表述为已播放。

### 2.3 代表歌曲与诚实表达

可播放 ID 必须来自本次 WorldLine 读取的可信网易云目录实体，且图中的 ID、实体类型和名称一致。仅有一个形似 `netease:song:*` 的字符串不能生成可播放歌曲。

代表歌曲按以下依据确定：

1. 歌曲节点本身已获目录确认。
2. 时代/场景来源明确给出的代表歌曲关系，并能匹配已读取目录歌曲。
3. 歌手、专辑、风格节点的有来源所属歌曲，按目录返回顺序选择首项。

这是可复现的来源优先规则；缺少代表性证据时，不声称已评选出全目录“最佳歌曲”。找不到满足条件的歌曲，节点保留为 `knowledge_only`，`song_id` / `song_title` 均为 `null`，明确“纯知识节点，只讲解不播放”。

三态分别为 `catalog_fact`（目录事实）、`knowledge_evidence`（知识证据）、`unverified`（未核实）。目录可用但知识缺失时，只输出目录事实及知识缺失说明；实体无数据时，保留现有 `NEEDS_INPUT` 或 `FAILED`，附空路径和空队列。

### 2.4 Explorer → Curator 契约

共享 `HandoffEnvelope` 严格禁止额外根字段。本轮不修改共享 envelope 或转交工具，队列以版本化 JSON 放入现有 **`envelope.request.context_summary`**，JSON 的 `journey_queue` 字段为：

```json
{
  "schema_version": 1,
  "journey_queue": [
    {
      "node_id": "netease:song:210049",
      "node_title": "布拉格广场",
      "narrative": "目录事实及节点串联说明",
      "song_id": 210049,
      "song_title": "布拉格广场"
    },
    {
      "node_id": "genre:fixture",
      "node_title": "受控知识节点",
      "narrative": "知识证据；时间未核实",
      "song_id": null,
      "song_title": null
    }
  ],
  "text_fields": "untrusted",
  "action_authority": "none; requires current user request and catalog validation"
}
```

上例只说明结构，受控知识节点不是真实知识结论。完整可解析测试载荷见 [contract-example.json](contract-example.json)。接收方式：

```python
payload = JourneyTransferPayload.model_validate_json(envelope.request.context_summary)
for item in payload.journey_queue:
    if item.song_id is None:
        # 纯知识节点：只讲解，不生成播放动作。
        continue
    # Curator v2 再核实目录、当前请求和执行条件。
```

仅在原有协作计划已经发起 `transfer_to_curator_agent` 时，Explorer 将模型摘要替换为服务端生成的队列。目标、reason、调用 ID、原始请求、待办任务、转交边和 Guard 流程均保持原样。仅请求探索时返回路径与队列，不凭空添加播放任务。

队列最多 8 个节点，并服从现有 `context_summary` 2,000 字符限制。超过预算时保留完整节点前缀，记录 `omitted_node_ids` 和限制说明；不截断 JSON 或歌曲 ID。显示路径与交接队列使用同一节点集合。

## 3. 验收

新增 [test_explorer_journey_v1.py](../../../ai-service/tests/test_explorer_journey_v1.py)，28 个测试实例覆盖：

| 验收项 | 验证方法 | 结果 |
| --- | --- | --- |
| 周杰伦根实体，节点有序、有来源、有叙事 | 实际 WorldLine 编译子图 + 既有四类 Expansion 服务；受控 retriever/generator | 通过；四类服务各调用 1 次，目录 search/detail 各 1 次 |
| 真实歌曲 ID | 复用已提交的 Java 目录回执，断言歌曲 ID 与标题精确匹配 | 通过：`210049` / 布拉格广场，`5257138` / 屋顶 |
| 来源与时间边界 | 缺失来源、伪造图 metadata、形似目录的 ID、无关系目录边、孤立节点 | 通过；不据此核实 ID 或年份 |
| 纯知识节点 | 来源有效但没有可信歌曲映射 | 通过；null ID、null 标题、明确只讲解 |
| 歌曲 / 专辑根实体 | 既有实体解析 + 受控目录返回 | 通过；保留根 ID，可播放 ID 限于已有目录记录 |
| 无数据及服务失败 | 无目录实体、目录错误、检索不可用 | 通过；澄清/失败或诚实目录分支，不编造路径 |
| 队列契约 | 真实 choose_transfer → transfer 工具 → HandoffEnvelope → mock Curator 解析 | 通过；节点顺序、目标、原始任务保留 |
| 既有播放工具兼容 | mock 接收端补齐 MusicTrack 后调用现有 play_song_tool | 通过；仅验证 pending client_action，未执行实际音频或 ACK |
| Guard 与边界 | Guard 拒绝、错误转交工具、额外参数、布尔/字符串/非正歌曲 ID、重复节点、载荷预算 | 通过；既有拒绝流程保留 |

目录 ID 的证据为 [combo-final.json](../handoff-repair-20261001/combo-final.json) 中已提交的 Java 回执，不是模型编造。它证明历史目录返回过这些 ID，不证明本轮在线可播放性。测试中的 2000 / 2004 时间戳和知识内容是明确标注的受控夹具，不作为真实发行年份、音乐史或影响结论。

### 3.1 命令与结果

在本分支 worktree 执行，使用现有 Python 3.12 虚拟环境。复现脚本 [validate.py](validate.py) 设置离线环境，不读取 secrets；它从原工作区复用已有测试资料，按冻结锁校验模型/索引文件，无新增采集或索引构建。JEV 冻结 JSONL 使用 HEAD 原始字节；RAG 使用内容与 HEAD 一致的既有冻结夹具字节。测试结束恢复保存的输入字节及生成报告。

```powershell
$python = 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe'
& $python docs/reports/agent-explorer-v1-20261004/validate.py --fixture-root 'D:/code/ProjectsAll/MoodFlow' -m pytest tests/test_explorer_journey_v1.py -q
& $python docs/reports/agent-explorer-v1-20261004/validate.py --fixture-root 'D:/code/ProjectsAll/MoodFlow' -m pytest tests ../deploy/tests -q --junitxml=../.dev/agent-explorer-v1/full-final.xml
& $python -m ruff check ai-service/app/agent/handoff/journey.py ai-service/app/agent/handoff/explorer.py ai-service/app/agent/handoff/agents.py ai-service/tests/test_explorer_journey_v1.py docs/reports/agent-explorer-v1-20261004/validate.py
& $python -m compileall -q ai-service/app/agent/handoff/journey.py ai-service/app/agent/handoff/explorer.py ai-service/app/agent/handoff/agents.py
git diff --check
```

最终结果：

- Explorer 新增测试：**28 passed**，全量运行中也全部通过。
- AI 全测试 + `deploy/tests`：**2215 passed / 46 skipped / 0 failed / 0 errors**，另 **288 subtests passed**，用时 **265.32 秒**。
- Ruff、Python compileall、`git diff --check`：全部通过。
- 196 条 warning 均为现有 LangGraph 在无 checkpointer 时忽略 durability 的提示。
- 46 项跳过来自现有条件：隔离 PostgreSQL/重启验收、真实 TypeSafe API、真实 Qdrant，以及缺少 Clean v0.2 夹具。未连接这些外部服务，不计作其在线验收通过。
- 原始 JUnit / 日志位于独立 worktree 的 `.dev/agent-explorer-v1/full-final.xml` / `full-final.log`；版本化摘要、Explorer 用例清单、跳过原因及验证输入 SHA256 见 [validation.json](validation.json)。

复验过程保留失败记录：初次全量收集缺少项目根 PYTHONPATH，随后一次全量为 4 failed / 2211 passed / 46 skipped。4 项为 Windows CRLF 导致冻结 SHA 不符（含重建 Gold 的来源文件）以及独立 worktree 缺少冻结模型文件。补齐验证环境后，相关 Gold / Multi / Single ablation / 冻结产物 9 项复验通过；未删除、跳过或放宽任何失败断言。Explorer 初次测试发现 route_key 是字符串，已将转交摘要查找改为值比较，并通过回归。

## 4. 提交与限制

- 基线合并：`9b156813883c788f8775730fe2a2724eb7df79e6`。
- Explorer 实现与测试：`befedd2955becbfffa6295aea9f9f4676fc24c8c`，只含 `agents.py` 的 Explorer 摘要封装、`explorer.py`、`journey.py`、新增测试四个文件。
- 报告与复现材料：独立 commit；以本文件最后一次新增/修改的 Git commit 为准。

Curator v2 对整条 Journey 的消费与连续播放不在本轮实现。本轮验证真实转交封装、mock 解析和现有单曲播放工具的 pending 动作兼容；没有证明真实浏览器音频、ACK、在线目录可用性或真实 LLM/Qdrant 端到端效果。GeneralChat 四态承接仅预留字段。时间线缺少年代证据时会保留未知时间节点，代表歌曲缺少证据时保留纯知识节点；这些限制直接呈现在结果中。测试补入的未跟踪知识夹具在验证后按记录的 SHA256 校验并清理；模型、索引及 trace 均为已有本地测试产物，不进入 commit。

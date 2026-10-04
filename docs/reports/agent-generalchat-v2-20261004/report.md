# GeneralChat v2：结构化结果、当前歌曲对话与会话偏好验收报告

日期：2026-10-04。分支：`codex/agent-generalchat-v2`。工作树：`C:/Users/33994/.codex/worktrees/agent-generalchat-v2/MoodFlow`。

本轮实现 GeneralChat 的结构化事实接收与引用、只读歌曲对话、显式确认及撤销的会话偏好。**GeneralChat 单体功能完结，单体功能冻结，不启动 v3。** Curator `polish_request` 和 Explorer `narration` 的发送端实际接线属于独立集成轮，本轮只用结构化 mock 验证接收能力。

## 基线、提交与范围

- 从已冻结的 Explorer v2 分支 `89f1b0c` 建立独立工作树，合并 Curator v2 `08a809c`，形成共同基线 `6616490cd9684b44807fcb036d1f32ba3b029a98`，包含 GeneralChat v1、Curator v1/v2、Explorer v1/v2 及已有可靠性工作。
- 实现与测试 commit：`b3c97876d1337bcebead42a43f33f09f7d11ed23`，只含表中十个 GeneralChat 实现及测试文件。报告与复现脚本另行独立提交，避免报告内自引用 commit；其提交可通过 `git log -1 -- docs/reports/agent-generalchat-v2-20261004/report.md` 查询。
- 原工作区 `D:/code/ProjectsAll/MoodFlow` 的观测分支及未提交文件保持原状。

| 文件 | 改动 |
| --- | --- |
| `ai-service/app/agent/foundation/subgraphs/general_chat/structured.py` | 通用接收契约、两个发送端格式适配器、证据校验、分页及引用详情。 |
| `.../general_chat/session.py` | 会话隔离的临时状态、偏好提议/确认/撤销、历史与输出约束。 |
| `.../general_chat/song_dialogue.py` | 可信元数据解析、缺歌/切歌/候选澄清、自然陪伴。 |
| `.../general_chat/conversation.py` | 单体能力分发，结构化输入及详情按需读取。 |
| `.../general_chat/assistant.py`、`graph.py`、`state.py` | 接收入口、提示、结果数据保留、NEEDS_INPUT 与只读调用。 |
| `ai-service/app/agent/handoff/agents.py` | 仅 GeneralChat 回调：偏好上下文、歌曲元数据筛选、真实切歌状态及读歌失败表达。 |
| `ai-service/tests/test_general_chat_v2.py` | 三项能力、异常输入、事实来源与实际 handoff 回归测试。 |
| `ai-service/tests/test_general_chat_results_v1.py` | 收紧两个歌曲对话断言，保留四态、来源、澄清与只读约束。 |

没有修改 Curator/Explorer 实现、路由、转交边、Guard、记账、音乐工具或生产配置；没有新依赖、新数据源、部署或 push。`results.py` 的四态和 A/C 来源读取保持原样。

## X1：结构化事实接收与引用

入口是 GeneralChat 子图的 `structured_results: list[dict]`。它是程序提供的输入字段，不从用户 JSON、历史话术或模型输出解析。多个输入分别加命名空间，防止不同发送端的事实/来源 ID 碰撞。

通用格式示例：

```json
{
  "schema_version": 1,
  "facts": [{
    "id": "fact:1",
    "assertion": "来源记录了这一场景关联。",
    "kind": "knowledge",
    "evidence_refs": ["ref:1"],
    "detail": "按需读取的完整说明。"
  }],
  "sources": [{
    "id": "source:1",
    "title": "既有知识来源",
    "text": "原始资料：来源记录了这一场景关联。"
  }],
  "evidence_refs": [{
    "id": "ref:1",
    "source_id": "source:1",
    "excerpt": "来源记录了这一场景关联。"
  }]
}
```

- `kind` 为 `catalog / knowledge / effect / unverified`。前两种必须带证据引用；`unverified` 明确标注未核实。`effect` 可提供 `operation_ref`，但不能传入自称 `confirmed` 的状态字段。
- Pydantic 拒绝额外字段、重复 ID、不存在的来源、悬空引用和不属于原文的摘录。默认最多 256 条事实、512 个来源、1024 个引用；超出界限请求补齐或拆分资料，不静默截断。单条断言最多 2000 字符，完整详情/来源文本最多 20000 字符。
- Curator 适配器接收 `polish_request` 包装或直接的 `draft / songs / result_facts`。歌曲来自已有目录 DTO，ID 必须是合法整数；`draft` 不作为事实，发送端的效果状态不作为 ACK/readback。歌曲事实保留目录对象来源，操作事实只保留操作标识供 A/C 查证。
- Explorer 适配器支持单个 `narration`、`nodes[*].narration` 或 `journey.nodes[*].narration`，保留 statement、transition、source_ids 和来源原文。只接受原有三种证据类型；`preference_song_ids` 不成为用户已确认偏好。
- 程序按事实输出 `[F1]` 等稳定编号，并在关键断言后输出 `[S1]` 等来源引用及来源标题。正文按完整事实分界分页，预算约 8500 字符；原有普通模型回答的 2000 字符上限保持不变。
- 全部事实、来源和引用保存在会话接收对象及 `TaskResult.data.structured_facts`，包括未展示的页。详情不在第一页堆叠；“继续结果”读下一页，“F140 详情”“S1 原文”按标识读取，“刚才说的第二个是什么”按上一展示页的序号找回原事实、详情和引用摘录。
- 超长详情显式提示“继续详情”，分段读取直至原文末端；不会用省略号隐去尾部。非法新输入不附带缓存旧事实；旧的有效引用仍可单独追问。

四态继续来自 v1 的 `read_result_receipt`，即 A 的 journal/原动作 applied ACK 与 C 的组合状态投影。结构化效果断言仅按相同 `operation_ref` 的真实回执渲染；缺回执为未知。详情和来源原文中的完成说法也不能提升状态。来源中的用户偏好描述不参与会话偏好确认。

| 状态 | 允许表达与依据 |
| --- | --- |
| 已生成 | 指令已生成，待客户端执行/确认；依据 C 的待执行动作投影。 |
| 已执行 | 原动作有同会话、匹配引用的 applied ACK；不保证此刻仍在播放。 |
| 已确认 | A 的只读 readback 已核对具体操作。 |
| 未知 | 效果未知，缺少有效证据或 A 为 unknown。 |

后端成功响应未经 readback 仍只是收到成功回执，沿用 v1 的 `state=null`，不会新增第五种完成状态。旧的自由聊天文本摘要仍保留原 800 字符兼容路径；X1 使用独立的结构化输入和完整数据输出解决事实丢失，未改变发送端或冒充端到端接线已完成。

## 当前歌曲对话

只调用已有 `get_current_song_context`，继续经过原只读调用路径。模型可用工具集合不增加，GeneralChat 不执行播放、切歌或写入。

1. 无当前歌曲时明确说明没有可核对的歌曲，建议选一首再聊，也可以先陪用户聊心情；返回 NEEDS_INPUT。
2. 切歌指令存在但 ACK/readback 未支持完成时，先用 v1 回执表达已生成或未知，说明尚不能说已切换。不会读取计划中的歌曲后把目标元数据当作已经播放的证据。
3. 多候选时列出歌曲、歌手和 ID，让用户选编号。保存原问题以延续心情对话；补充选择后重新只读核对候选 ID，旧候选消失则重新澄清，不代选或执行播放。
4. 正常轻交流只引用歌名、歌手和专辑，标注 `get_current_song_context` 来源。歌词、故事、年代、风格等未核对细节不使用；可以回应用户自己表达的难过、疲惫，提出开放的问题，而不把这种心情归因于歌曲属性。“现在在听什么”“这是哪首歌”等口语问法同样读取可信信息；模型主动读取歌曲后，即使原问题没有歌曲关键词，也按可信元数据回答。
5. 指令目标资料明确称为“刚才指令的目标歌曲资料”；元数据不是当前播放状态的证明。读歌异常、非法 ID、身份冲突、重复候选或失败响应里的候选，都诚实请求再核对。

v1 原用例曾要求逐字复述模型的“这首歌的怀念感”，但该属性没有歌曲工具来源；本轮改为断言真实元数据、陪伴、来源及不出现无依据属性。个人工作进展仍保留情绪交流，若同时明确询问当前歌曲，则正常读取或说明缺歌，而不是把工作“完成”误当音乐操作完成。相关原断言没有删除、跳过或放松。

## 会话内偏好

- 只处理本轮显式提议，如“我喜欢爵士”。先问“我记下你喜欢爵士，好吗？”；此时仅保存 pending，不写入已确认列表，也不提供给模型作为偏好。
- 用户紧接着确认后才放入 confirmed。拒绝、取消、不相关输入或其它 Agent 已插入新一轮答复都会解除旧确认问题，防止稍后的“好的”误确认。
- 后续偏好查询直接读取 confirmed；普通陪伴的提示也只提供 `confirmed_session_preferences`。过滤带偏好记录的历史文字，并约束模型输出，避免历史或其它 Agent 的说法成为偏好权威。
- “别记这个了”撤销；多项时先澄清撤销哪项，也支持“撤销爵士”“清除全部偏好”。撤销后删除 confirmed，阻止后续模型重新引用已撤销值。
- 会话键只来自服务器已有的 conversation/session identity，优先使用活动 A journal 的身份。不解析用户文字里的身份，不从历史猜测偏好。缺会话 ID 时使用临时状态，不能跨轮记住；不同 session 不继承确认。
- 这是进程内临时状态，不写数据库、long-term memory 或跨会话存储。缓存最多 256 个会话，闲置 3600 秒过期；每会话最多 32 项已确认偏好。缓存淘汰、过期或进程重启会清空临时偏好，需重新确认。未宣称跨进程持久化。

## 验收结果

专项回归：**152 passed、2 subtests passed，零失败**，24.73 秒。其中新增 v2 用例 66 个；覆盖三项能力、v1 四态/NEEDS_INPUT/真实来源、GeneralChat 子图和 handoff runtime。

全量 AI 服务与部署契约：**2403 passed、46 skipped、288 subtests passed，零失败**，327.49 秒。354 条 warning 均为现有 LangGraph 在无 checkpointer 时忽略 `durability` 的提示。

| 验收项 | 结果 |
| --- | --- |
| 结构化输入与来源 | 通用 facts/sources/evidence_refs 带引用；Curator 和三种 Explorer 结构 mock 均可接收；不把用户 JSON 当可信输入。 |
| 复杂资料与追问 | 140 条长事实逐页全部可读，第 140 条可定位；12000+ 字符来源可读到末端；序号追问找回原文；编号紧邻中文的 F2详情 / 引用S2原文也能定位。 |
| 无效资料 | 缺来源、悬空引用、伪造摘录、重复 ID、额外状态、超限、非法发送端形状及越界编号均明确澄清。 |
| 效果诚实 | 发送端 confirmed 标签、原稿和来源里的播放完成说法不能提升效果；真实 C 已生成与原动作 applied ACK 使用原 v1 机制。 |
| 歌曲边界 | 无歌、切歌待 ACK、未知队列、多候选、选择后重新核对、服务失败和非法 ID 均有明确表达。 |
| 自然交流 | 保留对用户心情的陪伴；不输出模型或工具额外字段中的虚构歌词、背景、含义、发行年份；没有歌曲关键词但模型主动读歌的路径也不能补写无来源故事。 |
| 偏好流程 | 提议→确认→复用→撤销→不再引用；未确认、取消、其它会话、历史或外部 narration 均不能生效。实际 handoff 多轮流程也通过。 |
| v1 回归 | 四态、ACK 原动作/会话校验、backend 成功不升级、组合部分失败、澄清继续、只读工具约束保留。 |

Ruff、格式检查、Python 编译检查和 `git diff --check` 全部通过。`agents.py` 的 GeneralChat 回调以外内容逐段与基线比对一致；v1 的 `results.py` 与 `clarification.py` 保持基线内容。

## 复现与验证限制

在本工作树执行。`validate.py` 复用此前单体轮的离线验证流程：不读取生产密钥，只给测试子进程设置离线参数；复用原工作区已存在的知识/trace/锁定模型夹具，校验冻结输入，并在 `finally` 恢复原文件字节及测试生成报告。复制的两份未跟踪知识夹具在全量结束后按来源记录的 SHA256 核对并清理，不进入提交。来源摘要记录于本地 `.dev/agent-generalchat-v2/fixture-provenance.json`。

```powershell
$generalChatPython = 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe'
& $generalChatPython docs/reports/agent-generalchat-v2-20261004/validate.py --fixture-root D:/code/ProjectsAll/MoodFlow -m pytest tests/test_general_chat_v2.py tests/test_general_chat_results_v1.py tests/test_general_chat_subgraph.py tests/test_handoff_runtime.py -q
& $generalChatPython docs/reports/agent-generalchat-v2-20261004/validate.py --fixture-root D:/code/ProjectsAll/MoodFlow -m pytest tests ../deploy/tests -q -ra
& $generalChatPython -m ruff check ai-service/app/agent/foundation/subgraphs/general_chat/assistant.py ai-service/app/agent/foundation/subgraphs/general_chat/conversation.py ai-service/app/agent/foundation/subgraphs/general_chat/graph.py ai-service/app/agent/foundation/subgraphs/general_chat/session.py ai-service/app/agent/foundation/subgraphs/general_chat/song_dialogue.py ai-service/app/agent/foundation/subgraphs/general_chat/state.py ai-service/app/agent/foundation/subgraphs/general_chat/structured.py ai-service/app/agent/handoff/agents.py ai-service/tests/test_general_chat_results_v1.py ai-service/tests/test_general_chat_v2.py docs/reports/agent-generalchat-v2-20261004/validate.py
& $generalChatPython -m ruff format --check ai-service/app/agent/foundation/subgraphs/general_chat/assistant.py ai-service/app/agent/foundation/subgraphs/general_chat/conversation.py ai-service/app/agent/foundation/subgraphs/general_chat/graph.py ai-service/app/agent/foundation/subgraphs/general_chat/session.py ai-service/app/agent/foundation/subgraphs/general_chat/song_dialogue.py ai-service/app/agent/foundation/subgraphs/general_chat/state.py ai-service/app/agent/foundation/subgraphs/general_chat/structured.py ai-service/tests/test_general_chat_v2.py docs/reports/agent-generalchat-v2-20261004/validate.py
& $generalChatPython -m compileall -q ai-service/app/agent/foundation/subgraphs/general_chat ai-service/app/agent/handoff/agents.py ai-service/tests/test_general_chat_v2.py ai-service/tests/test_general_chat_results_v1.py docs/reports/agent-generalchat-v2-20261004/validate.py
git diff --check
```

- 结构化能力使用内部 mock，真实 Curator 发 polish_request、Explorer 发 narration 的端到端链路尚未接线或验证，按要求留给集成轮。
- 模型、目录、只读 provider 使用受控夹具；A 的来源与 ACK 约束运行真实 SQLite journal。未调用真实音乐账号、收费模型或浏览器播放器，未实测线上听感/自然语言模型质量与时延。
- 46 项跳过沿用原有环境条件：42 项需要隔离 PostgreSQL/真实数据库及重启验收；2 项需要真实 Qdrant；1 项需要真实 TypeSafe API；1 项缺少 Clean v0.2 历史夹具。没有为本轮新增跳过，不能算这些环境验证通过。
- 临时会话状态有上述进程、容量及闲置期限限制；本轮不提供跨会话或长期记忆。

## 功能冻结

**GeneralChat 单体功能完结，单体功能冻结，不启动 v3。** 本轮三个单体缺口已闭环。Curator 导语润色请求和 Explorer narration 的实际发送接线留给独立集成轮；跨会话持久化留给 Context Engineering 的 long-term memory 阶段。未扩大本轮范围；后续新想法仅进入 backlog，不在本轮开发。

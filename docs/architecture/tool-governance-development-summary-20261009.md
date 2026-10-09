# MoodFlow 工具治理模块开发总结

整理日期：2026-10-09。源码基线：`ea2b50c`；核对当前工作树。本文总结已有实现及其边界，不新增业务代码或改变运行配置。

## 1. 核心结论：工具实现共用，工具权限按 Agent 和任务分配

项目采用 **共享工具实现 + Agent 职责分工 + 当前任务工具裁剪 + 共享执行治理** 的组织方式。

音乐服务工具和播放器工具集中定义，Curator 复用这些定义；GeneralChat、Curator、Explorer 获得不同的工具视图。任务内的歌曲上下文工具和转交工具由工厂生成，绑定当前可信状态。它们共享实现方式，但不是所有 Agent 都能自由访问全部工具，也没有为每个 Agent 复制一套音乐操作实现。

当前“工具治理模块”是一个逻辑模块，分布在工具定义、子图执行、handoff、continuity 和前端动作处理层中；尚无单独的统一 Tool Governance 包。

### 1.1 三个 Agent 的实际工具视图

下表描述 **handoff 三 Agent 模式** 的源码行为。运行时是否启用该模式取决于入口和配置，本次未检查生产开关。

| Agent / 能力 | 业务工具或内部能力 | 转交工具 | 绑定与权限边界 |
| --- | --- | --- | --- |
| GeneralChat / `general_chat` | `get_current_song_context`；存在多步骤组合任务进度时增加 `get_combination_task_status` | `transfer_to_curator_agent` | 只读歌曲上下文和已有任务状态；没有音乐写入或播放器工具 |
| Curator / `tool_calling` | 共用的 12 个音乐服务工具、18 个客户端动作工具，另加歌曲上下文工具；一般任务候选共 31 个 | `transfer_to_general_chat_agent`、`transfer_to_explorer_agent` | 模型和执行器使用同一份候选列表；当前任务可继续缩小集合 |
| Curator / `current_song` | 程序化调用歌曲上下文工具，然后运行只读分析模型 | 同上，转交阶段另行绑定 | 歌曲分析分支不把音乐写入和播放器工具绑定到分析模型 |
| Explorer / `worldline` | 按需程序化读取歌曲上下文，复用 WorldLine 的实体解析、目录事实、知识检索、图构建、叙述与分支生成 | `transfer_to_curator_agent` | 没有自由调用 Curator 工具池的通用 ToolNode 循环；内部工作流负责调用依赖 |

业务模型与转交模型分阶段绑定。`choose_transfer()` 只绑定该 Agent 被允许的转交工具，不能在中转任务时顺便调用音乐写入工具。仅允许四条边：GeneralChat→Curator、Curator→GeneralChat、Curator→Explorer、Explorer→Curator。

`AGENT_REGISTRY` 声明能力归属、工具域和转交边；`CAPABILITY_OWNERS` 与运行图检查任务 owner。实际业务工具列表由 `execute_owned_task()` 的能力分支及 scope 函数组装。`task_tool_domains()` 是领域约束辅助函数，不是一个自动拦截所有底层调用的全局工具网关。

### 1.2 工具列表还会按任务缩小

| 当前任务 | 模型与执行器可见集合 |
| --- | --- |
| 明确的单一目录搜索 | `search_music`、`search_entities_tool` |
| 简单的指定歌曲播放 | `search_music`、`play_song` |
| 混合操作、需要详情或其他不满足收缩条件的任务 | 保留相应原候选集合，继续执行参数、实体与授权校验 |

这里的裁剪属于保守规则：不能因识别成“搜索”而误删用户明确要求的其他操作。工具在候选列表中也不等于已经获得执行授权，后续校验仍然必需。

### 1.3 Foundation 模式的区别

默认 Foundation 架构按 `general_chat`、`current_song`、`tool_calling`、`worldline` 四个能力子图派发，不应直接等同于三个 handoff Agent 的工具绑定。

- Foundation 的 GeneralChat 默认助手是无工具聊天；handoff 为其增加受限的只读工具适配。
- Foundation 的 `tool_calling` 子图复用同一批 30 个基础工具及执行治理，并采用搜索/播放范围裁剪。
- `current_song` 先程序化读取可信歌曲上下文，再调用分析模型。
- `worldline` 复用相同的探索工作流。

## 2. 共用工具定义清单

### 2.1 音乐服务工具：12 个

定义位置：`ai-service/app/tools/music.py` 的 `MUSIC_AGENT_TOOLS`。

| 类型 | 工具名 | 职责 |
| --- | --- | --- |
| 读取 | `search_music` | 搜索歌曲 |
| 读取 | `search_entities_tool` | 搜索歌手、专辑、歌单等目录实体 |
| 读取 | `get_song_lyrics` | 获取歌词 |
| 读取 | `get_user_playlists` | 获取当前用户歌单 |
| 读取 | `get_playlist_detail_tool` | 获取歌单详情及歌曲 |
| 读取 | `get_album_detail_tool` | 获取专辑详情及歌曲 |
| 读取 | `get_liked_songs` | 获取当前账号云端红心歌曲 ID 集合 |
| 写入 | `add_song_to_playlist` | 将歌曲加入可编辑歌单 |
| 写入 | `set_song_liked_tool` | 设置云端红心状态 |
| 写入 | `create_playlist_tool` | 创建指定名称和隐私设置的歌单 |
| 写入 | `remove_song_from_playlist_tool` | 从目标歌单移除歌曲 |
| 写入 | `delete_playlist_tool` | 删除经用户确认的自建歌单 |

当前为 7 个读取工具和 5 个写入工具。旧文档中的“10 个音乐工具”已经不能代表当前源码。

### 2.2 客户端动作工具：18 个

定义位置：`ai-service/app/agent/foundation/subgraphs/tool_calling/client_tools.py` 的 `CLIENT_ACTION_TOOLS`。

| 分组 | 工具名 |
| --- | --- |
| 播放控制 | `pause_player`、`resume_player`、`play_next`、`play_previous` |
| 音量与进度 | `set_volume`、`set_muted`、`seek_to`、`seek_by`、`set_play_mode` |
| 播放与队列 | `play_song`、`insert_next_track`、`append_to_queue`、`play_album`、`play_playlist`、`jump_to_queue_item`、`clear_queue` |
| 状态与界面 | `set_liked`、`open_lyrics` |

这些工具产出 `pending_client_execution` 指令，实际执行在浏览器。`set_liked` 用于应用后端已确认的红心状态，前端执行器调用 `applyLikedState()`，避免再次写云端；它不能代替 `set_song_liked_tool` 的后端写入。

### 2.3 动态工具

- `create_song_context_tool()` 复制当前可信播放器上下文和前置任务结果，生成无业务参数的只读工具。三个 Agent 可以复用这个工厂，但每次得到的是绑定当前任务状态的实例。
- `create_combination_status_tool()` 基于已有结果生成只读组合任务状态查询，按条件开放给 GeneralChat。
- `create_transfer_tools()` 按 source Agent 和当前 run 生成允许的转交工具；当前状态由服务端持有，模型只提出 reason 和 context summary。

`all_tool_calling_tools()` 将两份基础定义列表合并为 30 个工具，不复制其业务逻辑。`app/tools/executor.py` 中仅注册 `search_music` 的早期执行器，也不是当前三 Agent 的全量工具注册表。

## 3. 执行流程与模块分工

普通模型工具调用的主要路径：

```text
任务归属与工具范围确定
  → 模型选择工具
  → 白名单 / 参数 / 实体来源 / 用户意图 / 重复请求校验
  → OperationJournal 记录提案并检查已有回执
  → ToolNode 调用音乐服务或生成客户端指令
  → 校验并记录工具结果；必要时只读对账
  → 生成 TaskResult 与公开响应；客户端指令等待 ACK
```

明确的暂停、音量等请求还可走 `direct_actions` 确定性路径，减少模型选择；当前歌曲读取和 Explorer 的内部工作流也不需要自由工具循环。因此治理由不同层协作完成，不能理解成所有调用都强制经过同一个 ToolNode。

| 层 | 主要文件 | 已实现职责 |
| --- | --- | --- |
| 工具定义与音乐 HTTP | `app/tools/music.py`、`app/schemas/music.py` | 工具参数、结果模型、可信会话注入、请求超时与错误转换 |
| Agent 能力与绑定 | `agent/handoff/registry.py`、`agents.py`、`search_scope.py`、`tool_calling/scope.py` | owner、允许边、只读边界及当前任务候选集合 |
| 通用工具子图 | `tool_calling/graph.py` | 执行前校验、整批拒绝、调用次数限制、ToolMessage 配对、最终失败判定 |
| 操作账本 | `long_horizon/operations.py`、`public_events.py` | read/write/client_action 分类、稳定操作 ID、提案与回执、结果复用 |
| 写入对账 | `long_horizon/write_reconciliation.py` | 只读核对外部效果，保存证据并补记成功，保留未知结果 |
| 执行与恢复 | `long_horizon/execution.py`、`coordinator.py`、`runner.py`、`recovery*.py` | 会话锁、执行租约、检查点、当前事实核对与恢复决策 |
| 转交治理 | `handoff/tools.py`、`state.py`、`guard.py`、`continuity.py` | 转交边/任务/预算校验、可选语义护栏、转交账本及完整性核对 |
| 前端动作闭环 | `services/aiClientActions.ts`、`store/aiChatStore.ts`、`services/aiChat.ts` | 动作去重、实际执行、失败显示、applied/failed ACK |

表中列出主要文件名与路径片段；完整位置见第 8 节源码链接。

## 4. 已实现的治理机制

### 4.1 执行前校验

模型与执行器绑定同一份 allowlist。工具参数经 Pydantic / StructuredOutputService 校验，多余字段和非法参数被拒绝；登录会话通过 `ToolRuntime` 注入，模型参数不能指定 `user_id`、cookie 或 runtime。

歌曲、歌单、专辑等 ID 必须来自可信当前状态或已有工具结果，模型不能编造 ID。完整专辑/歌单播放使用已验证详情结果；移除歌曲要求本轮读过目标歌单并确认歌曲存在；删除整张歌单要求准确目标 ID 的本轮用户确认、归属基线和 OperationJournal。

工具结果与历史消息可以提供实体信息，但不能授权本轮播放、收藏或歌单写入。当前任务文本中的否定也参与动作授权校验。

可解析的同一批模型调用包含无效、越权或重复请求时，执行器为整批返回配对的错误 ToolMessage，不会先执行其中看似合法的一部分。不可解析的模型工具输出也会停止执行。这里保证的是**执行前整批校验**，不是外部 API 的整批事务。

### 4.2 调用次数、超时和预算

- 模型工具循环的调用上限为 `MAX_TOOL_CALLS = 5`；同名同参数的已尝试调用不能反复执行。修改了无效参数的请求仍需重新通过校验。
- 音乐 HTTP 请求使用配置的超时；超时、连接不可用、HTTP 拒绝、响应格式错误转换为工具错误。
- handoff 默认限制为最多 5 次转交、每 Agent 最多 1 次重试、Agent 执行窗口 45 秒、整轮 120 秒、每 Agent 64,000 token、整轮 128,000 token。
- 模型请求的瞬时故障重试由 LLM runtime 和账本计数控制；音乐业务 HTTP 请求没有通用自动退避重试。
- 恢复沿用原运行的时间和预算，不能通过重启或恢复重置额度。

默认值不代表当前所有部署的实际参数，已保存运行也可能使用创建时的 limits。

### 4.3 失败分类与结果真实性

| 情况 | 处理 |
| --- | --- |
| 未知工具、非法参数、无可信实体或未获授权 | 执行前拒绝并反馈具体错误 |
| 读取超时或依赖不可用 | 返回工具错误；实际执行失败影响最终任务状态 |
| Spring 入口明确拒绝写入：400、401、403、422 | 账本记录 `failed`，不重复派发同一操作 |
| 写入超时、断连、502、冲突或成功响应无法校验 | 账本保留 `unknown`，尝试只读核对，不因传输错误认定外部未生效 |
| 无有效成功回执却被模型描述为成功 | 最终输出按实际工具结果判定，写入失败/未知不会被模型文本改成成功 |
| 部分步骤成功、后续失败或预算耗尽 | 保留已完成结果和已生成动作，说明后续未全部完成 |

账本事件内部使用 `proposed`、`success`、`failed`、`unknown` 等状态；对外操作状态视图映射为 `confirmed`、`failed`、`unknown`。`confirmed` 可来源于原始响应，也可来源于只读核对证据。

### 4.4 去重与未知写入对账

稳定 operation ID 基于 run、工具名和规范化参数计算。相同逻辑写入即使更换模型 tool call ID，也会找到原提案及回执；跨轮存在尚未解决的相同写入时，会沿用旧操作核对，不能把新 run 当成再次派发的授权。

已成功操作复用经过完整性校验的结果。只有提案、没有结果，或结果仍未知时，系统核对外部事实，不重发原写请求。

| 写工具 | 只读核对方式 / 边界 |
| --- | --- |
| 加入歌单 | 核对目标歌单可编辑性和详情，目标歌曲存在即可确认请求的成员状态 |
| 移除歌曲 | 核对归属及完整歌单，完整结果中目标歌曲不存在才确认 |
| 云端红心 | 读取当前账号红心 ID 集合，核对目标布尔状态 |
| 删除歌单 | 先保存账号/归属/歌单类型基线；之后核对同一账号自有歌单列表中目标消失；成功响应本身也仍需读回确认 |
| 创建歌单 | 记录创建前基线；即使发现新增同名歌单，现有读 DTO 无法证明 privacy 等完整参数时仍保留 `unknown` |

例：加入歌单返回 502，但读回发现歌曲已经存在，系统保存核对证据并追加成功回执，原始错误记录保留，实际 POST 不重复执行。

这个机制提供应用侧重复派发防护与后置条件证明，不构成外部供应商的端到端 exactly-once 保证。

### 4.5 客户端动作和 ACK

动作有稳定 `action_id`，前端使用动作 ID 与内容签名去重。同 ID 不同内容被拒绝；相同动作复用已保存的执行 Promise。当前去重缓存仅保存在浏览器内存中，最多记住 500 个动作。

浏览器执行后通过 `/api/ai/actions/ack` 上报 `applied` 或 `failed`。服务端核对图、会话、账号及动作归属，并记录 ACK。动作生成成功、浏览器执行成功、服务端 ACK 保存成功是不同阶段。

ACK 丢失时，恢复不能伪造执行成功，也不能仅凭缺回执自动重新发出可能已生效的动作。页面刷新后的去重和 ACK 补传尚不是持久化自动闭环。

### 4.6 持久化、并发与恢复

| PostgreSQL 表 | 治理用途 |
| --- | --- |
| `agent_history_event` | 工具提案/结果、客户端指令/ACK、预算、重验证等不可变事件 |
| `agent_artifact` | 工具结果、写入前基线及只读核对证据 |
| `agent_execution` | 执行 owner、租约、fence 和 revision；PostgreSQL advisory lock 配合防并发 |
| `agent_task`、`agent_session`、`agent_handoff` | 任务、会话与交接归属 |
| `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` | 运行状态与续跑依据 |

`OperationJournal` 在每个执行请求中绑定当前身份和 accounting，通过 ContextVar 接入执行；共享存储不等于不同用户共用一个可自由访问的操作状态。查询和对账会检查原操作的账号/会话作用域。

恢复先核对检查点/账本证据、当前权限、外部效果和原预算，再决定续跑、补充只读事实、请求输入或停止。补回执不是重新执行业务写入。当前 journal 重建得到的 RecoveryCheckpoint 也不等同于恢复了可直接执行的全部图状态。

### 4.7 转交护栏与公共数据保护

允许边、下一任务 owner、顺序、时间、预算和跳数先由本地契约检查。可选 JEV 护栏再评估转交语义；Shadow 模式只记录判断。JEV 不可用时，在已通过本地契约后执行一次本地回退，其放行不等同于通过语义审查。

公开历史和工具产物使用类型模型、字段/大小约束、内容摘要与敏感文本检查。面向模型的通用异常返回公共错误；内部 Python 日志仍可能包含堆栈，不能据此宣称整个项目已经具备统一日志脱敏平台。

## 5. 共享治理的覆盖边界

正常 Foundation / handoff 请求通过 coordinator、runner 设置操作账本、执行身份和会话锁，模型 ToolNode 调用经 `journal_tool_call()` 包裹。handoff 的歌曲上下文程序化读取也通过 `invoke_song_context()` 接入账本。

以下路径需要区分：

1. Explorer 的目录服务复用 `MusicServiceClient`，但知识检索、目录读取和图生成由内部工作流直接调用，不统一进入 Curator 的 ToolNode / OperationJournal 包裹链。
2. 组合任务状态查询读取服务端已有结果，其工具调用没有使用相同的 OperationJournal 包裹函数；不能据此断言所有内部读取都有统一的 operation 事件。
3. 独立子图测试或直接调用底层工具时，`active_operations` 可能为空，普通工具会直接执行；整张歌单删除另有强制 journal 检查。
4. 早期 `app/tools/executor.py` 不提供当前完整的操作账本、客户端 ACK 和三 Agent 域治理，应避免把它作为新工具接入的默认执行层。

所以“共用治理”是已接入主运行链路的模块复用，尚不是覆盖所有入口、所有内部依赖的强制统一网关。

## 6. 已知缺口与后续开发方向

| 缺口 | 现状与建议 |
| --- | --- |
| 工具元数据分散 | 工具列表、WRITES、动作授权和结果 schema 分布在多处；后续可统一 ToolSpec 元数据，校验工具域、effect、授权和结果 schema 的一致性，继续复用现有实现 |
| 调用覆盖不统一 | 明确哪些内部读取需要操作审计；新增外部写工具优先经过账本，避免新增绕过主入口的派发路径 |
| 未知创建结果无法完整核实 | 补齐读取 DTO 或供应商可核验信息；信息不足时继续保留 unknown，不能放宽成功判定 |
| 客户端 ACK 缺持久化补传 | 在保持同一 action ID 和内容签名的条件下设计 ACK outbox；补传回执与重新执行动作必须分开 |
| 音乐服务无通用退避/熔断 | 可为幂等读取增加明确的可重试错误、退避、总时限和熔断；写入仍须按操作性质和结果证据处理 |
| 外部多步骤没有自动回滚 | 保留并展示部分成功；如需要补偿，应为具体业务设计且核验补偿权限，不能用数据库回滚推断外部效果已撤销 |
| 指标与审计未统一 | 现有日志、事件和转交观测分散；可统一工具延迟、失败码、unknown 数、读回确认率、重复拦截及 ACK 缺口指标 |
| 生产验证不完整 | 按真实依赖补充超时、写后断连、重启、账号变化和 ACK 丢失演练；受控测试通过不代表生产全链路已验收 |

上述为后续方向，本次只整理文档，不实施工具平台重构、重试策略、数据迁移或配置变更。

## 7. 验证记录

### 7.1 本次新增核对

使用 Python AST 读取工具定义列表，核对音乐工具 12 个、客户端动作工具 18 个，合并后 30 个唯一名称，Curator 加入歌曲上下文后的通常候选数为 31。核对 Agent 注册表、实际绑定分支、两种 scope 裁剪及转交允许边。

本次绑定、隔离与 ACK 定向测试命令，在 `ai-service` 目录使用既有 `.venv/Scripts/python.exe` 执行：

```powershell
python -m pytest tests/test_handoff_contracts.py tests/test_handoff_runtime.py tests/test_handoff_search_scope.py tests/test_handoff_direct_actions.py tests/test_action_ack_namespace.py tests/test_handoff_network.py::test_curator_relay_does_not_bind_or_execute_music_business_tools tests/test_handoff_network.py::test_explorer_current_song_uses_scoped_read_tool_and_skips_catalog_guess tests/test_handoff_network.py::test_direct_general_to_explorer_is_rejected_before_any_exploration tests/test_handoff_network.py::test_explorer_cannot_turn_catalog_graph_identity_into_playable_track -q --tb=short
```

结果：**168 passed、28 warnings**，退出码 0，耗时 99.41 秒。27 条 warning 为无 checkpointer 的受控图中 durability 参数不生效；另 1 条为 Qdrant 客户端未取得服务器版本，不能据此认定客户端/服务器兼容性检查已通过。

在 `soul-music-room` 执行 `npm run test:ai-stream`，退出码 0，输出 `AI chat streaming store and durable ACK checks passed.`。这是流式状态和 ACK 相关受控检查，不替代真实浏览器播放验证。

### 7.2 本会话已执行的失败治理测试

本会话上一轮执行 `python -m pytest` 加 `-q --tb=short`，文件为：

```text
tests/test_tool_executor.py
tests/test_tool_calling_subgraph.py
tests/test_tool_action_authorization.py
tests/test_tool_definitions.py
tests/test_music_asset_tools.py
tests/test_write_reconciliation.py
tests/test_tools_t1t2.py
tests/test_recovery_selfhealing.py
tests/test_recovery_combo_query_compat.py
tests/test_journal_checkpoint_recovery.py
```

结果：**162 passed、56 subtests passed、3 warnings**，退出码 0。三条 warning 来自没有 checkpointer 的受控 GeneralChat 测试，其 durability 参数不生效。

覆盖未知工具与参数错误、授权拒绝、同批及跨调用去重、执行超时/失败、写后 502、只读对账、未知创建、删除确认、账号变化、缺回执补账和 journal 恢复。它们使用受控依赖及隔离存储，不等于真实供应商或生产 PostgreSQL 故障演练。

### 7.3 文档与交付范围

- 对文档工具清单和源码定义集合、31/30/12/18 的数量、源码与测试链接执行一致性核对；交付前执行 `git diff --check`。
- 本次只新增此 Markdown 文档，不修改应用代码、依赖、数据库或运行配置；保留现有用户未提交修改。
- 无源码或 UI 改动，未运行全量应用测试、构建、静态检查、响应式视觉验证或真实账号操作。
- 未读取生产开关，未访问 ECS，未进行真实故障注入；没有环境阻塞本次文档交付。

## 8. 源码入口

| 内容 | 文件 |
| --- | --- |
| Agent / 工具域 / 允许边 | [registry.py](../../ai-service/app/agent/handoff/registry.py) |
| 实际业务和转交绑定 | [agents.py](../../ai-service/app/agent/handoff/agents.py)、[tools.py](../../ai-service/app/agent/handoff/tools.py) |
| 歌曲上下文与组合状态 | [song_context.py](../../ai-service/app/agent/handoff/song_context.py)、[progress.py](../../ai-service/app/agent/handoff/progress.py) |
| 工具集合与任务收缩 | [assistant.py](../../ai-service/app/agent/foundation/subgraphs/tool_calling/assistant.py)、[search_scope.py](../../ai-service/app/agent/handoff/search_scope.py)、[scope.py](../../ai-service/app/agent/foundation/subgraphs/tool_calling/scope.py) |
| 音乐和客户端工具定义 | [music.py](../../ai-service/app/tools/music.py)、[client_tools.py](../../ai-service/app/agent/foundation/subgraphs/tool_calling/client_tools.py) |
| 通用执行治理 | [graph.py](../../ai-service/app/agent/foundation/subgraphs/tool_calling/graph.py) |
| 账本与只读对账 | [operations.py](../../ai-service/app/agent/foundation/long_horizon/operations.py)、[write_reconciliation.py](../../ai-service/app/agent/foundation/long_horizon/write_reconciliation.py) |
| 执行锁与运行接入 | [execution.py](../../ai-service/app/agent/foundation/long_horizon/execution.py)、[coordinator.py](../../ai-service/app/agent/foundation/long_horizon/coordinator.py)、[runner.py](../../ai-service/app/agent/foundation/long_horizon/runner.py) |
| 恢复决策与 journal 重建 | [recovery.py](../../ai-service/app/agent/foundation/long_horizon/recovery.py)、[recovery_executor.py](../../ai-service/app/agent/foundation/long_horizon/recovery_executor.py)、[journal_rebuild.py](../../ai-service/app/agent/foundation/long_horizon/journal_rebuild.py) |
| Foundation 与 Explorer 入口 | [main_graph.py](../../ai-service/app/agent/foundation/main_graph.py)、[explorer.py](../../ai-service/app/agent/handoff/explorer.py)、[WorldLine graph.py](../../ai-service/app/agent/foundation/subgraphs/worldline/graph.py) |
| 前端执行与 ACK | [aiClientActions.ts](../../soul-music-room/src/services/aiClientActions.ts)、[aiChatStore.ts](../../soul-music-room/src/store/aiChatStore.ts) |
| 操作与恢复状态 API | [chat.py](../../ai-service/app/api/chat.py) |

# Jev Capability Alignment V1

日期：2026-09-29。此文档是代码与验收证据的审查记录；四条路由的统一 Capability Spec 以 [`../config/router_capability_contract.json`](../config/router_capability_contract.json) 为唯一机器可读真值。该 JSON 对每条路由列出 `core_capabilities`、`supported_intents`、`required_context`、`required_entities`、`supported_actions`、`unsupported_or_not_yet_verified`、`failure_boundaries`、`example`（positive/negative/boundary）、`ambiguous_queries`、`multi_intent_combinations`，以及 `can_handle`、`cannot_handle`、`requires`。状态 A/B/C/D 的定义也在该文件中。此处只记录证据与重要边界，避免维护第二份会分叉的清单。

## 审查依据与现状

- MainGraph：`app/agent/foundation/main_graph.py`、`routing.py`、`hybrid_routing.py`。Hybrid Router 先产出有序 `RouteTask`；主图依赖顺序执行四个子图，失败或需补充信息时停止。
- CurrentSong：`subgraphs/current_song/{graph,assistant}.py` 与 `tests/test_foundation_current_song.py`。真实 HTTP 当前歌曲分析、缺歌曲 `needs_input` 及上下文失败路径见 `evals/reports/foundation_subgraph_e2e_acceptance.md`。分析只使用当前播放器 ID 所取 SongContext，或同轮前置 `play_song` 目标；元数据、歌词、主题、情绪、叙事与谨慎听感在范围内。普通音乐知识、事实背景核查、搜索与播放器动作不在范围内。未接入 RAG 事实检索；不能把歌词推断写成发行史或制作事实。
- ToolCalling：`subgraphs/tool_calling/{graph,assistant,client_tools,direct_actions}.py`、`app/tools/music.py`、`tests/test_tool_calling_subgraph.py`。服务端工具支持搜索、歌词、歌单读取与写入；客户端白名单支持暂停/继续/切歌、音量、静音、进度、播放模式、播放、喜欢与打开歌词。受控 E2E 与本机 SSE 暂停动作已验收；真实浏览器动作完成确认及服务端搜索/歌单写入的完整 E2E 未验收。查询信息（搜索歌曲、读取歌词）也是工具任务；“歌词是什么意思”属于分析而非歌词读取。浏览器命令只排队，不能声称已执行。
- GeneralChat：`subgraphs/general_chat/{graph,assistant}.py`、`tests/test_general_chat_subgraph.py`。普通对话、音乐概念解释、近期历史和同轮前置任务摘要已接线与基本 HTTP 验收。没有工具、实时搜索或知识图；不能吞掉明确的当前歌曲分析、动作或有证据关系探索。更广泛的真实多轮语义质量尚未验收。
- Worldline：`subgraphs/worldline/{graph,entity_resolver,retrieval,builder,generation,session,era_expansion,scene_expansion,influence_expansion}.py`、`tests/test_worldline_runtime_integration.py`。根实体枚举只有 song/artist/album；流程包含实体解析、候选证据检索、图、带来源叙事、图支持的分支及会话续探。受控 E2E 已走通；代表性 `林俊杰` 在线链路曾完成三次闭环，见 `evals/reports/structured_output_v1_implementation_report.md`。这不代表所有实体和默认配置通过生产验收：`晴天` 实体服务 502、默认检索 `empty/off`、真实生成稳定性及持久化覆盖仍有限。Era/Scene/Influence 都有当前代码；Era 的真实 evidence coverage 与 Influence/Scene 的局部问题见 `evals/reports/semantic_relations_v1_influence_context_repair/p0_influence_context_repair_report.md`，不能写成无条件准确的事实关系。Worldline 没有反事实历史模拟器；泛音乐知识、普通歌曲问答也不自动进入它。

## 状态区分

| Route | A：已实现且已验收的路径 | B：已实现但未完整 E2E 验收 | C：设计/诉求但当前未实现 | D：明确不属于该子图 |
| --- | --- | --- | --- | --- |
| CurrentSong | 当前歌曲上下文读取、歌词/元数据约束分析、缺歌曲处理 | 真实浏览器切歌后的跨轮同步 | 基于 RAG 的发行/制作事实核查 | 搜索、写操作、关系图 |
| ToolCalling | 工具契约、受控执行、动作排队、输入与重复调用防护 | 浏览器动作完成确认、真实歌单写入全链路 | 无任意外部工具扩展 | 主观歌词分析、引用关系图 |
| GeneralChat | 基本对话、近期历史、任务摘要接线 | 广泛多轮语义质量 | 无专用新增能力可计为当前能力 | 播放器操作、实时搜索、歌曲上下文分析、证据图 |
| Worldline | 受控实体/检索/图/引用链，Scene/Influence/Era 模块 | 多实体真实 E2E、Era coverage、默认配置在线质量 | 反事实历史模拟 | 普通知识解释、播放器操作 |

## Jev 与能力 Contract 的差距

当前 Jev 路由的四条 `Choice` criteria 仍写在 `app/agent/decision/jev/router.py`，并**不会读取**本 Contract；本阶段按约束没有改 Prompt。其 `tool_calling` criteria 只说 “Perform a music player or library operation”，遗漏只读搜索、歌词、歌单查询；`worldline` 写 “Research music knowledge or relationships”，比真实“可解析实体 + 有据关系图”宽；`general_chat` 的 “General conversation or questions” 与泛音乐知识、无依据假设重叠；`current_song` 未明示需要当前 SongContext。故可以确认描述层存在边界错位；是否在当前模型输出中造成错误，以本次 baseline trace 判断，不能把相关性当成已证因果。

## Gold Set 审核

`evals/datasets/router_eval_v2.jsonl` 的 47 条历史标签逐字保留，其中 31 条 single、14 条 multi、2 条 follow-up；后者保存 `recent_context`，当前 Router/Jev 不消费该字段，不能列入单轮准确率。`evals/datasets/jev_router_baseline_v1.jsonl` 在这些样本后追加 12 条 contract-derived **待审核候选**：覆盖当前歌曲/普通知识、搜索/探索、查询/动作、双意图、模糊/缺失上下文与不支持能力。候选的 proposed route 不进入正式 Gold 指标。

历史 `questionable_cases` 在数据集与基线 metrics 中保留，主要是把反事实问题标成 Worldline（如 `manual-world-002`）；`manual-world-017` 还要求以 producer 为独立探索根实体，而根实体枚举仅有 song/artist/album。`manual-multi-025` 的“这首歌背景”又与 CurrentSong 当前无事实背景核查能力冲突。所有旧标签原样计入 **raw historical** 指标，不能把这些差异直接定为模型错误；需后续人工判定后再建立新的已审批 Gold 版本。

## 复现边界

本阶段只运行 Router 决策层，未调用四个子图去验证答案质量、播放器完成状态或 Worldline 在线检索。`JEV_MODEL=jev-latest` 是可变别名，逐 case 记录实际 response model、UTC 时间、usage、latency 与失败。数据集 SHA256 逐行绑定结果。复现命令与当前指标见 `jev_router_baseline_v1.md`。

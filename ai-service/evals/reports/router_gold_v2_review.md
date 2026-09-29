# Router Gold V2 capability adjudication

本轮先依据 `evals/config/router_capability_contract.json` v1.0、四个 Foundation Subgraph 当前代码、`jev_capability_alignment_v1.md` 和 `foundation_subgraph_e2e_acceptance.md` 裁决，之后才运行 Jev ablation。原始 `router_eval_v2.jsonl` 未改动。裁决为本次能力审查，未声称独立人工盲审。

## 判定原则

- Worldline 要求可解析的 song/artist/album 与证据/关系图；当前没有改变历史的模拟器。
- GeneralChat 可作明确不确定的开放假设讨论，不能以模型记忆声称具体历史因果。
- 请求中若有当前任何子图无法满足的关键事实任务，或特定实体无法从输入/上下文解析，移出计分并保留 negative capability case；不以能回答一小部分代替完整成功。
- `KEEP` 保留原标签，`RELABEL` 改为当前能力可承接的 route set，`REMOVE_FROM_SCORING` 保留 query 但不计入准确率。

## 15 条 questionable cases 的逐条裁决

| case_id | query | old_label | capability_analysis | final_label | action | reason |
| --- | --- | --- | --- | --- | --- | --- |
| manual-hard-019 | 先别收藏,我想知道如果这首歌没发行会怎样 | worldline | 否定收藏动作不是执行意图；普通审慎假设由 GeneralChat 承接。 | general_chat | RELABEL | 可作不主张史实的假设讨论；Worldline 无反事实模拟。 |
| manual-hard-021 | 如果这首歌没火,不是说音量大小,是说它的职业影响会怎样 | worldline | 条件句排除了音量操作；Worldline 不支持未发生事件的影响图。 | general_chat | RELABEL | 职业影响仅可讨论可能性，不能生成具体历史因果。 |
| manual-hard-027 | 如果这首歌当年没有发行,今天的华语乐坛会不会不一样 | worldline | 没有可检索证据能证明未发行后的现实；GeneralChat 可表达假设与限制。 | general_chat | RELABEL | 对华语乐坛的变化只能作明确不确定的开放讨论。 |
| manual-multi-011 | 如果这张专辑晚十年发行会怎样,再分析一下当前这首歌 | current_song, worldline | CurrentSong 使用当前 SongContext；GeneralChat 仅讨论可能性，不断言具体发行影响。 | current_song, general_chat | RELABEL | 当前歌曲分析仍有效；专辑晚发行是假设讨论，不是 Worldline 关系探索。 |
| manual-multi-020 | 假如这位歌手晚五年出道会怎样,再聊聊音乐市场为什么变化这么快 | general_chat, worldline | 晚出道是假设；音乐市场变化是开放讨论，不能把反事实当作 Worldline 事实。 | general_chat | RELABEL | 两个子句均可由普通对话完成，无独立证据图任务。 |
| manual-test-worldline-001 | 要是陈奕迅没有成为歌手，华语乐坛可能会怎样 | worldline | 具体艺人名称不自动构成 Worldline 探索；没有反事实模拟能力。 | general_chat | RELABEL | 陈奕迅未成为歌手是假设讨论，不能做可引用的历史结论。 |
| manual-world-002 | 如果这首歌从来没有发行过会发生什么 | worldline | 指向当前歌曲的反事实问题不属于 CurrentSong 歌词分析，也不是 Worldline 证据图。 | general_chat | RELABEL | 未发行的后果可审慎猜想，但不可称作已证事实。 |
| manual-world-007 | 假如这首歌当年没有爆火会怎么样 | worldline | Worldline 可检索真实关系，不能模拟作品未走红后的历史。 | general_chat | RELABEL | 未爆火的结果属于开放假设。 |
| manual-world-022 | 如果这个乐队提前十年出现会怎么样 | worldline | 即使可解析乐队实体，当前图仍不支持改变年代的模拟。 | general_chat | RELABEL | 乐队提前出现是假设；只能给条件化的通用讨论。 |
| manual-hard-020 | 不是分析歌词,我想假设一下这首歌从没出现过会怎样 | worldline | 非 CurrentSong；Worldline 不应把未发生事件输出为有来源的关系。 | general_chat | RELABEL | 用户明确排除歌词分析并要求想象性讨论。 |
| manual-multi-009 | 如果这首歌从来没有发行过会怎样,然后把它收藏 | tool_calling, worldline | GeneralChat 不承诺史实；ToolCalling 在有当前歌曲 ID 时排队收藏动作。 | general_chat, tool_calling | RELABEL | 反事实讨论与明确收藏动作是两个任务。 |
| manual-multi-010 | 假如这个歌手没有出道会怎样,再跟我聊聊这种假设有什么意思 | general_chat, worldline | 并未要求真实艺人证据图；Worldline 不能模拟从未出道的历史。 | general_chat | RELABEL | 两个子句都属于假设及其意义的普通对话。 |
| manual-multi-014 | 先告诉我这首歌为什么这么伤感,再想象一下它晚十年发行会怎样 | current_song, worldline | 需当前 SongContext 做第一问；第二问不应输出 Worldline 虚构关系。 | current_song, general_chat | RELABEL | 伤感听感是 CurrentSong；晚发行是假设讨论。 |
| manual-multi-025 | 讲讲这首歌的背景,如果它当时没有发行会怎样,再帮我收藏 | current_song, tool_calling, worldline | CurrentSong 无 RAG 背景核查；Worldline 缺可解析具名根实体且无反事实模拟。收藏虽可执行，但没有一个可计分的完整 route set。 | unsupported | REMOVE_FROM_SCORING | 歌曲事实背景核查和反事实影响无法由现有子图完整满足。 |
| manual-world-017 | 假如这位制作人没有参与这张专辑会怎么样 | worldline | Worldline 根实体仅 song/artist/album；GeneralChat 无当前专辑/制作人事实，不能给出特定作品结论。 | unsupported | REMOVE_FROM_SCORING | 制作人与专辑均未给出可解析身份，且请求反事实制作史。 |

## 冻结集统计

- 历史 47 条：KEEP **32**，RELABEL **13**，REMOVE **2**。15 条 questionable 全部明确裁决，无 pending。
- Gold V2 共 **63** 条：正式 Single **46**，正式 Multi **11**，不计分 follow-up **2**，negative/unsupported **4**。
- Boundary 标签共 **30** 条，其中新增 **14** 条、7 对。新增对照只比较所需能力，避免把泛音乐知识一律归 Worldline。
- Scored route support（多意图按包含的每条 route 分别计数）：current_song=13, tool_calling=18, general_chat=32, worldline=5。

## 新增对照与负例

| pair_id | case A → route | case B → route |
| --- | --- | --- |
| concept-harmony | 什么是和声？ → general_chat | Radiohead 对哪些乐队产生过影响？请给出证据来源。 → worldline |
| concept-album | 什么叫概念专辑？ → general_chat | 探索《OK Computer》与哪些专辑存在有证据的影响关系。 → worldline |
| version-scene | 为什么现场版和录音室版听起来不同？ → general_chat | 探索 Nirvana 与哪些音乐场景有关，并给出来源。 → worldline |
| genre-graph | 摇滚和流行有什么区别？ → general_chat | 查看 Radiohead 的影响关系图和证据来源。 → worldline |
| sadness-context | 一般来说，音乐为什么会让人觉得伤感？ → general_chat | 当前播放的这首歌为什么听起来这么伤感？ → current_song |
| search-explore | 搜索 Radiohead 的歌曲。 → tool_calling | 探索 Radiohead 的音乐世界线，并列出来源与关系图。 → worldline |
| play-analyze | 帮我播放 Creep。 → tool_calling | 当前播放的 Creep 为什么听起来这么伤感？ → current_song |

移出计分的历史 case 是 `manual-world-017`、`manual-multi-025`；另纳入 `cap-negative-01`（要求真实反事实历史）与 `cap-negative-02`（票务预订）作为 negative。这些样本不参与 Choice 单意图准确率；保留它们是为了后续检查强制路由与不支持能力边界。

## 复现与冻结

运行 `python -m evals.jev_gold_v2` 会从未修改的历史 JSONL、同一 Contract 和冻结裁决表重建数据集与本报告。每行保留原 case_id、original_label、adjudicated_label、adjudication_reason、capability_contract_version、原始来源 SHA256 和计分状态。新增边界行没有历史标签，`original_label=[]`；它们在指标中与历史标签修正 cohort 分开报告。

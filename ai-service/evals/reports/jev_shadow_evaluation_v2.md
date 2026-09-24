# Jev Shadow Evaluation v2

## 1. Evaluation Design 与资产盘点

- 复用资产：Gold V4 frozen 50 条、Router v1 47 条（31 单意图、14 多意图、2 依赖上下文的追问）、已有 Retrieval/RAG trace、Foundation E2E acceptance、v1 failure audit 与 `run_jev_eval.py`。Router 中的历史失败、边界和容易混淆表述继续保留。
- 旧标签缺口：Router 的多 route gold 曾与生产 single Choice 混算；Memory v1 只有 9 个不同 demo memory ID，缺真实用户历史；Context v1 的 useful 主要由 Gold V4 `relevant_parent_ids` 推导，只表示 RAG evidence 相关性，未覆盖任务所需的 Memory、History、Tool、Handoff。Gold V4 是已审批的 AI 辅助标签，不等于本次各组件的独立人工盲审。
- 本次新增：Router 按单意图 / 多意图 / 追问分析分组；Memory 使用 100 条符合 `MemoryRecord` 的合成记录与 130 次会话问题回放；Context 重新构造 30 个任务场景并逐项给出 required/useful/irrelevant 与理由。新增标签是显式规则设计，尚未经双人盲审。
- Memory 分类：{"music_preference":30,"negative_preference":15,"habit":20,"episodic":15,"conflict":10,"unrelated":10}；默认 Top-8 查询 100 条，另对 30 条 episodic 目标做 `memory_type=episodic` 诊断探针。合成记录仅临时写入本地 pgvector 表，采样后按专用 user_id 和 ID 前缀清理；结果 JSONL 固定候选集，Jev 回放读取同一候选集。
- Context 来源分布：{"system":30,"user_query":30,"conversation":54,"long_term_memory":48,"tool_result":36,"rag_evidence":66,"handoff_projection":12}；Gold 标签数 {"required":108,"useful":72,"irrelevant":96}。任务相关性独立于 RAG relevance 标注。
- 所有 Jev 调用使用官方 TypeSafe SDK 与 `jev-latest`；Baseline/Jev 使用同一 query 和候选，Context 两路使用同一 `ContextCollector` pool、`ContextSelector` policy 和 token counter。`JEV_SHADOW_MODE=true`，输出仅评测，未修改生产开关。Router Baseline 的本地模型及必要时的 LLM fallback 与 Jev 是不同模型，延迟是组件决策耗时，不能解释为只替换模型的因果差。

### 模型血缘

| 组件 | requested_model | response_model | evaluation_timestamp (UTC) | Calls / cases |
| --- | --- | --- | --- | ---: |
| Router | jev-latest | jev-1.13.0 | 2026-09-23T23:56:55.378516+00:00 | 47 |
| Memory | jev-latest | jev-1.13.0 | 2026-09-24T00:08:03.747217+00:00 | 130 |
| Context | jev-latest | jev-1.13.0 | 2026-09-24T00:02:03.710378+00:00 | 30 |

`jev-latest` 是 mutable alias。每个 case 的 requested_model、API response_model 和 evaluation_timestamp 也保存在结果 JSONL；跨时间复评必须按实际 response_model 分组。

## 2. Router Results

单意图 31 条：Existing 64.5% (20/31)，Jev 80.6% (25/31)，agreement 48.4%；old_wrong_jev_right=10、old_right_jev_wrong=5、both_wrong=1。Jev 平均 confidence=0.753。Baseline 平均/P95 1393.6 ms / 3501.9 ms；Jev 决策平均/P95 637.7 ms / 858.2 ms。有效 Jev 响应 31/31。
多意图 14 条单独分析：现有 Router 识别多个意图 10/14；评测用四个独立 Noul 问题（阈值 0.5）识别多个意图 12/14，但完整 route 集仅 2/14，漏掉 3 个意图、额外添加 12 个意图；Jev 平均/P95 643.1 ms / 892.2 ms。这不是生产 Router 的 single Choice 能力，也未计入单意图 accuracy。另有 2 条追问保存 recent_context；现有两条路由路径均未消费，故不计入准确率。

## 3. Memory Results

| 指标 | Baseline pgvector Top-8 | Jev Filter（同一 Top-8） |
| --- | ---: | ---: |
| 默认 100 条 Key Memory Recall（完整链路） | 44.0% (44/100) | 44.0% (44/100) |
| 已检索到目标时的 Filter Recall | 100% | 100.0% |
| Precision（candidate occurrence） | 5.5% | 39.3% |
| 无关项保留 / False Keep | 100% | 9.0% |
| Candidate reduction | 0% | 86.0% |
| 平均 / P95 latency | 861.5 ms / 984.6 ms | Filter 额外 616.4 ms / 832.7 ms |

默认模式包含 800 次候选出现，其中 episodic 0 次。Jev False Drop Rate（仅已检索到 gold）0.0%；fallback 0.0%。完整链路召回将检索遗漏也计入，Filter Recall 仅衡量 Jev 是否删掉已命中的关键 Memory，两者不能混淆。
Episodic-only 探针 30 条：Baseline 检索目标 30/30 (100.0%)，Jev 之后 29/30 (96.7%)；条件 Filter Recall 96.7%，Jev Precision 69.0%，Candidate reduction 82.5%，额外 Filter latency 594.3 ms / 729.8 ms。此探针显式指定 `memory_type=episodic`，不能合并进默认 100 条主指标。

## 4. Context Results

| 指标 | Existing Select | Jev Select（同 pool 后接 Existing Selector） |
| --- | ---: | ---: |
| Required Preservation | 100.0% | 100.0% |
| Useful Context Recall | 100.0% | 87.5% |
| Irrelevant Context Retention | 100.0% | 6.2% |
| Context item reduction | 0% | 35.9% |
| Token reduction | 0% | 37.8% |
| 平均 / P95 latency | 0.6 ms / 1.4 ms | Filter 额外 647.6 ms / 742.2 ms |

30 条任务场景共 276 个 item；required false drop=0，useful false drop=9，fallback=0.0%。Context 的 token reduction 来自生产 token counter 在相同 policy 下的最终包计数。

## 5. Failure Audit

完整逐项清单：`jev_shadow_failure_audit_v2.jsonl`，共 125 项；分类计数 {"old_right_jev_wrong":5,"old_wrong_jev_right":10,"both_wrong":1,"multi_intent_mismatch":12,"jev_false_keep":81,"jev_false_drop":1,"useful_false_drop":9,"irrelevant_false_keep":6}。每项包含 case_id、input、gold、baseline result、Jev result、confidence/probability 和原因。

### Router 单意图差异及多意图错误

| Case | 类别 | 输入 | Gold | Baseline | Jev | Confidence / Probability | 原因 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| manual-chat-018 | old_right_jev_wrong | 为什么现场版和录音室版本听起来不一样 | ["general_chat"] | ["general_chat"] | ["worldline"] | 0.34 | single-intent gold=general_chat; baseline=['general_chat']; Jev=['worldline']; case tags=['easy', 'historically_failed'] |
| manual-hard-009 | old_wrong_jev_right | 这首歌不用收藏,给我分析一下就好 | ["current_song"] | [] | ["current_song"] | 0.96 | single-intent gold=current_song; baseline=unresolved; Jev=['current_song']; case tags=['boundary', 'historically_failed'] |
| manual-hard-013 | old_wrong_jev_right | 如果你知道的话,说说什么是编曲 | ["general_chat"] | [] | ["general_chat"] | 0.39 | single-intent gold=general_chat; baseline=unresolved; Jev=['general_chat']; case tags=['boundary'] |
| manual-hard-014 | old_wrong_jev_right | 如果有空,聊聊摇滚和流行的区别 | ["general_chat"] | [] | ["general_chat"] | 0.82 | single-intent gold=general_chat; baseline=unresolved; Jev=['general_chat']; case tags=['boundary'] |
| manual-hard-016 | old_wrong_jev_right | 我不是问当前这首歌,我想知道什么叫概念专辑 | ["general_chat"] | [] | ["general_chat"] | 0.33 | single-intent gold=general_chat; baseline=unresolved; Jev=['general_chat']; case tags=['boundary', 'historically_failed'] |
| manual-hard-019 | old_wrong_jev_right | 先别收藏,我想知道如果这首歌没发行会怎样 | ["worldline"] | [] | ["worldline"] | 0.66 | single-intent gold=worldline; baseline=unresolved; Jev=['worldline']; case tags=['boundary'] |
| manual-hard-021 | old_wrong_jev_right | 如果这首歌没火,不是说音量大小,是说它的职业影响会怎样 | ["worldline"] | [] | ["worldline"] | 0.36 | single-intent gold=worldline; baseline=unresolved; Jev=['worldline']; case tags=['boundary'] |
| manual-hard-028 | old_wrong_jev_right | 先别执行任何操作,我只想聊聊音乐为什么让人有共鸣 | ["general_chat"] | [] | ["general_chat"] | 0.97 | single-intent gold=general_chat; baseline=unresolved; Jev=['general_chat']; case tags=['boundary'] |
| manual-test-current-001 | old_wrong_jev_right | 概括一下正在播放的歌曲想表达什么 | ["current_song"] | ["general_chat"] | ["current_song"] | 1.0 | single-intent gold=current_song; baseline=['general_chat']; Jev=['current_song']; case tags=['easy', 'historically_failed'] |
| manual-test-worldline-001 | old_right_jev_wrong | 要是陈奕迅没有成为歌手，华语乐坛可能会怎样 | ["worldline"] | ["worldline"] | ["general_chat"] | 0.52 | single-intent gold=worldline; baseline=['worldline']; Jev=['general_chat']; case tags=['easy'] |
| manual-world-002 | old_right_jev_wrong | 如果这首歌从来没有发行过会发生什么 | ["worldline"] | ["worldline"] | ["general_chat"] | 0.33 | single-intent gold=worldline; baseline=['worldline']; Jev=['general_chat']; case tags=['easy'] |
| manual-world-007 | old_right_jev_wrong | 假如这首歌当年没有爆火会怎么样 | ["worldline"] | ["worldline"] | ["general_chat"] | 0.35 | single-intent gold=worldline; baseline=['worldline']; Jev=['general_chat']; case tags=['easy'] |
| manual-chat-020 | old_right_jev_wrong | 什么是和声 | ["general_chat"] | ["general_chat"] | ["worldline"] | 0.57 | single-intent gold=general_chat; baseline=['general_chat']; Jev=['worldline']; case tags=['easy'] |
| manual-hard-015 | old_wrong_jev_right | 这首歌先不说,聊聊为什么人会喜欢听音乐 | ["general_chat"] | [] | ["general_chat"] | 0.99 | single-intent gold=general_chat; baseline=unresolved; Jev=['general_chat']; case tags=['boundary'] |
| manual-hard-017 | old_wrong_jev_right | 不用播放任何歌,跟我聊聊R&B是什么 | ["general_chat"] | [] | ["general_chat"] | 0.69 | single-intent gold=general_chat; baseline=unresolved; Jev=['general_chat']; case tags=['boundary'] |
| manual-hard-020 | both_wrong | 不是分析歌词,我想假设一下这首歌从没出现过会怎样 | ["worldline"] | [] | ["general_chat"] | 0.69 | single-intent gold=worldline; baseline=unresolved; Jev=['general_chat']; case tags=['boundary'] |
| manual-multi-006 | multi_intent_mismatch | 先继续播放,然后讲讲当前这首歌的主题 | ["current_song","tool_calling"] | [] | ["current_song","general_chat","tool_calling"] | {"current_song":0.94,"tool_calling":0.96,"worldline":0.45,"general_chat":0.86} | missing=[]; extra=['general_chat'] |
| manual-multi-008 | multi_intent_mismatch | 先收藏这首歌,再说说它为什么这么火 | ["current_song","tool_calling"] | [] | ["current_song","general_chat","tool_calling","worldline"] | {"current_song":0.68,"tool_calling":0.88,"worldline":0.63,"general_chat":0.87} | missing=[]; extra=['general_chat', 'worldline'] |
| manual-multi-011 | multi_intent_mismatch | 如果这张专辑晚十年发行会怎样,再分析一下当前这首歌 | ["current_song","worldline"] | ["current_song","worldline"] | ["current_song","general_chat","worldline"] | {"current_song":0.89,"tool_calling":0.04,"worldline":0.79,"general_chat":0.62} | missing=[]; extra=['general_chat'] |
| manual-multi-015 | multi_intent_mismatch | 先暂停,再聊聊为什么人会反复听同一首歌 | ["general_chat","tool_calling"] | [] | ["general_chat","tool_calling","worldline"] | {"current_song":0.07,"tool_calling":0.83,"worldline":0.64,"general_chat":0.91} | missing=[]; extra=['worldline'] |
| manual-multi-018 | multi_intent_mismatch | 收藏这首歌,再跟我说说什么叫概念专辑 | ["general_chat","tool_calling"] | ["general_chat","tool_calling"] | ["general_chat","tool_calling","worldline"] | {"current_song":0.09,"tool_calling":0.93,"worldline":0.86,"general_chat":0.92} | missing=[]; extra=['worldline'] |
| manual-multi-002 | multi_intent_mismatch | 暂停一下,然后告诉我这首歌歌词什么意思 | ["current_song","tool_calling"] | ["current_song","tool_calling"] | ["current_song","general_chat","tool_calling"] | {"current_song":0.94,"tool_calling":0.92,"worldline":0.42,"general_chat":0.9} | missing=[]; extra=['general_chat'] |
| manual-multi-009 | multi_intent_mismatch | 如果这首歌从来没有发行过会怎样,然后把它收藏 | ["tool_calling","worldline"] | ["tool_calling","worldline"] | ["general_chat"] | {"current_song":0.13,"tool_calling":0.46,"worldline":0.26,"general_chat":0.53} | missing=['tool_calling', 'worldline']; extra=['general_chat'] |
| manual-multi-010 | multi_intent_mismatch | 假如这个歌手没有出道会怎样,再跟我聊聊这种假设有什么意思 | ["general_chat","worldline"] | ["worldline"] | ["general_chat"] | {"current_song":0.03,"tool_calling":0.02,"worldline":0.24,"general_chat":0.85} | missing=['worldline']; extra=[] |
| manual-multi-014 | multi_intent_mismatch | 先告诉我这首歌为什么这么伤感,再想象一下它晚十年发行会怎样 | ["current_song","worldline"] | ["current_song","worldline"] | ["current_song","general_chat","worldline"] | {"current_song":0.75,"tool_calling":0.03,"worldline":0.69,"general_chat":0.77} | missing=[]; extra=['general_chat'] |
| manual-multi-016 | multi_intent_mismatch | 把音量调小一点,再跟我讲讲R&B和Soul有什么区别 | ["general_chat","tool_calling"] | ["general_chat","tool_calling"] | ["general_chat","tool_calling","worldline"] | {"current_song":0.04,"tool_calling":0.93,"worldline":0.82,"general_chat":0.94} | missing=[]; extra=['worldline'] |
| manual-multi-021 | multi_intent_mismatch | 分析当前这首歌,然后聊聊编曲和作曲有什么区别 | ["current_song","general_chat"] | ["current_song","general_chat"] | ["current_song","general_chat","worldline"] | {"current_song":0.91,"tool_calling":0.06,"worldline":0.86,"general_chat":0.87} | missing=[]; extra=['worldline'] |
| manual-multi-025 | multi_intent_mismatch | 讲讲这首歌的背景,如果它当时没有发行会怎样,再帮我收藏 | ["current_song","tool_calling","worldline"] | ["current_song","tool_calling","worldline"] | ["current_song","general_chat","tool_calling","worldline"] | {"current_song":0.58,"tool_calling":0.83,"worldline":0.92,"general_chat":0.89} | missing=[]; extra=['general_chat'] |

### Memory 关键误删

| Case | 类别 | 输入 | Gold | Baseline | Jev | Confidence / Probability | 原因 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| memory-v2-episodic_only_probe-120 | jev_false_drop | 我上次关于牙医问过什么？ | {"jev-v2-unrelated-005":true} | true | false | 0.46 | target memory was retrieved but Jev score fell below filter threshold |

Memory false keep 逐候选记录在 audit JSONL。合成查询刻意窄化为单个目标事实；其他候选按本轮规则标为 false，此标签不能推断其在真实对话中永远无用。

### Context required / useful 误删与大量无关保留

| Case | 类别 | 输入 | Gold | Baseline | Jev | Confidence / Probability | 原因 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| context-v2-rag-01 | useful_false_drop | 说明周杰伦在音乐中的特点，并给出依据 | {"tool_result:source-index":"useful"} | true | false | 0.33 | provenance lookup |
| context-v2-rag-02 | useful_false_drop | 说明民谣在音乐中的特点，并给出依据 | {"tool_result:source-index":"useful"} | true | false | 0.34 | provenance lookup |
| context-v2-rag-03 | useful_false_drop | 说明爵士乐在音乐中的特点，并给出依据 | {"tool_result:source-index":"useful"} | true | false | 0.37 | provenance lookup |
| context-v2-rag-04 | useful_false_drop | 说明摇滚在音乐中的特点，并给出依据 | {"tool_result:source-index":"useful"} | true | false | 0.39 | provenance lookup |
| context-v2-rag-05 | useful_false_drop | 说明电影配乐在音乐中的特点，并给出依据 | {"tool_result:source-index":"useful"} | true | false | 0.4 | provenance lookup |
| context-v2-rag-06 | useful_false_drop | 说明古典钢琴在音乐中的特点，并给出依据 | {"tool_result:source-index":"useful"} | true | false | 0.42 | provenance lookup |
| context-v2-mixed-03 | useful_false_drop | 别把爵士乐的现场版当录音室版；结合我上次反馈分析歌词 | {"tool_result:version":"useful"} | true | false | 0.47 | version disambiguation |
| context-v2-mixed-04 | useful_false_drop | 别把摇滚的现场版当录音室版；结合我上次反馈分析歌词 | {"tool_result:version":"useful"} | true | false | 0.46 | version disambiguation |
| context-v2-mixed-06 | useful_false_drop | 别把古典钢琴的现场版当录音室版；结合我上次反馈分析歌词 | {"tool_result:version":"useful"} | true | false | 0.38 | version disambiguation |

Context 的所有 irrelevant false keep 逐项记录在 audit JSONL。

## 6. Baseline vs Jev Summary

| 组件 | Baseline | Jev | 判读 |
| --- | --- | --- | --- |
| Router 单意图 | accuracy 64.5%；latency 1393.6 ms / 3501.9 ms | accuracy 80.6%；agreement 48.4%；latency 637.7 ms / 858.2 ms | 5 个 old-right/Jev-wrong；多意图另评，精确集 2/14 |
| Memory 默认 | key recall 44.0%；precision 5.5%；无关保留 100% | key recall 44.0%；precision 39.3%；无关保留 9.0%；候选减少 86.0%；额外 latency 616.4 ms / 832.7 ms | 关键目标误删见 audit；检索召回和过滤召回分别判断 |
| Context | required 100.0%；useful 100.0%；无关保留 100.0% | required 100.0%；useful 87.5%；无关保留 6.2%；token 减少 37.8%；额外 latency 647.6 ms / 742.2 ms | required 满足硬约束，但 useful 丢失 9 项 |

三个组件没有合成总分。

## 7. E2E Diagnostic 与 Live Decision Recommendation

未执行完整 E2E A/B：现有 Foundation E2E acceptance 依赖固定路由与测试替身，Gold V4 主要标注 RAG 证据，缺少针对相同主 LLM、RAG、tools、输入下最终 task success、groundedness、personalization 的可靠成对 gold/judge。Router/Memory/Context 的组件差异与 token/latency 已按各自同输入单独记录，不伪造端到端质量分。
- Router：继续 Shadow。单意图 Jev accuracy 高于 Baseline，但 5 个原本正确的 case 退化；多意图 Noul 分析仅 2/14 精确集，尚未成为生产路由合同。先复核退化 case 和 multi gold，再考虑仅单意图门控 Live。
- Memory：继续 Shadow。默认 100 条已检索目标未被 Jev 删除，但 episodic-only 探针有 1 项关键误删；加上默认检索仅命中 44/100，尚不足以满足完整链路 Key Memory Recall 门槛。
- Context：继续 Shadow。Required Preservation=100%，但 Useful Recall 从 100% 降到 87.5%，存在 9 项 useful false drop；须解决或接受这些具体损失后再评估 Live。
- `JEV_SHADOW_MODE` 保持 true，未修改生产路径。

## 8. 已知限制

- Memory 使用合成用户与规则标签，短会话回放不是长期真实用户历史；默认检索的 profile/episodic 比例受现有优先策略影响。重复候选按 occurrence 统计 Precision/False Keep。
- Memory 数据集保存 recent_context 以说明合成事实的会话来源；现有 `MemoryRetriever` / Jev Filter 接口只消费当前 query 与已检索候选，故本轮不能测量多轮上下文推理。
- Context pool 是经生产 Collector 构造的诊断输入，不是线上完整轨迹；required/useful/irrelevant 来源于任务 rubric，未经双人独立复核。Baseline 的全保留是本诊断 pool 和预算条件下的结果。
- Router 的 2 条 follow-up recent_context 两路都未消费，不能据此判断多轮路由质量；Noul 阈值 0.5 未调优，不等于已实现的生产多意图 Router。
- `jev-latest` 可变；本报告的 API 实际版本和时间见模型血缘表，不能与别的版本或时间直接混合。

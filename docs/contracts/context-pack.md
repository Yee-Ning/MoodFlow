# ContextPack 契约

版本：Phase 3，2026-10-05。实现基线：Phase 2 `7c286b3`。本契约描述现有 L1，不改变选择、压缩、路由或预算策略。

## 所有权与生命周期

Assembler 是模型可见消息的唯一组装者。ContextPack 是一次调用的临时视图，调用完成后释放；不得作为下一轮 Collect 的来源。manifest 是该视图的可观测投影，不是第二份 Pack、记忆或账本。入口 state 的 lite/full manifest 及日志允许保留统计，不允许用其重新选择上下文。

## 字段

| 字段 | 类型 / 含义 | 所有者 |
| --- | --- | --- |
| items | ContextItem 列表；经过六步处理的来源条目 | L1 临时视图 |
| messages | BaseMessage 列表；实际模型消息视图 | Assembler |
| total_tokens / budget | 组装后输入计数 / 本次可用输入预算 | L1 |
| dropped_items / compressed_items | 条目 ID；不包含被丢弃的全文 | 观测 |
| trace | ContextTraceEntry 列表 | 观测 |
| manifest | 下表的统计投影 | 观测 |
| intent | 当前调用意图；供 Select 和 `<current_intent>` 使用，不进入 Collect | 调用作用域 |

ContextItem 的来源、类型、required、priority、token_count、metadata 继续遵循 `context_engineering.py`。Collect 重新计数，不信任来源自报 token。注册表位于 `context_sources.py`，配额/优先级位于 `context_policy.json`。

| ContextItem 字段 | 类型 / 约束 |
| --- | --- |
| id / type / source / content | 字符串；type 为现有 ContextType 白名单，content 是区段数据 |
| priority / relevance | 0–100 整数，默认 50 / 0–1 浮点数，默认 1 |
| token_count | 非负整数，默认 0，Collect 重新计数 |
| required / compressible | 布尔，默认 false / true |
| metadata | 既有来源元数据；意图适用性、抽取信息等，默认空对象 |
| references | 既有来源引用 ID 字符串列表；去重/溯源使用，不作为授权；默认空列表 |

ContextItem.references 是来源域的 ID（包含知识库/画像版本等），不等同于 Journal 的 HistoryPointer 或 EventPointer。需要下钻 Immutable History 时使用带长期 task_id 的 Journal 接口，不能把其他来源 ID 当 event_id。Phase 3 不改来源 ID 或 L1 去重算法。

ContextTraceEntry 为 item_id、stage（collected/selected/deduplicated/compressed/dropped/final）、reason、非负 token_before/token_after，以及可选 task_id/session_id/request_id；manifest 只投影 compressed/dropped/deduplicated。这里可选身份用于观测关联，不能自行赋予读取权限。

## 区段与预算

保留既有 system、当前用户输入和 user/assistant/tool 消息协议；业务状态、当前歌曲、handoff_projection、history_drilldown、artifact_projection、memory_source、long_term_memory、user_profile 是有类型的数据区段。长期记忆明确标注来源，不获得指令权；画像按相关性节选。文本由 Assembler 转义。意图作为独立 `<current_intent>` 区段。

规则命中直接进入 full；未命中使用固定小预算 lite 进行分诊，再进入 full。两者仍经过最终序列化预算门。provider/schema framing 在最终调用边界计入；L1 内容计数与供应商实际输入用量并非同一口径。

| manifest 字段 | 含义 |
| --- | --- |
| tokens / budget | Pack 组装计数 / 输入预算 |
| sources.<name>.candidate_tokens / final_tokens | 来源候选 / 最终内容 token；不含 framing |
| sources.<name>.events | 压缩、丢弃、去重事件：item_id、stage、reason、token_before/after、可选 task/session/request ID |
| sources.<name>.dedup_dropped | 去重丢弃 ID；观测索引，不是内容副本 |

Phase 2 的 `context_engineering.call_manifest` 合并以上字段，并增加 call_id、trace_id、task_id、agent、model、input_tokens、output_tokens、total_tokens、estimated、usage_basis、max_output_tokens、output_limit_exceeded、status、budget_events。这里 task_id/trace_id 是本次原始 run_id；agent 是计费维度，可为 jev。`total_tokens = input_tokens + output_tokens`。usage_basis 区分实数、只有 total 的估算拆分和分词估算；异常未结算保留预留。

调用 manifest 不写回选择策略，也不计第二笔账。MODEL_USAGE 是持久用量事实；账本是它的查询视图，manifest/handoff.trace 是观测投影。max_tokens 和 L2/L3 阈值仍取既有 context_policy/RunLimits，Phase 3 不调整。

有 DurableAccounting 时，call_manifest 增加 usage_ref（长期 task_id + settled event_id），指向已经写入的 MODEL_USAGE 事实。此指针的 task_id 与 manifest 顶层的 run_id 口径不同，不能混用。没有持久作用域时不伪造 usage_ref。

| call_manifest 新增字段 | 类型 / 约束 |
| --- | --- |
| call_id / trace_id / task_id / model | 非空字符串；trace_id 必须等于顶层 task_id（run_id） |
| agent | general_chat / curator / explorer / jev 计费维度 |
| input_tokens / output_tokens / total_tokens | 非负整数，拆分之和必须等于 total |
| estimated / usage_basis | 布尔 / 非空来源标记；实数或明确估算 |
| max_output_tokens / output_limit_exceeded | 正整数 / 布尔，后者必须与 output > max 一致 |
| status | completed / unknown |
| budget_events | trace_id/task_id/agent/level/stage/agent_tokens/task_tokens 观测列表 |
| usage_ref | 可选 EventPointer，指向 settled 事实 |

独立 SDK 调用没有 Pack 时允许不提供 tokens/budget/sources；有 Pack 时三者必须齐全，不能伪造半个 Pack 投影。budget_events 的 level 为 l2/l3，stage 为既有六种降级阶段；它是观测快照，不用于再次计费。

## 校验与禁止事项

Pack/调用 manifest 的数值、来源字段和缺失/多余字段在契约边界校验。SDK dispatch 必须在 accounted_call 内；来源不得调用 SDK 或自行构造 prompt。不得在最终预算门后追加来源全文。原 AST lint 继续生效。

## RAG Source 增补（2026-10-08）

新来源的 item ID 使用 `evidence:rag:{parent_id}`，metadata.evidence_id 保持原 parent_id 去重语义。与旧 rag_pack 同时出现时，观测 ID 不混用；chunk 是否被保留按 source + item_id 判断。

正式注册的 `rag` 来源读取现有 hybrid 检索器，用当前输入构造单条确定性的 SearchPlan；不调用 LLM 改写、HyDE 或生成接口。`AI_RAG_RETRIEVAL_MODE` 仍为 empty / shadow / hybrid，在线模式仍要求现有 `AI_RAG_INDEX_MANIFEST`。empty 不检索，shadow 执行检索及重排并记录成本但不向 Pack 提供证据。Source 的 rerank 始终启用，不受旧 WorldLine 的 `AI_RAG_RERANK_MODE=off` 影响；旧 `rag_pack` 侧通道保留原行为。

`rag` 配额为 24 个重排输入候选、5000 个来源内容 token、优先级 85。候选上限参考既有 history_memory 的 24 项，来源预算/优先级沿用 WorldLine 的 rag_evidence 配额。无文档 ID、来源 ID/名称、chunk ID 的候选先剔除；只将命中的 Child 内容送入 L1，不隐式展开 Parent。代码 Select → Dedup → Budget → Assemble 保持原实现，来源超限整条丢弃并进入 dropped_items，不增加新的压缩策略。

既有 candidate_tokens、final_tokens、events、dedup_dropped 保持原含义。新增字段均为可选，旧 manifest 继续校验通过：

SourceManifest 保留原文的字段缺席语义：原文未提供 chunks、model_usage 或 collection，且对应字段没有审计内容时，序列化不补写默认空值。显式提供的空列表或 null，以及实际产生的审计内容均保留。此规则不改变必填统计字段、extra=forbid 或嵌套审计 DTO 的既有校验与默认值语义。

| sources.rag 新增字段 | 含义 |
| --- | --- |
| chunks | 逐候选 item_id、doc_id、chunk_id、来源 ID/名称/URL、标题、fusion_score、rerank_score、score 及 score_basis；candidate_tokens / final_tokens、included、丢弃/去重原因。不含 chunk 全文 |
| model_usage | 每次 Embedding / rerank 的独立模型维度、版本、call_id、输入/总 token、output_tokens=0、估算标记、计数口径、completed/unknown、预算作用域和可选 usage_ref |
| collection | mode、rerank_enabled=true、检索/重排状态、候选限制、无出处拒绝数量、plan/index 版本、回退标记及机器原因 |

Embedding / rerank 是本地非生成推理，按实际格式化/截断后的 tokenizer 输入计数，basis=local_model_tokenizer。替身编码器/打分器使用显式 unicode_heuristic 估算。模型维度分别为 `rag-embedding:Qwen/Qwen3-Embedding-0.6B` 与 `rag-rerank:Qwen/Qwen3-Reranker-0.6B`；不新增 Agent 枚举或 BudgetLedger 方法，当前 Agent 的 L2 和任务 L3 均累计。无请求作用域的离线调用明确标为 unscoped，不伪造持久指针。

所有模型 forward 前调用现有 RequestLedger.reserve，使用 DurableAccounting 写 MODEL_USAGE 预留；成功后 settle，usage_ref 指向长期 task_id 中的结算事件。没有生成输出上限，PublicUsage 的 max_output_tokens 留空。推理失败保留预留事实并按 unknown 估算计费，usage_ref 指向 reserved 事件；重排沿用现有 RRF 回退。预算拒绝穿透检索器回退和可选 Source 捕获，仍触发现有 L2/L3 降级；持久记账失败中止请求。manifest 不计第二笔账，也不反向参与选择。

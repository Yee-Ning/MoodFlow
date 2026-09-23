# Jev Shadow Evaluation v1

## 1. 数据集来源与标签

- Router：`data/intent/splits/test.jsonl` 与 `val.jsonl` 中去重后的 45 条人工标注保留集；`manual_test.jsonl` 与 test 重复，不重复计数。另从 Foundation E2E acceptance 的连续会话加入 2 条（总 47）；历史 intent model `error_analysis.jsonl` 命中 14 条作为 historically_failed 标签。`recent_context` 在两条 E2E 案例中保留，但当前生产 Hybrid Router/Choice 都没有消费会话文本，所以这是覆盖缺口的诊断，不能算完整多轮能力验证。
- Context：Gold V4 冻结 50 条中的 29 条 answerable 与 1 条 insufficient_evidence，共 30 条；输入证据来自 `gold_v4_hybrid_shadow_trace_50_20260917.jsonl.gz` 的 `final_context`，按现有 `ContextCollector` 重建进入 Foundation Selector 之前的 pool。`system`/`query` 是必需项；证据 parent ID 命中 Gold V4 `relevant_parent_ids` 标为 useful，其余标为 irrelevant。这些是 owner-approved AI-assisted Gold 的派生标签，未做新的盲审；未命中的证据不一定在所有下游用途上绝对无关。
- Memory：从现有 PostgreSQL/pgvector 读取，使用项目原有 Qwen embedding 与 `MemoryRetriever` 排序，本诊断请求 Top-K=3；因宿主 `DATABASE_URL` 缺少密码，本次经 Docker 内只读 `psql` 访问同一 pgvector 表，复用 `PostgresMemoryStore` 的列定义/记录解析及相同的 profile/episodic 查询逻辑。不创建第二套记忆库。当前库有 11 条活跃 demo memory（5 episodic、6 profile；5 个用户），本集覆盖 3 个用户、9 个不同 memory ID。30 个 query case 共 90 次候选出现；true/false/uncertain 由评测者按 demo 内容与查询意图标注，未经独立盲审。含 memory 文本的诊断与结果文件在被 Git 忽略的 `evals/local/`。
- Foundation E2E acceptance、RAG/failure audit 已检查；前者使用固定 Router/test doubles，后者是 RAG 证据标签，均不能直接当成 Memory Gold 或真实主 LLM A/B 判断。

## 2. A/B 设置与模型漂移追踪

同一 case 使用相同 query；Context 的 Baseline 与 Jev 共享同一个 `ContextCollector` 候选池，并均使用现有 `ContextSelector.build`、相同 WORLDLINE policy/token counter。Router Baseline 是现有 Hybrid Router（其中 LLM fallback 模型 `deepseek-v4-flash`），Jev 调用生产 `compare_route` 的同一 Choice 配置。所有 Jev 调用请求 `jev-latest`，未使用 `jev-preview`。始终保持 `JEV_SHADOW_MODE=true`，Jev 结果只作为拟选择结果；`jev-latest` 是 mutable alias。

| requested_model | response_model | 调用案例数 |
|---|---|---:|
| jev-latest | jev-1.13.0 | 107 |

| 组件 | evaluation_timestamp (UTC) |
|---|---|
| Router | 2026-09-23T14:50:35.713352+00:00 |
| Memory | 2026-09-23T14:43:15.548949+00:00 |
| Context | 2026-09-23T14:43:27.931830+00:00 |

## 3. Router 结果

47 cases；Baseline 有效 47，Jev 有效 47，两边可比较 47。本轮 Jev 响应全部有效。Baseline accuracy 66.0%，Jev accuracy 59.6%，agreement 38.3%。old_wrong_jev_right=10，old_right_jev_wrong=13，both_wrong=6。Fallback 70.2%；Baseline 平均/P95 latency 1763.9 ms/3954.0 ms，Jev 决策平均/P95 latency 688.3 ms/1041.2 ms。

多意图 gold 不能由单 Choice 完整表达；生产 Router 对复杂既有路由继续保留 Baseline。单意图 33 条中，Baseline 63.6% (33)，Jev 84.8% (33)。实际满足本次低置信度/复杂路由门控的 14 条，Baseline 85.7%，Jev 100.0%；此子集不能外推为全面 Live 证据。

## 4. Memory 结果

30 个 query case、90 个候选出现次数；true=39、false=39、uncertain=12。重复 demo memory 与重复 query 的相关性会高估独立样本数。`old_keep` 与 `jev_keep` 的逐候选差异见本地 `evals/local/jev_memory_shadow_v1.jsonl`。
Baseline/Jev Key Memory Recall 100.0%/100.0%；Precision 50.0%/100.0%；Irrelevant Retention 100.0%/0.0%；candidate reduction 44.4%；false drop 0，false keep 0；Jev latency avg/P95 651.4 ms/723.3 ms。

## 5. Context 结果

30 cases；required=60（仅 system/query）。Baseline/Jev Required Preservation 100.0%/100.0%；Useful Recall 100.0%/100.0%；Irrelevant Retention 100.0%/55.1%；Jev preselect candidate reduction 27.5%、最终 item reduction 27.5%、最终 token reduction 31.8%；Baseline Selector avg/P95 2.1 ms/16.0 ms，Jev API avg/P95 664.0 ms/820.7 ms。

## 6. Existing vs Jev 对比

| 组件 | 指标 | Existing | Jev |
|---|---|---:|---:|
| Router | Accuracy | 66.0% (47) | 59.6% (47) |
| Router | Agreement / fallback | — | 38.3% / 70.2% |
| Router | Decision latency avg / P95 | 1763.9 ms / 3954.0 ms | 688.3 ms / 1041.2 ms |
| Memory | Key Memory Recall | 100.0% | 100.0% |
| Memory | Precision | 50.0% | 100.0% |
| Memory | Irrelevant Retention | 100.0% | 0.0% |
| Memory | Candidate Reduction / latency | — | 44.4% / 651.4 ms |
| Context | Required Preservation | 100.0% | 100.0% |
| Context | Useful Recall | 100.0% | 100.0% |
| Context | Irrelevant Retention | 100.0% | 55.1% |
| Context | Token Reduction / latency | 2.1 ms | 31.8% / 664.0 ms API |

## 7. Failure Audit

逐案例、逐候选审计也保存为 `jev_shadow_failure_audit_v1.jsonl`；每行包含 case_id、input、gold、baseline、Jev、confidence/probability 或 Noul score、reason。下表列出全部审计项。
分类计数：both_wrong=6；irrelevant_false_keep=75；many_irrelevant_kept=14；old_right_jev_wrong=13；old_wrong_jev_right=10。Memory 关键误删与明显无关保留在本轮均为 0。

| case_id | 类型 | input | gold | baseline | Jev | confidence/probability/score | reason |
|---|---|---|---|---|---|---:|---|
| manual-hard-009 | old_wrong_jev_right | 这首歌不用收藏,给我分析一下就好 | ["current_song"] | [] | ["current_song"] | {"confidence": 0.98, "probabilities": {"worldline": 0.0, "current_song": 0.99, "tool_calling": 0.0, "general_chat": 0.01}} | existing router left request unresolved |
| manual-hard-013 | old_wrong_jev_right | 如果你知道的话,说说什么是编曲 | ["general_chat"] | [] | ["general_chat"] | {"confidence": 0.5, "probabilities": {"general_chat": 0.63, "tool_calling": 0.0, "current_song": 0.0, "worldline": 0.37}} | existing router left request unresolved |
| manual-hard-014 | old_wrong_jev_right | 如果有空,聊聊摇滚和流行的区别 | ["general_chat"] | [] | ["general_chat"] | {"confidence": 0.81, "probabilities": {"worldline": 0.14, "current_song": 0.0, "general_chat": 0.86, "tool_calling": 0.0}} | existing router left request unresolved |
| manual-hard-016 | both_wrong | 我不是问当前这首歌,我想知道什么叫概念专辑 | ["general_chat"] | [] | ["worldline"] | {"confidence": 0.37, "probabilities": {"current_song": 0.0, "tool_calling": 0.0, "general_chat": 0.47, "worldline": 0.53}} | existing router left request unresolved |
| manual-hard-019 | old_wrong_jev_right | 先别收藏,我想知道如果这首歌没发行会怎样 | ["worldline"] | [] | ["worldline"] | {"confidence": 0.71, "probabilities": {"current_song": 0.06, "tool_calling": 0.0, "worldline": 0.78, "general_chat": 0.16}} | existing router left request unresolved |
| manual-hard-021 | old_wrong_jev_right | 如果这首歌没火,不是说音量大小,是说它的职业影响会怎样 | ["worldline"] | [] | ["worldline"] | {"confidence": 0.33, "probabilities": {"current_song": 0.17, "general_chat": 0.33, "worldline": 0.5, "tool_calling": 0.0}} | existing router left request unresolved |
| manual-hard-028 | old_wrong_jev_right | 先别执行任何操作,我只想聊聊音乐为什么让人有共鸣 | ["general_chat"] | [] | ["general_chat"] | {"confidence": 0.97, "probabilities": {"general_chat": 0.98, "current_song": 0.0, "tool_calling": 0.0, "worldline": 0.02}} | existing router left request unresolved |
| manual-multi-006 | both_wrong | 先继续播放,然后讲讲当前这首歌的主题 | ["current_song", "tool_calling"] | [] | ["current_song"] | {"confidence": 0.44, "probabilities": {"current_song": 0.58, "general_chat": 0.0, "worldline": 0.0, "tool_calling": 0.42}} | single Choice cannot express multi-route gold |
| manual-multi-008 | both_wrong | 先收藏这首歌,再说说它为什么这么火 | ["current_song", "tool_calling"] | [] | ["tool_calling"] | {"confidence": 0.7, "probabilities": {"worldline": 0.09, "general_chat": 0.03, "tool_calling": 0.77, "current_song": 0.11}} | single Choice cannot express multi-route gold |
| manual-multi-011 | old_right_jev_wrong | 如果这张专辑晚十年发行会怎样,再分析一下当前这首歌 | ["current_song", "worldline"] | ["current_song", "worldline"] | ["current_song"] | {"confidence": 0.34, "probabilities": {"tool_calling": 0.0, "worldline": 0.31, "general_chat": 0.18, "current_song": 0.51}} | single Choice cannot express multi-route gold |
| manual-multi-015 | both_wrong | 先暂停,再聊聊为什么人会反复听同一首歌 | ["general_chat", "tool_calling"] | [] | ["general_chat"] | {"confidence": 0.68, "probabilities": {"current_song": 0.0, "worldline": 0.03, "tool_calling": 0.21, "general_chat": 0.76}} | single Choice cannot express multi-route gold |
| manual-multi-018 | old_right_jev_wrong | 收藏这首歌,再跟我说说什么叫概念专辑 | ["general_chat", "tool_calling"] | ["general_chat", "tool_calling"] | ["tool_calling"] | {"confidence": 0.57, "probabilities": {"worldline": 0.1, "tool_calling": 0.68, "general_chat": 0.21, "current_song": 0.01}} | single Choice cannot express multi-route gold |
| manual-multi-020 | old_right_jev_wrong | 假如这位歌手晚五年出道会怎样,再聊聊音乐市场为什么变化这么快 | ["general_chat", "worldline"] | ["general_chat", "worldline"] | ["general_chat"] | {"confidence": 0.5, "probabilities": {"general_chat": 0.62, "worldline": 0.38, "current_song": 0.0, "tool_calling": 0.0}} | single Choice cannot express multi-route gold |
| manual-test-current-001 | old_wrong_jev_right | 概括一下正在播放的歌曲想表达什么 | ["current_song"] | ["general_chat"] | ["current_song"] | {"confidence": 1.0, "probabilities": {"general_chat": 0.0, "tool_calling": 0.0, "current_song": 1.0, "worldline": 0.0}} | existing semantic route disagrees with gold; manual cause review needed |
| manual-test-multi-001 | old_right_jev_wrong | 分析当前歌曲，再帮我点个喜欢 | ["current_song", "tool_calling"] | ["current_song", "tool_calling"] | ["tool_calling"] | {"confidence": 0.41, "probabilities": {"tool_calling": 0.56, "worldline": 0.0, "general_chat": 0.0, "current_song": 0.44}} | single Choice cannot express multi-route gold |
| manual-test-worldline-001 | old_right_jev_wrong | 要是陈奕迅没有成为歌手，华语乐坛可能会怎样 | ["worldline"] | ["worldline"] | ["general_chat"] | {"confidence": 0.47, "probabilities": {"worldline": 0.4, "current_song": 0.0, "general_chat": 0.6, "tool_calling": 0.0}} | Jev semantic route disagrees with gold; manual cause review needed |
| manual-world-007 | old_right_jev_wrong | 假如这首歌当年没有爆火会怎么样 | ["worldline"] | ["worldline"] | ["general_chat"] | {"confidence": 0.31, "probabilities": {"worldline": 0.44, "general_chat": 0.48, "tool_calling": 0.0, "current_song": 0.08}} | Jev semantic route disagrees with gold; manual cause review needed |
| manual-chat-020 | old_right_jev_wrong | 什么是和声 | ["general_chat"] | ["general_chat"] | ["worldline"] | {"confidence": 0.58, "probabilities": {"current_song": 0.0, "tool_calling": 0.0, "worldline": 0.69, "general_chat": 0.31}} | Jev semantic route disagrees with gold; manual cause review needed |
| manual-hard-015 | old_wrong_jev_right | 这首歌先不说,聊聊为什么人会喜欢听音乐 | ["general_chat"] | [] | ["general_chat"] | {"confidence": 0.99, "probabilities": {"tool_calling": 0.0, "current_song": 0.0, "worldline": 0.01, "general_chat": 0.99}} | existing router left request unresolved |
| manual-hard-017 | old_wrong_jev_right | 不用播放任何歌,跟我聊聊R&B是什么 | ["general_chat"] | [] | ["general_chat"] | {"confidence": 0.7, "probabilities": {"general_chat": 0.78, "current_song": 0.0, "worldline": 0.22, "tool_calling": 0.0}} | existing router left request unresolved |
| manual-hard-020 | both_wrong | 不是分析歌词,我想假设一下这首歌从没出现过会怎样 | ["worldline"] | [] | ["general_chat"] | {"confidence": 0.61, "probabilities": {"worldline": 0.28, "tool_calling": 0.0, "general_chat": 0.71, "current_song": 0.01}} | existing router left request unresolved |
| manual-multi-002 | old_right_jev_wrong | 暂停一下,然后告诉我这首歌歌词什么意思 | ["current_song", "tool_calling"] | ["current_song", "tool_calling"] | ["current_song"] | {"confidence": 0.72, "probabilities": {"worldline": 0.0, "tool_calling": 0.2, "general_chat": 0.0, "current_song": 0.8}} | single Choice cannot express multi-route gold |
| manual-multi-009 | old_right_jev_wrong | 如果这首歌从来没有发行过会怎样,然后把它收藏 | ["tool_calling", "worldline"] | ["tool_calling", "worldline"] | ["tool_calling"] | {"confidence": 0.29, "probabilities": {"current_song": 0.01, "worldline": 0.09, "general_chat": 0.42, "tool_calling": 0.48}} | single Choice cannot express multi-route gold |
| manual-multi-010 | both_wrong | 假如这个歌手没有出道会怎样,再跟我聊聊这种假设有什么意思 | ["general_chat", "worldline"] | ["worldline"] | ["general_chat"] | {"confidence": 0.71, "probabilities": {"worldline": 0.21, "general_chat": 0.79, "tool_calling": 0.0, "current_song": 0.0}} | single Choice cannot express multi-route gold |
| manual-multi-014 | old_right_jev_wrong | 先告诉我这首歌为什么这么伤感,再想象一下它晚十年发行会怎样 | ["current_song", "worldline"] | ["current_song", "worldline"] | ["current_song"] | {"confidence": 0.78, "probabilities": {"current_song": 0.83, "tool_calling": 0.0, "general_chat": 0.09, "worldline": 0.08}} | single Choice cannot express multi-route gold |
| manual-multi-016 | old_right_jev_wrong | 把音量调小一点,再跟我讲讲R&B和Soul有什么区别 | ["general_chat", "tool_calling"] | ["general_chat", "tool_calling"] | ["tool_calling"] | {"confidence": 0.44, "probabilities": {"tool_calling": 0.58, "current_song": 0.0, "general_chat": 0.19, "worldline": 0.23}} | single Choice cannot express multi-route gold |
| manual-multi-021 | old_right_jev_wrong | 分析当前这首歌,然后聊聊编曲和作曲有什么区别 | ["current_song", "general_chat"] | ["current_song", "general_chat"] | ["current_song"] | {"confidence": 0.71, "probabilities": {"worldline": 0.11, "general_chat": 0.1, "tool_calling": 0.0, "current_song": 0.79}} | single Choice cannot express multi-route gold |
| manual-multi-025 | old_right_jev_wrong | 讲讲这首歌的背景,如果它当时没有发行会怎样,再帮我收藏 | ["current_song", "tool_calling", "worldline"] | ["current_song", "tool_calling", "worldline"] | ["worldline"] | {"confidence": 0.34, "probabilities": {"worldline": 0.51, "general_chat": 0.03, "tool_calling": 0.29, "current_song": 0.17}} | single Choice cannot express multi-route gold |
| foundation-e2e-current-song-followup | old_wrong_jev_right | 现在这首呢 | ["current_song"] | ["tool_calling"] | ["current_song"] | {"confidence": 0.95, "probabilities": {"current_song": 0.97, "tool_calling": 0.0, "worldline": 0.0, "general_chat": 0.03}} | existing semantic route disagrees with gold; manual cause review needed |
| rag-v0-001 | irrelevant_false_keep | 介绍一下周杰伦的音乐生涯 | {"evidence:parent-f920c4f8d6dcc713eb4888a7": "irrelevant"} | true | true | 0.95 | irrelevant false keep |
| rag-v0-001 | irrelevant_false_keep | 介绍一下周杰伦的音乐生涯 | {"evidence:parent-abd13550e82163b9f04dd8c1": "irrelevant"} | true | true | 0.91 | irrelevant false keep |
| rag-v0-001 | irrelevant_false_keep | 介绍一下周杰伦的音乐生涯 | {"evidence:parent-bc23e565000bbb36d4ec44e9": "irrelevant"} | true | true | 0.76 | irrelevant false keep |
| rag-v0-001 | irrelevant_false_keep | 介绍一下周杰伦的音乐生涯 | {"evidence:parent-0dc65cb1964f6289526a1cd8": "irrelevant"} | true | true | 0.74 | irrelevant false keep |
| rag-v0-001 | many_irrelevant_kept | 介绍一下周杰伦的音乐生涯 | {"irrelevant_ids": ["evidence:parent-f920c4f8d6dcc713eb4888a7", "evidence:parent-abd13550e82163b9f04dd8c1", "evidence:parent-bc23e565000bbb36d4ec44e9", "evidence:parent-0dc65cb1964f6289526a1cd8"]} | 5 | 4 | {"evidence:parent-f920c4f8d6dcc713eb4888a7": 0.95, "evidence:parent-abd13550e82163b9f04dd8c1": 0.91, "evidence:parent-bc23e565000bbb36d4ec44e9": 0.76, "evidence:parent-0dc65cb1964f6289526a1cd8": 0.74} | three or more irrelevant items remain after Jev |
| rag-v0-002 | irrelevant_false_keep | 周董的音乐风格有什么特点 | {"evidence:parent-de75bd50c15ff676ba87b5f8": "irrelevant"} | true | true | 0.88 | irrelevant false keep |
| rag-v0-002 | irrelevant_false_keep | 周董的音乐风格有什么特点 | {"evidence:parent-ab0b3104609bdcde1e432542": "irrelevant"} | true | true | 0.67 | irrelevant false keep |
| rag-v0-002 | irrelevant_false_keep | 周董的音乐风格有什么特点 | {"evidence:parent-0f47d2c44c117054a7095763": "irrelevant"} | true | true | 0.52 | irrelevant false keep |
| rag-v0-002 | irrelevant_false_keep | 周董的音乐风格有什么特点 | {"evidence:parent-2e9e3060bb0c77d64bdc4313": "irrelevant"} | true | true | 0.91 | irrelevant false keep |
| rag-v0-002 | many_irrelevant_kept | 周董的音乐风格有什么特点 | {"irrelevant_ids": ["evidence:parent-de75bd50c15ff676ba87b5f8", "evidence:parent-ab0b3104609bdcde1e432542", "evidence:parent-0f47d2c44c117054a7095763", "evidence:parent-2e9e3060bb0c77d64bdc4313"]} | 4 | 4 | {"evidence:parent-de75bd50c15ff676ba87b5f8": 0.88, "evidence:parent-ab0b3104609bdcde1e432542": 0.67, "evidence:parent-0f47d2c44c117054a7095763": 0.52, "evidence:parent-2e9e3060bb0c77d64bdc4313": 0.91} | three or more irrelevant items remain after Jev |
| rag-v0-004 | irrelevant_false_keep | 歌手周杰伦的背景故事 | {"evidence:parent-0dc65cb1964f6289526a1cd8": "irrelevant"} | true | true | 0.84 | irrelevant false keep |
| rag-v0-004 | irrelevant_false_keep | 歌手周杰伦的背景故事 | {"evidence:parent-62e917bc84f9d2a722710d2c": "irrelevant"} | true | true | 0.71 | irrelevant false keep |
| rag-v0-004 | irrelevant_false_keep | 歌手周杰伦的背景故事 | {"evidence:parent-abd13550e82163b9f04dd8c1": "irrelevant"} | true | true | 0.65 | irrelevant false keep |
| rag-v0-004 | irrelevant_false_keep | 歌手周杰伦的背景故事 | {"evidence:parent-72f268a2a2c3d7381851a0ca": "irrelevant"} | true | true | 0.92 | irrelevant false keep |
| rag-v0-004 | irrelevant_false_keep | 歌手周杰伦的背景故事 | {"evidence:parent-bc23e565000bbb36d4ec44e9": "irrelevant"} | true | true | 0.91 | irrelevant false keep |
| rag-v0-004 | irrelevant_false_keep | 歌手周杰伦的背景故事 | {"evidence:parent-ad18bce717cdf9110f9a631a": "irrelevant"} | true | true | 0.91 | irrelevant false keep |
| rag-v0-004 | many_irrelevant_kept | 歌手周杰伦的背景故事 | {"irrelevant_ids": ["evidence:parent-0dc65cb1964f6289526a1cd8", "evidence:parent-62e917bc84f9d2a722710d2c", "evidence:parent-abd13550e82163b9f04dd8c1", "evidence:parent-72f268a2a2c3d7381851a0ca", "evidence:parent-bc23e565000bbb36d4ec44e9", "evidence:parent-ad18bce717cdf9110f9a631a"]} | 6 | 6 | {"evidence:parent-0dc65cb1964f6289526a1cd8": 0.84, "evidence:parent-62e917bc84f9d2a722710d2c": 0.71, "evidence:parent-abd13550e82163b9f04dd8c1": 0.65, "evidence:parent-72f268a2a2c3d7381851a0ca": 0.92, "evidence:parent-bc23e565000bbb36d4ec44e9": 0.91, "evidence:parent-ad18bce717cdf9110f9a631a": 0.91} | three or more irrelevant items remain after Jev |
| rag-v0-007 | irrelevant_false_keep | 歌曲《青花瓷》体现了什么风格 | {"evidence:parent-69be3e2d5f415936f61a9a23": "irrelevant"} | true | true | 0.97 | irrelevant false keep |
| rag-v0-007 | irrelevant_false_keep | 歌曲《青花瓷》体现了什么风格 | {"evidence:parent-b236c730c6a014e5e0d3c00c": "irrelevant"} | true | true | 0.83 | irrelevant false keep |
| rag-v0-007 | irrelevant_false_keep | 歌曲《青花瓷》体现了什么风格 | {"evidence:parent-48f30e0b001ba4c93805f7d0": "irrelevant"} | true | true | 0.93 | irrelevant false keep |
| rag-v0-007 | many_irrelevant_kept | 歌曲《青花瓷》体现了什么风格 | {"irrelevant_ids": ["evidence:parent-69be3e2d5f415936f61a9a23", "evidence:parent-b236c730c6a014e5e0d3c00c", "evidence:parent-48f30e0b001ba4c93805f7d0"]} | 4 | 3 | {"evidence:parent-69be3e2d5f415936f61a9a23": 0.97, "evidence:parent-b236c730c6a014e5e0d3c00c": 0.83, "evidence:parent-48f30e0b001ba4c93805f7d0": 0.93} | three or more irrelevant items remain after Jev |
| rag-v0-023 | irrelevant_false_keep | 周杰伦和方文山有哪些合作关系 | {"evidence:parent-a5cf1d47acb23747609e6fd1": "irrelevant"} | true | true | 0.97 | irrelevant false keep |
| rag-v0-023 | irrelevant_false_keep | 周杰伦和方文山有哪些合作关系 | {"evidence:parent-35487c91c9077427f4346634": "irrelevant"} | true | true | 0.9 | irrelevant false keep |
| rag-v0-023 | irrelevant_false_keep | 周杰伦和方文山有哪些合作关系 | {"evidence:parent-2525a1d36535990fd748a5b4": "irrelevant"} | true | true | 0.8 | irrelevant false keep |
| rag-v0-023 | irrelevant_false_keep | 周杰伦和方文山有哪些合作关系 | {"evidence:parent-30fd04221dc89e7b2df6ac65": "irrelevant"} | true | true | 0.95 | irrelevant false keep |
| rag-v0-023 | many_irrelevant_kept | 周杰伦和方文山有哪些合作关系 | {"irrelevant_ids": ["evidence:parent-a5cf1d47acb23747609e6fd1", "evidence:parent-35487c91c9077427f4346634", "evidence:parent-2525a1d36535990fd748a5b4", "evidence:parent-30fd04221dc89e7b2df6ac65"]} | 5 | 4 | {"evidence:parent-a5cf1d47acb23747609e6fd1": 0.97, "evidence:parent-35487c91c9077427f4346634": 0.9, "evidence:parent-2525a1d36535990fd748a5b4": 0.8, "evidence:parent-30fd04221dc89e7b2df6ac65": 0.95} | three or more irrelevant items remain after Jev |
| rag-v0-024 | irrelevant_false_keep | 谁为周杰伦的歌曲作词 | {"evidence:parent-a5cf1d47acb23747609e6fd1": "irrelevant"} | true | true | 0.98 | irrelevant false keep |
| rag-v0-024 | irrelevant_false_keep | 谁为周杰伦的歌曲作词 | {"evidence:parent-e3418df3604de7d9a65ee225": "irrelevant"} | true | true | 0.76 | irrelevant false keep |
| rag-v0-024 | irrelevant_false_keep | 谁为周杰伦的歌曲作词 | {"evidence:parent-14c72d65f559008aede864d6": "irrelevant"} | true | true | 0.9 | irrelevant false keep |
| rag-v0-024 | irrelevant_false_keep | 谁为周杰伦的歌曲作词 | {"evidence:parent-69be3e2d5f415936f61a9a23": "irrelevant"} | true | true | 0.92 | irrelevant false keep |
| rag-v0-024 | many_irrelevant_kept | 谁为周杰伦的歌曲作词 | {"irrelevant_ids": ["evidence:parent-a5cf1d47acb23747609e6fd1", "evidence:parent-e3418df3604de7d9a65ee225", "evidence:parent-14c72d65f559008aede864d6", "evidence:parent-69be3e2d5f415936f61a9a23"]} | 6 | 4 | {"evidence:parent-a5cf1d47acb23747609e6fd1": 0.98, "evidence:parent-e3418df3604de7d9a65ee225": 0.76, "evidence:parent-14c72d65f559008aede864d6": 0.9, "evidence:parent-69be3e2d5f415936f61a9a23": 0.92} | three or more irrelevant items remain after Jev |
| rag-v0-026 | irrelevant_false_keep | 周杰伦与洪敬尧的编曲合作 | {"evidence:parent-790e32e522e2ba2f480a77ea": "irrelevant"} | true | true | 0.96 | irrelevant false keep |
| rag-v0-026 | irrelevant_false_keep | 周杰伦与洪敬尧的编曲合作 | {"evidence:parent-4db1234114fbccee395ff04f": "irrelevant"} | true | true | 0.94 | irrelevant false keep |
| rag-v0-026 | irrelevant_false_keep | 周杰伦与洪敬尧的编曲合作 | {"evidence:parent-25439435d1b600e21855d86b": "irrelevant"} | true | true | 0.95 | irrelevant false keep |
| rag-v0-026 | irrelevant_false_keep | 周杰伦与洪敬尧的编曲合作 | {"evidence:parent-5e931662cd87b086e0ac1a27": "irrelevant"} | true | true | 0.97 | irrelevant false keep |
| rag-v0-026 | irrelevant_false_keep | 周杰伦与洪敬尧的编曲合作 | {"evidence:parent-783134afb2ed899b455de9b7": "irrelevant"} | true | true | 0.95 | irrelevant false keep |
| rag-v0-026 | many_irrelevant_kept | 周杰伦与洪敬尧的编曲合作 | {"irrelevant_ids": ["evidence:parent-790e32e522e2ba2f480a77ea", "evidence:parent-4db1234114fbccee395ff04f", "evidence:parent-25439435d1b600e21855d86b", "evidence:parent-5e931662cd87b086e0ac1a27", "evidence:parent-783134afb2ed899b455de9b7"]} | 5 | 5 | {"evidence:parent-790e32e522e2ba2f480a77ea": 0.96, "evidence:parent-4db1234114fbccee395ff04f": 0.94, "evidence:parent-25439435d1b600e21855d86b": 0.95, "evidence:parent-5e931662cd87b086e0ac1a27": 0.97, "evidence:parent-783134afb2ed899b455de9b7": 0.95} | three or more irrelevant items remain after Jev |
| rag-v0-037 | irrelevant_false_keep | 周杰伦2003年的重要经历是什么 | {"evidence:parent-f920c4f8d6dcc713eb4888a7": "irrelevant"} | true | true | 0.93 | irrelevant false keep |
| rag-v0-037 | irrelevant_false_keep | 周杰伦2003年的重要经历是什么 | {"evidence:parent-09a09073bd75874b3cc5b72a": "irrelevant"} | true | true | 0.97 | irrelevant false keep |
| rag-v0-037 | irrelevant_false_keep | 周杰伦2003年的重要经历是什么 | {"evidence:parent-a83673d2b967ec913e6b0ef3": "irrelevant"} | true | true | 0.75 | irrelevant false keep |
| rag-v0-037 | irrelevant_false_keep | 周杰伦2003年的重要经历是什么 | {"evidence:parent-fec4e2f5d74c8fe65d787dd1": "irrelevant"} | true | true | 0.87 | irrelevant false keep |
| rag-v0-037 | many_irrelevant_kept | 周杰伦2003年的重要经历是什么 | {"irrelevant_ids": ["evidence:parent-f920c4f8d6dcc713eb4888a7", "evidence:parent-09a09073bd75874b3cc5b72a", "evidence:parent-a83673d2b967ec913e6b0ef3", "evidence:parent-fec4e2f5d74c8fe65d787dd1"]} | 4 | 4 | {"evidence:parent-f920c4f8d6dcc713eb4888a7": 0.93, "evidence:parent-09a09073bd75874b3cc5b72a": 0.97, "evidence:parent-a83673d2b967ec913e6b0ef3": 0.75, "evidence:parent-fec4e2f5d74c8fe65d787dd1": 0.87} | three or more irrelevant items remain after Jev |
| rag-v0-040 | irrelevant_false_keep | 周杰伦的音乐生涯时间线 | {"evidence:parent-f920c4f8d6dcc713eb4888a7": "irrelevant"} | true | true | 0.89 | irrelevant false keep |
| rag-v0-040 | irrelevant_false_keep | 周杰伦的音乐生涯时间线 | {"evidence:parent-abd13550e82163b9f04dd8c1": "irrelevant"} | true | true | 0.77 | irrelevant false keep |
| rag-v0-040 | irrelevant_false_keep | 周杰伦的音乐生涯时间线 | {"evidence:parent-1f9ae0cea1bde44c19e39a75": "irrelevant"} | true | true | 0.92 | irrelevant false keep |
| rag-v0-040 | irrelevant_false_keep | 周杰伦的音乐生涯时间线 | {"evidence:parent-62e917bc84f9d2a722710d2c": "irrelevant"} | true | true | 0.79 | irrelevant false keep |
| rag-v0-040 | irrelevant_false_keep | 周杰伦的音乐生涯时间线 | {"evidence:parent-bc23e565000bbb36d4ec44e9": "irrelevant"} | true | true | 0.63 | irrelevant false keep |
| rag-v0-040 | irrelevant_false_keep | 周杰伦的音乐生涯时间线 | {"evidence:parent-0dc65cb1964f6289526a1cd8": "irrelevant"} | true | true | 0.72 | irrelevant false keep |
| rag-v0-040 | many_irrelevant_kept | 周杰伦的音乐生涯时间线 | {"irrelevant_ids": ["evidence:parent-f920c4f8d6dcc713eb4888a7", "evidence:parent-abd13550e82163b9f04dd8c1", "evidence:parent-1f9ae0cea1bde44c19e39a75", "evidence:parent-62e917bc84f9d2a722710d2c", "evidence:parent-bc23e565000bbb36d4ec44e9", "evidence:parent-0dc65cb1964f6289526a1cd8"]} | 6 | 6 | {"evidence:parent-f920c4f8d6dcc713eb4888a7": 0.89, "evidence:parent-abd13550e82163b9f04dd8c1": 0.77, "evidence:parent-1f9ae0cea1bde44c19e39a75": 0.92, "evidence:parent-62e917bc84f9d2a722710d2c": 0.79, "evidence:parent-bc23e565000bbb36d4ec44e9": 0.63, "evidence:parent-0dc65cb1964f6289526a1cd8": 0.72} | three or more irrelevant items remain after Jev |
| rag-v0-042 | irrelevant_false_keep | 周杰伦的英文资料 | {"evidence:parent-e4f78657cba475a6ba1dba38": "irrelevant"} | true | true | 0.68 | irrelevant false keep |
| rag-v0-042 | irrelevant_false_keep | 周杰伦的英文资料 | {"evidence:parent-e904178de003b1944b83fb70": "irrelevant"} | true | true | 0.5 | irrelevant false keep |
| rag-v0-043 | irrelevant_false_keep | 周杰伦的采访资料 | {"evidence:parent-3a44ee7b3c184f20a127fb50": "irrelevant"} | true | true | 0.94 | irrelevant false keep |
| rag-v0-043 | irrelevant_false_keep | 周杰伦的采访资料 | {"evidence:parent-95026191ffad2d2e2c90c600": "irrelevant"} | true | true | 0.86 | irrelevant false keep |
| rag-v0-043 | irrelevant_false_keep | 周杰伦的采访资料 | {"evidence:parent-c16184f17fca06e81503b474": "irrelevant"} | true | true | 0.84 | irrelevant false keep |
| rag-v0-043 | irrelevant_false_keep | 周杰伦的采访资料 | {"evidence:parent-9296714d8c041271766afc1a": "irrelevant"} | true | true | 0.91 | irrelevant false keep |
| rag-v0-043 | many_irrelevant_kept | 周杰伦的采访资料 | {"irrelevant_ids": ["evidence:parent-3a44ee7b3c184f20a127fb50", "evidence:parent-95026191ffad2d2e2c90c600", "evidence:parent-c16184f17fca06e81503b474", "evidence:parent-9296714d8c041271766afc1a"]} | 5 | 4 | {"evidence:parent-3a44ee7b3c184f20a127fb50": 0.94, "evidence:parent-95026191ffad2d2e2c90c600": 0.86, "evidence:parent-c16184f17fca06e81503b474": 0.84, "evidence:parent-9296714d8c041271766afc1a": 0.91} | three or more irrelevant items remain after Jev |
| rag-v0-044 | irrelevant_false_keep | 周杰伦的新闻资料，不要现场版 | {"evidence:parent-5e614cf3933d7f2fd173b0b7": "irrelevant"} | true | true | 0.87 | irrelevant false keep |
| rag-v0-044 | irrelevant_false_keep | 周杰伦的新闻资料，不要现场版 | {"evidence:parent-840ab7580c0ffbbf813681bf": "irrelevant"} | true | true | 0.65 | irrelevant false keep |
| rag-v0-044 | irrelevant_false_keep | 周杰伦的新闻资料，不要现场版 | {"evidence:parent-531672f53462b6956869e0eb": "irrelevant"} | true | true | 0.84 | irrelevant false keep |
| rag-v0-044 | irrelevant_false_keep | 周杰伦的新闻资料，不要现场版 | {"evidence:parent-396d8237183dea741ebce052": "irrelevant"} | true | true | 0.79 | irrelevant false keep |
| rag-v0-044 | many_irrelevant_kept | 周杰伦的新闻资料，不要现场版 | {"irrelevant_ids": ["evidence:parent-5e614cf3933d7f2fd173b0b7", "evidence:parent-840ab7580c0ffbbf813681bf", "evidence:parent-531672f53462b6956869e0eb", "evidence:parent-396d8237183dea741ebce052"]} | 6 | 4 | {"evidence:parent-5e614cf3933d7f2fd173b0b7": 0.87, "evidence:parent-840ab7580c0ffbbf813681bf": 0.65, "evidence:parent-531672f53462b6956869e0eb": 0.84, "evidence:parent-396d8237183dea741ebce052": 0.79} | three or more irrelevant items remain after Jev |
| rag-v0-049 | irrelevant_false_keep | 第十三届金曲奖中，《范特西》的《爱在西元前》《威廉古堡》《双截棍》分别对应哪些创作奖项和得奖者？ | {"evidence:parent-8d6fb6a17ca9c6a0c7a56e15": "irrelevant"} | true | true | 0.97 | irrelevant false keep |
| rag-v0-049 | irrelevant_false_keep | 第十三届金曲奖中，《范特西》的《爱在西元前》《威廉古堡》《双截棍》分别对应哪些创作奖项和得奖者？ | {"evidence:parent-838a72da46cd723ab7d11da0": "irrelevant"} | true | true | 0.78 | irrelevant false keep |
| rag-v0-049 | irrelevant_false_keep | 第十三届金曲奖中，《范特西》的《爱在西元前》《威廉古堡》《双截棍》分别对应哪些创作奖项和得奖者？ | {"evidence:parent-820d5f9eb98ed96ee8dec9ef": "irrelevant"} | true | true | 0.96 | irrelevant false keep |
| rag-v0-049 | irrelevant_false_keep | 第十三届金曲奖中，《范特西》的《爱在西元前》《威廉古堡》《双截棍》分别对应哪些创作奖项和得奖者？ | {"evidence:parent-2749bf73a755e44e7ed623e9": "irrelevant"} | true | true | 0.95 | irrelevant false keep |
| rag-v0-049 | irrelevant_false_keep | 第十三届金曲奖中，《范特西》的《爱在西元前》《威廉古堡》《双截棍》分别对应哪些创作奖项和得奖者？ | {"evidence:parent-3bbc1733f68650724acafd42": "irrelevant"} | true | true | 0.73 | irrelevant false keep |
| rag-v0-049 | many_irrelevant_kept | 第十三届金曲奖中，《范特西》的《爱在西元前》《威廉古堡》《双截棍》分别对应哪些创作奖项和得奖者？ | {"irrelevant_ids": ["evidence:parent-8d6fb6a17ca9c6a0c7a56e15", "evidence:parent-838a72da46cd723ab7d11da0", "evidence:parent-820d5f9eb98ed96ee8dec9ef", "evidence:parent-2749bf73a755e44e7ed623e9", "evidence:parent-3bbc1733f68650724acafd42"]} | 5 | 5 | {"evidence:parent-8d6fb6a17ca9c6a0c7a56e15": 0.97, "evidence:parent-838a72da46cd723ab7d11da0": 0.78, "evidence:parent-820d5f9eb98ed96ee8dec9ef": 0.96, "evidence:parent-2749bf73a755e44e7ed623e9": 0.95, "evidence:parent-3bbc1733f68650724acafd42": 0.73} | three or more irrelevant items remain after Jev |
| rag-v0-051 | irrelevant_false_keep | 周杰伦20周年黑胶复刻的母带制作和刻片分别由谁负责？ | {"evidence:parent-804bfbd4815dc7b68bae851a": "irrelevant"} | true | true | 0.96 | irrelevant false keep |
| rag-v0-053 | irrelevant_false_keep | 杰威尔公布的周杰伦珍藏DEMO中，哪首此前未发表？ | {"evidence:parent-4b227545d72dc08d224836b1": "irrelevant"} | true | true | 0.97 | irrelevant false keep |
| rag-v0-058 | irrelevant_false_keep | 《流浪詩人》由谁作词，采用什么对唱形式？ | {"evidence:parent-124172c91dc3903b46ad8310": "irrelevant"} | true | true | 0.95 | irrelevant false keep |
| rag-v0-058 | irrelevant_false_keep | 《流浪詩人》由谁作词，采用什么对唱形式？ | {"evidence:parent-90ca17a56472b07f07a50fcf": "irrelevant"} | true | true | 0.62 | irrelevant false keep |
| rag-v0-059 | irrelevant_false_keep | 《蘭亭序》的歌词由谁写，索尼称它取材于什么？ | {"evidence:parent-a5cf1d47acb23747609e6fd1": "irrelevant"} | true | true | 0.84 | irrelevant false keep |
| rag-v0-059 | irrelevant_false_keep | 《蘭亭序》的歌词由谁写，索尼称它取材于什么？ | {"evidence:parent-42300f5a78e01d08b5d9c89f": "irrelevant"} | true | true | 0.53 | irrelevant false keep |
| rag-v0-061 | irrelevant_false_keep | 方文山在采访中如何解释用流行歌曲传承传统文化？ | {"evidence:parent-c8a84e2395d740bce0703ad3": "irrelevant"} | true | true | 0.53 | irrelevant false keep |
| rag-v0-061 | irrelevant_false_keep | 方文山在采访中如何解释用流行歌曲传承传统文化？ | {"evidence:parent-afca7c2d173dc002cca86aef": "irrelevant"} | true | true | 0.93 | irrelevant false keep |
| rag-v0-061 | irrelevant_false_keep | 方文山在采访中如何解释用流行歌曲传承传统文化？ | {"evidence:parent-f2a037ccb12b3b5dc3d62d44": "irrelevant"} | true | true | 0.89 | irrelevant false keep |
| rag-v0-061 | irrelevant_false_keep | 方文山在采访中如何解释用流行歌曲传承传统文化？ | {"evidence:parent-7eb1ff1a7882c02c42b34864": "irrelevant"} | true | true | 0.82 | irrelevant false keep |
| rag-v0-061 | irrelevant_false_keep | 方文山在采访中如何解释用流行歌曲传承传统文化？ | {"evidence:parent-e533e554073401e40d93d72e": "irrelevant"} | true | true | 0.89 | irrelevant false keep |
| rag-v0-061 | many_irrelevant_kept | 方文山在采访中如何解释用流行歌曲传承传统文化？ | {"irrelevant_ids": ["evidence:parent-c8a84e2395d740bce0703ad3", "evidence:parent-afca7c2d173dc002cca86aef", "evidence:parent-f2a037ccb12b3b5dc3d62d44", "evidence:parent-7eb1ff1a7882c02c42b34864", "evidence:parent-e533e554073401e40d93d72e"]} | 5 | 5 | {"evidence:parent-c8a84e2395d740bce0703ad3": 0.53, "evidence:parent-afca7c2d173dc002cca86aef": 0.93, "evidence:parent-f2a037ccb12b3b5dc3d62d44": 0.89, "evidence:parent-7eb1ff1a7882c02c42b34864": 0.82, "evidence:parent-e533e554073401e40d93d72e": 0.89} | three or more irrelevant items remain after Jev |
| rag-v0-063 | irrelevant_false_keep | 环球音乐日本的艺人简介怎样描述2022年《最伟大的作品》的全球专辑销售排名？ | {"evidence:parent-96cd9c2ace6ea0307dcc280d": "irrelevant"} | true | true | 0.73 | irrelevant false keep |
| rag-v0-063 | irrelevant_false_keep | 环球音乐日本的艺人简介怎样描述2022年《最伟大的作品》的全球专辑销售排名？ | {"evidence:parent-f920c4f8d6dcc713eb4888a7": "irrelevant"} | true | true | 0.77 | irrelevant false keep |
| rag-v0-064 | irrelevant_false_keep | 马来亚大学的《中国风》研究摘要分析了哪两首周杰伦歌曲，使用了哪些研究方法？ | {"evidence:parent-ec3acdb6175edab04bc7dd7c": "irrelevant"} | true | true | 0.93 | irrelevant false keep |
| rag-v0-064 | irrelevant_false_keep | 马来亚大学的《中国风》研究摘要分析了哪两首周杰伦歌曲，使用了哪些研究方法？ | {"evidence:parent-c8c703adc1264135ac042cb4": "irrelevant"} | true | true | 0.54 | irrelevant false keep |
| rag-v0-003 | irrelevant_false_keep | Jay Chou 对华语音乐有什么影响 | {"evidence:parent-555cb8032618fee5a6d75449": "irrelevant"} | true | true | 0.84 | irrelevant false keep |
| rag-v0-003 | irrelevant_false_keep | Jay Chou 对华语音乐有什么影响 | {"evidence:parent-57488ecc3d566479b885475f": "irrelevant"} | true | true | 0.89 | irrelevant false keep |
| rag-v0-003 | irrelevant_false_keep | Jay Chou 对华语音乐有什么影响 | {"evidence:parent-108af5e7239094ee08fc6607": "irrelevant"} | true | true | 0.93 | irrelevant false keep |
| rag-v0-003 | irrelevant_false_keep | Jay Chou 对华语音乐有什么影响 | {"evidence:parent-ba2372d43eb5413814eaf746": "irrelevant"} | true | true | 0.84 | irrelevant false keep |
| rag-v0-003 | irrelevant_false_keep | Jay Chou 对华语音乐有什么影响 | {"evidence:parent-0f47d2c44c117054a7095763": "irrelevant"} | true | true | 0.89 | irrelevant false keep |
| rag-v0-003 | many_irrelevant_kept | Jay Chou 对华语音乐有什么影响 | {"irrelevant_ids": ["evidence:parent-555cb8032618fee5a6d75449", "evidence:parent-57488ecc3d566479b885475f", "evidence:parent-108af5e7239094ee08fc6607", "evidence:parent-ba2372d43eb5413814eaf746", "evidence:parent-0f47d2c44c117054a7095763"]} | 5 | 5 | {"evidence:parent-555cb8032618fee5a6d75449": 0.84, "evidence:parent-57488ecc3d566479b885475f": 0.89, "evidence:parent-108af5e7239094ee08fc6607": 0.93, "evidence:parent-ba2372d43eb5413814eaf746": 0.84, "evidence:parent-0f47d2c44c117054a7095763": 0.89} | three or more irrelevant items remain after Jev |

## 8. End-to-End Diagnostic Set

未运行：Router 全量 Jev accuracy 低于 Baseline，三个组件级门槛未全部通过；现有 E2E acceptance 用固定路由与测试替身，无法据此伪造主 LLM task success、answer quality、groundedness 或 personalization 指标。

## 9. Live 建议

本次没有达到可建议立即切 Live 的模块。Memory/Context 的诊断指标满足部分数值门槛，独立样本和 required 类型覆盖尚不足。

- Router：继续 Shadow。全量 Jev 准确率低于 Baseline，且 13 个 old_right_jev_wrong；门控后仅 14 条，样本尚不足以证明可安全切换。
- Memory：继续 Shadow。本地 demo 样本仅 9 个不同 memory ID；若有关键 memory 被 Jev 删除，直接阻塞 Live。即使没有误删，也需更多独立用户/真实查询和复核标签。
- Context：本诊断集达到 required 100%、useful 无下降、token/irrelevant 有减少，具备进一步单独验证的信号。仍建议继续 Shadow：本集 required 只有 system/query，尚未覆盖 required RAG/tool/handoff；切换前需复核非 gold parent 的标签，并扩大真实 ContextPool/多轮样本。本次不改生产开关。

## 10. 已知限制

- Context 是由历史冻结 RAG trace 重建的 Foundation 诊断 pool，system prompt 为固定诊断文本；不是线上整条会话轨迹。required 仅覆盖 system/query，未覆盖 required RAG/tool/handoff。Gold V4 的 relevant_parent_ids 是证据相关性，不等于完整的上下文效用人工盲审。
- Router 的 2 条 E2E follow-up 虽保存 recent_context，但两条生产路由路径都没有消费它；多意图包含在全量统计，而 Jev 当前 Choice 只返回单路由。
- `jev-latest` 可变；每次复跑须对 requested_model、response_model 与 evaluation_timestamp 分组，不跨版本直接混算。
- 首轮 45 条 Router 试跑曾有 1 次 TypeSafe 超时；最终 47 条重跑 47/47 有效。报告聚合只采用最终重跑，未把不同 evaluation_timestamp 混算。
- 各组件只作单独判断，无 Jev 总分。Shadow 开关未变更。

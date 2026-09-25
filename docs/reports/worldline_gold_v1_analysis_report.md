# WorldLine Gold Evaluation V1 质量分析报告

## Executive Summary

- Gold V1 包含 **8 条已审核 artist → genre 关系**，覆盖 C-pop、Hip-Hop、R&B；Real Gold baseline 将其转换为 18 个执行 case，结果为 **18/18 通过**。
- Semantic relation precision、recall 与 grounded relation precision 均为 **100%（8/8）**；8 条关系全部有 evidence，exact span 与冻结 evidence text 全部一致。
- 8 个 artist identity 全部成功解析到审核确认的 NetEase catalog artist，resolved 为 **8/8**，ambiguous 与 unresolved 均为 0。
- 这些结果证明当前链路能够重放已审核、受控词表内的正样本。数据集没有 negative、ambiguous 或 unresolved Gold case，且只有 Hip-Hop 可执行，因此当前结果不能衡量错误关系拒绝能力、开放世界泛化能力或完整 session/checkpoint 可靠性。

## 1. Dataset Overview

### 1.1 数据规模与 Evaluation 展开

| 项目 | 数量 |
|---|---:|
| 正式 Gold cases | 8 |
| Accepted Gold cases | 8 |
| Rejected Gold cases | 0 |
| 唯一 sources | 7 |
| Evidence records | 8 |
| Real Gold Evaluation cases | 18 |
| Semantic expansion cases | 8 |
| Identity resolution cases | 8 |
| Genre exploration cases | 2 |

G.E.M. 与陈奕迅共用同一条 i-D evidence，因此 8 条 Gold case 对应 7 个唯一 source。Evaluation 的 18 个 case 是由同一批 Gold 数据派生出的不同任务，不代表有 18 条独立音乐关系。

### 1.2 Genre 分布

| Genre | Gold cases | 占比 | 当前可执行探索 |
|---|---:|---:|---|
| C-pop | 3 | 37.5% | 否 |
| Hip-Hop | 3 | 37.5% | 是 |
| R&B | 2 | 25.0% | 否 |

Jazz Hip-Hop 已存在于 registry v2，但未出现在正式 Gold V1；它当前为 `executable=false`、`expandable=false`。

### 1.3 Region 分布

| Region | Gold cases | 占比 |
|---|---:|---:|
| 华语（`cjk_chinese`） | 3 | 37.5% |
| 欧美（`western`） | 3 | 37.5% |
| 日本（`japanese`） | 1 | 12.5% |
| 韩国（`korean`） | 1 | 12.5% |

V1 达到了华语、欧美、日韩覆盖目标，但日本和韩国都只有一个样本，无法用于稳定的区域对比。

### 1.4 Source 类型分布

按 Gold case 引用次数统计：

| Source type | Evidence references | 占比 |
|---|---:|---:|
| `streaming_catalog_editorial` | 3 | 37.5% |
| `music_editorial` | 2 | 25.0% |
| `official_label_biography` | 1 | 12.5% |
| `official_artist_profile` | 1 | 12.5% |
| `government_portal_editorial` | 1 | 12.5% |

按唯一 source 统计时，`music_editorial` 为 1 个，其余分布不变，共 7 个唯一 source。3 条关系使用 streaming catalog editorial；它们已通过人工审核，但仍比官方 artist profile 或 label biography 更依赖页面分类语境。Kendrick Lamar、Nas、Beyoncé 的 review notes 已保留该来源 caveat。

## 2. Semantic Expansion Analysis

Real Gold baseline 对 8 条 artist → genre 关系逐条执行 Semantic Expansion：

| 指标 | 结果 |
|---|---:|
| Semantic relation precision | 100%（8/8） |
| Semantic relation recall | 100%（8/8） |
| Grounded relation precision | 100%（8/8） |
| Accepted relations | 8 |
| Rejected relations | 0 |
| Unsupported relation count | 0 |

每条 case 都产生 1 个 candidate，随后被 validator 接受并投影为 `associated_with` relation。trace 汇总为 candidate 8、accepted 8、rejected 0。

该结果验证了以下链路：

1. 已注册 genre 能被识别。
2. 已审核 `supported_claims` 能形成 relation candidate。
3. Candidate → Validator → Graph Projection 可以保留 evidence id。
4. artist catalog node 与 genre knowledge node 能按预期形成 `associated_with` edge。

当前 Gold 全部是 accepted positive case，`expected_rejection_accuracy` 为 N/A。因此 100% precision/recall 只描述这 8 条受控正样本，尚未证明系统能稳定拒绝无 evidence、错误 genre 或错误实体关系。

## 3. Evidence Grounding Analysis

| 指标 | 结果 |
|---|---:|
| 有 evidence 的 Gold cases | 8/8（100%） |
| Evidence provenance validity | 8/8（100%） |
| Exact span 定位通过 | 8/8（100%） |
| Content hash 一致 | 8/8（100%） |
| Source ledger 引用有效 | 8/8（100%） |
| 唯一 sources | 7 |

每条 evidence 都满足：

- `source_id` 能在 Gold source ledger 中找到。
- `exact_span == evidence_text[span_start:span_end]`。
- evidence text 的 SHA-256 与 ledger hash 一致。
- Semantic Expansion 的输出 evidence id 被 trace 与 projected relation 保存。

当前 baseline 中没有独立的 evidence-grounding task，因此报告里的 standalone `citation_validity` 与 `grounding_validator_accuracy` 为 N/A。现有 100% 结论来自 semantic case 的 provenance 与 evidence integrity 检查，不等价于 narrative citation 或端到端 UI citation 的覆盖率。

## 4. Identity Resolution Analysis

| Identity 状态 | 数量 | 占比 |
|---|---:|---:|
| Resolved | 8 | 100% |
| Ambiguous | 0 | 0% |
| Unresolved | 0 | 0% |

相关 baseline 指标：

- Knowledge → catalog resolution success: **100%（8/8）**
- Catalog identity accuracy: **100%（8/8）**
- False executable identity count: **0**

8 条 Gold case 都包含审核确认的 knowledge artist id、NetEase catalog reference 和必要 aliases。EntityBridge 重放时，8 个 catalog node id 均与预期一致。

Gold V1 没有 ambiguous 或 unresolved identity case，因此 `ambiguity_detection_rate` 和 `unresolved_reason_accuracy` 为 N/A。当前结果验证准确映射能力，没有验证冲突候选、短名称碰撞或缺少 catalog artist 时的拒绝表现。

## 5. Failure Analysis

### 5.1 Baseline 执行失败

Real Gold baseline 没有执行失败：18 passed、0 failed、0 skipped、0 errors。

### 5.2 Primary pending candidates

| Candidate | 当前状态 | 分类 | 证据 |
|---|---|---|---|
| 蔡依林 → C-pop | Relation pending；Evidence pending；Identity resolved | Weak evidence | 当前 exact span 为“華語樂壇天后Jolin蔡依林”。它能证明艺人属于华语乐坛，但不能直接证明 registry 中的 C-pop 关系。需要明确把蔡依林归类为 C-pop/Chinese Pop 的直接来源。 |
| Nujabes → Jazz Hip-Hop | Relation pending；Evidence pending；Identity resolved | Source quality | 当前来源为 retailer release description。人工审核认为来源质量不足，尚不能作为正式 Gold。Jazz Hip-Hop 已注册，因此未进入 Gold 的直接原因是 source quality，而非 genre 未注册。 |

### 5.3 Backup candidates

| Candidate | 当前状态 | 分类 | 说明 |
|---|---|---|---|
| MC HotDog | 全部 pending | Review incomplete | 仅为 Hip-Hop backup hypothesis，尚未进入 source/identity 最终审核。 |
| Alicia Keys | 全部 pending | Review incomplete | 仅为 R&B backup hypothesis，尚未进入 source/identity 最终审核。 |
| Common | 全部 pending | Identity risk / review incomplete | 常见艺名存在名称碰撞风险，但目前没有人工结论，不能标记为 ambiguous。 |
| Dynamic Duo | 全部 pending | Identity risk / review incomplete | 需要确认 group identity，避免与成员页面混淆；目前没有人工结论。 |

### 5.4 分类汇总

- **Weak evidence：**1 个已知 case，蔡依林。
- **Source quality：**1 个已知 case，Nujabes。
- **Identity ambiguity：**0 个已确认 case；Common、Dynamic Duo 等只有待审核风险，不能当作已确认 ambiguity。
- **Registry limitation：**0 个 candidate 因 genre 未注册而被排除。执行层仍有限制：只有 Hip-Hop 为 executable/expandable；C-pop、R&B、Jazz Hip-Hop 目前只能参与 artist → genre 展示，不能执行 genre → artist 探索。
- **Review incomplete：**4 个 backup candidates 尚未完成 relation、evidence 与 identity 审核。

## 6. Baseline Conclusion

### 6.1 已验证能力

- 在 registry v2 和审核 evidence 的约束下，artist → genre Semantic Expansion 对 8 条 Gold positive case 全部正确。
- Evidence source、exact span、content hash 和 projected relation 的 provenance 能保持一致。
- 8 个 knowledge artist 均能解析到人工审核确认的 NetEase catalog artist。
- Hip-Hop genre → artist 的 2 个执行 case 均成功，action gate、evidence validation 与 catalog resolution 均通过。
- Synthetic Baseline 与 Real Gold Baseline 保持独立数据源和独立统计。

### 6.2 未覆盖能力

- 错误关系、无 evidence、未注册 genre、ambiguous identity、unresolved identity 等 negative Gold 场景。
- C-pop、R&B、Jazz Hip-Hop 的 genre → artist 执行能力。
- Influence、Era、Scene、similar artist 等其他语义关系。
- Narrative citation 与前端 citation 展示的端到端正确性。
- 本次 real-gold run 没有 PostgreSQL tier，因此 graph merge、session continuity、checkpoint recovery、post-recovery exploration 与 failure atomicity 均为 N/A。
- 数据规模只有 8 条 accepted relation，区域和来源类型覆盖不足以支持广泛质量结论。

### 6.3 下一阶段优化方向

1. **先补评测盲区。**增加经过人工审核的 negative、ambiguous 和 unresolved Gold case，使 rejection、ambiguity detection 与 unresolved reason 指标有真实分母。
2. **完成 pending evidence 审核。**蔡依林需要直接支持 C-pop 的来源；Nujabes 需要替换或补充高质量、可冻结且直接支持 Jazz Hip-Hop 的来源。
3. **增加来源多样性。**降低对 streaming catalog 分类的依赖，优先补充官方 artist profile、label biography、权威音乐媒体或机构资料。
4. **执行可靠性基线。**在现有 Gold 数据不变的前提下运行 PostgreSQL tier，补齐 graph merge、session continuity 与 checkpoint recovery 指标。
5. **再决定 genre executor 扩围。**在 C-pop、R&B、Jazz Hip-Hop 拥有足够的正负 Gold evidence 后，再评估开启 executable/expandable；当前数据不足以支持直接开放。

## 7. 分析口径与数据来源

本报告只分析当前仓库内已冻结的 Evaluation artifacts：

- `ai-service/evals/datasets/worldline_gold_v1.jsonl`
- `ai-service/evals/fixtures/worldline_gold_v1_sources.jsonl`
- `ai-service/evals/reports/worldline_gold_v1_metrics.json`
- `ai-service/evals/reports/worldline_gold_v1_results.jsonl`
- `ai-service/evals/worldline_gold_v1/candidate_review/candidate_review_tracking.jsonl`
- `ai-service/evals/worldline_gold_v1/candidate_review/candidate_pool.jsonl`
- `ai-service/evals/worldline_gold_v1/source_review/batch_a_primary_source_candidates.jsonl`
- `ai-service/app/agent/foundation/subgraphs/worldline/registry/genres_v2.yaml`

本阶段没有重新运行或修改业务逻辑、RAG、Evaluation 逻辑或 Gold Dataset。

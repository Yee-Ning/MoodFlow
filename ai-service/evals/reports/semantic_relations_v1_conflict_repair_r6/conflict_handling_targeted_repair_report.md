# Conflict Handling Targeted Repair — r6

P0 尚未完整验收：真实 conflict 从 **0/3 → 2/3**；Era 1/1、Scene 1/1、Influence 0/1。正式人工审核 cohort 从 17/25 → 19/25。未修改 Gold、评分函数、category gate、旧 freeze；未执行独立 P1 Direction / Evidence Mismatch 修复。

`baseline_ready=true`、`can_enter_targeted_repairs=true` 保持成立，表示审核覆盖允许修复。**不等于 P0 已达到 3/3**；`p0_conflict_target_met=false`、`can_enter_p1_direction_repair=false`。

## 设计与改动范围

1. Era：在既有结构/claim/span/time 校验后，将作品具名的 release 日期与 normalized belongs_to_era 做兼容。年份必须支持目标 decade，录制日期、不同作品、艺术家不同发行活动、再版/版本不能合并为同一 release event。已验证的同一作品 release event 跨 decade 聚合；保留每个互斥 target、双方 evidence、temporal_conflict；start/end=null、precision=unknown，confidence 使用既有 temporal conflict 规则。

2. Scene：仅做 scene/movement/community 后缀别名规范化，验证之后映射到现有身份或更完整的 scene 名。具名 frontrunner/membership 可支持归属；成员第一人称须由具名引用标题绑定，并验证同一 scene 的否定/指代作用域。反证单独不输出关系；有已验证 support 才进入 attribution aggregation。双方 claim IDs 与 stance 存在 evidence/provenance；归属不确定时不派生确定的 artist scene association。

3. Influence：上下文方向兼容只对同一 relation key 的潜在正反断言启用，要求结构、证据与强度检查已通过。列表中的借鉴来源必须有明确的借鉴主体；采访否认必须有 source 影响提问、具名 respondent 与 target 的上下文绑定。唯一 surname alias 可匹配来源；Italian ‘direi di no’ 保留限定否认 stance，不改成绝对无影响。无问题/主语/绑定时继续拒绝。standalone Pixies failures 仍保留；未新增通用 P1 方向补全。

4. 使用原有 DTO/projector/merge 接口。冲突写入 metadata 的 boolean 和 conflict_flags；evidence 中保留原始 claim ID/span。证据容量先保留相反 stance，overflow 明示；图描述明确冲突，后续回合不会恢复确定措辞。Era 来源身份仍不可变，只有已派生的 relation annotation 可以复用既有 source；真实 provenance drift 仍触发 source_conflict。重复合并相同 DTO 保持幂等。validator 的 negated_* rejection 表示不能当作正事实；aggregation trace 会区分可路由的 contradiction 和被结构/方向拒绝的 excluded。

接受阈值 0.85、证据上限 8、既有 confidence penalty 和全部 evaluator 指标/分母保持不变。没有新依赖、case_id 条件或真实实体专用规则。所有算法均只读取 runtime documents/claims，未读取 Gold/receipt/review notes。

| 文件 | 修改目的 |
|---|---|
| app/agent/foundation/subgraphs/worldline/conflict_semantics.py | 有界别名、引用主体、证据平衡、不可变来源、多轮冲突描述、聚合 trace |
| app/agent/foundation/subgraphs/worldline/era_expansion.py | release 兼容、跨 decade event aggregation、冲突投影及幂等合并 |
| app/agent/foundation/subgraphs/worldline/scene_expansion.py | 归属/否认 scope、别名 normalization、attribution aggregation 和多轮保留 |
| app/agent/foundation/subgraphs/worldline/influence_expansion.py | 仅 paired conflict 的上下文方向验证、限定否认、平衡证据及冲突投影 |
| tests/test_worldline_conflict_handling.py | 独立名称的正反/边界/溢出/多轮/来源篡改专项测试 |
| tests/test_worldline_scene_expansion.py | 更精确的否定诊断断言；拒绝行为和年份校验要求保留 |
| evals/tests/historical_scope.py, test_semantic_relations_review_r5.py, test_semantic_relations_review_r6.py | 28 个审核期 runtime hash 对照原始 Git 创建提交；不错误禁止之后授权修复，旧 freeze 仍全量逐字节验证 |
| evals/conflict_handling_targeted_repair_report.py, evals/tests/test_conflict_handling_targeted_repair_report.py | 只读结果的报告组装与导出审计，无评分变更 |

文档索引更新于 evals/README.md；本轮独立导出位于 evals/reports/semantic_relations_v1_conflict_repair_r6/。

## 修复前后指标

每项同时展示 overall / era / scene / influence。N/A 不等于 100%；Evidence Coverage 是所需 unique claim 覆盖，traceability 是输出证据计数（同一证据可绑定多个关系）。

### approved_real_baseline

| 指标 | overall 前→后 | era 前→后 | scene 前→后 | influence 前→后 |
|---|---|---|---|---|
| relation_precision | 1/1 (100.0%) → 4/4 (100.0%) | 1/1 (100.0%) → 3/3 (100.0%) | N/A (0/0) → 1/1 (100.0%) | N/A (0/0) → N/A (0/0) |
| evidence_coverage | 1/12 (8.3%) → 5/12 (41.7%) | 1/3 (33.3%) → 3/3 (100.0%) | 0/3 (0.0%) → 2/3 (66.7%) | 0/6 (0.0%) → 0/6 (0.0%) |
| evidence_traceability | 1/1 (100.0%) → 7/7 (100.0%) | 1/1 (100.0%) → 5/5 (100.0%) | N/A (0/0) → 2/2 (100.0%) | N/A (0/0) → N/A (0/0) |
| unsupported_hallucination_rate | 0/1 (0.0%) → 0/4 (0.0%) | 0/1 (0.0%) → 0/3 (0.0%) | N/A (0/0) → 0/1 (0.0%) | N/A (0/0) → N/A (0/0) |
| influence_direction_accuracy | 0/5 (0.0%) → 0/5 (0.0%) | N/A (0/0) → N/A (0/0) | N/A (0/0) → N/A (0/0) | 0/5 (0.0%) → 0/5 (0.0%) |
| weak_evidence_rejection | 4/4 (100.0%) → 4/4 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 2/2 (100.0%) → 2/2 (100.0%) |
| negation_rejection | 3/3 (100.0%) → 3/3 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) |
| conflict_handling | 0/3 (0.0%) → 2/3 (66.7%) | 0/1 (0.0%) → 1/1 (100.0%) | 0/1 (0.0%) → 1/1 (100.0%) | 0/1 (0.0%) → 0/1 (0.0%) |
| query_isolation | 25/25 (100.0%) → 25/25 (100.0%) | 6/6 (100.0%) → 6/6 (100.0%) | 6/6 (100.0%) → 6/6 (100.0%) | 13/13 (100.0%) → 13/13 (100.0%) |
| checkpoint_continuity | 25/25 (100.0%) → 25/25 (100.0%) | 6/6 (100.0%) → 6/6 (100.0%) | 6/6 (100.0%) → 6/6 (100.0%) | 13/13 (100.0%) → 13/13 (100.0%) |

### synthetic_regression

| 指标 | overall 前→后 | era 前→后 | scene 前→后 | influence 前→后 |
|---|---|---|---|---|
| relation_precision | 10/12 (83.3%) → 10/12 (83.3%) | 3/4 (75.0%) → 3/4 (75.0%) | 3/4 (75.0%) → 3/4 (75.0%) | 4/4 (100.0%) → 4/4 (100.0%) |
| evidence_coverage | 13/19 (68.4%) → 13/19 (68.4%) | 4/6 (66.7%) → 4/6 (66.7%) | 4/6 (66.7%) → 4/6 (66.7%) | 5/7 (71.4%) → 5/7 (71.4%) |
| evidence_traceability | 15/15 (100.0%) → 15/15 (100.0%) | 5/5 (100.0%) → 5/5 (100.0%) | 5/5 (100.0%) → 5/5 (100.0%) | 5/5 (100.0%) → 5/5 (100.0%) |
| unsupported_hallucination_rate | 2/12 (16.7%) → 2/12 (16.7%) | 1/4 (25.0%) → 1/4 (25.0%) | 1/4 (25.0%) → 1/4 (25.0%) | 0/4 (0.0%) → 0/4 (0.0%) |
| influence_direction_accuracy | 4/6 (66.7%) → 4/6 (66.7%) | N/A (0/0) → N/A (0/0) | N/A (0/0) → N/A (0/0) | 4/6 (66.7%) → 4/6 (66.7%) |
| weak_evidence_rejection | 1/3 (33.3%) → 1/3 (33.3%) | 0/1 (0.0%) → 0/1 (0.0%) | 0/1 (0.0%) → 0/1 (0.0%) | 1/1 (100.0%) → 1/1 (100.0%) |
| negation_rejection | 3/3 (100.0%) → 3/3 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) |
| conflict_handling | 3/3 (100.0%) → 3/3 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) |
| query_isolation | 28/28 (100.0%) → 28/28 (100.0%) | 9/9 (100.0%) → 9/9 (100.0%) | 9/9 (100.0%) → 9/9 (100.0%) | 10/10 (100.0%) → 10/10 (100.0%) |
| checkpoint_continuity | 28/28 (100.0%) → 28/28 (100.0%) | 9/9 (100.0%) → 9/9 (100.0%) | 9/9 (100.0%) → 9/9 (100.0%) | 10/10 (100.0%) → 10/10 (100.0%) |

Reverse-direction negative：2/2 严格拒绝，未放行反向关系。
新增 regression 为 0；synthetic 指标逐项不变（20/28，原有 8 个失败仍记录）。真实 weak 4/4、negation 3/3、query isolation 25/25、checkpoint 25/25 均未下降。

## 三个真实 conflict 的完整链路

完整的逐字段 trace 在 conflict_case_traces.jsonl；包含原文/URL/publisher/locator/source SHA、candidate、validator、明确 aggregation、final、Gold 以及 checkpoint。以下为可读链路，无补写 evidence。

### real-era-r3-giant-steps-release-conflict

Query → Giant Steps 原专辑首次发行的年代归属（1950s / 1960s）有什么来源冲突？保留双方，不作确定裁决。

Evidence →

[Coltrane: 1960-1964 Mono (Rhino High Fidelity)](https://media.rhino.com/press-release/coltrane-1960-1964-mono-rhino-high-fidelity) — Rhino Media
Locator: Historical Giant Steps paragraph, immediately after description of first Atlantic sessions; NBSP after comma retained; SHA256: `cc38602829665b8f4d57e0d9edef02e1cd7bd925921336a6d152deec660c798c`

> Released in early 1960, Giant Steps introduced the “Coltrane Changes,”

[Cycles And Symmetric Division Of The Octave In The Works Of John Coltrane](https://static1.squarespace.com/static/5b4756089772aefb8c13401c/t/5c36518e0e2e72da3b5fbdd6/1547063701367/Jeff%2BPifher%2BAcademic%2BSeries-Coltrane%2BCycles.pdf) — Jeff Pifher (authored educational essay)
Locator: PDF page 7 (zero-based page 6); Countdown and Giant Steps section, opening sentence, before footnote 21; SHA256: `7501aa06ba0cd982888371456c616c614dc4c1206bb11518e35b455368e5c2d2`

> Coltrane's album Giant Steps was released in 1959.

Candidate →

```json
[
  {
    "claim_object_entity_id": null,
    "claim_object_value": "1960s",
    "claim_predicate": "belongs_to_era",
    "claim_subject_entity_id": "album:giant_steps",
    "claim_time": {
      "date": null,
      "end_year": null,
      "raw": null,
      "start_year": null,
      "year": 1960
    },
    "confidence": 0.96,
    "end_year": 1960,
    "era": {
      "canonical_name": "1960s",
      "node_id": "era:1960s"
    },
    "evidence": {
      "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
      "confidence": 0.96,
      "end_year": 1960,
      "evidence_id": "knowledge:era:c009fc634d127a48175bcfec",
      "exact_span": "Released in early 1960, Giant Steps introduced the “Coltrane Changes,”",
      "raw_time": null,
      "source_id": "knowledge:era:c009fc634d127a48175bcfec",
      "start_year": 1960
    },
    "evidence_source": {
      "excerpt": "Released in early 1960, Giant Steps introduced the “Coltrane Changes,”",
      "id": "knowledge:era:c009fc634d127a48175bcfec",
      "metadata": {
        "child_id": "chunk:real-era-r3-giant-steps-release-conflict-1960",
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
        "context_version": "context-v1",
        "document_id": "real-era-r3-giant-steps-release-conflict-1960",
        "era_expansion_version": "v1",
        "parent_id": "real-era-r3-giant-steps-release-conflict-1960",
        "retrieval_version": "evaluation-frozen-v1",
        "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1960",
        "validator_version": "v1"
      },
      "source": "Rhino Media",
      "source_type": "knowledge",
      "title": "Coltrane: 1960-1964 Mono (Rhino High Fidelity)",
      "url": "https://media.rhino.com/press-release/coltrane-1960-1964-mono-rhino-high-fidelity"
    },
    "relation_type": "belongs_to_era",
    "source_entity": {
      "canonical_name": "Giant Steps (album)",
      "knowledge_entity_id": "album:giant_steps",
      "node_id": "eval:album:giant_steps",
      "node_type": "album"
    },
    "start_year": 1960,
    "target_entity": null,
    "temporal_precision": "exact"
  },
  {
    "claim_object_entity_id": null,
    "claim_object_value": "1950s",
    "claim_predicate": "belongs_to_era",
    "claim_subject_entity_id": "album:giant_steps",
    "claim_time": {
      "date": null,
      "end_year": null,
      "raw": null,
      "start_year": null,
      "year": 1959
    },
    "confidence": 0.96,
    "end_year": 1959,
    "era": {
      "canonical_name": "1950s",
      "node_id": "era:1950s"
    },
    "evidence": {
      "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
      "confidence": 0.96,
      "end_year": 1959,
      "evidence_id": "knowledge:era:6cdcb684f8d265d3b9563883",
      "exact_span": "Coltrane's album Giant Steps was released in 1959.",
      "raw_time": null,
      "source_id": "knowledge:era:6cdcb684f8d265d3b9563883",
      "start_year": 1959
    },
    "evidence_source": {
      "excerpt": "Coltrane's album Giant Steps was released in 1959.",
      "id": "knowledge:era:6cdcb684f8d265d3b9563883",
      "metadata": {
        "child_id": "chunk:real-era-r3-giant-steps-release-conflict-1959",
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
        "context_version": "context-v1",
        "document_id": "real-era-r3-giant-steps-release-conflict-1959",
        "era_expansion_version": "v1",
        "parent_id": "real-era-r3-giant-steps-release-conflict-1959",
        "retrieval_version": "evaluation-frozen-v1",
        "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1959",
        "validator_version": "v1"
      },
      "source": "Jeff Pifher (authored educational essay)",
      "source_type": "knowledge",
      "title": "Cycles And Symmetric Division Of The Octave In The Works Of John Coltrane",
      "url": "https://static1.squarespace.com/static/5b4756089772aefb8c13401c/t/5c36518e0e2e72da3b5fbdd6/1547063701367/Jeff%2BPifher%2BAcademic%2BSeries-Coltrane%2BCycles.pdf"
    },
    "relation_type": "belongs_to_era",
    "source_entity": {
      "canonical_name": "Giant Steps (album)",
      "knowledge_entity_id": "album:giant_steps",
      "node_id": "eval:album:giant_steps",
      "node_type": "album"
    },
    "start_year": 1959,
    "target_entity": null,
    "temporal_precision": "exact"
  }
]
```

Validator →

```json
[
  {
    "candidate": {
      "claim_object_entity_id": null,
      "claim_object_value": "1960s",
      "claim_predicate": "belongs_to_era",
      "claim_subject_entity_id": "album:giant_steps",
      "claim_time": {
        "date": null,
        "end_year": null,
        "raw": null,
        "start_year": null,
        "year": 1960
      },
      "confidence": 0.96,
      "end_year": 1960,
      "era": {
        "canonical_name": "1960s",
        "node_id": "era:1960s"
      },
      "evidence": {
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
        "confidence": 0.96,
        "end_year": 1960,
        "evidence_id": "knowledge:era:c009fc634d127a48175bcfec",
        "exact_span": "Released in early 1960, Giant Steps introduced the “Coltrane Changes,”",
        "raw_time": null,
        "source_id": "knowledge:era:c009fc634d127a48175bcfec",
        "start_year": 1960
      },
      "evidence_source": {
        "excerpt": "Released in early 1960, Giant Steps introduced the “Coltrane Changes,”",
        "id": "knowledge:era:c009fc634d127a48175bcfec",
        "metadata": {
          "child_id": "chunk:real-era-r3-giant-steps-release-conflict-1960",
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
          "context_version": "context-v1",
          "document_id": "real-era-r3-giant-steps-release-conflict-1960",
          "era_expansion_version": "v1",
          "parent_id": "real-era-r3-giant-steps-release-conflict-1960",
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1960",
          "validator_version": "v1"
        },
        "source": "Rhino Media",
        "source_type": "knowledge",
        "title": "Coltrane: 1960-1964 Mono (Rhino High Fidelity)",
        "url": "https://media.rhino.com/press-release/coltrane-1960-1964-mono-rhino-high-fidelity"
      },
      "relation_type": "belongs_to_era",
      "source_entity": {
        "canonical_name": "Giant Steps (album)",
        "knowledge_entity_id": "album:giant_steps",
        "node_id": "eval:album:giant_steps",
        "node_type": "album"
      },
      "start_year": 1960,
      "target_entity": null,
      "temporal_precision": "exact"
    },
    "rejection_reasons": [],
    "status": "accepted"
  },
  {
    "candidate": {
      "claim_object_entity_id": null,
      "claim_object_value": "1950s",
      "claim_predicate": "belongs_to_era",
      "claim_subject_entity_id": "album:giant_steps",
      "claim_time": {
        "date": null,
        "end_year": null,
        "raw": null,
        "start_year": null,
        "year": 1959
      },
      "confidence": 0.96,
      "end_year": 1959,
      "era": {
        "canonical_name": "1950s",
        "node_id": "era:1950s"
      },
      "evidence": {
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
        "confidence": 0.96,
        "end_year": 1959,
        "evidence_id": "knowledge:era:6cdcb684f8d265d3b9563883",
        "exact_span": "Coltrane's album Giant Steps was released in 1959.",
        "raw_time": null,
        "source_id": "knowledge:era:6cdcb684f8d265d3b9563883",
        "start_year": 1959
      },
      "evidence_source": {
        "excerpt": "Coltrane's album Giant Steps was released in 1959.",
        "id": "knowledge:era:6cdcb684f8d265d3b9563883",
        "metadata": {
          "child_id": "chunk:real-era-r3-giant-steps-release-conflict-1959",
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
          "context_version": "context-v1",
          "document_id": "real-era-r3-giant-steps-release-conflict-1959",
          "era_expansion_version": "v1",
          "parent_id": "real-era-r3-giant-steps-release-conflict-1959",
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1959",
          "validator_version": "v1"
        },
        "source": "Jeff Pifher (authored educational essay)",
        "source_type": "knowledge",
        "title": "Cycles And Symmetric Division Of The Octave In The Works Of John Coltrane",
        "url": "https://static1.squarespace.com/static/5b4756089772aefb8c13401c/t/5c36518e0e2e72da3b5fbdd6/1547063701367/Jeff%2BPifher%2BAcademic%2BSeries-Coltrane%2BCycles.pdf"
      },
      "relation_type": "belongs_to_era",
      "source_entity": {
        "canonical_name": "Giant Steps (album)",
        "knowledge_entity_id": "album:giant_steps",
        "node_id": "eval:album:giant_steps",
        "node_type": "album"
      },
      "start_year": 1959,
      "target_entity": null,
      "temporal_precision": "exact"
    },
    "rejection_reasons": [],
    "status": "accepted"
  }
]
```

Conflict aggregation →

```json
[
  {
    "inputs": [
      {
        "aggregation_role": "support",
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
        "evidence_id": "knowledge:era:c009fc634d127a48175bcfec",
        "rejection_reasons": [],
        "validation_status": "accepted"
      },
      {
        "aggregation_role": "support",
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
        "evidence_id": "knowledge:era:6cdcb684f8d265d3b9563883",
        "rejection_reasons": [],
        "validation_status": "accepted"
      }
    ],
    "outputs": [
      {
        "conflict_flags": [
          "temporal_conflict"
        ],
        "evidence_claim_ids": [
          "claim:real-era-r3-giant-steps-release-conflict-1960",
          "claim:real-era-r3-giant-steps-release-conflict-1959"
        ],
        "predicate": "belongs_to_era",
        "source_entity": "eval:album:giant_steps",
        "target_entity": "era:1960s"
      },
      {
        "conflict_flags": [
          "temporal_conflict"
        ],
        "evidence_claim_ids": [
          "claim:real-era-r3-giant-steps-release-conflict-1959",
          "claim:real-era-r3-giant-steps-release-conflict-1960"
        ],
        "predicate": "belongs_to_era",
        "source_entity": "eval:album:giant_steps",
        "target_entity": "era:1950s"
      }
    ],
    "stage": "validation_normalization_then_conflict_aggregation"
  }
]
```

Final →

```json
[
  {
    "evidence": [
      {
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
        "confidence": 0.96,
        "end_year": 1960,
        "evidence_id": "knowledge:era:c009fc634d127a48175bcfec",
        "exact_span": "Released in early 1960, Giant Steps introduced the “Coltrane Changes,”",
        "raw_time": null,
        "snapshot_source_id": "source:r3:rhino-giant-steps-1960",
        "source_id": "knowledge:era:c009fc634d127a48175bcfec",
        "source_metadata": {
          "child_id": "chunk:real-era-r3-giant-steps-release-conflict-1960",
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
          "context_version": "context-v1",
          "document_id": "real-era-r3-giant-steps-release-conflict-1960",
          "era_expansion_version": "v1",
          "parent_id": "real-era-r3-giant-steps-release-conflict-1960",
          "relation_key": "eval:album:giant_steps|era:1950s|belongs_to_era",
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1960",
          "temporal_conflict": true,
          "validator_version": "v1"
        },
        "span_end": 70,
        "span_start": 0,
        "start_year": 1960,
        "traceable": true
      },
      {
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
        "confidence": 0.96,
        "end_year": 1959,
        "evidence_id": "knowledge:era:6cdcb684f8d265d3b9563883",
        "exact_span": "Coltrane's album Giant Steps was released in 1959.",
        "raw_time": null,
        "snapshot_source_id": "source:r3:pifher-giant-steps-1959",
        "source_id": "knowledge:era:6cdcb684f8d265d3b9563883",
        "source_metadata": {
          "child_id": "chunk:real-era-r3-giant-steps-release-conflict-1959",
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
          "context_version": "context-v1",
          "document_id": "real-era-r3-giant-steps-release-conflict-1959",
          "era_expansion_version": "v1",
          "parent_id": "real-era-r3-giant-steps-release-conflict-1959",
          "relation_key": "eval:album:giant_steps|era:1950s|belongs_to_era",
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1959",
          "temporal_conflict": true,
          "validator_version": "v1"
        },
        "span_end": 50,
        "span_start": 0,
        "start_year": 1959,
        "traceable": true
      }
    ],
    "predicate": "belongs_to_era",
    "raw": {
      "confidence": 0.8099999999999999,
      "end_year": null,
      "era": {
        "canonical_name": "1960s",
        "node_id": "era:1960s"
      },
      "evidence": [
        {
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
          "confidence": 0.96,
          "end_year": 1960,
          "evidence_id": "knowledge:era:c009fc634d127a48175bcfec",
          "exact_span": "Released in early 1960, Giant Steps introduced the “Coltrane Changes,”",
          "raw_time": null,
          "source_id": "knowledge:era:c009fc634d127a48175bcfec",
          "start_year": 1960
        },
        {
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
          "confidence": 0.96,
          "end_year": 1959,
          "evidence_id": "knowledge:era:6cdcb684f8d265d3b9563883",
          "exact_span": "Coltrane's album Giant Steps was released in 1959.",
          "raw_time": null,
          "source_id": "knowledge:era:6cdcb684f8d265d3b9563883",
          "start_year": 1959
        }
      ],
      "metadata": {
        "claim_predicates": [
          "belongs_to_era"
        ],
        "conflict_flags": [
          "temporal_conflict"
        ],
        "derivation": "verified_claim",
        "era_expansion_version": "v1",
        "evidence_overflow_count": 0,
        "temporal_assertions": [
          {
            "end_year": 1960,
            "evidence_id": "knowledge:era:c009fc634d127a48175bcfec",
            "precision": "exact",
            "raw_time": null,
            "start_year": 1960
          },
          {
            "end_year": 1959,
            "evidence_id": "knowledge:era:6cdcb684f8d265d3b9563883",
            "precision": "exact",
            "raw_time": null,
            "start_year": 1959
          }
        ],
        "temporal_conflict": true,
        "validator_version": "v1"
      },
      "relation_type": "belongs_to_era",
      "source_entity": {
        "canonical_name": "Giant Steps (album)",
        "knowledge_entity_id": "album:giant_steps",
        "node_id": "eval:album:giant_steps",
        "node_type": "album"
      },
      "start_year": null,
      "target_entity": null,
      "temporal_precision": "unknown"
    },
    "relation_type": "era",
    "source_entity": "eval:album:giant_steps",
    "target_entity": "era:1960s"
  },
  {
    "evidence": [
      {
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
        "confidence": 0.96,
        "end_year": 1959,
        "evidence_id": "knowledge:era:6cdcb684f8d265d3b9563883",
        "exact_span": "Coltrane's album Giant Steps was released in 1959.",
        "raw_time": null,
        "snapshot_source_id": "source:r3:pifher-giant-steps-1959",
        "source_id": "knowledge:era:6cdcb684f8d265d3b9563883",
        "source_metadata": {
          "child_id": "chunk:real-era-r3-giant-steps-release-conflict-1959",
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
          "context_version": "context-v1",
          "document_id": "real-era-r3-giant-steps-release-conflict-1959",
          "era_expansion_version": "v1",
          "parent_id": "real-era-r3-giant-steps-release-conflict-1959",
          "relation_key": "eval:album:giant_steps|era:1950s|belongs_to_era",
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1959",
          "temporal_conflict": true,
          "validator_version": "v1"
        },
        "span_end": 50,
        "span_start": 0,
        "start_year": 1959,
        "traceable": true
      },
      {
        "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
        "confidence": 0.96,
        "end_year": 1960,
        "evidence_id": "knowledge:era:c009fc634d127a48175bcfec",
        "exact_span": "Released in early 1960, Giant Steps introduced the “Coltrane Changes,”",
        "raw_time": null,
        "snapshot_source_id": "source:r3:rhino-giant-steps-1960",
        "source_id": "knowledge:era:c009fc634d127a48175bcfec",
        "source_metadata": {
          "child_id": "chunk:real-era-r3-giant-steps-release-conflict-1960",
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
          "context_version": "context-v1",
          "document_id": "real-era-r3-giant-steps-release-conflict-1960",
          "era_expansion_version": "v1",
          "parent_id": "real-era-r3-giant-steps-release-conflict-1960",
          "relation_key": "eval:album:giant_steps|era:1950s|belongs_to_era",
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1960",
          "temporal_conflict": true,
          "validator_version": "v1"
        },
        "span_end": 70,
        "span_start": 0,
        "start_year": 1960,
        "traceable": true
      }
    ],
    "predicate": "belongs_to_era",
    "raw": {
      "confidence": 0.8099999999999999,
      "end_year": null,
      "era": {
        "canonical_name": "1950s",
        "node_id": "era:1950s"
      },
      "evidence": [
        {
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
          "confidence": 0.96,
          "end_year": 1959,
          "evidence_id": "knowledge:era:6cdcb684f8d265d3b9563883",
          "exact_span": "Coltrane's album Giant Steps was released in 1959.",
          "raw_time": null,
          "source_id": "knowledge:era:6cdcb684f8d265d3b9563883",
          "start_year": 1959
        },
        {
          "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
          "confidence": 0.96,
          "end_year": 1960,
          "evidence_id": "knowledge:era:c009fc634d127a48175bcfec",
          "exact_span": "Released in early 1960, Giant Steps introduced the “Coltrane Changes,”",
          "raw_time": null,
          "source_id": "knowledge:era:c009fc634d127a48175bcfec",
          "start_year": 1960
        }
      ],
      "metadata": {
        "claim_predicates": [
          "belongs_to_era"
        ],
        "conflict_flags": [
          "temporal_conflict"
        ],
        "derivation": "verified_claim",
        "era_expansion_version": "v1",
        "evidence_overflow_count": 0,
        "temporal_assertions": [
          {
            "end_year": 1959,
            "evidence_id": "knowledge:era:6cdcb684f8d265d3b9563883",
            "precision": "exact",
            "raw_time": null,
            "start_year": 1959
          },
          {
            "end_year": 1960,
            "evidence_id": "knowledge:era:c009fc634d127a48175bcfec",
            "precision": "exact",
            "raw_time": null,
            "start_year": 1960
          }
        ],
        "temporal_conflict": true,
        "validator_version": "v1"
      },
      "relation_type": "belongs_to_era",
      "source_entity": {
        "canonical_name": "Giant Steps (album)",
        "knowledge_entity_id": "album:giant_steps",
        "node_id": "eval:album:giant_steps",
        "node_type": "album"
      },
      "start_year": null,
      "target_entity": null,
      "temporal_precision": "unknown"
    },
    "relation_type": "era",
    "source_entity": "eval:album:giant_steps",
    "target_entity": "era:1950s"
  }
]
```

Expected Gold →

```json
[
  {
    "conflict_flags": [
      "temporal_conflict"
    ],
    "evidence_claim_ids": [
      "claim:real-era-r3-giant-steps-release-conflict-1960",
      "claim:real-era-r3-giant-steps-release-conflict-1959"
    ],
    "fields": {
      "end_year": null,
      "start_year": null,
      "temporal_precision": "unknown"
    },
    "predicate": "belongs_to_era",
    "source_entity": "eval:album:giant_steps",
    "target_entity": "era:1960s"
  },
  {
    "conflict_flags": [
      "temporal_conflict"
    ],
    "evidence_claim_ids": [
      "claim:real-era-r3-giant-steps-release-conflict-1960",
      "claim:real-era-r3-giant-steps-release-conflict-1959"
    ],
    "fields": {
      "end_year": null,
      "start_year": null,
      "temporal_precision": "unknown"
    },
    "predicate": "belongs_to_era",
    "source_entity": "eval:album:giant_steps",
    "target_entity": "era:1950s"
  }
]
```

Result: PASS; failure reasons: []

### real-scene-r3-blur-britpop-conflict

Query → Blur 的历史 Britpop 场景归属是否存在外部归类与成员自述的分歧？保留双方，不作确定裁决。

Evidence →

[Blur at the BRITs: 1995 - 2012](https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/) — The BRIT Awards
Locator: Paragraph on the 2012 Outstanding Contribution award; historical Britpop scene description; SHA256: `b6b853efe0dddb40c89ea9782d79cebfea99e278b64ad1dd9135c2a8301210d8`

> One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.

[Blur: ‘We’re not AC/DC. We’ve never been basic. We play too many chords to be basic’](https://english.elpais.com/culture/2023-07-21/blur-were-not-acdc-weve-never-been-basic-we-play-too-many-chords-to-be-basic.html?outputType=amp) — EL PAÍS / Álex Vicente
Locator: Interview paragraph about Blair, England and political appropriation; final two sentences of the answer following Coxon adds; SHA256: `257f424837d1b688167b6a5ca344cc55c5655b079dbe919739f0b9b29a9b562b`

> We never really did ‘Britpop.’ We’re not part of that.

Candidate →

```json
[
  {
    "claim_object_entity_id": null,
    "claim_object_value": "Britpop movement",
    "claim_predicate": "belongs_to_scene",
    "claim_subject_entity_id": "artist:blur",
    "claim_time": null,
    "confidence": 0.96,
    "end_year": null,
    "evidence": {
      "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
      "confidence": 0.96,
      "end_year": null,
      "evidence_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
      "exact_span": "One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.",
      "raw_time": null,
      "source_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
      "start_year": null
    },
    "evidence_source": {
      "excerpt": "One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.",
      "id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
      "metadata": {
        "child_id": "chunk:real-scene-r3-blur-britpop-conflict-external",
        "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
        "context_version": "context-v1",
        "document_id": "real-scene-r3-blur-britpop-conflict-external",
        "parent_id": "real-scene-r3-blur-britpop-conflict-external",
        "retrieval_version": "evaluation-frozen-v1",
        "scene_expansion_version": "v1",
        "source_chunk_id": "chunk:real-scene-r3-blur-britpop-conflict-external",
        "validator_version": "v1"
      },
      "source": "The BRIT Awards",
      "source_type": "knowledge",
      "title": "Blur at the BRITs: 1995 - 2012",
      "url": "https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/"
    },
    "location": null,
    "relation_type": "belongs_to_scene",
    "scene": {
      "canonical_name": "Britpop movement",
      "node_id": "scene:britpop_movement"
    },
    "scene_name": "Britpop movement",
    "source_entity": {
      "canonical_name": "Blur",
      "knowledge_entity_id": "artist:blur",
      "node_id": "eval:artist:blur",
      "node_type": "artist"
    },
    "start_year": null,
    "target_entity": null,
    "temporal_precision": "unknown"
  },
  {
    "claim_object_entity_id": null,
    "claim_object_value": "Britpop movement",
    "claim_predicate": "belongs_to_scene",
    "claim_subject_entity_id": "artist:blur",
    "claim_time": null,
    "confidence": 0.96,
    "end_year": null,
    "evidence": {
      "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
      "confidence": 0.96,
      "end_year": null,
      "evidence_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
      "exact_span": "We never really did ‘Britpop.’ We’re not part of that.",
      "raw_time": null,
      "source_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
      "start_year": null
    },
    "evidence_source": {
      "excerpt": "We never really did ‘Britpop.’ We’re not part of that.",
      "id": "knowledge:scene:192cd3d7c4f33575e02cc867",
      "metadata": {
        "child_id": "chunk:real-scene-r3-blur-britpop-conflict-self",
        "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
        "context_version": "context-v1",
        "document_id": "real-scene-r3-blur-britpop-conflict-self",
        "parent_id": "real-scene-r3-blur-britpop-conflict-self",
        "retrieval_version": "evaluation-frozen-v1",
        "scene_expansion_version": "v1",
        "source_chunk_id": "chunk:real-scene-r3-blur-britpop-conflict-self",
        "validator_version": "v1"
      },
      "source": "EL PAÍS / Álex Vicente",
      "source_type": "knowledge",
      "title": "Blur: ‘We’re not AC/DC. We’ve never been basic. We play too many chords to be basic’",
      "url": "https://english.elpais.com/culture/2023-07-21/blur-were-not-acdc-weve-never-been-basic-we-play-too-many-chords-to-be-basic.html?outputType=amp"
    },
    "location": null,
    "relation_type": "belongs_to_scene",
    "scene": {
      "canonical_name": "Britpop movement",
      "node_id": "scene:britpop_movement"
    },
    "scene_name": "Britpop movement",
    "source_entity": {
      "canonical_name": "Blur",
      "knowledge_entity_id": "artist:blur",
      "node_id": "eval:artist:blur",
      "node_type": "artist"
    },
    "start_year": null,
    "target_entity": null,
    "temporal_precision": "unknown"
  }
]
```

Validator →

```json
[
  {
    "candidate": {
      "claim_object_entity_id": null,
      "claim_object_value": "Britpop movement",
      "claim_predicate": "belongs_to_scene",
      "claim_subject_entity_id": "artist:blur",
      "claim_time": null,
      "confidence": 0.96,
      "end_year": null,
      "evidence": {
        "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
        "confidence": 0.96,
        "end_year": null,
        "evidence_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
        "exact_span": "One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.",
        "raw_time": null,
        "source_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
        "start_year": null
      },
      "evidence_source": {
        "excerpt": "One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.",
        "id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
        "metadata": {
          "child_id": "chunk:real-scene-r3-blur-britpop-conflict-external",
          "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
          "context_version": "context-v1",
          "document_id": "real-scene-r3-blur-britpop-conflict-external",
          "parent_id": "real-scene-r3-blur-britpop-conflict-external",
          "retrieval_version": "evaluation-frozen-v1",
          "scene_expansion_version": "v1",
          "source_chunk_id": "chunk:real-scene-r3-blur-britpop-conflict-external",
          "validator_version": "v1"
        },
        "source": "The BRIT Awards",
        "source_type": "knowledge",
        "title": "Blur at the BRITs: 1995 - 2012",
        "url": "https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/"
      },
      "location": null,
      "relation_type": "belongs_to_scene",
      "scene": {
        "canonical_name": "Britpop movement",
        "node_id": "scene:britpop_movement"
      },
      "scene_name": "Britpop movement",
      "source_entity": {
        "canonical_name": "Blur",
        "knowledge_entity_id": "artist:blur",
        "node_id": "eval:artist:blur",
        "node_type": "artist"
      },
      "start_year": null,
      "target_entity": null,
      "temporal_precision": "unknown"
    },
    "rejection_reasons": [],
    "status": "accepted"
  },
  {
    "candidate": {
      "claim_object_entity_id": null,
      "claim_object_value": "Britpop movement",
      "claim_predicate": "belongs_to_scene",
      "claim_subject_entity_id": "artist:blur",
      "claim_time": null,
      "confidence": 0.96,
      "end_year": null,
      "evidence": {
        "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
        "confidence": 0.96,
        "end_year": null,
        "evidence_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
        "exact_span": "We never really did ‘Britpop.’ We’re not part of that.",
        "raw_time": null,
        "source_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
        "start_year": null
      },
      "evidence_source": {
        "excerpt": "We never really did ‘Britpop.’ We’re not part of that.",
        "id": "knowledge:scene:192cd3d7c4f33575e02cc867",
        "metadata": {
          "child_id": "chunk:real-scene-r3-blur-britpop-conflict-self",
          "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
          "context_version": "context-v1",
          "document_id": "real-scene-r3-blur-britpop-conflict-self",
          "parent_id": "real-scene-r3-blur-britpop-conflict-self",
          "retrieval_version": "evaluation-frozen-v1",
          "scene_expansion_version": "v1",
          "source_chunk_id": "chunk:real-scene-r3-blur-britpop-conflict-self",
          "validator_version": "v1"
        },
        "source": "EL PAÍS / Álex Vicente",
        "source_type": "knowledge",
        "title": "Blur: ‘We’re not AC/DC. We’ve never been basic. We play too many chords to be basic’",
        "url": "https://english.elpais.com/culture/2023-07-21/blur-were-not-acdc-weve-never-been-basic-we-play-too-many-chords-to-be-basic.html?outputType=amp"
      },
      "location": null,
      "relation_type": "belongs_to_scene",
      "scene": {
        "canonical_name": "Britpop movement",
        "node_id": "scene:britpop_movement"
      },
      "scene_name": "Britpop movement",
      "source_entity": {
        "canonical_name": "Blur",
        "knowledge_entity_id": "artist:blur",
        "node_id": "eval:artist:blur",
        "node_type": "artist"
      },
      "start_year": null,
      "target_entity": null,
      "temporal_precision": "unknown"
    },
    "rejection_reasons": [
      "negated_scene_membership"
    ],
    "status": "rejected"
  }
]
```

Conflict aggregation →

```json
[
  {
    "inputs": [
      {
        "aggregation_role": "support",
        "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
        "evidence_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
        "rejection_reasons": [],
        "validation_status": "accepted"
      },
      {
        "aggregation_role": "contradiction",
        "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
        "evidence_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
        "rejection_reasons": [
          "negated_scene_membership"
        ],
        "validation_status": "rejected"
      }
    ],
    "outputs": [
      {
        "conflict_flags": [
          "attribution_conflict"
        ],
        "evidence_claim_ids": [
          "claim:real-scene-r3-blur-britpop-conflict-external",
          "claim:real-scene-r3-blur-britpop-conflict-self"
        ],
        "predicate": "belongs_to_scene",
        "source_entity": "eval:artist:blur",
        "target_entity": "scene:britpop_movement"
      }
    ],
    "stage": "validation_normalization_then_conflict_aggregation"
  }
]
```

Final →

```json
[
  {
    "evidence": [
      {
        "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
        "confidence": 0.96,
        "end_year": null,
        "evidence_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
        "exact_span": "One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.",
        "raw_time": null,
        "snapshot_source_id": "source:r3:brits-blur-membership",
        "source_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
        "source_metadata": {
          "child_id": "chunk:real-scene-r3-blur-britpop-conflict-external",
          "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
          "context_version": "context-v1",
          "document_id": "real-scene-r3-blur-britpop-conflict-external",
          "parent_id": "real-scene-r3-blur-britpop-conflict-external",
          "retrieval_version": "evaluation-frozen-v1",
          "scene_expansion_version": "v1",
          "source_chunk_id": "chunk:real-scene-r3-blur-britpop-conflict-external",
          "validator_version": "v1"
        },
        "span_end": 106,
        "span_start": 0,
        "start_year": null,
        "traceable": true
      },
      {
        "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
        "confidence": 0.96,
        "end_year": null,
        "evidence_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
        "exact_span": "We never really did ‘Britpop.’ We’re not part of that.",
        "raw_time": null,
        "snapshot_source_id": "source:r3:elpais-blur-denial",
        "source_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
        "source_metadata": {
          "child_id": "chunk:real-scene-r3-blur-britpop-conflict-self",
          "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
          "context_version": "context-v1",
          "document_id": "real-scene-r3-blur-britpop-conflict-self",
          "parent_id": "real-scene-r3-blur-britpop-conflict-self",
          "retrieval_version": "evaluation-frozen-v1",
          "scene_expansion_version": "v1",
          "source_chunk_id": "chunk:real-scene-r3-blur-britpop-conflict-self",
          "validator_version": "v1"
        },
        "span_end": 54,
        "span_start": 0,
        "start_year": null,
        "traceable": true
      }
    ],
    "predicate": "belongs_to_scene",
    "raw": {
      "confidence": 0.96,
      "end_year": null,
      "evidence": [
        {
          "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
          "confidence": 0.96,
          "end_year": null,
          "evidence_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
          "exact_span": "One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.",
          "raw_time": null,
          "source_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
          "start_year": null
        },
        {
          "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
          "confidence": 0.96,
          "end_year": null,
          "evidence_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
          "exact_span": "We never really did ‘Britpop.’ We’re not part of that.",
          "raw_time": null,
          "source_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
          "start_year": null
        }
      ],
      "location": null,
      "metadata": {
        "attribution_conflict": true,
        "conflict_flags": [
          "attribution_conflict"
        ],
        "evidence_overflow_count": 0,
        "location_assertions": [
          {
            "evidence_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
            "location": null
          },
          {
            "evidence_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
            "location": null
          }
        ],
        "location_conflict": false,
        "provenance": [
          {
            "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
            "evidence_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
            "stance": "support"
          },
          {
            "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
            "evidence_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
            "stance": "contradiction"
          }
        ],
        "scene_expansion_version": "v1",
        "temporal_assertions": [
          {
            "end_year": null,
            "evidence_id": "knowledge:scene:4b0f2a485335f4c9daa047cd",
            "raw_time": null,
            "start_year": null
          },
          {
            "end_year": null,
            "evidence_id": "knowledge:scene:192cd3d7c4f33575e02cc867",
            "raw_time": null,
            "start_year": null
          }
        ],
        "temporal_conflict": false,
        "validator_version": "v1"
      },
      "relation_type": "belongs_to_scene",
      "scene": {
        "canonical_name": "Britpop movement",
        "node_id": "scene:britpop_movement"
      },
      "scene_name": "Britpop movement",
      "source_entity": {
        "canonical_name": "Blur",
        "knowledge_entity_id": "artist:blur",
        "node_id": "eval:artist:blur",
        "node_type": "artist"
      },
      "start_year": null,
      "target_entity": null,
      "temporal_precision": "unknown"
    },
    "relation_type": "scene",
    "source_entity": "eval:artist:blur",
    "target_entity": "scene:britpop_movement"
  }
]
```

Expected Gold →

```json
[
  {
    "conflict_flags": [
      "attribution_conflict"
    ],
    "evidence_claim_ids": [
      "claim:real-scene-r3-blur-britpop-conflict-external",
      "claim:real-scene-r3-blur-britpop-conflict-self"
    ],
    "fields": {},
    "predicate": "belongs_to_scene",
    "source_entity": "eval:artist:blur",
    "target_entity": "scene:britpop_movement"
  }
]
```

Result: PASS; failure reasons: []

### real-influence-r3-stockhausen-cluster-conflict

Query → Karlheinz Stockhausen → Cluster 的早期音乐 influenced 归因是否有争议？保留作者判断与成员否定。

Evidence →

[An awfully big adventure](https://www.theguardian.com/music/2005/apr/08/popandrock) — The Guardian / Dorian Lynskey
Locator: Historical Cluster paragraph starting Music critics have explained Krautrock; excerpt of the second sentence, whose grammatical subject is Cluster; SHA256: `a798a652865f566e6cd6ebfe22b54fb892e49d79c29c34f63d5b943819128261`

> tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.

[Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010](https://www.indie-eye.it/recensore/generi/experimental/cluster-lintervista-di-indie-eye-it.html) — Indie-eye / Federico Fragasso and Michele Baldini
Locator: Moebius answer immediately after question whether Stockhausen influenced them as he did other Krautrock artists; SHA256: `9268f84e46dbb53d3e88cebfcc472609eb7eb7bb80cfea5967e57fdcce9f80a4`

> Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.

Candidate →

```json
[
  {
    "claim_confidence": 0.96,
    "claim_object_entity_id": "artist:cluster",
    "claim_predicate": "influenced",
    "claim_subject_entity_id": "artist:karlheinz_stockhausen",
    "confidence": 0.96,
    "direction": "influencer_to_influenced",
    "evidence": {
      "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
      "confidence": 0.96,
      "evidence_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
      "exact_span": "tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
      "source_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
      "stance": "support",
      "strength": "historical_attribution"
    },
    "evidence_source": {
      "excerpt": "tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
      "id": "knowledge:influence:cfc5239531dc9b6380ec9423",
      "metadata": {
        "child_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-report",
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
        "context_version": "context-v1",
        "document_id": "real-influence-r3-stockhausen-cluster-conflict-report",
        "influence_expansion_version": "v1",
        "parent_id": "real-influence-r3-stockhausen-cluster-conflict-report",
        "retrieval_version": "evaluation-frozen-v1",
        "source_chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-report",
        "validator_version": "v1"
      },
      "source": "The Guardian / Dorian Lynskey",
      "source_type": "knowledge",
      "title": "An awfully big adventure",
      "url": "https://www.theguardian.com/music/2005/apr/08/popandrock"
    },
    "influence_scope": null,
    "relation_type": "influenced",
    "scope_mentions": [],
    "source_entity": {
      "canonical_name": "Karlheinz Stockhausen",
      "knowledge_entity_id": "artist:karlheinz_stockhausen",
      "node_id": "eval:artist:karlheinz_stockhausen",
      "node_type": "artist"
    },
    "target_entity": {
      "canonical_name": "Cluster",
      "knowledge_entity_id": "artist:cluster",
      "node_id": "eval:artist:cluster",
      "node_type": "artist"
    },
    "uncertain": false
  },
  {
    "claim_confidence": 0.96,
    "claim_object_entity_id": "artist:cluster",
    "claim_predicate": "influenced",
    "claim_subject_entity_id": "artist:karlheinz_stockhausen",
    "confidence": 0.96,
    "direction": "influencer_to_influenced",
    "evidence": {
      "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
      "confidence": 0.96,
      "evidence_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
      "exact_span": "Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
      "source_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
      "stance": "contradiction",
      "strength": "historical_attribution"
    },
    "evidence_source": {
      "excerpt": "Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
      "id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
      "metadata": {
        "child_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-denial",
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
        "context_version": "context-v1",
        "document_id": "real-influence-r3-stockhausen-cluster-conflict-denial",
        "influence_expansion_version": "v1",
        "parent_id": "real-influence-r3-stockhausen-cluster-conflict-denial",
        "retrieval_version": "evaluation-frozen-v1",
        "source_chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-denial",
        "validator_version": "v1"
      },
      "source": "Indie-eye / Federico Fragasso and Michele Baldini",
      "source_type": "knowledge",
      "title": "Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010",
      "url": "https://www.indie-eye.it/recensore/generi/experimental/cluster-lintervista-di-indie-eye-it.html"
    },
    "influence_scope": null,
    "relation_type": "influenced",
    "scope_mentions": [],
    "source_entity": {
      "canonical_name": "Karlheinz Stockhausen",
      "knowledge_entity_id": "artist:karlheinz_stockhausen",
      "node_id": "eval:artist:karlheinz_stockhausen",
      "node_type": "artist"
    },
    "target_entity": {
      "canonical_name": "Cluster",
      "knowledge_entity_id": "artist:cluster",
      "node_id": "eval:artist:cluster",
      "node_type": "artist"
    },
    "uncertain": false
  }
]
```

Validator →

```json
[
  {
    "candidate": {
      "claim_confidence": 0.96,
      "claim_object_entity_id": "artist:cluster",
      "claim_predicate": "influenced",
      "claim_subject_entity_id": "artist:karlheinz_stockhausen",
      "confidence": 0.96,
      "direction": "influencer_to_influenced",
      "evidence": {
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
        "confidence": 0.96,
        "evidence_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
        "exact_span": "tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
        "source_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
        "stance": "support",
        "strength": "historical_attribution"
      },
      "evidence_source": {
        "excerpt": "tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
        "id": "knowledge:influence:cfc5239531dc9b6380ec9423",
        "metadata": {
          "child_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-report",
          "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
          "context_version": "context-v1",
          "document_id": "real-influence-r3-stockhausen-cluster-conflict-report",
          "influence_expansion_version": "v1",
          "parent_id": "real-influence-r3-stockhausen-cluster-conflict-report",
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-report",
          "validator_version": "v1"
        },
        "source": "The Guardian / Dorian Lynskey",
        "source_type": "knowledge",
        "title": "An awfully big adventure",
        "url": "https://www.theguardian.com/music/2005/apr/08/popandrock"
      },
      "influence_scope": null,
      "relation_type": "influenced",
      "scope_mentions": [],
      "source_entity": {
        "canonical_name": "Karlheinz Stockhausen",
        "knowledge_entity_id": "artist:karlheinz_stockhausen",
        "node_id": "eval:artist:karlheinz_stockhausen",
        "node_type": "artist"
      },
      "target_entity": {
        "canonical_name": "Cluster",
        "knowledge_entity_id": "artist:cluster",
        "node_id": "eval:artist:cluster",
        "node_type": "artist"
      },
      "uncertain": false
    },
    "rejection_reasons": [
      "direction_missing"
    ],
    "status": "rejected"
  },
  {
    "candidate": {
      "claim_confidence": 0.96,
      "claim_object_entity_id": "artist:cluster",
      "claim_predicate": "influenced",
      "claim_subject_entity_id": "artist:karlheinz_stockhausen",
      "confidence": 0.96,
      "direction": "influencer_to_influenced",
      "evidence": {
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
        "confidence": 0.96,
        "evidence_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
        "exact_span": "Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
        "source_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
        "stance": "contradiction",
        "strength": "historical_attribution"
      },
      "evidence_source": {
        "excerpt": "Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
        "id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
        "metadata": {
          "child_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-denial",
          "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
          "context_version": "context-v1",
          "document_id": "real-influence-r3-stockhausen-cluster-conflict-denial",
          "influence_expansion_version": "v1",
          "parent_id": "real-influence-r3-stockhausen-cluster-conflict-denial",
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-denial",
          "validator_version": "v1"
        },
        "source": "Indie-eye / Federico Fragasso and Michele Baldini",
        "source_type": "knowledge",
        "title": "Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010",
        "url": "https://www.indie-eye.it/recensore/generi/experimental/cluster-lintervista-di-indie-eye-it.html"
      },
      "influence_scope": null,
      "relation_type": "influenced",
      "scope_mentions": [],
      "source_entity": {
        "canonical_name": "Karlheinz Stockhausen",
        "knowledge_entity_id": "artist:karlheinz_stockhausen",
        "node_id": "eval:artist:karlheinz_stockhausen",
        "node_type": "artist"
      },
      "target_entity": {
        "canonical_name": "Cluster",
        "knowledge_entity_id": "artist:cluster",
        "node_id": "eval:artist:cluster",
        "node_type": "artist"
      },
      "uncertain": false
    },
    "rejection_reasons": [
      "direction_missing",
      "negated_influence"
    ],
    "status": "rejected"
  }
]
```

Conflict aggregation →

```json
[
  {
    "inputs": [
      {
        "aggregation_role": "excluded",
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
        "evidence_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
        "rejection_reasons": [
          "direction_missing"
        ],
        "validation_status": "rejected"
      },
      {
        "aggregation_role": "excluded",
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
        "evidence_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
        "rejection_reasons": [
          "direction_missing",
          "negated_influence"
        ],
        "validation_status": "rejected"
      }
    ],
    "outputs": [],
    "stage": "validation_normalization_then_conflict_aggregation"
  }
]
```

Final →

```json
[]
```

Expected Gold →

```json
[
  {
    "conflict_flags": [
      "attribution_conflict"
    ],
    "evidence_claim_ids": [
      "claim:real-influence-r3-stockhausen-cluster-conflict-report",
      "claim:real-influence-r3-stockhausen-cluster-conflict-denial"
    ],
    "fields": {},
    "predicate": "influenced",
    "source_entity": "eval:artist:karlheinz_stockhausen",
    "target_entity": "eval:artist:cluster"
  }
]
```

Result: FAIL; failure reasons: ['conflict failure', 'direction error']

Influence 冻结 runtime 输入缺少报道的明确主语、采访提问和成员身份上下文；审核说明不是运行时证据，不能用它补写 exact span 或绕过方向校验。
当前 report span 不能确定隐含借鉴主体，denial 缺少影响提问；因此两者仍 direction_missing，限定否认还带 negated_influence，属于 aggregation 的 excluded。完整上下文的独立专项测试可成功形成 support + contradiction；它不计入真实 3/3。

## 真实 failures、blocker 与下一轮

人工审核真实失败剩余 6 条；没有把 synthetic failure 当作真实失败。

| 优先级 | 实际失败类别 | 影响 | 建议修复点（本轮未做） |
|---|---|---|---|
| P0 依赖 | Influence conflict / source-context 缺失 | 1 个 conflict 不达标 | 保留 r6；另建可追溯的上下文输入版本，保存报道完整主语、采访问题及 speaker binding，并人工复核；不从审核说明推断 |
| P1 | Direction missing | 5 条，含上述 conflict；Pixies 同源正例/forward 有重复，非 5 个独立来源 | 后续再处理列表、采访和跨句方向；保留 reverse negative 拒绝 |
| 后续 | Scene evidence mismatch | 1 条 Neko Case positive | 后续单独解决 city/scene alias 指代，不扩大本轮 conflict 修复 |

**Blocker：有。P0 尚未达到 real conflict handling 3/3，不能声称 Conflict Handling 全部完成，也不能无条件进入 P1 Direction Repair。** 先解决可追溯 runtime 上下文依赖，保持旧 freeze；新增上下文需独立版本和审核。本轮不构造/批准新 Gold，不联网补证据，不部署或运行 Final Regression / Online E2E。

## 验证、复现与限制

- 统一评测：`.venv/Scripts/python.exe run_semantic_relations_eval.py --suite evals/semantic_relations_v1_review_r6 --output evals/reports/semantic_relations_v1_conflict_repair_r6`。退出码 2 表示仍有真实质量失败；metrics/results/gate 已完整写出，不是评分规则或执行异常。
- 报告组装：`.venv/Scripts/python.exe -m evals.conflict_handling_targeted_repair_report`。
- 自动化、静态检查与实际命令结果见 validation.json。355 个旧 dataset/report/receipt/hash 文件逐字节未变；原 28 个 runtime/evaluation 文件中只改动上述 3 个关系模块，25 个其他文件未变；新增 helper 独立记录 hash。
- r5/r6 审核期 scope lock 测试现在校验其 Git 创建提交中的 28 个源码 blob；原 freeze/receipt/parent hash 检查不变。这是历史审核期保护与新授权修复的区分，不允许修改原 scope lock。
- checkpoint 指标仍仅代表 application InMemorySaver serialization/replay；多轮合并另由专项及 session 回归验证。生产 PostgreSQL restart 测试跳过，未测在线 retrieval/Qdrant/Music API/LLM，也未运行部署。
- runner_report.md 保留 unchanged harness 的原始自动 prose，其中‘运行时未修改’等静态旧措辞不适用于本轮；本报告、runtime hash 与 validation.json 是本轮范围依据。

最后结论：baseline gate=true/true；真实 Conflict Handling=2/3，P0 有上下文 blocker；无新增 regression。Final Worldline Regression 前必须解决剩余 Influence conflict、方向漏检、Scene evidence mismatch，并补齐真实 retrieval/context 和生产 persistence 验证。

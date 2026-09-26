# P0 Influence Context Repair

**P0 Conflict Handling 正式完成：真实 conflict 2/3 → 3/3，Era / Scene / Influence 均 1/1。**
`baseline_ready=true`、`can_enter_targeted_repairs=true`、`p0_conflict_target_met=true`、`can_enter_p1_direction_repair=true`。
P1 仅具备启动条件，本轮未开始；Final Regression / Online E2E / 部署未执行。

r6 Gold、receipts、旧 freeze、评分函数、category gate、projector 均保持原 hash。新增 context-input v1 只补两个 runtime document 的原文上下文。
保护 373 个旧 artifact；新 runtime 使用原阈值 0.85、原 evidence cap 8 和原 confidence penalty。

## 设计与修改文件

- source_context.py：通用、冻结的 RelationSourceContext / ContextSegment；按 URL、publisher、title、source/document/chunk/claim ID、原文 SHA、anchor/section offsets 绑定；输入不合法即不采用。
- influence_expansion.py：仅 paired attribution support/contradiction 的 direction_missing 可用已绑定 supplement。报道要求 target 执行借鉴/转化、source 位于借鉴列表；采访要求真实问题、相邻回答和标题中的 respondent→target 绑定。
- 问题支持主动影响句和被动归因后的省略追问；不把同时出现两个实体当作方向证据。contradiction 仍为 negated_influence，validation 后才进入明确 aggregation。
- semantic_relations_models.py / semantic_relations_context_inputs.py：在既有 load_suite 增加 input-only overlay 分支，校验 base manifest / core / review hashes 与每条 input；除 relation_source_context 外与原 r6 必须完全相等。未修改评分 runner 或 metrics。
- build_influence_context_inputs_v1.py：从原 URL 抓取 DOM 原文，不读取审核说明构造 evidence；保留 source response SHA 与 locator。context capture 不声称是新的 human Gold approval。
- tests/test_worldline_influence_source_context.py、evals/tests/test_influence_context_input_repair.py：上下文、方向/说话人边界、tamper、旧 r6 control、导出验收测试。
- evals/p0_influence_context_repair_report.py、evals/README.md 与独立 input/report 目录：指标、trace、lineage、failure diagnosis 和复现入口。

无 case_id / 真实实体专用 runtime 条件，未修 standalone Pixies Direction 或 Neko Case。

## Source context 与 provenance

原 evidence exact span 和原 [0,length) offsets 留在 r6 source ledger；补充上下文另有 anchor offsets 和独立 SHA。完整 ledger 在 context_provenance.json 与 context_sources.jsonl。

| 来源 | 原 source ID | 原 claim ID | context anchor offsets | 原 source SHA256 | context SHA256 |
|---|---|---|---|---|---|
| [An awfully big adventure](https://www.theguardian.com/music/2005/apr/08/popandrock) | source:r3:guardian-cluster-stockhausen | claim:real-influence-r3-stockhausen-cluster-conflict-report | [104,234) | a798a652865f566e6cd6ebfe22b54fb892e49d79c29c34f63d5b943819128261 | 8919c5f66a72799c17fce7ed64b1cf736d2fe5c31611242cf1b62ff05415fa83 |
| [Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010](https://www.indie-eye.it/recensore/generi/experimental/cluster-lintervista-di-indie-eye-it.html) | source:r3:indieeye-cluster-stockhausen-denial | claim:real-influence-r3-stockhausen-cluster-conflict-denial | [411,496) | 9268f84e46dbb53d3e88cebfcc472609eb7eb7bb80cfea5967e57fdcce9f80a4 | 22c9b353ecbfa307b8ddd5dc0410a716c60dbaad2fd97f55698d7581e288021b |

## 前后指标（相同 r6 Gold，不同独立 context input）

Precision 只计算输出关系；Direction Accuracy 包含漏检正例。N/A 不是 100%。Evidence Coverage 是 required unique claim 的覆盖。

### approved_real_baseline

| 指标 | overall 前→后 | era 前→后 | scene 前→后 | influence 前→后 |
|---|---|---|---|---|
| relation_precision | 4/4 (100.0%) → 5/5 (100.0%) | 3/3 (100.0%) → 3/3 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | N/A (0/0) → 1/1 (100.0%) |
| evidence_coverage | 5/12 (41.7%) → 7/12 (58.3%) | 3/3 (100.0%) → 3/3 (100.0%) | 2/3 (66.7%) → 2/3 (66.7%) | 0/6 (0.0%) → 2/6 (33.3%) |
| evidence_traceability | 7/7 (100.0%) → 9/9 (100.0%) | 5/5 (100.0%) → 5/5 (100.0%) | 2/2 (100.0%) → 2/2 (100.0%) | N/A (0/0) → 2/2 (100.0%) |
| unsupported_hallucination_rate | 0/4 (0.0%) → 0/5 (0.0%) | 0/3 (0.0%) → 0/3 (0.0%) | 0/1 (0.0%) → 0/1 (0.0%) | N/A (0/0) → 0/1 (0.0%) |
| influence_direction_accuracy | 0/5 (0.0%) → 1/5 (20.0%) | N/A (0/0) → N/A (0/0) | N/A (0/0) → N/A (0/0) | 0/5 (0.0%) → 1/5 (20.0%) |
| weak_evidence_rejection | 4/4 (100.0%) → 4/4 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 2/2 (100.0%) → 2/2 (100.0%) |
| negation_rejection | 3/3 (100.0%) → 3/3 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) |
| conflict_handling | 2/3 (66.7%) → 3/3 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 1/1 (100.0%) → 1/1 (100.0%) | 0/1 (0.0%) → 1/1 (100.0%) |
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

正式真实通过 19/25 → 20/25；synthetic 20/28 不变、conflict 3/3 不变。新增 regression=0。真实 weak 4/4、negation 3/3、query isolation 25/25、checkpoint 25/25；reverse negative 2/2 均无输出关系。
新 runtime + 原 r6 输入 control 的所有 actual outputs / pass 状态 / 指标与上轮完全相等（仍 conflict 2/3），见 r6_control_comparison.json。成功来自独立 context 输入，不是改写 Gold 或静默改变旧 baseline。

## Influence 完整 trace

conflict_case_traces.jsonl 包含三个 conflict 的完整逐字段 trace：retrieved document（含真实 context）、candidate、validator、aggregation、final、Gold 和 checkpoint。下表按执行结果展开剩余 Influence 链路。

Case: `real-influence-r3-stockhausen-cluster-conflict` — PASS

Query → Karlheinz Stockhausen → Cluster 的早期音乐 influenced 归因是否有争议？保留作者判断与成员否定。

Evidence → 原 span 从 r6 ledger 原样传入；完整报道句提供 actor，采访标题提供 respondent binding，完整 question 紧接 answer。新增上下文与原 span 对应的 offsets/hash 见上表及 context_provenance.json。

Candidate →

| claim | source → target | predicate | stance |
|---|---|---|---|
| claim:real-influence-r3-stockhausen-cluster-conflict-report | Karlheinz Stockhausen → Cluster | influenced | support |
| claim:real-influence-r3-stockhausen-cluster-conflict-denial | Karlheinz Stockhausen → Cluster | influenced | contradiction |

Validator →

| claim | status | reasons |
|---|---|---|
| claim:real-influence-r3-stockhausen-cluster-conflict-report | accepted | [] |
| claim:real-influence-r3-stockhausen-cluster-conflict-denial | rejected | ['negated_influence'] |

report 的 direction_missing 被已验证 article context 解开；denial 的 direction_missing 被已验证问答和 speaker context 解开。denial 仍 rejected / negated_influence，表示不是正事实；可路由的 contradiction 未被丢弃。

Conflict aggregation →

```json
[
  {
    "inputs": [
      {
        "aggregation_role": "support",
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
        "evidence_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
        "rejection_reasons": [],
        "validation_status": "accepted"
      },
      {
        "aggregation_role": "contradiction",
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
        "evidence_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
        "rejection_reasons": [
          "negated_influence"
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
          "claim:real-influence-r3-stockhausen-cluster-conflict-report",
          "claim:real-influence-r3-stockhausen-cluster-conflict-denial"
        ],
        "predicate": "influenced",
        "source_entity": "eval:artist:karlheinz_stockhausen",
        "target_entity": "eval:artist:cluster"
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
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
        "confidence": 0.96,
        "evidence_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
        "exact_span": "tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
        "snapshot_source_id": "source:r3:guardian-cluster-stockhausen",
        "source_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
        "source_metadata": {
          "child_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-report",
          "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
          "context_direction_basis": "paired_attribution_source_context",
          "context_document_sha256": "a798a652865f566e6cd6ebfe22b54fb892e49d79c29c34f63d5b943819128261",
          "context_version": "context-v1",
          "document_id": "real-influence-r3-stockhausen-cluster-conflict-report",
          "exact_span_end": 130,
          "exact_span_start": 0,
          "influence_expansion_version": "v1",
          "influence_stance": "support",
          "influence_strength": "historical_attribution",
          "parent_id": "real-influence-r3-stockhausen-cluster-conflict-report",
          "relation_source_context": {
            "anchor": {
              "end": 234,
              "exact_span": "tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
              "role": "article_sentence",
              "start": 104
            },
            "capture_method": "verbatim_web_excerpt",
            "captured_at": "2026-09-26T09:21:19.827685+00:00",
            "chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-report",
            "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
            "content": "Cluster listened to quintessentially American groups like the Band and Crosby, Stills, Nash & Young and tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
            "content_sha256": "8919c5f66a72799c17fce7ed64b1cf736d2fe5c31611242cf1b62ff05415fa83",
            "context_id": "context:3b1d8ba20cd9bb4104b43ef7",
            "document_id": "real-influence-r3-stockhausen-cluster-conflict-report",
            "locator": "DOM p[14] (zero-based): complete sentence containing original claim span",
            "publisher": "The Guardian / Dorian Lynskey",
            "response_sha256": "379d3d209a0e4cef5be1315a593f59752d7f2e21413f1e160fbd15188c6e3e1d",
            "schema_version": "relation-source-context-v1",
            "segments": [
              {
                "end": 234,
                "exact_span": "Cluster listened to quintessentially American groups like the Band and Crosby, Stills, Nash & Young and tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
                "role": "article_sentence",
                "start": 0
              }
            ],
            "source_content_sha256": "a798a652865f566e6cd6ebfe22b54fb892e49d79c29c34f63d5b943819128261",
            "source_id": "source:r3:guardian-cluster-stockhausen",
            "source_url": "https://www.theguardian.com/music/2005/apr/08/popandrock",
            "title": "An awfully big adventure"
          },
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-report",
          "validator_version": "v1"
        },
        "span_end": 130,
        "span_start": 0,
        "stance": "support",
        "strength": "historical_attribution",
        "traceable": true
      },
      {
        "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
        "confidence": 0.96,
        "evidence_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
        "exact_span": "Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
        "snapshot_source_id": "source:r3:indieeye-cluster-stockhausen-denial",
        "source_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
        "source_metadata": {
          "child_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-denial",
          "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
          "context_direction_basis": "paired_attribution_source_context",
          "context_document_sha256": "9268f84e46dbb53d3e88cebfcc472609eb7eb7bb80cfea5967e57fdcce9f80a4",
          "context_version": "context-v1",
          "document_id": "real-influence-r3-stockhausen-cluster-conflict-denial",
          "exact_span_end": 85,
          "exact_span_start": 0,
          "influence_expansion_version": "v1",
          "influence_stance": "contradiction",
          "influence_strength": "historical_attribution",
          "parent_id": "real-influence-r3-stockhausen-cluster-conflict-denial",
          "relation_source_context": {
            "anchor": {
              "end": 496,
              "exact_span": "Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
              "role": "interview_answer",
              "start": 411
            },
            "capture_method": "verbatim_web_excerpt",
            "captured_at": "2026-09-26T09:21:23.500465+00:00",
            "chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-denial",
            "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
            "content": "Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010\n\nIntervista rilasciata dai Cluster a Federico Fragasso e Michele Baldini in occasione della loro data a Cascina (Fosfeni) il 19 marzo 2010\n\nLa critica vi ha sempre accostato al movimento Krautrock . Molti degli artisti provenienti da quella scena si dichiaravano influenzati dall’opera di Karlheinz Stockhausen. È così anche per voi?\n\nDieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
            "content_sha256": "22c9b353ecbfa307b8ddd5dc0410a716c60dbaad2fd97f55698d7581e288021b",
            "context_id": "context:cb3a5c0fd3db74d769e57b79",
            "document_id": "real-influence-r3-stockhausen-cluster-conflict-denial",
            "locator": "DOM h1; interview introduction; p[7] question immediately preceding p[8] answer",
            "publisher": "Indie-eye / Federico Fragasso and Michele Baldini",
            "response_sha256": "6dc39375a2c8282c131cc6d53908b684e69e4927a1854db328febbe72dae1f40",
            "schema_version": "relation-source-context-v1",
            "segments": [
              {
                "end": 75,
                "exact_span": "Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010",
                "role": "speaker_binding",
                "start": 0
              },
              {
                "end": 409,
                "exact_span": "La critica vi ha sempre accostato al movimento Krautrock . Molti degli artisti provenienti da quella scena si dichiaravano influenzati dall’opera di Karlheinz Stockhausen. È così anche per voi?",
                "role": "interview_question",
                "start": 216
              },
              {
                "end": 496,
                "exact_span": "Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
                "role": "interview_answer",
                "start": 411
              }
            ],
            "source_content_sha256": "9268f84e46dbb53d3e88cebfcc472609eb7eb7bb80cfea5967e57fdcce9f80a4",
            "source_id": "source:r3:indieeye-cluster-stockhausen-denial",
            "source_url": "https://www.indie-eye.it/recensore/generi/experimental/cluster-lintervista-di-indie-eye-it.html",
            "title": "Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010"
          },
          "retrieval_version": "evaluation-frozen-v1",
          "source_chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-denial",
          "validator_version": "v1"
        },
        "span_end": 85,
        "span_start": 0,
        "stance": "contradiction",
        "strength": "historical_attribution",
        "traceable": true
      }
    ],
    "predicate": "influenced",
    "raw": {
      "confidence": 0.8099999999999999,
      "direction": "influencer_to_influenced",
      "evidence": [
        {
          "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
          "confidence": 0.96,
          "evidence_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
          "exact_span": "tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
          "source_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
          "stance": "support",
          "strength": "historical_attribution"
        },
        {
          "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
          "confidence": 0.96,
          "evidence_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
          "exact_span": "Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
          "source_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
          "stance": "contradiction",
          "strength": "historical_attribution"
        }
      ],
      "influence_scope": null,
      "metadata": {
        "attribution_conflict": true,
        "conflict_flags": [
          "attribution_conflict"
        ],
        "direction_conflict": false,
        "evidence_overflow_count": 0,
        "influence_expansion_version": "v1",
        "provenance": [
          {
            "evidence_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
            "stance": "support",
            "strength": "historical_attribution"
          },
          {
            "evidence_id": "knowledge:influence:c73eb7ee6498c8bfe644d850",
            "stance": "contradiction",
            "strength": "historical_attribution"
          }
        ],
        "scope_assertions": [
          {
            "evidence_id": "knowledge:influence:cfc5239531dc9b6380ec9423",
            "scope": null,
            "scope_mentions": []
          }
        ],
        "scope_conflict": false,
        "validator_version": "v1"
      },
      "relation_type": "influenced",
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
      }
    },
    "relation_type": "influence",
    "source_entity": "eval:artist:karlheinz_stockhausen",
    "target_entity": "eval:artist:cluster"
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

Failure reasons → []。final 同时绑定 report + denial claim IDs；两条原 exact span、source/chunk IDs、SHA 未替换。checkpoint 查询语义保持一致。

## 验证、真实剩余失败与边界

复现：`.venv/Scripts/python.exe run_semantic_relations_eval.py --suite evals/semantic_relations_context_inputs_v1 --output <new-output-dir>`。本次正式评测 exit=2，原因仍有 5 个真实质量失败需要后续诊断；执行已完成，P0 gate 为 true。
自动化测试、静态检查与实际命令结果见 validation.json；旧 freezes 与所有 receipts 逐字节校验。runner_report.md 保存未修改的 runner 模板输出，其中‘运行时未修改’等旧静态 prose 不适用于本轮，以本报告与 scope_validation.json 为准。

- P0 context blocker：已解除；没有新增 regression/blocker。
- P1 Direction：4 个真实漏检（2 个独立 Pixies 证据组及其 forward 重复样本），方向准确率 1/5，后续独立修复；本轮未开始。
- 后续 Scene evidence mismatch：Neko Case 1 条，未修。
- synthetic 原有 8 个失败仍记录；弱证据误接收、retrieval missing 等不在本轮修复范围。不得把 synthetic 20/28 当作全部通过。
- checkpoint 是 application InMemorySaver serialization/replay；生产 PostgreSQL restart opt-in 未启用时仍跳过，未验证在线 retrieval / Qdrant / LLM / Online E2E。Final Regression 前需解决剩余真实失败并另行完成所需生产 persistence 验证。

最后结论：P0 Conflict Handling 在独立 context-input v1 的统一评测中正式完成 3/3。`can_enter_p1_direction_repair=true`；P1 尚未执行。`final_regression_ready=false`。

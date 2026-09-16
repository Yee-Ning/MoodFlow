# Gold V1 同名歧义结果策略与固定回放（2026-09-16）

项目负责人确认：对裸名“七里香的创作背景”和“霍元甲的创作背景”，继续维持 Query Understanding 的 `needs_input`，先澄清所指音乐实体，不默认按歌曲检索。现有 Query Understanding Golden Set 已按此判定；在线路由代码未修改。

## 数据变更与血缘

- 保留 `datasets/rag_eval_gold_v0.approved.jsonl` 原件（SHA256 `45d5db91cb2f9a87aee19b75ab648699cab1ed05d3e0a9dc5607cafc3f4b1eaf`）。
- 新增 `datasets/rag_eval_gold_v1.outcome_policy.approved.jsonl`（SHA256 `8534364fad8a3b7a16e58f2d21b78f615029aa498f8a37cb3a552e785cb0f7a1`）。30 条 case ID 和 JSONL 合同 `schema_version=v0` 保持不变；仅 046、047 的 `expected_outcome` 由 `insufficient_evidence` 改为 `needs_input`，并按合同清空这两条的 answer evidence labels。28 条其余 case 不变。
- V1 两条原有的 relevant Parent、supporting span、required facts、reference answer 仍完整保存在 V0；V1 的 `notes` 标明本次结果策略。不是删除源证据，也不是批准 `gold_v1_source_span_candidates.jsonl` 的 18 条候选。
- 结果分布从 18 `answerable` / 11 `insufficient_evidence` / 1 `needs_input` 变为 18 / 9 / 3。046、047 在 V1 中不再参加检索/上下文的可回答题指标；它们在 V0 本来也不属于 `answerable`，因此相关指标的样本数未变。
- 复现命令：`python run_rag_gold_outcome_policy.py --baseline evals/datasets/rag_eval_gold_v0.approved.jsonl --output evals/datasets/rag_eval_gold_v1.outcome_policy.approved.jsonl`。命令拒绝覆盖 V0，若输出已经存在且内容不同则报错。

## 同一历史 Trace 的确定性回放

同一批 30 条 Trace：调优前 SHA256 `96cd907265b3037fe976892a2cf5709bd21507ebdd21ea13311f4d23e08a401e`；调优后 SHA256 `540d6b32e116bc682f52ba1cb0962dfa99f80500613336930fe2a82a04aa806e`。输出分别在 `gold_v1_outcome_policy_context_baseline_20260916.json` 和 `gold_v1_outcome_policy_context_tuned_20260916.json`。只执行 `run_rag_eval.py evaluate`，未重跑模型，未调用 Judge 或付费 API。

| 指标 | V0 调优后 | V1 调优前 | V1 调优后 |
| --- | ---: | ---: | ---: |
| Outcome 状态一致率 | 66.67% | 73.33% | 73.33% |
| Top40 精确 Parent Recall | 53.80% | 53.80% | 53.80% |
| Top12 精确 Parent Recall | 23.15% | 23.15% | 23.15% |
| Final Context Precision | 12.50% | 7.18% | 12.50% |
| Supporting Span Coverage | 17.59% | 17.59% | 17.59% |

Outcome 状态一致率提高 6.67 个百分点，**完全由两条标签与既有 Trace 的 `needs_input` 对齐造成**，不是检索、精排、Context Adapter 或生成质量提升。其余指标不变；Context 前后差异仍来自以前的 Adapter 参数回放，与本次策略修订无关。低精确 Parent Recall 和 Supporting Span Coverage 仍不能据此验收。

## 后续门槛

`gold_v1_source_span_candidates.jsonl` 的 18 条来源匹配提案仍待语义支撑核实，尤其 003、034、040、042；002、037 的替代 Parent 也尚未完成事实覆盖审查。先稳定证据标签，再针对真实检索/精排损失做固定集调优。不要把本次纯标签修订当作算法优化成果。

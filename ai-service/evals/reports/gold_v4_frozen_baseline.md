# Frozen Gold V4：固定历史 Trace baseline

- 正式 Gold：`evals/datasets/rag_eval_gold_v4.frozen.jsonl`；SHA-256 `1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`；50 条，full 29 / partial 10 / insufficient 7 / needs_input 4。
- 全量 supported evidence units：95。历史 Trace 仅覆盖其中原 30 条；新增 049–068 共 20 条没有 Trace，不能计算 50 条全量 baseline。
- 下列是**有历史 Trace 的 30-case 子集基线**。V4 evaluator 仅对 approved full/partial case 计分；Recall 与 Final Context Precision 均按 eligible case 宏平均。Final Context Precision 是唯一 Parent 的 precision，不是 evidence-unit 生存率。
- 只读 replay：逐份核对 Trace SHA-256，未重新运行 Retrieval。

| Trace | eligible full | eligible partial | supported units | insufficient | needs_input | Evidence Recall@40 | Evidence Rerank Recall@12 | Final Context Precision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fallback | 13 | 8 | 68 | 6 | 3 | 0.641383 | 0.442630 | 0.119048 |
| nav_context6 | 13 | 8 | 68 | 6 | 3 | 0.641383 | 0.442630 | 0.139683 |

## Trace 覆盖与口径

- `fallback`：`evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl`；SHA-256 `7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e`；case 30；未覆盖 `rag-v0-049, rag-v0-050, rag-v0-051, rag-v0-052, rag-v0-053, rag-v0-054, rag-v0-055, rag-v0-056, rag-v0-057, rag-v0-058, rag-v0-059, rag-v0-060, rag-v0-061, rag-v0-062, rag-v0-063, rag-v0-064, rag-v0-065, rag-v0-066, rag-v0-067, rag-v0-068`；未覆盖 supported units 27。
- `nav_context6`：`evals/artifacts/gold_v2_trace_v3_nav_context6_20260916.jsonl`；SHA-256 `9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967`；case 30；未覆盖 `rag-v0-049, rag-v0-050, rag-v0-051, rag-v0-052, rag-v0-053, rag-v0-054, rag-v0-055, rag-v0-056, rag-v0-057, rag-v0-058, rag-v0-059, rag-v0-060, rag-v0-061, rag-v0-062, rag-v0-063, rag-v0-064, rag-v0-065, rag-v0-066, rag-v0-067, rag-v0-068`；未覆盖 supported units 27。
- Top40/Top12 命中按 claim-scoped duplicate/equivalent/complementary 关系计；未标 claim 的旧 span 仍按既有 evaluator 的独立 evidence-unit 回退规则计，不推断为同一 claim。
- historical Trace 未记录 Top40 以外排名、reranker score 或逐 Parent 的 context 淘汰规则；本报告不推断这些不存在的字段。

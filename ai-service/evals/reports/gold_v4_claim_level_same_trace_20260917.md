# Gold V4 claim 标注后 SAME TRACE 复算

只读取原有两份 historical Trace 的 Top40、Top12 和 final context；复用未修改的 V4-aware + group-aware scorer。未运行 Retrieval。

- Gold V3 SHA-256：`5d2888542042af16bf38d3ae63f45c956c8e37f658397e2f5d3a78c3759236e9`
- Gold V4 修改前 SHA-256：`1a3de179c82cd5e6d3c2cb49fc76ef82a947dc02ba4449536349dbce7297850a`
- Gold V4 修改后 SHA-256：`5a6e972ee7ffe4a94b2b35974d59b406613c696026264af0e35e1056863f585f`
- 显式 supported claims / evidence units：65；unsupported claims：8；evidence groups：2；无 claim 正向 span：0。

## fallback

Trace：`evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl`；SHA-256：`7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e`。
full=13，partial=8，insufficient=6，needs_input=3；评分单位=65（{'claim': 63, 'group': 2}）。

| 指标 | 修改前 | 修改后 | 差值 |
| --- | ---: | ---: | ---: |
| Evidence Recall@40 | 0.6413832200 | 0.6413832200 | +0.0000000000 |
| Evidence Rerank Recall@12 | 0.4283446712 | 0.4283446712 | +0.0000000000 |
| Final Context Precision | 0.1190476190 | 0.1190476190 | +0.0000000000 |

partial 逐 case 指标变化：无。

## nav_context6

Trace：`evals/artifacts/gold_v2_trace_v3_nav_context6_20260916.jsonl`；SHA-256：`9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967`。
full=13，partial=8，insufficient=6，needs_input=3；评分单位=65（{'claim': 63, 'group': 2}）。

| 指标 | 修改前 | 修改后 | 差值 |
| --- | ---: | ---: | ---: |
| Evidence Recall@40 | 0.6413832200 | 0.6413832200 | +0.0000000000 |
| Evidence Rerank Recall@12 | 0.4283446712 | 0.4283446712 | +0.0000000000 |
| Final Context Precision | 0.1396825397 | 0.1396825397 | +0.0000000000 |

partial 逐 case 指标变化：无。

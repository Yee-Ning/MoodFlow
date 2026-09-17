# Gold V4 evidence_groups：同 Trace 兼容性与差异

- Gold V3 SHA-256：`5d2888542042af16bf38d3ae63f45c956c8e37f658397e2f5d3a78c3759236e9`。
- Gold V4 candidate SHA-256：`75dc8910eab396ac7815ec303a126c14dc44b85bf97924f416f1278eee4ee038`。
- 两份历史 Trace 均经 SHA-256 核对；本次仅离线读取，没有运行 Retrieval / Rerank。
- 仅比较 Recall@40、Rerank Recall@12、Final Context Precision；case eligibility 仍是 approved 且 expected_outcome=answerable。部分可回答 case 当前不进入这三项指标，answerability_level 不参与筛选。
- Recall 分母仍是每条 case 的 relevant Parent 数，再对 eligible case 宏平均；Final Context Precision 分母仍是 final context 的唯一 Parent 数，再宏平均。
- 原 evaluator 在 Candidate Top40、Rerank Top12、Final Context 都仅将 `relevant_parent_ids` 与 retrieved Parent ID 取交集；不检查 evidence_text 是否出现在 Trace 内容中。Final Context Precision 以唯一 Parent ID 命中数除以唯一 context Parent 数。
- `expected_outcome` 还在旧 evaluator 中决定 outcome accuracy 的对照逻辑；`answerability_level` 目前只存于 Gold，不用于上述评分。
- 无 group 的 exact Parent 必须按 ID 命中；duplicate/equivalent 组任意 acceptable Parent 命中；complementary 组须全部 acceptable Parent 命中才算完整证据。

## fallback

历史 Trace：`evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl`；SHA-256 `7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e`。
V3 legacy 与 compatibility：18 条 eligible case；逐 case 差异 0 条；三项指标逐值相同。

| Metric | V3 Legacy | V3 Compatibility |
| --- | ---: | ---: |
| Recall@40 | 0.6673280423 | 0.6673280423 |
| Rerank Recall@12 | 0.4166666667 | 0.4166666667 |
| Final Context Precision | 0.1805555556 | 0.1805555556 |

| Metric | V4 Legacy ID-only | V4 Group-aware |
| --- | ---: | ---: |
| Recall@40 | 0.6868131868 | 0.6868131868 |
| Rerank Recall@12 | 0.3827838828 | 0.3827838828 |
| Final Context Precision | 0.1153846154 | 0.1153846154 |

V4 eligible case：13；指标差异 0 条。

- 无 MISS→HIT，也无异常 HIT→MISS。

### Group 逐 stage 核对

- rag-v0-026 / recall_at_40 / `026-award`：relation=`duplicate`，retrieved=`['parent-a734651048b7159210d5c4a6']`，acceptable=`['parent-a734651048b7159210d5c4a6', 'parent-287a536a7936d114db25a509']`，complete=`True`。
- rag-v0-026 / recall_at_40 / `026-track-1`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / recall_at_40 / `026-track-2`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / recall_at_40 / `026-track-3`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / recall_at_40 / `026-track-4`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / rerank_recall_at_12 / `026-award`：relation=`duplicate`，retrieved=`[]`，acceptable=`['parent-a734651048b7159210d5c4a6', 'parent-287a536a7936d114db25a509']`，complete=`False`。
- rag-v0-026 / rerank_recall_at_12 / `026-track-1`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / rerank_recall_at_12 / `026-track-2`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / rerank_recall_at_12 / `026-track-3`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / rerank_recall_at_12 / `026-track-4`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / final_context_precision / `026-award`：relation=`duplicate`，retrieved=`[]`，acceptable=`['parent-a734651048b7159210d5c4a6', 'parent-287a536a7936d114db25a509']`，complete=`False`。
- rag-v0-026 / final_context_precision / `026-track-1`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / final_context_precision / `026-track-2`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / final_context_precision / `026-track-3`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / final_context_precision / `026-track-4`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。

## nav_context6

历史 Trace：`evals/artifacts/gold_v2_trace_v3_nav_context6_20260916.jsonl`；SHA-256 `9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967`。
V3 legacy 与 compatibility：18 条 eligible case；逐 case 差异 0 条；三项指标逐值相同。

| Metric | V3 Legacy | V3 Compatibility |
| --- | ---: | ---: |
| Recall@40 | 0.6951058201 | 0.6951058201 |
| Rerank Recall@12 | 0.4166666667 | 0.4166666667 |
| Final Context Precision | 0.1777777778 | 0.1777777778 |

| Metric | V4 Legacy ID-only | V4 Group-aware |
| --- | ---: | ---: |
| Recall@40 | 0.6868131868 | 0.6868131868 |
| Rerank Recall@12 | 0.3827838828 | 0.3827838828 |
| Final Context Precision | 0.1307692308 | 0.1307692308 |

V4 eligible case：13；指标差异 0 条。

- 无 MISS→HIT，也无异常 HIT→MISS。

### Group 逐 stage 核对

- rag-v0-026 / recall_at_40 / `026-award`：relation=`duplicate`，retrieved=`['parent-a734651048b7159210d5c4a6']`，acceptable=`['parent-a734651048b7159210d5c4a6', 'parent-287a536a7936d114db25a509']`，complete=`True`。
- rag-v0-026 / recall_at_40 / `026-track-1`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / recall_at_40 / `026-track-2`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / recall_at_40 / `026-track-3`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / recall_at_40 / `026-track-4`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / rerank_recall_at_12 / `026-award`：relation=`duplicate`，retrieved=`[]`，acceptable=`['parent-a734651048b7159210d5c4a6', 'parent-287a536a7936d114db25a509']`，complete=`False`。
- rag-v0-026 / rerank_recall_at_12 / `026-track-1`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / rerank_recall_at_12 / `026-track-2`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / rerank_recall_at_12 / `026-track-3`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / rerank_recall_at_12 / `026-track-4`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / final_context_precision / `026-award`：relation=`duplicate`，retrieved=`[]`，acceptable=`['parent-a734651048b7159210d5c4a6', 'parent-287a536a7936d114db25a509']`，complete=`False`。
- rag-v0-026 / final_context_precision / `026-track-1`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / final_context_precision / `026-track-2`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / final_context_precision / `026-track-3`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。
- rag-v0-026 / final_context_precision / `026-track-4`：relation=`duplicate`，retrieved=`['parent-cfb9f049a0cfdf86866257fd']`，acceptable=`['parent-cfb9f049a0cfdf86866257fd', 'parent-d0c34fdc20c0a8d545deea06']`，complete=`True`。

## 解释与边界

- V4 当前仅 026 有五组 `duplicate`：四个独立歌曲 claim 各自复用同一对曲目 Parent，另有一个奖项组；没有 `equivalent` 或 `complementary` 的真实 case，后两种关系由单元测试验证。
- 026 在候选集和 Top12 中出现替代 Parent `parent-d0c34fdc20c0a8d545deea06`；是否产生 MISS→HIT 以本报告逐 case/stage 差异为准。Final context 未出现该替代 Parent。
- 当前 legacy 的 expected_outcome 决定 eligible case 和 outcome accuracy；answerability_level 不参与评分。V4 的 partially_answerable 投影到 insufficient_evidence，所以这些 case 暂不进入本次三项指标；本轮未改变该规则。
- 本报告不代表 Gold V4 冻结或 Retrieval 质量变化。

# Gold V4 freeze manifest

- 状态：正式冻结。文件：`evals/datasets/rag_eval_gold_v4.frozen.jsonl`。
- 50 条；SHA-256：`1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`。
- 与历史 candidate `evals/datasets/rag_eval_gold_v4.expanded_50.candidate.jsonl` 逐字节相同；candidate 原文件保留。没有修改任何 case 内容。
- 分类：fully_answerable 29、partially_answerable 10、insufficient_evidence 7、needs_input 4；pending/manual_review 0。
- frozen corpus release：`knowledge-v2-8608ffc2fa72e441`；`knowledge_enriched_chunks.jsonl` SHA-256：`8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。
- V4 evaluator：`app/rag/evaluation/v4_answerability.py`（answerability-aware）与 `app/rag/evaluation/group_aware.py`（group-aware）；最近 evaluator commit：`b6ea8c77cdba94db055113b14d973803090f058a`。文件 SHA-256 分别为 `efd6f6dbf4a7fe48c5f6cdbac68fe137c19ad4ff4245226766631574bc96499d`、`d5e5b90013a90ede22d831224324cf96eefd9acbc2cdf359899c46c96d0e8e9ee`。
- 冻结操作前代码 commit：`5f3dfe75387bbdf4a5095e859c21bd0280d860bc`。冻结提交本身由 Git 历史记录；避免在 manifest 中自引用提交哈希。
- 固定 historical Trace（各 30 条，原 seed case；不是完整 50 条）：
  - `evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl`；SHA-256 `7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e`。
  - `evals/artifacts/gold_v2_trace_v3_nav_context6_20260916.jsonl`；SHA-256 `9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967`。

历史 Trace 缺少新增 049–068 共 20 条；在不得重新运行 Retrieval 的约束下，后续 baseline 与 failure analysis 仅能对有 Trace 的子集计算，不得把缺失 Trace 视为检索失败，也不得把子集结果冒充 50 条全量基线。

**Gold V4 自此冻结；后续 Retrieval 调优不得通过修改 Gold 提升指标。**

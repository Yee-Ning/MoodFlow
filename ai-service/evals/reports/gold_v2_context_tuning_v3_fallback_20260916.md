# Context Adapter V1 参数实验报告

固定评测集：30 条；可回答调优样本：18 条。
上游数据：`D:\code\ProjectsAll\MoodFlow\ai-service\evals\artifacts\gold_v2_trace_v3_fallback_20260916.jsonl`。

> 注意：Top40/Top12、Parent 内容和阶段延迟来自固定真实 trace；原 trace 未持久化 subquery retrieval leg，5 个分解查询样本的 leg 归属按原 Context Pack 顺序恢复。

| 实验 | 变更 | Precision | Span Coverage | Context Recall | Subquery | Entity | Duplicate | Avg Tokens | Context P95/P99 ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline-current | current defaults | 0.1667 | 0.2593 | 0.2778 | 1.0000 | 1.0000 | 0.0463 | 2119.2 | 22.050/24.768 |
| context_top_k-4 | context_top_k=4 | 0.1667 | 0.2593 | 0.2778 | 1.0000 | 1.0000 | 0.0463 | 2119.2 | 25.521/27.152 |
| context_top_k-6 | context_top_k=6 | 0.1454 | 0.2778 | 0.3102 | 1.0000 | 1.0000 | 0.0463 | 2651.3 | 21.400/21.865 |
| control-fixed | fixed 900 / 0.90 | 0.1315 | 0.2778 | 0.3102 | 1.0000 | 1.0000 | 0.0463 | 2733.9 | 21.284/24.142 |
| neighbor_window-0 | neighbor_window=0 | 0.1593 | 0.2917 | 0.3565 | 1.0000 | 1.0000 | 0.0463 | 2381.3 | 19.672/20.180 |
| neighbor_window-1 | neighbor_window=1 | 0.1481 | 0.2917 | 0.3241 | 1.0000 | 1.0000 | 0.0463 | 2582.3 | 21.316/24.696 |
| neighbor_window-2 | neighbor_window=2 | 0.1454 | 0.2778 | 0.3102 | 1.0000 | 1.0000 | 0.0463 | 2651.3 | 21.580/21.616 |
| source_cap-1 | source_cap=1 | 0.1426 | 0.2639 | 0.3148 | 1.0000 | 1.0000 | 0.0463 | 2357.8 | 19.576/19.988 |
| source_cap-2 | source_cap=2 | 0.1574 | 0.2917 | 0.3565 | 1.0000 | 1.0000 | 0.0463 | 2395.4 | 19.464/20.261 |
| source_cap-3 | source_cap=3 | 0.1593 | 0.2917 | 0.3565 | 1.0000 | 1.0000 | 0.0463 | 2381.3 | 20.730/21.995 |
| token_budget-3000 | token_budget=3000 | 0.1593 | 0.2917 | 0.3565 | 1.0000 | 1.0000 | 0.0463 | 2381.3 | 19.437/19.749 |
| token_budget-4500 | token_budget=4500 | 0.1574 | 0.2917 | 0.3565 | 1.0000 | 1.0000 | 0.0463 | 2565.5 | 24.014/24.641 |
| token_budget-6000 | token_budget=6000 | 0.1574 | 0.2917 | 0.3565 | 1.0000 | 1.0000 | 0.0463 | 2565.5 | 19.015/20.351 |

## 推荐配置

推荐实验：`token_budget-3000`。

```yaml
context_top_k: 6
neighbor_window: 0
source_cap: 3
token_budget: 3000
max_item_tokens: 900
near_duplicate_threshold: 0.90
```

## Baseline vs Best

- Final Context Precision: -0.0074
- Supporting Span Coverage: +0.0324
- Final Context Recall: +0.0787
- Average Tokens: +262.1
- Context P95: -2.613 ms

Evidence Span Extractor 后续分析：需要；最佳配置的 Supporting Span Coverage 仍低于 0.85。

- Supporting spans: 13/51
- 已选 Parent 但窗口未覆盖标注 span: 4
- Parent 未进入最终 Context: 34
- 标注 span 不在索引 Parent: 0
- 结论：主要缺口来自 Parent 选择，暂不应优先升级 Evidence Span Extractor。

## 全链路延迟

| 阶段 | 样本数 | P50 ms | P95 ms | P99 ms |
|---|---:|---:|---:|---:|
| Retrieval | 27 | 385.130 | 696.322 | 4478.423 |
| Rerank | 27 | 1845.821 | 5182.001 | 11514.063 |
| Context Adapter | 27 | 16.580 | 22.792 | 23.810 |
| Generation | 0 | - | - | - |

> Generation 尚未运行；参数扫描阶段不重复调用 LLM。


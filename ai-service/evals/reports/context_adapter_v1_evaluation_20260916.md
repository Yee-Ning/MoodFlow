# Context Adapter V1 调优与验收（2026-09-16）

## 范围与数据

固定 `rag_eval_v0.approved.silver.jsonl`：50 条（44 可回答、5 追问、1 证据不足）。
Top40/Top12 复用先前真实采集的 `baseline-v0/traces.context-v1.jsonl`，Parent 内容由同一索引
Release `knowledge-v2-8608ffc2fa72e441` 的投影恢复。Docker Desktop 本次因 stale runtime
socket 无法启动，未重新请求 Qdrant；这不影响固定上游 Context 参数对比，但不是一次新的
端到端在线采集。原 trace 未持久化每条 subquery retrieval leg，5 个分解查询样本按原 Context
顺序恢复 leg 归属，因此 Subquery Coverage=1.0 仅作回放诊断，不能视为独立证据。

Silver 标签由原链路生成，存在同源偏差；以下 Precision、Recall 和 Span 是这批固定 Silver
上的相对回归指标，不是独立人工 Gold 质量证明。没有调用 DeepEval Judge。

## 实验方法与结果

保留原默认 `8 / 1 / 3 / 6000 / 1200 / 0.92` 为 baseline。先固定
`max_item_tokens=900`、`near_duplicate_threshold=0.90`，再依次扫描 Top K、邻居窗口、来源
上限、总 Token 预算；每阶段三个取值仅改变一个参数，胜出配置传给下一阶段，共 13 组，
无全组合。以 Precision、Span、Subquery/Entity 加权分数排序，禁止相对阶段参考配置显著
降低 Context Recall 或 Span；同分优先更少 Token。

| 配置 | Precision | Span Coverage | Context Recall | Subquery / Entity | Duplicate | 平均 Tokens | Context P95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| 原默认 baseline | 0.4005 | 0.6364 | 0.9242 | 1.0 / 1.0 | 0.1418 | 4130.9 | 18.82 |
| V1 推荐配置 | 0.7045 | 0.6591 | 0.9242 | 1.0 / 1.0 | 0.1418 | 2095.3 | 24.25 |
| 变化 | +0.3040 | +0.0227 | 0 | 0 / 0 | 0 | −2035.6（−49.3%） | +5.42 |

完整 13 组参数、Token P50/P95/P99、Context P50/P95/P99 与 delta 见
`evals/artifacts/context-adapter-v1/context-tuning-report.md` 和同目录 JSON。延迟是本机离线
回放单次测量，毫秒级差异受运行环境噪声影响，不据此宣称线上 P95 改善。

推荐并已落地的 V1 配置：`context_top_k=4`、`neighbor_window=2`、`source_cap=3`、
`token_budget=3000`、`max_item_tokens=900`、`near_duplicate_threshold=0.90`。

## 最佳配置全链路验收

只对最佳配置调用答案模型，44/44 可回答样本成功；另 5 条追问和 1 条证据不足均符合预期。
确定性结果：Recall@40=1.0、Rerank Recall@12=1.0、Final Context Precision=0.7045、
Context Recall=0.9242、Supporting Span Coverage=0.6591；引用出现率、引用 ID 合法率、
Token 预算合规率均为 1.0。语义 Faithfulness/Answer Relevancy 本轮未调用 Judge，未验收。

| 阶段 | n | P50 ms | P95 ms | P99 ms |
|---|---:|---:|---:|---:|
| Retrieval（固定历史真实 trace） | 44 | 95.73 | 1095.62 | 2096.91 |
| Rerank（固定历史真实 trace） | 44 | 1562.05 | 2746.79 | 5377.35 |
| Context Adapter（本机最佳配置回放） | 44 | 16.08 | 23.47 | 24.45 |
| Generation（真实 API 请求，不含批处理排队） | 44 | 4567.41 | 6178.57 | 7574.31 |

原始生成 Trace 和确定性验收 JSON 分别见
`evals/artifacts/context-adapter-v1/best-traces.generated.jsonl`、
`evals/artifacts/context-adapter-v1/final-v1-evaluation-report.json`。

## 未收敛项

132 个已标注 supporting spans 中覆盖 87 个，缺失 45 个：35 个所在 Parent 已选中，但
当前 Child 锚点的邻居窗口/900-token 截断没有包含标注原文；10 个 Parent 未进入最终 Top4；
没有标注 span 缺失于索引 Parent。因 35/45 的主要缺口是提取窗口，本轮只记录
Evidence Span Extractor 后续设计需求，不开发 V2，也不继续提高 Top K/Token 预算。

V1 的条件性验收结论：对当前 Silver 固定集，Precision 和 Token 目标改善、Recall 未退化，
合同与引用通过；Span Coverage 仍仅 0.6591，不能宣称证据片段覆盖目标已收敛。下一阶段
应先补一份独立 Gold 标注并在 Docker/Qdrant 恢复后复验在线路径，再单独评审 Extractor V2。

# Jev Choice V2 independent Single blind validation

使用 `single_blind_v3.jsonl` 20 条，SHA256 `8ca3cf3aca26530a3b8bfdcc9b83b0828bede2ba5b7916eb6c6f5caaeaf0118e`，每 route 5 条。构造时检查其 query 与 Capability Contract 正反例、边界例、模糊例及 Gold V2 query 不重复，也不与 V3 dev/blind query 完全相同。样本在 Multi dev 调参之前冻结，但仍由本轮人工撰写；未做独立人工盲审或真实生产流量采样。它不参与 Multi head 或阈值选择。

原样使用 Contract-aligned Jev Choice V2 的 `router_criteria()`、原 question instructions、`jev-latest` alias、5 秒超时、零重试和现有 .8 低置信度口径。没有修改 Single criteria、threshold、Shadow Mode 或生产接管状态。实际响应模型为 `jev-1.13.0`。

| 指标 | 本轮 Single blind |
| --- | ---: |
| Accuracy | **20/20 = 100%** |
| Macro F1 | **1.000** |
| CurrentSong / ToolCalling / GeneralChat / Worldline | 各 5/5，P/R/F1 均 1.000 |
| GeneralChat ↔ Worldline 混淆 | 0 |
| 低置信度（confidence < .8） | 0；最低 .91（`s-g-04`） |
| parse/schema、API/timeout 失败 | 0 / 0 |
| API calls、input/output tokens | 20 次；20,883 / 1,050 |
| Wall latency average / P50 / P95 / P99 | 637.17 / 570.30 / 963.16 / 1408.36 ms |

逐条预测、confidence 与概率保存在 `jev_single_blind_v3_results.jsonl`，机读汇总在 `jev_single_blind_v3_metrics.json`。本集合虽不复用示例原句，但能力类别由同一 Contract 指导且每类仅 5 条，一次运行不能证明跨域或长期稳定。**未观察到明显的 Single 泛化下降；正式接管生产仍缺独立盲审、重复稳定性和完整下游验收。** 本阶段仅记录风险，不回调 Single criteria。

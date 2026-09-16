# Gold V2 检索召回复测（2026-09-16）

固定输入：Gold V2 的 30 条已批准 case，其中 18 条 `answerable` 参与 Parent 召回；冻结来源 `knowledge-v2-8608ffc2fa72e441`，索引投影 V3。全程 `--no-llm`，精排为本地 Qwen 模型，未调用付费 Judge。比较口径均为**精确 Parent ID**；不把在线排序结果用于修改 Gold。

| 指标 | V3 + 探索型实体兜底 | 本次最终配置 | 变化 |
| --- | ---: | ---: | ---: |
| Top40 Parent Recall | 68.61% | 71.39% | +2.78 pp |
| Top12 Parent Recall | 38.43% | 38.43% | 0 |
| Final Context Recall | 27.78% | 35.65% | +7.87 pp |
| Supporting Span Coverage | 25.93% | 29.17% | +3.24 pp |
| Final Context Precision | 16.67% | 15.93% | -0.74 pp |
| Token Budget Compliance | 100% | 100% | 0 |

本次改动：时间线查询排除段落标题为“外部連結 / 外部链接 / External links”的导航类命中，039 的 Top40 从 0/2 变为 1/2，其他 Gold case 的 Top40 Recall 未下降。单独把时间线 overfetch 从 2 提到 4 并未增加召回，已撤销。Context Adapter 基于固定上游 trace 的单变量扫描，将 `max_items` 从 4 改为 6、`neighbor_window` 从 2 改为 0；其余 `source_cap=3`、`token_budget=3000`、`max_item_tokens=900`、`near_duplicate_threshold=0.90` 不变。完整 live trace 的最终 Context 指标与固定上游扫描一致。

最终 18 条 answerable 的阶段延迟 P50/P95/P99：Retrieval 363/1029/2756 ms；Rerank 1981/2929/3532 ms；Context Adapter 16/23/26 ms。Generation 本轮未运行，不能填零或声称其延迟通过。小样本本机延迟不是生产 P99。

**验收结论：未达到可宣布“召回指标均达标”的状态。** 当前配置没有事前批准的数值质量门槛，不能事后按现有结果设置及格线；仅依据原有 Context 调优分析的 85% Supporting Span Coverage 目标，本次 29.17% 明显不足。18 条中，Top40 对 039 只命中 1/2；001、002、004、026、040、043 等存在已召回但未进入 Top12 的精确 Parent。039 的 Gold 包含来自 Wikipedia 的荣誉与商业事实，来源本身有可靠性注意项；002、026 的 Top12 也存在尚未完全标注的替代证据，因此精确 Parent 召回不能等同于事实证据召回。下一轮应先固定**事实/证据级**核验口径，再针对精排输入的 best-matched Child 与替代证据做误差分析和小规模实验，不应继续单纯扩大候选池或调整 Gold 来提高数字。

机器可读结果：`gold_v2_full_v3_fallback_20260916.json`、`gold_v2_context_tuning_v3_fallback_20260916.json`、`gold_v2_full_v3_nav_context6_20260916.json`。原始 live traces 在 `evals/artifacts/`，为生成产物，未入库。

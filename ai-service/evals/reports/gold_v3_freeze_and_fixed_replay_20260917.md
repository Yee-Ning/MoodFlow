# Gold V3 冻结来源修订与固定 Trace 回放（2026-09-17）

## 冻结结论与边界

- Gold V3：`evals/datasets/rag_eval_gold_v3.ai_assisted_source_resolution.jsonl`，SHA256 `5d2888542042af16bf38d3ae63f45c956c8e37f658397e2f5d3a78c3759236e9`。从未改动的 Gold V2（SHA256 `7963e11cf73dab316903b5a7904998852f10e677e5f5c93a479583981c27937d`）生成；30 条 case 中只修改 8 条，outcome 分布仍为 18 `answerable` / 9 `insufficient_evidence` / 3 `needs_input`。
- 证据 release 为 `knowledge-v2-8608ffc2fa72e441`，Parent artifact SHA256 `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。生成程序核对 release 哈希、Parent / Source 关联、所有现有与修订 span 的逐字包含、相关 Parent 的 span 覆盖和数据 schema。修订候选见 `gold_v3_source_audit_candidates.jsonl`；构建程序为 `run_rag_gold_v3_finalize.py`。
- 此版本是**项目负责人授权的 AI 辅助来源审查**，不是独立人类盲标；`approved` 沿用原数据集审批状态并标明 V3 审查来源。它是不可变版本化评测集，不表示质量目标已验收。配置仍要求 `expected_case_count: 50`，当前仅 30 条，`baseline_ready: false`。
- V2/V3 指标对比只换标签、不换 Trace，不是模型或检索改进。未调用在线 Retrieval、Rerank、Generation、DeepEval Judge 或付费 API。

## 036、039 证据问题的解决

| Case | 原问题 | V3 处理 | 剩余限制 |
| --- | --- | --- | --- |
| 036 | V2 声称“到 2005 仍延续个人化、中国风与多元表达”，但四个现标 Parent 均不直接支撑该年份的整体风格判断；其中 `jc-v2-048` 还将《七里香》与《听妈妈的话》的年份混写。 | 删除该未经证实的 2005 风格断言和 `parent-fec4e2f5d74c8fe65d787dd1`；保留 2000 多元融合与 2003《东风破》具体风格证据。增补唱片公司来源 `jc-v1-008` 的 `parent-95160d6644f27840faf6b97d`，仅证明《十一月的萧邦》的 2005 发行节点。参考答案明确说明无法精确概括 2005 整张专辑风格。 | 这是谨慎收窄，不是已经找到 2005 风格演变的直接原始研究。若以后补入新来源，须另做版本，不可修改 V3。 |
| 039 | V2 大段 span 混入多个缺乏优质来源的 2009 荣誉、具体投资金额及无关年份，且两个 Parent 同属一份 Wikipedia 文档。 | 删除 2009 荣誉与金额断言，把 span 缩成 2010《Fast Company》榜单、2011 DejaVu 餐厅、2012 西安 KTV 三个精确节点；required facts 与参考答案只说公众影响力向商业活动扩展，不声称逐年完整变化。 | 冻结语料内仍是同一 Wikipedia 来源的两个 Parent，不能把它们算作独立来源。外部一手材料只用于事实复核，**没有**冒充新的冻结 Parent 或参与 Recall 计算。 |

039 的外部复核：榜单主办方的 [2010 年原始榜单](https://www.fastcompany.com/most-creative-people/2010)列出 Jay Chou 第 70 位；2011 年 [餐厅开业现场报道](https://tw.news.yahoo.com/%E5%91%A8%E6%9D%B0%E5%80%AB-%E4%BC%BC%E6%9B%BE%E7%9B%B8%E8%AD%98-%E9%A4%90%E5%BB%B3%E4%BB%8A%E9%96%8B%E5%B9%95-%E8%BF%BD%E6%98%9F%E6%96%B0%E6%99%AF%E9%BB%9E-120100872.html)记录 Deja Vu 开业；2012 年 [周杰伦谈 KTV 的采访](https://ent.sina.com.cn/s/h/p/2012-01-17/11593536234.shtml)记录其参与真爱范特西。三者支持保留**事件级别**叙述，但不支持 V2 原来的其他奖项或投资金额。网页材料与冻结 release 分离，不计入本轮标签的 `expected_source_ids`。

其余 6 条调整沿用前一份 [来源审计](gold_v3_source_audit_20260917.md) 中的逐项理由：002、006、026、040 补事实证据，042、043 校正来源归属。026 的一首合作被纠正为冻结曲目表可核实的四首；040 补齐缺失 span，并加入独立英文论文对 2007 电影年份的交叉说明。没有按在线排序结果反向标注。

## V2 与 V3：同 Trace 确定性指标

两条历史 Trace 分别固定：

- `evals/artifacts/gold_v2_trace_v3_fallback_20260916.jsonl`，SHA256 `7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e`。
- `evals/artifacts/gold_v2_trace_v3_nav_context6_20260916.jsonl`，SHA256 `9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967`。

| 指标 | Fallback V2 | Fallback V3 | Nav/Context6 V2 | Nav/Context6 V3 |
| --- | ---: | ---: | ---: | ---: |
| Outcome 准确率 | 73.33% | 73.33% | 73.33% | 73.33% |
| Retrieval Recall@20 | 61.20% | 59.79% | 61.20% | 59.79% |
| Retrieval Recall@40 | 68.61% | 66.73% | 71.39% | 69.51% |
| Retrieval MRR@10 | 0.2349 | 0.2381 | 0.2349 | 0.2381 |
| Rerank Recall@12 | 38.43% | 41.67% | 38.43% | 41.67% |
| Rerank MRR@10 | 0.3359 | 0.4192 | 0.3359 | 0.4192 |
| Final Context Precision | 16.67% | 18.06% | 15.93% | 17.78% |
| Final Context Recall | 27.78% | 30.09% | 35.65% | 38.89% |
| Supporting Span Coverage | 25.93% | 27.69% | 29.17% | 31.85% |

V3 的 Recall@40 **下降**、Rerank Recall@12 等上升，原因是 relevant Parent 集合和 span 定义变了；不能据此称系统变好或变差。Outcome、候选序列、精排序列、上下文和耗时均取自原历史 Trace。机器报告为 `gold_v3_fixed_replay_v2_fallback_20260917.json`、`gold_v3_fixed_replay_v3_fallback_20260917.json`、`gold_v3_fixed_replay_v2_nav_20260917.json`、`gold_v3_fixed_replay_v3_nav_20260917.json`。

延迟数值不因标签改变。Fallback Trace 中 Retrieval P50/P95/P99 为 406/1472/4937 ms，Rerank 为 1848/3122/3828 ms，Context Adapter 为 22/30/44 ms；Nav/Context6 Trace 中分别为 363/1029/2756 ms、1981/2929/3532 ms、16/23/26 ms。每个阶段仅 18 条可回答案例样本，P99 不具生产负载代表性；这两条 Trace 的 Generation 延迟样本数为 0，不能报告其分位数。

## 验收判断与后续

- 验证：V3 `validate-dataset` 成功（30/30 approved；`baseline_ready=false` 是配置样本数门槛）；针对 V3 生成、来源审计及评测合同的测试 `23 passed`；`python -m pytest tests -q` 为 `729 passed, 2 skipped, 128 subtests passed`；新增代码的 Ruff 检查和 `compileall` 成功；重复运行 V3 构建返回 `verified_identical`，不会覆盖已冻结的不同内容。四份固定 Trace 指标报告均由现有确定性评测命令成功输出。
- 验证限制：从项目根执行 `python -m pytest -q` 时，在收集 `evals/tests/test_judges.py` 阶段因本地虚拟环境未安装可选依赖 `deepeval` 而中止；不是本轮代码的断言失败。未为此安装新依赖或调用付费 Judge。Generation 阶段也无延迟样本，不能验收其 P95/P99。
- V3 已作为**冻结数据版本**生成并可复现；18/18 可回答案例的 relevant Parent 均有逐字 supporting span。V2 完整保留。
- **检索质量尚未验收**：当前 V3 的 Recall@40、Rerank Recall@12 和 Final Context 指标仍低；这轮仅解决标签可靠性并重算分数。下一阶段应固定 V3，按 case 分解 Top40 → Top12 → Final Context 的真实断点，再进行 Retrieval/Rerank 局部调优与新一轮同配置实跑。先不要把 V3 回放差值当成调优收益。
- 外部网页复核的材料尚未被编入冻结语料；未来若要让线上检索直接使用这些材料，需单独进行来源入库、质量审核、版本发布与索引更新，不能直接修改现有 release。

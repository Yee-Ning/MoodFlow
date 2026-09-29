# Jev Router Single Intent criteria ablation V2

## 冻结口径

Gold V2 在调用 Jev 前冻结并提交：`datasets/router_eval_gold_v2.jsonl` SHA256 `fb4961666dd15c693a45ea16e4ac7f5d3de404e77dcea72cc61c26c948f416a6`。Contract v1.0 SHA256 `238d373ff43db0eea72dae17525525276a666c3819f959de8925206b0915a97c`。正式 Single 集合 **46** 条；11 条 Multi、2 条 follow-up、4 条 negative/unsupported 均未送入本次 Choice 计分。46 条中，30 条是历史 Single 的可配对 cohort，14 条是新增成对边界样本，2 条由历史 Multi 裁决为 Single。

A 使用第一阶段生产 Router 的四句原始 criteria；B 使用同一 Contract 经 `capability_criteria.py` 投影的 criteria。两组使用同一个 `Choice` question instructions、同一个 `JevClient` / TypeSafe SDK、`jev-latest` alias、5 秒超时、零重试、同一 query 截断与同一 Gold。按 case 顺序交替 AB/BA。实际响应模型均为 `jev-1.13.0`。0.8 仅用于现有低置信度统计；未调阈值、未改变 Shadow Mode、未调用生产 Subgraph。每一条的已校验结构化输出、置信度、概率、usage、延迟和失败类型保存在 `jev_router_single_ablation_v2_results.jsonl`；SDK wire payload 不可用。

## Gold correction effect：固定预测，只换标签

第一阶段原始 31 条 Single 中有 1 条被移出计分，因此在**相同 30 条、相同第一阶段 Jev 输出**上重算：旧标签 **23/30 = 76.67%**，Gold V2 标签 **23/30 = 76.67%**。8 条标签改变，其中 4 条由错变对、4 条由对变错，净准确率 **0 个百分点**。第一阶段公布的 **24/31 = 77.42%** 含被移出的样本；它与 46 条新正式集不能直接相减。

在本轮 A 的同一 30 条预测上重算，也得到旧标签 **21/30 = 70.00%**、Gold V2 **21/30 = 70.00%**。该轮 A 有两次 API 超时，且模型输出本身会波动，因此 21/30 与第一阶段 23/30 的差不是 Gold 修正收益。此配对 cohort 的宏 F1 从 0.7643 变为 0.6539，但裁决后该 cohort 的 Worldline support 为 0；四类宏平均包含零支持类的 F1=0，不能把这项差值解释为性能退化。

## Criteria alignment effect：固定 Gold V2，只换 criteria

| 指标，46 条 Single | A 旧 criteria | B Contract V2 |
| --- | ---: | ---: |
| Accuracy | 33/46 = 71.74% | **46/46 = 100.00%** |
| Macro F1 | 77.08% | **100.00%** |
| GeneralChat ↔ Worldline 错路由 | 11 | **0** |
| 低置信度 `<0.8` | 20 | **1** |
| parse/schema failure | 0 | 0 |
| API failure | 2（TypeSafe API timeout） | 0 |
| API latency P50/P95/P99，成功调用 ms | 552.41 / 890.54 / 1631.05 | 556.32 / 683.22 / 1038.60 |
| Wall latency P50/P95/P99，全调用 ms | 556.27 / 1849.20 / 5259.97 | 556.86 / 683.76 / 1039.18 |
| input / output tokens 总量 | 16,941 / 2,312 | 47,964 / 2,407 |

| Route | support | A Precision / Recall / F1 | B Precision / Recall / F1 |
| --- | ---: | ---: | ---: |
| CurrentSong | 6 | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 |
| ToolCalling | 10 | 1.000 / 0.800 / 0.889 | 1.000 / 1.000 / 1.000 |
| GeneralChat | 25 | 1.000 / 0.560 / 0.718 | 1.000 / 1.000 / 1.000 |
| Worldline | 5 | 0.313 / 1.000 / 0.476 | 1.000 / 1.000 / 1.000 |

混淆矩阵行是 Gold，列是预测；`none` 表示调用失败。A：

| Gold \ A | CurrentSong | ToolCalling | GeneralChat | Worldline | none |
| --- | ---: | ---: | ---: | ---: | ---: |
| CurrentSong | 6 | 0 | 0 | 0 | 0 |
| ToolCalling | 0 | 8 | 0 | 0 | 2 |
| GeneralChat | 0 | 0 | 14 | 11 | 0 |
| Worldline | 0 | 0 | 0 | 5 | 0 |

B：

| Gold \ B | CurrentSong | ToolCalling | GeneralChat | Worldline | none |
| --- | ---: | ---: | ---: | ---: | ---: |
| CurrentSong | 6 | 0 | 0 | 0 | 0 |
| ToolCalling | 0 | 10 | 0 | 0 | 0 |
| GeneralChat | 0 | 0 | 25 | 0 | 0 |
| Worldline | 0 | 0 | 0 | 5 | 0 |

## 错误分析与决策

1. **Gold V2 的旧 Choice 正式基线是 33/46 = 71.74%，Macro F1 77.08%。** 这是本轮冻结集上的 A；第一阶段 24/31 是不同分母、旧标签，单列保留。
2. **GeneralChat ↔ Worldline 明显改善：11 → 0。** A 把和声、概念专辑、现场/录音室版本区别、摇滚/流行区别及若干反事实开放讨论误判为 Worldline；B 将它们送往 GeneralChat，同时保留五条实体证据关系探索为 Worldline。11 条混淆修复不依赖两次 API 超时。
3. **CurrentSong 未损害：6/6 → 6/6。ToolCalling 8/10 → 10/10；A 的两条未命中都因单次 API 超时，不能归因为旧 criteria 语义。** 两组都保持 Shadow，未验证实际下游执行。
4. **Worldline precision 0.313 → 1.000，recall 1.000 → 1.000。** support 仅 5 条且均为新增边界对的实体关系样本，估计不稳定。
5. B 仍有一条低置信度：`manual-hard-021`（“如果这首歌没火,不是说音量大小,是说它的职业影响会怎样”），预测 GeneralChat 正确，confidence **0.64**。A 低置信度 20 条及逐条详情见 `jev_router_single_failure_cases_v2.jsonl`。低于当前生产 0.8 时仍会回退；100% Choice 提议准确率不等于正式接管准确率。
6. **Gold 修正收益与 criteria 收益已分离。** 对固定的旧预测换 Gold，净准确率变化 **0**；对固定 Gold V2 换 criteria，本轮 **+13/46 = +28.26 个百分点**，其中 11 条 G/W 错路由被修复，另 2 条是 A 组 API 超时。两次超时可能是瞬时网络波动，不能声称是文案带来的可靠性提升。

B 的 input token 用量约为 A 的 **2.83 倍**；现有 Contract 全字段投影使描述较长。46 条中 14 条边界样本与 Contract 正反例的措辞相近，Worldline 仅 5 条，且这里只进行一次模型调用。**Single Intent V2 在这份冻结集表现良好，但尚不能认定为跨语料稳定。暂不进入正式 Multi Intent Strategy V3 调优**；下一轮应先补独立盲测、更多真实 Worldline 实体及重复运行稳定性检查。当前阶段到此为止，不做 Top-K、Noul、阈值或生产接管。

## 验证记录

- `python -m pytest tests -q`：1232 passed、14 skipped、228 subtests passed；这是最终代码微调前的全量运行，微调只增强 Contract 缺失时的回退与结果哈希检查，随后相关回归 25 passed。
- `python -m pytest deploy/tests -q`：64 passed。
- 本次修改文件的 `ruff check` 与 `compileall`：通过。全量 `ruff check app evals tests`：5 处既存未改文件中的 unused import/variable，未在本任务中改动。
- `docker buildx build --check --build-context wheelhouse=./deployment-artifacts/wheelhouse -f deploy/ai.Dockerfile .` 和 `docker compose --env-file .env.example -f docker-compose.production.yml config --quiet`：通过。
- 使用本地 wheelhouse 构建 `moodflow/ai-service:jev-router-v2-validation`：成功；临时容器确认 Contract 存在并可投影四条 route。未部署服务，也未验证真实浏览器、音乐服务或 Worldline 在线链路的下游效果。

# WorldLine Gold Dataset V1 Promotion 实施报告

## 1. 实施范围

本阶段仅实现 Evaluation 范围内的 Gold Promotion。输入来自 Batch A 人工审核材料，输出为正式 Real Gold fixture 及其 source ledger。

未修改 WorldLine、Semantic Expansion、Genre Executor、EntityBridge、RAG、Knowledge Base、数据库结构或 Evaluation Runner 核心执行逻辑。

## 2. Promotion 结果

| 项目 | 结果 |
|---|---:|
| 已提升 Gold cases | 8 |
| Gold 使用的去重 sources | 7 |
| 保持 pending | 6 |
| 明确 rejected | 0 |

已提升：

- 周杰伦 → C-pop
- G.E.M. 邓紫棋 → C-pop
- 陈奕迅 → C-pop
- Kendrick Lamar → Hip-Hop
- Nas → Hip-Hop
- Beyoncé → R&B
- 宇多田光 → R&B
- Epik High → Hip-Hop

保持 pending，未进入 Gold：

- 蔡依林
- Nujabes
- MC HotDog
- Alicia Keys
- Common
- Dynamic Duo

## 3. 新增与修改文件

### 新增

- `ai-service/evals/worldline_gold_v1_promoter.py`
  - 加载 tracking、candidate records、source candidates 与 identity candidates。
  - 仅选择 `relation_status=pass`、`evidence_status=pass`、`identity_status=resolved` 的候选。
  - 验证 evidence span、content hash、source ledger 一致性和 catalog identity 一致性。
  - 先写临时文件并用正式 Gold loader 完整加载；全部通过后才替换正式输出。
- `ai-service/evals/tests/test_worldline_gold_v1_promoter.py`
  - 覆盖 promotion gate、pending 排除、unresolved identity 排除、缺失 evidence 和 source hash mismatch。
- `ai-service/evals/datasets/worldline_gold_v1.jsonl`
  - 8 条正式 Gold cases。
- `ai-service/evals/fixtures/worldline_gold_v1_sources.jsonl`
  - 7 条仅供 Gold 使用的 source ledger entries。

### 修改

- `ai-service/evals/worldline_gold_v1_models.py`
  - Gold review 增加 `reviewer_notes` 与 `promotion_time`。
  - approved case 强制要求 promotion timestamp。
  - loader 同时兼容内部字段 `semantic_relation` 与正式 JSON 字段 `relation`。
- `ai-service/evals/tests/worldline_gold_v1_fixtures.py`
  - 测试 fixture 补充 promotion review 字段。

### Evaluation 输出

- `ai-service/evals/reports/worldline_gold_v1_results.jsonl`
- `ai-service/evals/reports/worldline_gold_v1_metrics.json`
- `ai-service/evals/reports/worldline_gold_v1_failures.jsonl`
- `ai-service/evals/reports/worldline_gold_v1_report.md`

## 4. Gold Schema 与完整性验证

Promotion 执行了以下 fail-closed 校验：

1. tracking 的 candidate id 唯一。
2. 只有三个审核状态完全满足 gate 的记录可进入 Gold。
3. candidate record 必须通过现有 candidate schema 校验。
4. reviewer notes 中的 catalog reference 必须与 identity ledger 和 candidate record 一致。
5. accepted case 必须存在非空 evidence。
6. `exact_span` 必须与 `evidence_text[span_start:span_end]` 完全一致。
7. evidence content hash 必须重新计算一致。
8. case source 必须与 source candidate ledger 一致。
9. 输出后必须通过正式 `load_worldline_gold_dataset` 全量校验。
10. dataset 与 source ledger 只有同时验证成功后才会提交到目标路径。

验证结果：通过。

- Dataset SHA256: `24acff750f04b87f2f362d42ff13982ecd117d2a0299de93337645b641307b8d`
- Source ledger SHA256: `003b27417e16afc1186e81185d48803d2016b66abc5af25686c25b2233f49e6f`
- pending case 进入 Gold：0
- unresolved identity 进入 Gold：0
- missing evidence 进入 Gold：0

## 5. Source Ledger 结果

8 条 cases 使用 7 个唯一 source。G.E.M. 与陈奕迅共享同一个已审核来源，因此 source ledger 按 `source_id` 去重。重复引用只有在 title、publisher、URL、source type、language、evidence text 与 hash 一致时才允许合并。

每条 Gold evidence 都能定位到 ledger 中同一 `source_id`，且 fixture source id、exact span 和 hash 均通过 loader 校验。

## 6. Real Gold Evaluation

运行命令：

```bash
python run_worldline_eval.py --suite real-gold
```

结果：

| 项目 | 结果 |
|---|---:|
| Cases | 18 |
| Passed | 18 |
| Failed | 0 |
| Skipped | 0 |
| Errors | 0 |
| Execution success | 100% |

主要指标：

- Semantic relation precision: 100%（8/8）
- Semantic relation recall: 100%（8/8）
- Grounded relation precision: 100%（8/8）
- Evidence provenance validity: 100%（8/8）
- Knowledge → catalog resolution success: 100%（8/8）
- Catalog identity accuracy: 100%（8/8）
- Genre exploration success: 100%（2/2）
- Genre artist precision / recall: 100%（2/2）

Real Gold 使用独立的 `real_gold_fixture` dataset kind、输入文件和输出报告，不与 Synthetic Baseline 混合统计。

## 7. 测试与检查

- Promotion、loader、adapter、runner 和 candidate review 专项测试：21 passed。
- 排除外部 Judge 测试后的全部 Evaluation 测试：27 passed。
- Ruff 静态检查：通过。
- Gold loader 对正式输出复验：8 cases、8 approved、7 sources，通过。
- 全量 `evals/tests` 在收集既有 `test_judges.py` 时因环境未安装可选依赖 `deepeval` 而中止；本阶段未安装依赖，也未修改该测试。

## 8. 当前限制

- V1 只有 8 条 accepted positive cases，没有 approved negative Gold，因此 expected rejection、ambiguity detection 等负样本指标仍为 N/A。
- 当前可执行 genre 只产生 2 条 genre → artist 评测 case；样本量尚小。
- 本次运行未包含 PostgreSQL tier，因此 graph merge、session continuity 和 checkpoint recovery 指标为 N/A。
- 蔡依林、Nujabes 与 4 个 backup candidates 仍为 pending；promoter 不会自动提升它们。
- Gold sources 是评测专用冻结片段，不会写入正式 Knowledge Base。

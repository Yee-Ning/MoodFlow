# WorldLine Evaluation V0 实施报告

## 1. 实施结论

WorldLine 专项 Evaluation V0 已完成，当前覆盖：

- artist → genre Semantic Expansion。
- genre → artist Genre Executor。
- supported claim、EvidenceSource 和 Citation Grounding。
- knowledge artist → catalog artist EntityBridge。
- Graph Merge、Evidence Union 和失败原子性。
- Session create/explore/restore 连续性。
- 真实 PostgreSQL Checkpointer 跨进程恢复与恢复后继续探索。

本次只新增 Evaluation Dataset、runner、metrics、fixtures、报告和测试，没有修改 WorldLine Graph、Semantic Expansion、Genre Executor、Session、RAG Pipeline、Retrieval、Embedding、Reranker、数据库或关系类型。

## 2. Evaluation 架构

Evaluation 分为三层：

```text
C0 Component Replay
  ├─ Semantic Expansion
  ├─ Genre Executor
  ├─ EntityBridge
  ├─ Graph Merge
  └─ Grounding Validator

S1 Session Replay
  └─ create → server action → explore → merge → checkpoint load

P1 PostgreSQL Restart Replay
  └─ process A create → process B recover/explore → process C recover/verify
```

组件结果直接消费现有 `SemanticExpansionTrace`、`GenreExecutorTrace` 和 `CatalogResolutionResult`。Session 层比较 response 与 checkpoint 中的完整结构化 state；P1 使用现有生产 PostgreSQL Checkpointer 和三个独立进程。

## 3. Dataset

新增 `worldline_eval_v0.jsonl`，共 38 个 approved case：

| 类别 | 数量 |
|---|---:|
| Artist → Genre Semantic Expansion | 14 |
| Genre → Artist Exploration | 10 |
| Entity Resolution | 6 |
| Graph Merge | 3 |
| Citation Grounding | 1 |
| Checkpoint Recovery | 4 |

数据集覆盖至少 20 个独立 Artist identity，并包含：

- 正确 semantic relation。
- 无 evidence、exact span 缺失、低置信度、错误 predicate 和未注册 genre。
- catalog resolved、unresolved 和 ambiguous。
- duplicate relation 和 evidence union。
- disabled genre action。
- missing session 和 checkpoint write failure。
- catalog 与 genre 两条 PostgreSQL 跨进程恢复路径。

当前 V0 使用明确标记为 `synthetic_contract_fixture` 的合成契约 Gold。它适合验证代码行为、Evidence Gate 和失败原子性，不代表真实音乐事实已经完成独立人工审核。

## 4. Metrics

已实现的确定性指标包括：

- Semantic Relation Precision / Recall。
- Grounded Relation Precision。
- Accepted Relation Rate / Rejection Rate。
- Expected Rejection Accuracy。
- Evidence Provenance Validity。
- Citation Validity 与 Grounding Validator Accuracy。
- Genre Expansion Success、Artist Precision / Recall、Valid Artist Yield。
- Evidence Acceptance、Catalog Resolution Yield、Projection Yield。
- Action Gate Accuracy。
- Knowledge → Catalog Resolution Success、Catalog Accuracy、Ambiguity Detection。
- Graph Merge Success、Invalid Merge Rejection。
- Session Continuity、Checkpoint Recovery、Post-Recovery Exploration。
- Failure Atomicity。

每个比率均保留 numerator、denominator 和 value。分母为零时输出 `null/N/A`；skipped 与 execution error 不会从执行可靠性分母中静默移除。

## 5. 首版 Baseline

完整 C0 + S1 + P1 运行结果：

```text
38 cases
38 passed
0 failed
0 skipped
0 errors
```

主要结果：

| 指标 | 结果 |
|---|---:|
| Semantic Relation Precision | 100% (8/8) |
| Semantic Relation Recall | 100% (8/8) |
| Grounded Relation Precision | 100% (8/8) |
| Evidence Provenance Validity | 100% (8/8) |
| Expected Rejection Accuracy | 100% (6/6) |
| Citation Validity | 100% (2/2) |
| Genre Expansion Success | 100% (6/6) |
| Genre Artist Precision / Recall | 100% / 100% |
| Action Gate Accuracy | 100% (10/10) |
| Catalog Identity Accuracy | 100% (2/2) |
| Ambiguity Detection | 100% (1/1) |
| Graph Merge Success | 100% (2/2) |
| Invalid Merge Rejection | 100% (1/1) |
| Session Continuity | 100% (3/3) |
| PostgreSQL Checkpoint Recovery | 100% (2/2) |
| Post-Recovery Exploration | 100% (2/2) |
| Failure Atomicity | 100% (5/5) |

诊断型 yield：

- Semantic accepted relation rate：81.8% (9/11)。
- Semantic rejection rate：18.2% (2/11)。
- Genre evidence acceptance：100% (9/9)。
- Catalog resolution yield：66.7% (6/9)。
- Projection yield：66.7% (6/9)。

Yield 只用于建立基线。正确拒绝无法映射或证据不足的 Candidate 不应被视为质量问题。

## 6. 新增与修改文件

### Dataset、Schema 与配置

| 文件 | 内容 |
|---|---|
| `ai-service/evals/datasets/worldline_eval_v0.jsonl` | 38-case V0 Dataset |
| `ai-service/evals/config/worldline_eval_v0.json` | Tier、输出与 PostgreSQL 环境开关配置 |
| `ai-service/evals/worldline_eval_models.py` | Case、Result、Run、Dataset validation 和 SHA256 |

### Runner 与 Metrics

| 文件 | 内容 |
|---|---|
| `ai-service/evals/worldline_eval_fixtures.py` | 冻结合成 Semantic、Genre、Session fixtures |
| `ai-service/evals/worldline_eval_runner.py` | C0、S1、P1 编排、Oracle 和 failure audit |
| `ai-service/evals/worldline_eval_metrics.py` | 纯确定性 Metrics |
| `ai-service/evals/worldline_eval_report.py` | Markdown 报告生成 |
| `ai-service/run_worldline_eval.py` | 命令行入口 |
| `ai-service/evals/README.md` | 运行说明与数据边界 |

### 测试与报告

| 文件 | 内容 |
|---|---|
| `ai-service/evals/tests/test_worldline_eval_dataset.py` | Dataset schema、覆盖和 exact span 校验 |
| `ai-service/evals/tests/test_worldline_eval_metrics.py` | 指标公式、N/A 和 error 分母测试 |
| `ai-service/evals/tests/test_worldline_eval_runner.py` | 36-case 非 PostgreSQL完整回放测试 |
| `ai-service/evals/reports/worldline_eval_v0_results.jsonl` | 逐案例 typed result |
| `ai-service/evals/reports/worldline_eval_v0_metrics.json` | 机器可读 Metrics |
| `ai-service/evals/reports/worldline_eval_v0_failures.jsonl` | Failure audit；当前为空 |
| `ai-service/evals/reports/worldline_eval_v0_report.md` | 人类可读 Baseline |

## 7. 运行方式

快速运行 C0 + S1：

```powershell
cd ai-service
.\.venv\Scripts\python.exe run_worldline_eval.py
```

包含真实 PostgreSQL P1：

```powershell
$env:RUN_WORLDLINE_EVAL_POSTGRES='1'
.\.venv\Scripts\python.exe run_worldline_eval.py --include-postgres
```

P1 不会用 InMemorySaver 冒充生产持久化验收。未设置环境开关时，PostgreSQL case 会明确显示为 skipped，不会产生虚假的 recovery 指标。

## 8. 验证结果

| 检查 | 结果 |
|---|---|
| Evaluation Dataset / Metrics / Runner tests | 6 passed |
| Evaluation C0 + S1 baseline | 36/36 passed |
| Evaluation C0 + S1 + real PostgreSQL P1 | 38/38 passed |
| 全部 WorldLine tests | 106 passed, 3 skipped |
| AI service `tests/` 全量 | 943 passed, 14 skipped, 206 subtests passed |
| Ruff（全部新增 Python 文件） | passed |
| Compileall（evals 与入口脚本） | passed |

## 9. 当前限制

1. V0 是合成契约 Dataset，不能据此宣称现实音乐关系 Precision 已达到 100%。
2. Genre Executor 正向 Gold 只覆盖当前可执行 `genre:hip_hop`；disabled genre 只作为 Action Gate 负样例。
3. Citation Validity 当前基于两个 session narrative 和一个 validator 负样例，规模较小。
4. P1 使用冻结业务输入和真实 PostgreSQL Checkpointer，验证持久化，不评测线上 Music API 或检索波动。
5. Evaluation 不评测 RAG Top-K、RRF、Reranker、Embedding 或 HNSW。
6. 没有使用 LLM Judge；Narrative 的风格和可读性尚未进入 V0。
7. Runner 复用现有 PostgreSQL restart worker 模式，没有新增数据库表或评测数据库。

## 10. 下一阶段建议

下一步应优先建立一批经过来源核对和独立审核的真实音乐关系 Gold，并与当前合成 Contract Set 分开统计。建议从 20–50 个 Artist 开始，冻结 supported claim、parent/child、exact span 和 catalog identity，再执行同一套 deterministic Oracle。确认真实 Gold baseline 后，再考虑扩大 genre 覆盖或增加 Narrative 人工 rubric。

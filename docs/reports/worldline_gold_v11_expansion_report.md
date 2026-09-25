# WorldLine Gold Dataset V1.1 Expansion 实施报告

## Executive Summary

WorldLine Gold V1.1 在保留 V1 8 条真实关系的基础上新增 10 条审核边界 case，总规模为 18 条 Gold、13 个唯一 source。新增 case 覆盖 semantic rejection、无可用证据、album-level relation boundary、identity ambiguous/unresolved、disabled genre action、duplicate evidence 和 failure atomicity。

V1.1 被 adapter 展开为 28 个 Real Gold Evaluation case，结果为 **28 passed、0 failed、0 skipped、0 errors**。Expected Rejection、Ambiguity Detection、Unresolved Reason、Action Gate 和 Failure Atomicity 首次获得非零真实 Gold 分母，均达到 100%；false executable identity 为 0/2。

## 1. 新增 Case 数量与分类

### 1.1 Dataset 规模

| 项目 | V1 | V1.1 | 增量 |
|---|---:|---:|---:|
| Gold cases | 8 | 18 | +10 |
| 唯一 sources | 7 | 13 | +6 |
| Real Gold Evaluation cases | 18 | 28 | +10 |

V1 文件保持不变；V1.1 使用独立 dataset、source ledger、config 和输出报告。

### 1.2 新增审核边界

| Case | 分类 | 预期行为 |
|---|---|---|
| 蔡依林 → C-pop | `relation_not_explicit` | “華語樂壇”不足以直接推出 registry C-pop，禁止扩图。 |
| Nujabes → Jazz Hip-Hop | `no_direct_evidence` | 没有通过 Gold source-quality gate 的直接证据，禁止扩图；不表示现实关系为假。 |
| 周杰伦 → R&B | `wrong_relation_scope` | 来源只描述音乐接近 R&B 并强调 hybrid，不能直接推出 artist-level genre relation。 |
| MC HotDog → Hip-Hop | `duplicate_evidence_boundary` | 两个重复 supported claims 只能投影一个 relation/evidence source。 |
| MC HotDog《Wake Up》→ Hip-Hop | `album_genre_scope` | Album-level Chinese Hip-Hop 标签不能单独推出新的 artist-level genre edge。 |
| Dynamic Duo identity | `catalog_mapping_ambiguous` | 冻结搜索快照包含多个同名 catalog artist id，必须返回 ambiguous。 |
| Common identity | `catalog_mapping_missing` | 以本名查询的冻结候选集为空，必须返回 unresolved；不表示 provider 中不存在该艺人。 |
| 周杰伦 → C-pop action | `disabled_genre_action` | C-pop 已注册但不可执行，不生成 enabled action。 |
| Beyoncé → R&B action | `disabled_genre_action` | R&B 已注册但不可执行，不生成 enabled action。 |
| Source conflict merge | `failure_atomicity` | 同一 source id 的冲突 payload 必须拒绝，旧 graph/evidence snapshot 保持不变。 |

### 1.3 新增来源

新增 6 个冻结来源：

- Sony Music Taiwan 的蔡依林官方 label editorial。
- 项目已有 CORE / University of Malaya 周杰伦研究文献摘录。
- Apple Music 的 MC HotDog artist playlist 分类。
- Apple Music 的 MC HotDog《Wake Up》album genre 分类。
- NetEase Dynamic Duo 同名候选搜索快照。
- NetEase Common 本名查询空候选搜索快照。

所有 evidence 继续执行 exact span、source ledger 和 SHA-256 一致性校验。

## 2. Accepted / Rejected 分布

### 2.1 Gold case 结果

| Gold outcome | 数量 | 占比 |
|---|---:|---:|
| Accepted | 12 | 66.7% |
| Rejected | 6 | 33.3% |
| 合计 | 18 | 100% |

12 条 accepted 包含原 V1 的 8 条关系、MC HotDog duplicate-evidence 正样本，以及 3 条复用已审核关系的 action/atomicity 边界。6 条 rejected 包含 4 条 semantic relation rejection 和 2 条 non-executable identity case。

### 2.2 Semantic Evaluation 分布

| 项目 | 数量 |
|---|---:|
| Semantic cases | 13 |
| Expected accepted | 9 |
| Expected rejected | 4 |
| Expected Rejection Accuracy | 4/4（100%） |

4 条 semantic rejection 都在 Candidate 形成前被 evidence/predicate gate 过滤，因此没有错误 relation 进入 graph。相应地，trace 中的 Candidate/Validator `rejection_rate` 仍为 0/10：这表示没有 candidate 到达 validator 后再被拒绝，不代表负样本没有被正确拦截。

## 3. Ambiguous / Unresolved 分布

Identity Evaluation 共 10 条：原有 resolved 8 条，新增 ambiguous 1 条、unresolved 1 条。

| Identity status | 数量 | 结果 |
|---|---:|---:|
| Resolved | 8 | 8/8 正确 |
| Ambiguous | 1 | 1/1 正确 |
| Unresolved | 1 | 1/1 原因正确 |

Hard cases：

- **Dynamic Duo：**两个 exact-name NetEase artist id 进入同一候选集，EntityBridge 返回 `catalog_artist_ambiguous`，没有生成 executable reference。
- **Common：**冻结本名查询候选集为空，EntityBridge 返回 `catalog_artist_not_found`，没有生成 executable reference。
- **False executable identity：**0/2；ambiguous 和 unresolved 均未进入 executable graph。

## 4. 新增指标结果

| 指标 | Numerator | Denominator | 结果 |
|---|---:|---:|---:|
| Expected Rejection Accuracy | 4 | 4 | 100% |
| Ambiguity Detection Rate | 1 | 1 | 100% |
| Unresolved Reason Accuracy | 1 | 1 | 100% |
| False Executable Identity | 0 | 2 | 0% |
| Action Gate Accuracy | 4 | 4 | 100% |
| Failure Atomicity | 1 | 1 | 100% |

Action Gate 的 4 个分母包含 2 个原有 Hip-Hop positive case 和 2 个新增 disabled genre negative case。两个 disabled case 均未暴露 enabled action。

其他保持通过的指标：

- Semantic Relation Precision：9/9（100%）
- Semantic Relation Recall：9/9（100%）
- Grounded Relation Precision：9/9（100%）
- Evidence Provenance Validity：9/9（100%）
- Catalog Identity Accuracy：8/8（100%）
- Invalid Merge Rejection：1/1（100%）

## 5. Failure Audit

Real Gold V1.1 没有失败记录：

- Passed：28
- Failed：0
- Skipped：0
- Errors：0
- `worldline_gold_v11_failures.jsonl`：空文件

新增边界的实际结果：

- 4 条 semantic negative 均未生成 relation。
- Duplicate supported claim 只生成一个 MC HotDog → Hip-Hop relation。
- Dynamic Duo 返回 ambiguous；Common 返回 unresolved。
- C-pop 与 R&B action gate 均保持关闭。
- Source conflict 被拒绝，旧 snapshot 未变化。

## 6. Validation 与 Regression

### 6.1 Dataset validation

- Gold schema validation：通过。
- Dataset version consistency：18/18 为 `v1.1`。
- Case id uniqueness：通过。
- Source id / fixture source id uniqueness：通过。
- Evidence exact span validation：通过。
- Source content hash validation：通过。
- Case evidence 与 source ledger consistency：通过。
- Duplicate evaluation variant detection：通过。

输出摘要：

- Dataset SHA256：`05f7a75c8cdcec7b112867df181d32cc9043b2b243e0956846280233d4621e45`
- Source ledger SHA256：`317dbbe0fe6945a2ad85ec7b79db7fbe7fbe23965235940eecfe0470a62a7cd3`

### 6.2 执行结果

| 验证 | 结果 |
|---|---:|
| Real Gold V1.1 suite | 28 passed |
| Synthetic Baseline | 36 passed |
| Evaluation tests（不含外部 Judge） | 31 passed |
| WorldLine regression | 106 passed、3 skipped |
| Ruff | 通过 |

3 个 skipped 为既有 PostgreSQL 环境条件测试。本阶段没有修改或绕过这些测试，也没有安装新依赖。

## 7. 修改范围

### 新增

- `ai-service/evals/config/worldline_gold_v11.json`
- `ai-service/evals/datasets/worldline_gold_v11.jsonl`
- `ai-service/evals/fixtures/worldline_gold_v11_sources.jsonl`
- `ai-service/evals/worldline_gold_v11_builder.py`
- `ai-service/evals/tests/test_worldline_gold_v11.py`
- `ai-service/evals/reports/worldline_gold_v11_results.jsonl`
- `ai-service/evals/reports/worldline_gold_v11_metrics.json`
- `ai-service/evals/reports/worldline_gold_v11_failures.jsonl`
- `ai-service/evals/reports/worldline_gold_v11_report.md`

### Evaluation-only 修改

- Gold schema 增加 V1.1 dataset version 与受控 evaluation boundary spec。
- Gold loader 校验 dataset version 一致性，并允许同一真实 relation 的不同审核 evaluation variant。
- Gold adapter 将 V1.1 boundary 映射到已有 semantic、identity、genre action 和 graph merge executor。
- Real Gold runner 从 dataset 读取真实版本，默认使用 V1.1 config。
- Evaluation report 展示 Unresolved Reason 与 False Executable Identity 的 numerator/denominator。

未修改 WorldLine、Semantic Expansion、Genre Executor、EntityBridge、RAG、Retrieval、Embedding、Reranker、数据库、relation type 或 genre executable 配置。

## 8. 当前仍未覆盖能力

- Candidate 已形成后因 confidence、span 或 validator policy 被拒绝的真实 Gold case；当前 4 个 semantic negative 都在 Candidate 前被过滤。
- Narrative citation 与前端 citation 的端到端真实 Gold case。
- PostgreSQL restart 下的 Real Gold checkpoint recovery 与 failure atomicity。
- C-pop、R&B、Jazz Hip-Hop 的正向 genre executor；本阶段按约束保持 disabled。
- Influence、Era、Scene、similar artist 等关系。
- 同一 identity hard case 的多 provider 冲突。
- 更大规模、跨来源复核和双 reviewer adjudication。

## 9. V2.3C 准入判断

**结论：达到进入 V2.3C 受控设计/开发阶段的最低评测准入条件，但未达到扩大关系范围或生产开放的准入条件。**

支持进入受控下一阶段的证据：

- Required blind spots 均有至少一个真实 Gold 分母。
- Relation rejection、identity gate、disabled action 和 atomicity 全部通过。
- 新增 negative case 没有要求修改业务阈值或 validator 规则。
- Synthetic 和现有 WorldLine 回归保持通过。

进入生产开放前仍需：

1. 增加 candidate-level validator rejection 的真实 Gold case。
2. 为 ambiguous/unresolved 与 disabled action 各补充更多独立样本。
3. 运行 PostgreSQL Real Gold recovery tier。
4. 补齐 narrative/citation 端到端评测。

本阶段到此停止，不实施 V2.3C。

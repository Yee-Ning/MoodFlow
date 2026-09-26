# Semantic Relations Evaluation v1 — r5 人工审核数据准备

本轮新增独立 r5 数据版本与 [human_review_pack.md](../../semantic_relations_v1_review_r5/human_review_pack.md)，补充 3 条真实 Influence query-family confusable pending 候选。没有运行新正式 baseline 或进行算法修复。

| Case | 当前 evidence 支持 | 所询 Influence | 原始来源 |
| --- | --- | --- | --- |
| `real-influence-r5-oasis-blur-britpop-confusable` | Britpop 场景和奖项竞争 | Oasis → Blur / influenced | [BRIT Awards / BPI](https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/) |
| `real-influence-r5-soundgarden-nirvana-grunge-confusable` | Seattle grunge / Sub Pop 共同场景 | Soundgarden → Nirvana / shaped_style | [MoPOP](https://www.mopop.org/experience/puget-sound-stories) |
| `real-influence-r5-ten-nevermind-era-confusable` | 原专辑 1991 / 1990s 发行事实 | Ten → Nevermind / inspired | [Pearl Jam](https://pearljam.com/music/album/ten)、[Nirvana / UME](https://www.nirvana.com/releases-archive/nevermind/) |

全部 proposed label 为 negative，expected relation 为 `[]`，review 为 pending。Negative 限于所提供 evidence，不断言现实世界绝无该影响。审核未完成，未生成新人工 receipt。当前正式真实 Gold 仍为 22 条；r5 总计 53 条 case / 53 条 input / 52 个 source（新增 4 个 source），原 28 条 synthetic 与 4 条 REJECT 排除记录保留。

4 个 URL 均从原发布方公共 HTTP 响应核对并捕获片段。Ten 的网页正文动态渲染，采用官网 HTML 内 `script#data` 的原始专辑身份和发行字段。其非连续字段分开标识，Gold span 只取发行字段。保留 excerpt 的 Unicode offsets、source / chunk / claim IDs、source-content / exact-span / label / replay-input hashes 与 HTTP provenance 均可检查。完整 HTML 未归档；response hash 不等同于可离线重放的完整网页，网页发布日期与作者缺失时明确置空。

## 覆盖判断

[coverage_projection.json](../../semantic_relations_v1_review_r5/coverage_projection.json) 是静态 category 清点，读取现有 evaluator 的未改动要求，不执行 evaluator 或关系查询。

- 目前缺口仍为 Influence query-family 的人工审核真实 `confusable`；pending 不计入 approved。
- 3 条中至少 1 条获明确人工 APPROVE 并在后续独立版本登记 receipt、晋升后，即足以满足该 coverage 条件。全部批准后真实 Gold 为 25 条，其中 Influence 13 条。
- 当前 `baseline_ready=false`、`can_enter_targeted_repairs=false` 保持原状。覆盖投影不代表任何新的关系质量分数或运行结果，也不会消除 r4 已发现的 failure。
- 数据构造 blocker：无；仍需人工审核。若审核不通过，保留本版本并构造新候选，不能修改冻结标签。

## 完整性与检查

父 r4 核心数据为追加前缀，22 条 approved Gold、schema、原 26 份 receipts 与历史文件逐字节保留。r5 manifest 绑定父 manifest / 核心数据与 r4 baseline artifact manifest，并覆盖新来源、pending label、input 和审核材料。190 个旧冻结文件与 28 个运行时/evaluator 文件逐项 hash 复核未变；Git 索引还核对新 manifest 对应 blobs 的 SHA256，避免换行规范化破坏 lineage。

在 `ai-service` 运行：

```powershell
$env:OPENBLAS_NUM_THREADS='1'
.\.venv\Scripts\python.exe -m pytest evals/tests/test_semantic_relations_review.py evals/tests/test_semantic_relations_review_r2.py evals/tests/test_semantic_relations_review_r3.py evals/tests/test_semantic_relations_review_r4.py evals/tests/test_semantic_relations_review_r5.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_worldline_era_expansion.py tests/test_worldline_scene_expansion.py tests/test_worldline_influence_expansion.py tests/test_worldline_session.py -q
.\.venv\Scripts\python.exe -m ruff check evals/tests/test_semantic_relations_review_r5.py
.\.venv\Scripts\python.exe -m compileall -q evals/tests/test_semantic_relations_review_r5.py
```

审核数据测试 64 passed；Era / Scene / Influence / session 功能回归 74 passed；Ruff 与编译检查通过。测试覆盖 pending 不晋升、标签/input 绑定、来源/原文 offsets、历史 receipt 与父 lineage、篡改拒绝、未调用评分器和未输出新 baseline。只新增数据、文档与测试，没有应用构建范围；未连接生产数据或写入运行时索引。

机器检查记录见 [review_preparation_validation.json](review_preparation_validation.json)。Final Regression、Online E2E、部署及新 baseline 按本轮范围未运行，无环境原因导致的验证缺项。本轮工作在人工审核包准备阶段结束。

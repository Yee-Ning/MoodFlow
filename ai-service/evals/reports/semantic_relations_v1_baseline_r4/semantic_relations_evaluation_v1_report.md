# Semantic Relations Evaluation v1 — r4 baseline

本轮统一执行 Era + Scene + Influence，冻结输入共 50 条：22 条人工审核真实 Gold、28 条 synthetic contract regression。真实 case 通过 14/22，8 条失败（6 组不同证据，存在两组旧正例与新增 forward 配对重复）。正式质量数字已记录，但完整 baseline 门禁尚未通过：Influence 缺少以其自身为 query family 的真实 confusable 覆盖。**本轮没有优化算法或修改任何 Gold/input/source/旧报告。**

Run: `2026-09-26T06:41:29.208734+00:00`；执行代码 revision: `c25e0c84f386b683637748db179081e10d966abb`；dirty: `True`。运行时 Worldline 与 evaluator 代码来自该提交，未修改；dirty 来自已有无关工作区修改（包括旧 RAG 审核包、报告移动和未跟踪数据）。本报告和新增审计测试随后提交。

## 审计与评测边界

复用现有 `EraExpansionService`、`SceneExpansionService`、`InfluenceExpansionService` 的 extractor → validator → projector；使用 SearchPlan、MusicKnowledgeDocument、WorldLineSessionResult 及应用 InMemorySaver。单个 query 顺序经过三个模块，包含 graph-only edge 的输出检查。旧 worldline_eval/Gold v1/v1.1 的 artist→genre adapter 未用于构造标签。

评测从冻结文档边界开始，claim/实体映射是独立评测输入注释，Gold 只进入评分器。这里验证文档已给定时的表达支持、拒绝、证据链与冲突投影，**没有测量生产检索召回、在线 claim extraction、entity resolution 或线上 relation precision**。Giant Steps 的 annotation predicate 与 release 分支兼容问题也属于这个边界，不能直接外推到线上 released claim。

`semantic_relations_runner.py` 的 diagnose 分类沿用原 taxonomy；本轮未改变指标分母、阈值、coverage gate 或 runtime service。报告只补充对真实 trace 的人工可读诊断。自动 runner 原始报告保存在 `runner_report.md`，其通用“待审核”描述不代表 r4 仍有 pending；以此报告、metrics 与 receipt 为准。

## Gold 与 exact evidence freeze

Gold 位于 `../../semantic_relations_v1_review_r4/`，schema 位于同目录 `schema.json`；22 条 approved 都绑定 human-review-WT / 2026-09-26 的标签哈希 receipt。原 4 条 REJECT 排除，pending=0。正式 Gold 与生产索引分离；本轮没有读取或写入生产知识数据。

每条 source snapshot 含 URL、publisher、title、locator、capture metadata 与 SHA256；span 使用 snapshot Unicode 字符偏移。loader 检查 span 原文、input document 快照一致、receipt 对应完整标签。真实 source 是冻结原文短摘录而非生成文本；审核时已核对完整网页上下文，本轮不刷新网页或重新裁定冲突双方真假。

数据文件 SHA256：

| File | SHA256 |
|---|---|
| gold.jsonl | `69bdcf6471349fdc74c81426775fd220dcffe3f82278ae1aa99fccfc0da88c07` |
| inputs.jsonl | `43948da8e7259f48777bc92f80cbe96e30e47454be5cde51631ab1108dff7c6b` |
| sources.jsonl | `85a7e20d60fb03ef78a43197bc0060e1dd6537bf401db6581c2de0d0ed7af31e` |
| schema.json | `019033ce34e5ad97bb6c2f1703dd97f4b300ba871bda46d8ad23b0dfa66fb193` |

运行前后核对 177 个既有数据/审核/报告文件的原字节 SHA256，全部不变；清单在 `protected_artifacts.json`。所有 50 条完整 trace 在 `results.jsonl`；`failures.jsonl` 包括 8 个真实失败和 8 个 synthetic 失败；`real_failures.jsonl` 只包含真实 Gold 失败，`failure_diagnosis.json` 补充原因定位与排序。

## 覆盖与门禁

| Query family | Approved real | Positive | Negative | Weak | Negation | Conflict | Confusable | Isolation tag | Direction tag |
|---|---|---|---|---|---|---|---|---|---|
| era | 6 | 2 | 4 | 1 | 1 | 1 | 2 | 4 | 0 |
| scene | 6 | 2 | 4 | 1 | 1 | 1 | 2 | 4 | 0 |
| influence | 10 | 5 | 5 | 2 | 1 | 1 | 0 | 4 | 6 |

Influence 来源的 confusable case 实际 query 是 Era/Scene，因此按 query relation_type 计入 Era/Scene。Bowie/Eno 与 Baez/Dylan 是已审核 weak negative；当前冻结标签没有同时标为 confusable，不能在运行后追加标签使门禁通过。reverse controls 验证方向，也不能代替 Era/Scene 证据被误判为 Influence 的真实 query。这个缺口需要下一份独立 pending 样本、真实来源和人工审核，不能改 r4。

当前 machine gate：`baseline_ready=False`，`can_enter_targeted_repairs=False`。Blockers：

- influence: approved real coverage missing confusable
- reviewed real failures require diagnosis before Final Worldline Regression

## 指标 baseline（overall + relation_type）

单元格为 numerator/denominator（百分比）。overall 为 cohort 内 micro aggregation；0 分母为 N/A。真实 cohort 与 synthetic 分开，不能混合成一个可信总分。

### approved_real_baseline

| Metric | overall | Era | Scene | Influence |
|---|---|---|---|---|
| relation_precision | 1/1 (100.0%) | 1/1 (100.0%) | N/A (0/0) | N/A (0/0) |
| evidence_coverage | 1/12 (8.3%) | 1/3 (33.3%) | 0/3 (0.0%) | 0/6 (0.0%) |
| evidence_traceability | 1/1 (100.0%) | 1/1 (100.0%) | N/A (0/0) | N/A (0/0) |
| unsupported_hallucination_rate | 0/1 (0.0%) | 0/1 (0.0%) | N/A (0/0) | N/A (0/0) |
| influence_direction_accuracy | 0/5 (0.0%) | N/A (0/0) | N/A (0/0) | 0/5 (0.0%) |
| weak_evidence_rejection | 4/4 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 2/2 (100.0%) |
| negation_rejection | 3/3 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| conflict_handling | 0/3 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| query_isolation | 22/22 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 10/10 (100.0%) |
| checkpoint_continuity | 22/22 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 10/10 (100.0%) |

| Group | Cases | Exact passes |
|---|---|---|
| overall | 22 | 14 |
| era | 6 | 5 |
| scene | 6 | 4 |
| influence | 10 | 5 |

### synthetic_regression

| Metric | overall | Era | Scene | Influence |
|---|---|---|---|---|
| relation_precision | 10/12 (83.3%) | 3/4 (75.0%) | 3/4 (75.0%) | 4/4 (100.0%) |
| evidence_coverage | 13/19 (68.4%) | 4/6 (66.7%) | 4/6 (66.7%) | 5/7 (71.4%) |
| evidence_traceability | 15/15 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) |
| unsupported_hallucination_rate | 2/12 (16.7%) | 1/4 (25.0%) | 1/4 (25.0%) | 0/4 (0.0%) |
| influence_direction_accuracy | 4/6 (66.7%) | N/A (0/0) | N/A (0/0) | 4/6 (66.7%) |
| weak_evidence_rejection | 1/3 (33.3%) | 0/1 (0.0%) | 0/1 (0.0%) | 1/1 (100.0%) |
| negation_rejection | 3/3 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| conflict_handling | 3/3 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) | 1/1 (100.0%) |
| query_isolation | 28/28 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 10/10 (100.0%) |
| checkpoint_continuity | 28/28 (100.0%) | 9/9 (100.0%) | 9/9 (100.0%) | 10/10 (100.0%) |

| Group | Cases | Exact passes |
|---|---|---|
| overall | 28 | 20 |
| era | 9 | 6 |
| scene | 9 | 6 |
| influence | 10 | 8 |

pending_real_diagnostic 为 0 条；overall、Era、Scene、Influence 所有指标均为 null / N/A，机器结果保留该 cohort。

Precision 的分母是实际输出关系。真实 cohort 只输出 Love Me Do 的 1 条 Era 关系，因此 overall precision=100% / unsupported=0% **不说明关系能力成熟**；Scene 与 Influence 零输出，precision、traceability、unsupported 均为 N/A。真实正例仅 1/9 完整通过，负例 13/13 拒绝，说明本轮主要暴露 false negatives；Evidence Coverage=1/12，按 unique claim 计，Giant Steps 的双方 evidence 不因两个候选而重复计数。Direction Accuracy 的 5 个正向关系分母包含拒绝，不会被 abstention 抬高。

指标语义沿用既有定义：precision 同时要求端点/predicate、证据链、Gold fields 与 conflict flags；traceability 只测证据回溯，不等同于语义支持；weak/negation 只计对应 negative；conflict 要求双方 evidence、全部候选、flags 与不确定字段；isolation 检查输出 family；checkpoint 仅测下述内存合同。

## 特别验证

- Era/Scene/Influence query isolation：真实 22/22、synthetic 28/28 没有跨 family 输出；缺失 Influence confusable 的真实覆盖仍构成门禁，不因这个比例消失。
- Influence forward/reverse：两个真实 reverse case 都返回空；两个配对 forward 也返回空，均为 direction_missing。当前没有观察到 B→A 反向边；taxonomy 的 direction error 在这 5 个失败中表示正向未识别，不是实际反向生成。
- Weak/negation：真实 weak 4/4、negation 3/3 拒绝。但 Era/Scene negation 的 rejection 是普通 relation/scene_not_in_evidence_span；只有 Influence Migung 命中 negated_influence。因此通过的是拒绝合同，并非证明系统理解了每个否定作用域。
- Conflicts：真实 0/3。三组都是双方候选被拒绝、最终零关系；没有观察到静默合并成单一事实，但要求的冲突语义同样未保留。Synthetic conflict 3/3 不能代替真实样本。
- Unsupported：真实无新增 unsupported relation，只有 1 条输出；synthetic Era/Scene weak 两个 case 接受了 unsupported relation，应保留回归信号，不能合并为真实失败统计。
- Checkpoint：真实 22/22、synthetic 28/28 的 restore/query replay/continuation 三项一致；后端是 application_in_memory_saver，production_restart_verified=false。它存储 DTO 后重编 reader、重复冻结 query 及状态 turn，未在生产 WorldLineSession 多轮或 PostgreSQL 进程重启中验证这 22 条真实关系。

## 真实 failure 明细

所有失败的原始查询、已检索 document、claim、candidate、validator decision、final relation、完整 expected Gold 与 taxonomy 均保存在 JSONL/diagnosis。下表 final 都是空列表；8 个 case 对应 6 组不同证据，Pixies 两组旧 positive 与新增 forward 有相关性。

| Case | Family | Reasons | Runtime rejection |
|---|---|---|---|
| real-scene-1-positive | scene | evidence mismatch | scene_not_in_evidence_span |
| real-influence-2-positive | influence | direction error | direction_missing |
| real-influence-3-positive | influence | direction error | direction_missing |
| real-era-r3-giant-steps-release-conflict | era | conflict failure, evidence mismatch | relation_not_in_evidence_span |
| real-scene-r3-blur-britpop-conflict | scene | conflict failure, evidence mismatch | scene_not_in_evidence_span |
| real-influence-r3-stockhausen-cluster-conflict | influence | conflict failure, direction error | direction_missing |
| real-influence-r3-pixies-nirvana-forward | influence | direction error | direction_missing |
| real-influence-r3-pixies-cobain-forward | influence | direction error | direction_missing |

### real-scene-1-positive

Query: Neko Case 的音乐场景归属

原文使用 Case、Vancouver 与 the city's indie scene；validator 要求 canonical entity 与完整 scene_name 同时在 exact span。证据已检索，候选已提取，失败位于 scene 语义验证。

Trace: query → 冻结文档（以下来源） → 1 个 candidate → validator rejected → final=[] → 下列 expected Gold → evidence mismatch。

Source: [Puget Sound Stories - Neko Case](https://www.mopop.org/experience/puget-sound-stories) — Museum of Pop Culture；document `real-scene-1-positive`；claim `claim:real-scene-1-positive`。
Exact source span: `source:mopop-neko-case` [0, 80)；SHA256 `2a9b3671bc0d5460052ab38160d6674328cb5bf37c18bfa2c060ec06089b49d9`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-scene-1-positive"], "fields": {}, "predicate": "belongs_to_scene", "source_entity": "eval:artist:neko_case", "target_entity": "scene:vancouver_indie_scene"}]`。

### real-influence-2-positive

Query: The Pixies 对 Nirvana 的音乐影响

机构页面以 Nirvana 为中心，Influences 下列出 The Pixies；方向正则要求双实体和句式 cue，不能从列表结构恢复归因。

Trace: query → 冻结文档（以下来源） → 1 个 candidate → validator rejected → final=[] → 下列 expected Gold → direction error。

Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame；document `real-influence-2-positive`；claim `claim:real-influence-2-positive`。
Exact source span: `source:rockhall-nirvana-influences` [0, 111)；SHA256 `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-influence-2-positive"], "fields": {}, "predicate": "influenced", "source_entity": "eval:artist:pixies", "target_entity": "eval:artist:nirvana"}]`。

### real-influence-3-positive

Query: Pixies 对 Kurt Cobain 的创作启发

采访的第一人称 I 和 trying to rip off 表达需要说话人归属与语义归因；span 中没有 Kurt Cobain 的完整名称，direction_missing 导致拒绝。

Trace: query → 冻结文档（以下来源） → 1 个 candidate → validator rejected → final=[] → 下列 expected Gold → direction error。

Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke；document `real-influence-3-positive`；claim `claim:real-influence-3-positive`。
Exact source span: `source:rollingstone-cobain-pixies` [0, 74)；SHA256 `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-influence-3-positive"], "fields": {}, "predicate": "inspired", "source_entity": "eval:artist:pixies", "target_entity": "eval:artist:kurt_cobain"}]`。

### real-era-r3-giant-steps-release-conflict

Query: Giant Steps 原专辑首次发行的年代归属（1950s / 1960s）有什么来源冲突？保留双方，不作确定裁决。

两条 retrieved span 都明确给出发行年份，候选 claim_predicate 却是 belongs_to_era。Era validator 的 release 分支要求 released；当前分支要求 1950s/1960s 完整名称，双方被 relation_not_in_evidence_span 拒绝，未到 conflict aggregation。冻结 input 保持原样；这是 annotation 与 runtime 接口兼容性诊断，不能据此认定实际线上 released claim 同样失败。

Trace: query → 冻结文档（以下来源） → 2 个 candidate → validator rejected → final=[] → 下列 expected Gold → conflict failure, evidence mismatch。

Source: [Coltrane: 1960-1964 Mono (Rhino High Fidelity)](https://media.rhino.com/press-release/coltrane-1960-1964-mono-rhino-high-fidelity) — Rhino Media；document `real-era-r3-giant-steps-release-conflict-1960`；claim `claim:real-era-r3-giant-steps-release-conflict-1960`。
Exact source span: `source:r3:rhino-giant-steps-1960` [0, 70)；SHA256 `cc38602829665b8f4d57e0d9edef02e1cd7bd925921336a6d152deec660c798c`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Source: [Cycles And Symmetric Division Of The Octave In The Works Of John Coltrane](https://static1.squarespace.com/static/5b4756089772aefb8c13401c/t/5c36518e0e2e72da3b5fbdd6/1547063701367/Jeff%2BPifher%2BAcademic%2BSeries-Coltrane%2BCycles.pdf) — Jeff Pifher (authored educational essay)；document `real-era-r3-giant-steps-release-conflict-1959`；claim `claim:real-era-r3-giant-steps-release-conflict-1959`。
Exact source span: `source:r3:pifher-giant-steps-1959` [0, 50)；SHA256 `7501aa06ba0cd982888371456c616c614dc4c1206bb11518e35b455368e5c2d2`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Expected: `[{"conflict_flags": ["temporal_conflict"], "evidence_claim_ids": ["claim:real-era-r3-giant-steps-release-conflict-1960", "claim:real-era-r3-giant-steps-release-conflict-1959"], "fields": {"end_year": null, "start_year": null, "temporal_precision": "unknown"}, "predicate": "belongs_to_era", "source_entity": "eval:album:giant_steps", "target_entity": "era:1960s"}, {"conflict_flags": ["temporal_conflict"], "evidence_claim_ids": ["claim:real-era-r3-giant-steps-release-conflict-1960", "claim:real-era-r3-giant-steps-release-conflict-1959"], "fields": {"end_year": null, "start_year": null, "temporal_precision": "unknown"}, "predicate": "belongs_to_era", "source_entity": "eval:album:giant_steps", "target_entity": "era:1950s"}]`。

### real-scene-r3-blur-britpop-conflict

Query: Blur 的历史 Britpop 场景归属是否存在外部归类与成员自述的分歧？保留双方，不作确定裁决。

外部归类的 Britpop 与规范名称 Britpop movement 不完全相同；成员自述使用 We/that 并否定归属。双方均 scene_not_in_evidence_span，未保留 attribution_conflict。源码审计另显示 Scene aggregation 目前只产出 temporal/location conflict，不处理 attribution stance；该潜在后续障碍未通过放宽验证器做反事实实验。

Trace: query → 冻结文档（以下来源） → 2 个 candidate → validator rejected → final=[] → 下列 expected Gold → conflict failure, evidence mismatch。

Source: [Blur at the BRITs: 1995 - 2012](https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/) — The BRIT Awards；document `real-scene-r3-blur-britpop-conflict-external`；claim `claim:real-scene-r3-blur-britpop-conflict-external`。
Exact source span: `source:r3:brits-blur-membership` [0, 106)；SHA256 `b6b853efe0dddb40c89ea9782d79cebfea99e278b64ad1dd9135c2a8301210d8`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Source: [Blur: ‘We’re not AC/DC. We’ve never been basic. We play too many chords to be basic’](https://english.elpais.com/culture/2023-07-21/blur-were-not-acdc-weve-never-been-basic-we-play-too-many-chords-to-be-basic.html?outputType=amp) — EL PAÍS / Álex Vicente；document `real-scene-r3-blur-britpop-conflict-self`；claim `claim:real-scene-r3-blur-britpop-conflict-self`。
Exact source span: `source:r3:elpais-blur-denial` [0, 54)；SHA256 `257f424837d1b688167b6a5ca344cc55c5655b079dbe919739f0b9b29a9b562b`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Expected: `[{"conflict_flags": ["attribution_conflict"], "evidence_claim_ids": ["claim:real-scene-r3-blur-britpop-conflict-external", "claim:real-scene-r3-blur-britpop-conflict-self"], "fields": {}, "predicate": "belongs_to_scene", "source_entity": "eval:artist:blur", "target_entity": "scene:britpop_movement"}]`。

### real-influence-r3-stockhausen-cluster-conflict

Query: Karlheinz Stockhausen → Cluster 的早期音乐 influenced 归因是否有争议？保留作者判断与成员否定。

Guardian 的完整文档主语与采访问题/说话人上下文不在短 exact span 内；Karlheinz Stockhausen 的完整名称及双实体方向句式也不满足。双方 direction_missing，未到 attribution conflict aggregation。

Trace: query → 冻结文档（以下来源） → 2 个 candidate → validator rejected → final=[] → 下列 expected Gold → conflict failure, direction error。

Source: [An awfully big adventure](https://www.theguardian.com/music/2005/apr/08/popandrock) — The Guardian / Dorian Lynskey；document `real-influence-r3-stockhausen-cluster-conflict-report`；claim `claim:real-influence-r3-stockhausen-cluster-conflict-report`。
Exact source span: `source:r3:guardian-cluster-stockhausen` [0, 130)；SHA256 `a798a652865f566e6cd6ebfe22b54fb892e49d79c29c34f63d5b943819128261`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Source: [Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010](https://www.indie-eye.it/recensore/generi/experimental/cluster-lintervista-di-indie-eye-it.html) — Indie-eye / Federico Fragasso and Michele Baldini；document `real-influence-r3-stockhausen-cluster-conflict-denial`；claim `claim:real-influence-r3-stockhausen-cluster-conflict-denial`。
Exact source span: `source:r3:indieeye-cluster-stockhausen-denial` [0, 85)；SHA256 `9268f84e46dbb53d3e88cebfcc472609eb7eb7bb80cfea5967e57fdcce9f80a4`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Expected: `[{"conflict_flags": ["attribution_conflict"], "evidence_claim_ids": ["claim:real-influence-r3-stockhausen-cluster-conflict-report", "claim:real-influence-r3-stockhausen-cluster-conflict-denial"], "fields": {}, "predicate": "influenced", "source_entity": "eval:artist:karlheinz_stockhausen", "target_entity": "eval:artist:cluster"}]`。

### real-influence-r3-pixies-nirvana-forward

Query: Pixies → Nirvana 的 influenced 音乐关系是否被所给证据支持？只返回该实体对。

与已审核旧 Nirvana positive 使用同一冻结来源与 claim；重现列表上下文归因失败，不是独立新事实。

Trace: query → 冻结文档（以下来源） → 1 个 candidate → validator rejected → final=[] → 下列 expected Gold → direction error。

Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame；document `real-influence-2-positive`；claim `claim:real-influence-2-positive`。
Exact source span: `source:rockhall-nirvana-influences` [0, 111)；SHA256 `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-influence-2-positive"], "fields": {}, "predicate": "influenced", "source_entity": "eval:artist:pixies", "target_entity": "eval:artist:nirvana"}]`。

### real-influence-r3-pixies-cobain-forward

Query: Pixies → Kurt Cobain 的 inspired 音乐关系是否被所给证据支持？只返回该实体对。

与已审核旧 Cobain positive 使用同一冻结来源与 claim；重现第一人称采访归因失败，不是独立新事实。

Trace: query → 冻结文档（以下来源） → 1 个 candidate → validator rejected → final=[] → 下列 expected Gold → direction error。

Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke；document `real-influence-3-positive`；claim `claim:real-influence-3-positive`。
Exact source span: `source:rollingstone-cobain-pixies` [0, 74)；SHA256 `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`。
完整 exact_span 与 locator/metadata 见 Gold/source snapshot 和 failure_diagnosis.json；未替换成模型改写文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-influence-3-positive"], "fields": {}, "predicate": "inspired", "source_entity": "eval:artist:pixies", "target_entity": "eval:artist:kurt_cobain"}]`。

## 下一轮：按影响排序的真实 failure categories

仅列真实已审核且实际出现的类别，类别计数可重叠；未观察到的 retrieval failure、unsupported relation、negation failure、weak evidence accepted、cross-relation contamination、checkpoint continuity failure 不伪造为真实错误。Synthetic 的检索缺失/坏 span 等有故意注入故障，20/28 exact passes 是既有诊断结果，不等同于 8 项自动化测试失败。

| Priority | Real category | Cases | Suggested fix point |
|---|---|---|---|
| P0 | conflict failure | 3 | 保留双方证据和 attribution/temporal conflict；先解决验证阶段漏检，再验证跨年代候选、不确定字段与冲突传递。 |
| P1 | direction error | 5 | 补足列表中心实体、采访说话人和跨句上下文的通用方向归因；保留严格的反向与 negation 控制。 |
| P1 | evidence mismatch | 3 | 对齐 released/normalized era claim 合同，支持场景别名与指代的可追溯验证；不得替换 Gold span 为生成文本。 |

优先级依据语义损失影响：先恢复冲突/不确定性，再处理定向归因和证据表达兼容。P1 的 5 个 direction case 中包含两组相关重复；不将其当作 5 个独立音乐事实。所有修复建议都是下一轮待实施点，本轮没有放宽 validator 或针对 case ID 添加规则。

## 自动化验证与限制

完整命令、退出状态及结果见 `validation.json`。现有 Worldline 与审核/评测测试：266 passed、3 skipped（PostgreSQL restart opt-in 未启用）。新增 baseline 审计测试 7 项通过，验证 cohort 分离、指标分母、Gold spans、完整 trace、冻结 hash、failure 分类、报告数字以及 coverage gate；总计 273 passed、3 skipped。Ruff/compileall 检查新增测试，任务范围的 git diff --check 验证改动。全仓库 diff 检查发现用户已有 gold_review_v0_blind_pack.md 的 trailing whitespace，本轮保留该无关修改，未将它纳入提交。

环境只读检查 docker ps 无运行中容器；本轮未启动数据库/服务，未启用 RUN_WORLDLINE_POSTGRES_RESTART。因此跨进程 PostgreSQL persistence 未验证；内存 checkpoint 的 100% 不覆盖这个范围。线上 Qdrant、真实音乐 API/LLM、检索/claim extraction、完整产品流程均不在本次离线验证内；没有改前端/API，未重跑无关前端构建。样本规模小、同源配对相关，不能声称生产统计代表性。

## 最终判断

1. 各项 baseline：上表给出全部 10 项 overall 与 Era/Scene/Influence 分组值。真实核心结果为 Evidence Coverage 1/12、Direction Accuracy 0/5、Conflict Handling 0/3；Relation Precision 1/1 仅来自一条 Era 输出。
2. 存在 blocker：Influence query-family confusable 真实覆盖缺口、8 条已确认真实失败，以及 Final Worldline Regression 前仍需完成的在线链路/生产 persistence 验证。本轮 evaluation 执行完成，数据/hash/receipt 有效；质量门禁未通过，不是 harness crash。
3. 当前完整门禁不允许宣布进入正式“针对真实失败点修复”（machine can_enter_targeted_repairs=false）。可以开展基于以上 trace 的修复设计；实施前先用独立新版本补齐 Influence confusable pending→人工审核，保持 r4 冻结。不能通过追加 category 或改评分标准清除门禁。
4. Final Worldline Regression 前必须解决：真实冲突双方 evidence/flags/null 语义保留；正向 Influence 的上下文归因并保持 reverse/negation 拒绝；Scene 指代/别名和 Era release claim 合同兼容；补齐 Influence 真实混淆覆盖；用真实 retrieval/ContextPack trace 验证输入链路，并完成生产 persistence/restart 的关系连续性验证。当前不进入 Final Regression 或部署。

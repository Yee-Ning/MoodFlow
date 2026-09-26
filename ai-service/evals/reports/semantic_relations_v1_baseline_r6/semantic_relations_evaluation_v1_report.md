# Semantic Relations Evaluation v1 — r6 baseline

统一执行 Era + Scene + Influence，冻结输入共 53 条：25 条人工审核真实 Gold、28 条 synthetic regression，pending=0。真实 case 通过 17/25，8 条失败（6 组不同证据）。新增三条 Influence confusable 均通过拒绝合同，真实类别覆盖完整。

正式 machine gate：**baseline_ready=true；can_enter_targeted_repairs=true**。这表示可信 baseline 的人工覆盖门禁已满足，允许下一轮针对真实失败点修复；本轮没有实施修复。CLI 退出码为 2，原因是 8 条真实失败仍阻止 Final Worldline Regression，不能把这解释为 coverage 门禁未通过或 harness crash。

Run: `2026-09-26T08:12:18.869283+00:00`；执行代码 revision: `7a1169d4a7a34e46a4c6a8d5f7f69f6e795c214d`；dirty: `True`。运行时和 evaluator 代码未变；dirty 包括用户既有无关工作区修改和本轮新输出，本次仅提交 baseline 记录及输出审计测试。

## 冻结、血缘与评测边界

Gold / input / source / schema 位于 ../../semantic_relations_v1_review_r6/。25 条真实 Gold 均绑定明确人工 receipt；原 4 条 REJECT 排除。没有刷新网页、重裁冲突或改变标签/评分/validator/projector/关系算法。生产索引未读写。

复用既有 extractor → validator → projector、SearchPlan、MusicKnowledgeDocument、WorldLineSessionResult 与 application InMemorySaver。单个 query 经过三个关系模块，包含 graph-only edge 输出检查。输入是冻结文档和独立 claim/实体注释；Gold 只用于评分。这不测量生产 retrieval、在线 claim extraction、entity resolution 或线上 precision。

Exact span 回溯到保留 source excerpt 的 Unicode offsets、claim/chunk/source IDs、URL/publisher/locator 与 SHA256；完整 HTTP 页面未归档。source audit 的采集状态不代替 case 的人工 receipt。

运行前后 341 个旧数据/审核/报告文件及 28 个运行时/evaluator 文件的 SHA256 均不变，清单见 protected_artifacts.json。原 7 个历史报告的 CRLF / Git LF 差异继续记录；只将本轮新导出统一为 LF，JSON 数据和评分不变。原 runner 报告保留在 runner_report.md；其通用“待审核”描述不代表 r6 有 pending。

| File | SHA256 |
|---|---|
| gold.jsonl | `2b796b9023969dd6ea8e199002d63cedbb72c1b4a148d445ee3add84e21c144c` |
| inputs.jsonl | `9a63528cb305b6fed5e8dc44f290e0709899e261bab81239ba5a81c641fd0237` |
| schema.json | `019033ce34e5ad97bb6c2f1703dd97f4b300ba871bda46d8ad23b0dfa66fb193` |
| sources.jsonl | `ac4eca0d1db532c23916b337c5cc6622528d18635a0bcc5f97167718b8849f76` |

## 覆盖与 machine gate

| Query family | Approved real | Positive | Negative | Weak | Negation | Conflict | Confusable | Isolation | Direction |
|---|---|---|---|---|---|---|---|---|---|
| era | 6 | 2 | 4 | 1 | 1 | 1 | 2 | 4 | 0 |
| scene | 6 | 2 | 4 | 1 | 1 | 1 | 2 | 4 | 0 |
| influence | 13 | 5 | 8 | 2 | 1 | 1 | 3 | 7 | 6 |

r4 的 Influence confusable coverage 缺口已由独立审核后的三条 Gold 补齐，gate 从 false / false 变为 true / true。未修改 category、门禁或任何旧 freeze。metrics.json 保留 evaluator 原始 flags / blockers；machine_gate.json 提供相同结果及 CLI 状态说明。

剩余 blocker：reviewed real failures require diagnosis before Final Worldline Regression。针对真实失败点修复可进入下一轮；Final Regression / Online E2E / 部署尚未进入。

## 指标 baseline（overall + relation_type）

单元格为 numerator/denominator（百分比），0 分母为 N/A。真实、synthetic 与 pending cohorts 分开，保持原分母及评分规则。

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
| query_isolation | 25/25 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 13/13 (100.0%) |
| checkpoint_continuity | 25/25 (100.0%) | 6/6 (100.0%) | 6/6 (100.0%) | 13/13 (100.0%) |

| Group | Cases | Exact passes |
|---|---|---|
| overall | 25 | 17 |
| era | 6 | 5 |
| scene | 6 | 4 |
| influence | 13 | 8 |

### pending_real_diagnostic

| Metric | overall | Era | Scene | Influence |
|---|---|---|---|---|
| relation_precision | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| evidence_coverage | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| evidence_traceability | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| unsupported_hallucination_rate | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| influence_direction_accuracy | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| weak_evidence_rejection | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| negation_rejection | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| conflict_handling | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| query_isolation | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |
| checkpoint_continuity | N/A (0/0) | N/A (0/0) | N/A (0/0) | N/A (0/0) |

| Group | Cases | Exact passes |
|---|---|---|
| overall | 0 | 0 |
| era | 0 | 0 |
| scene | 0 | 0 |
| influence | 0 | 0 |

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

真实 Relation Precision=1/1、Unsupported=0/1 只来自 Love Me Do 的一条 Era 输出；Scene 与 Influence 零输出，这些模块的 precision/traceability/unsupported 为 N/A。真实正例仅 1/9 完整通过、负例 16/16 拒绝；高 precision 不代表关系能力成熟。核心漏检指标仍为 Evidence Coverage 1/12、Influence Direction Accuracy 0/5、Conflict Handling 0/3。

r4 的原 50 条逐行结果/trace 与本轮完全一致，真实失败 IDs 未变，synthetic 仍为 20/28。17/25 相比 r4 的 14/22 只是新增三个 negative 通过，不是算法提升。类别计数会重叠，同源 forward / positive 控制不当作独立随机音乐事实。

## 特别验证

- 新 Influence confusable 3/3 输出 []、无跨 family 关系：Oasis→Blur 与 Ten→Nevermind 因 direction_missing 拒绝；Soundgarden→Nirvana 因 direction_missing / style_or_sound_scope_missing 拒绝。这证明当前输入边界的拒绝行为，不证明已具备完整因果理解。
- Query isolation：真实 25/25、synthetic 28/28；Era / Scene 查询未返回异类，Influence 新增 Scene / Era 证据均未误推为 Influence。
- Influence reverse controls 两条拒绝，forward 配对也拒绝。Direction error 的五条真实失败都是正向方向未识别（direction_missing），没有输出 B→A 反向关系。拒绝计入 Direction Accuracy 分母，不用 abstention 抬高分数。
- Weak 4/4、negation 3/3 拒绝；Era / Scene 否定 case 部分因普通 span 检查拒绝，不能据此断言系统完整理解否定 scope。真实 Unsupported=0/1 的分母只有一个输出；synthetic 的两个 unsupported 输出单独保留，不冒充真实失败。
- 真实 Conflict 0/3：双方候选全部被拒绝，最终为空，没有观察到静默合并成确定事实，但也未保留要求的双方 evidence、flags、互斥候选或 null/unknown 字段。
- Checkpoint：真实 25/25、synthetic 28/28 的 restore/query replay/continuation 一致。backend=application_in_memory_saver；production_restart_verified=false。该指标不覆盖生产 WorldLineSession 多轮或 PostgreSQL 跨进程重启。

## 真实 failure 明细

完整 53 条 trace 在 results.jsonl；全部 16 个失败（真实 8、synthetic 8）在 failures.jsonl；real_failures.jsonl 只含人工审核真实失败。failure_diagnosis.json 绑定本轮 query → retrieved evidence → candidate → filtering/scoring → final → expected Gold → failure reason，并附 source metadata。

8 条真实失败对应 6 组证据。每条原 50-case trace 已逐项与 r4 比较一致，因此沿用已核验的解释并绑定当前原始 trace；解释不进入评分器。

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

Trace: query → 冻结文档 → 1 个候选 → validator rejected → final=[] → expected Gold → evidence mismatch。

Source: [Puget Sound Stories - Neko Case](https://www.mopop.org/experience/puget-sound-stories) — Museum of Pop Culture；locator: Neko Case paragraph; speaker/entity and city coreference require full page review
IDs: source:mopop-neko-case / real-scene-1-positive / chunk:real-scene-1-positive / claim:real-scene-1-positive
Exact span: [0, 80)；source SHA256: `2a9b3671bc0d5460052ab38160d6674328cb5bf37c18bfa2c060ec06089b49d9`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-scene-1-positive"], "fields": {}, "predicate": "belongs_to_scene", "source_entity": "eval:artist:neko_case", "target_entity": "scene:vancouver_indie_scene"}]`。

### real-influence-2-positive

Query: The Pixies 对 Nirvana 的音乐影响

机构页面以 Nirvana 为中心，Influences 下列出 The Pixies；方向正则要求双实体和句式 cue，不能从列表结构恢复归因。

Trace: query → 冻结文档 → 1 个候选 → validator rejected → final=[] → expected Gold → direction error。

Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame；locator: Influence > Influences list ending with central Nirvana node; distinguish following Influenced list
IDs: source:rockhall-nirvana-influences / real-influence-2-positive / chunk:real-influence-2-positive / claim:real-influence-2-positive
Exact span: [0, 111)；source SHA256: `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-influence-2-positive"], "fields": {}, "predicate": "influenced", "source_entity": "eval:artist:pixies", "target_entity": "eval:artist:nirvana"}]`。

### real-influence-3-positive

Query: Pixies 对 Kurt Cobain 的创作启发

采访的第一人称 I 和 trying to rip off 表达需要说话人归属与语义归因；span 中没有 Kurt Cobain 的完整名称，direction_missing 导致拒绝。

Trace: query → 冻结文档 → 1 个候选 → validator rejected → final=[] → expected Gold → direction error。

Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke；locator: Answer to But you must have had a good time writing it; referent is Smells Like Teen Spirit
IDs: source:rollingstone-cobain-pixies / real-influence-3-positive / chunk:real-influence-3-positive / claim:real-influence-3-positive
Exact span: [0, 74)；source SHA256: `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-influence-3-positive"], "fields": {}, "predicate": "inspired", "source_entity": "eval:artist:pixies", "target_entity": "eval:artist:kurt_cobain"}]`。

### real-era-r3-giant-steps-release-conflict

Query: Giant Steps 原专辑首次发行的年代归属（1950s / 1960s）有什么来源冲突？保留双方，不作确定裁决。

两条 retrieved span 都明确给出发行年份，候选 claim_predicate 却是 belongs_to_era。Era validator 的 release 分支要求 released；当前分支要求 1950s/1960s 完整名称，双方被 relation_not_in_evidence_span 拒绝，未到 conflict aggregation。冻结 input 保持原样；这是 annotation 与 runtime 接口兼容性诊断，不能据此认定实际线上 released claim 同样失败。

Trace: query → 冻结文档 → 2 个候选 → validator rejected → final=[] → expected Gold → conflict failure, evidence mismatch。

Source: [Coltrane: 1960-1964 Mono (Rhino High Fidelity)](https://media.rhino.com/press-release/coltrane-1960-1964-mono-rhino-high-fidelity) — Rhino Media；locator: Historical Giant Steps paragraph, immediately after description of first Atlantic sessions; NBSP after comma retained
IDs: source:r3:rhino-giant-steps-1960 / real-era-r3-giant-steps-release-conflict-1960 / chunk:real-era-r3-giant-steps-release-conflict-1960 / claim:real-era-r3-giant-steps-release-conflict-1960
Exact span: [0, 70)；source SHA256: `cc38602829665b8f4d57e0d9edef02e1cd7bd925921336a6d152deec660c798c`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Source: [Cycles And Symmetric Division Of The Octave In The Works Of John Coltrane](https://static1.squarespace.com/static/5b4756089772aefb8c13401c/t/5c36518e0e2e72da3b5fbdd6/1547063701367/Jeff%2BPifher%2BAcademic%2BSeries-Coltrane%2BCycles.pdf) — Jeff Pifher (authored educational essay)；locator: PDF page 7 (zero-based page 6); Countdown and Giant Steps section, opening sentence, before footnote 21
IDs: source:r3:pifher-giant-steps-1959 / real-era-r3-giant-steps-release-conflict-1959 / chunk:real-era-r3-giant-steps-release-conflict-1959 / claim:real-era-r3-giant-steps-release-conflict-1959
Exact span: [0, 50)；source SHA256: `7501aa06ba0cd982888371456c616c614dc4c1206bb11518e35b455368e5c2d2`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Expected: `[{"conflict_flags": ["temporal_conflict"], "evidence_claim_ids": ["claim:real-era-r3-giant-steps-release-conflict-1960", "claim:real-era-r3-giant-steps-release-conflict-1959"], "fields": {"end_year": null, "start_year": null, "temporal_precision": "unknown"}, "predicate": "belongs_to_era", "source_entity": "eval:album:giant_steps", "target_entity": "era:1960s"}, {"conflict_flags": ["temporal_conflict"], "evidence_claim_ids": ["claim:real-era-r3-giant-steps-release-conflict-1960", "claim:real-era-r3-giant-steps-release-conflict-1959"], "fields": {"end_year": null, "start_year": null, "temporal_precision": "unknown"}, "predicate": "belongs_to_era", "source_entity": "eval:album:giant_steps", "target_entity": "era:1950s"}]`。

### real-scene-r3-blur-britpop-conflict

Query: Blur 的历史 Britpop 场景归属是否存在外部归类与成员自述的分歧？保留双方，不作确定裁决。

外部归类的 Britpop 与规范名称 Britpop movement 不完全相同；成员自述使用 We/that 并否定归属。双方均 scene_not_in_evidence_span，未保留 attribution_conflict。源码审计另显示 Scene aggregation 目前只产出 temporal/location conflict，不处理 attribution stance；该潜在后续障碍未通过放宽验证器做反事实实验。

Trace: query → 冻结文档 → 2 个候选 → validator rejected → final=[] → expected Gold → conflict failure, evidence mismatch。

Source: [Blur at the BRITs: 1995 - 2012](https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/) — The BRIT Awards；locator: Paragraph on the 2012 Outstanding Contribution award; historical Britpop scene description
IDs: source:r3:brits-blur-membership / real-scene-r3-blur-britpop-conflict-external / chunk:real-scene-r3-blur-britpop-conflict-external / claim:real-scene-r3-blur-britpop-conflict-external
Exact span: [0, 106)；source SHA256: `b6b853efe0dddb40c89ea9782d79cebfea99e278b64ad1dd9135c2a8301210d8`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Source: [Blur: ‘We’re not AC/DC. We’ve never been basic. We play too many chords to be basic’](https://english.elpais.com/culture/2023-07-21/blur-were-not-acdc-weve-never-been-basic-we-play-too-many-chords-to-be-basic.html?outputType=amp) — EL PAÍS / Álex Vicente；locator: Interview paragraph about Blair, England and political appropriation; final two sentences of the answer following Coxon adds
IDs: source:r3:elpais-blur-denial / real-scene-r3-blur-britpop-conflict-self / chunk:real-scene-r3-blur-britpop-conflict-self / claim:real-scene-r3-blur-britpop-conflict-self
Exact span: [0, 54)；source SHA256: `257f424837d1b688167b6a5ca344cc55c5655b079dbe919739f0b9b29a9b562b`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Expected: `[{"conflict_flags": ["attribution_conflict"], "evidence_claim_ids": ["claim:real-scene-r3-blur-britpop-conflict-external", "claim:real-scene-r3-blur-britpop-conflict-self"], "fields": {}, "predicate": "belongs_to_scene", "source_entity": "eval:artist:blur", "target_entity": "scene:britpop_movement"}]`。

### real-influence-r3-stockhausen-cluster-conflict

Query: Karlheinz Stockhausen → Cluster 的早期音乐 influenced 归因是否有争议？保留作者判断与成员否定。

Guardian 的完整文档主语与采访问题/说话人上下文不在短 exact span 内；Karlheinz Stockhausen 的完整名称及双实体方向句式也不满足。双方 direction_missing，未到 attribution conflict aggregation。

Trace: query → 冻结文档 → 2 个候选 → validator rejected → final=[] → expected Gold → conflict failure, direction error。

Source: [An awfully big adventure](https://www.theguardian.com/music/2005/apr/08/popandrock) — The Guardian / Dorian Lynskey；locator: Historical Cluster paragraph starting Music critics have explained Krautrock; excerpt of the second sentence, whose grammatical subject is Cluster
IDs: source:r3:guardian-cluster-stockhausen / real-influence-r3-stockhausen-cluster-conflict-report / chunk:real-influence-r3-stockhausen-cluster-conflict-report / claim:real-influence-r3-stockhausen-cluster-conflict-report
Exact span: [0, 130)；source SHA256: `a798a652865f566e6cd6ebfe22b54fb892e49d79c29c34f63d5b943819128261`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Source: [Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010](https://www.indie-eye.it/recensore/generi/experimental/cluster-lintervista-di-indie-eye-it.html) — Indie-eye / Federico Fragasso and Michele Baldini；locator: Moebius answer immediately after question whether Stockhausen influenced them as he did other Krautrock artists
IDs: source:r3:indieeye-cluster-stockhausen-denial / real-influence-r3-stockhausen-cluster-conflict-denial / chunk:real-influence-r3-stockhausen-cluster-conflict-denial / claim:real-influence-r3-stockhausen-cluster-conflict-denial
Exact span: [0, 85)；source SHA256: `9268f84e46dbb53d3e88cebfcc472609eb7eb7bb80cfea5967e57fdcce9f80a4`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Expected: `[{"conflict_flags": ["attribution_conflict"], "evidence_claim_ids": ["claim:real-influence-r3-stockhausen-cluster-conflict-report", "claim:real-influence-r3-stockhausen-cluster-conflict-denial"], "fields": {}, "predicate": "influenced", "source_entity": "eval:artist:karlheinz_stockhausen", "target_entity": "eval:artist:cluster"}]`。

### real-influence-r3-pixies-nirvana-forward

Query: Pixies → Nirvana 的 influenced 音乐关系是否被所给证据支持？只返回该实体对。

与已审核旧 Nirvana positive 使用同一冻结来源与 claim；重现列表上下文归因失败，不是独立新事实。

Trace: query → 冻结文档 → 1 个候选 → validator rejected → final=[] → expected Gold → direction error。

Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame；locator: Influence > Influences list ending with central Nirvana node; distinguish following Influenced list
IDs: source:rockhall-nirvana-influences / real-influence-2-positive / chunk:real-influence-2-positive / claim:real-influence-2-positive
Exact span: [0, 111)；source SHA256: `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-influence-2-positive"], "fields": {}, "predicate": "influenced", "source_entity": "eval:artist:pixies", "target_entity": "eval:artist:nirvana"}]`。

### real-influence-r3-pixies-cobain-forward

Query: Pixies → Kurt Cobain 的 inspired 音乐关系是否被所给证据支持？只返回该实体对。

与已审核旧 Cobain positive 使用同一冻结来源与 claim；重现第一人称采访归因失败，不是独立新事实。

Trace: query → 冻结文档 → 1 个候选 → validator rejected → final=[] → expected Gold → direction error。

Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke；locator: Answer to But you must have had a good time writing it; referent is Smells Like Teen Spirit
IDs: source:rollingstone-cobain-pixies / real-influence-3-positive / chunk:real-influence-3-positive / claim:real-influence-3-positive
Exact span: [0, 74)；source SHA256: `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`。原文内容见机器 trace / source snapshot，未替换为生成文本。

Expected: `[{"conflict_flags": [], "evidence_claim_ids": ["claim:real-influence-3-positive"], "fields": {}, "predicate": "inspired", "source_entity": "eval:artist:pixies", "target_entity": "eval:artist:kurt_cobain"}]`。

## 下一轮：按影响排序的真实 failure categories

仅列真实已审核且本轮实际出现的类别，不将 synthetic 或未观察到的 retrieval/unsupported/weak/negation/contamination/checkpoint 错误加入真实优先级。

| Priority | Real category | Cases | Suggested fix point |
|---|---|---|---|
| P0 | conflict failure | 3 | 保留双方证据和 attribution/temporal conflict；先解决验证阶段漏检，再验证跨年代候选、不确定字段与冲突传递。 |
| P1 | direction error | 5 | 补足列表中心实体、采访说话人和跨句上下文的通用方向归因；保留严格的反向与 negation 控制。 |
| P1 | evidence mismatch | 3 | 对齐 released/normalized era claim 合同，支持场景别名与指代的可追溯验证；不得替换 Gold span 为生成文本。 |

这些是下一轮建议，本轮未放宽规则或加入 case-specific 分支。conflict failure 优先保留不确定语义，再解决方向上下文和 evidence 表达兼容。

## 验证与限制

执行命令、CLI 退出状态、导出审计测试、冻结/hash/receipt 验证与 Git blob 校验见 validation.json。没有应用代码变更或前端/API 构建范围。本次不连接线上检索、音乐 API/LLM 或生产数据库，不运行 Online E2E、生产 persistence/restart 或部署；这些是评测范围限制，不能把内存 checkpoint 的 100% 外推为生产持久化验收。

## 最终判断

1. 各项 baseline：以上完整 10 项指标均提供 overall 与 Era / Scene / Influence breakdown；核心真实结果为 Evidence Coverage 1/12、Direction Accuracy 0/5、Conflict Handling 0/3，Precision 1/1 仅有一条 Era 输出。
2. Coverage blocker 已消除；仍有 8 条已审核真实失败阻止 Final Worldline Regression。CLI=2 与 baseline_ready=true / can_enter_targeted_repairs=true 同时成立，反映原 evaluator 对 baseline 覆盖与 Final Regression 失败的不同判断。
3. 可以进入下一轮针对真实失败点修复，保持 r6 与所有旧 freeze 不变；本轮没有进行 targeted repair。
4. Final Worldline Regression 前必须解决：真实 conflict 双方 evidence/flags/候选/null 语义保留；正向 Influence 上下文归因并保持 reverse/negation 拒绝；Scene 指代/别名与 Era release claim 合同兼容；另需完成真实 retrieval/ContextPack 输入链路及生产 persistence/restart 的关系连续性验证。本轮不进入 Final Regression 或部署。

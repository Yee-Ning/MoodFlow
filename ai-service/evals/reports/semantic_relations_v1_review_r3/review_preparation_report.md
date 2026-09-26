# Semantic Relations Evaluation v1 — r3 pending coverage preparation

2026-09-26。本轮完成真实来源候选与人工审核包，不运行最终 Evaluation，不修改关系算法、指标、runner 默认路径或生产索引。

## 本次交付

新建独立数据版本 `evals/semantic_relations_v1_review_r3/`，复用现有 `SemanticGoldCase`、`ReplayInput`、`SourceSnapshot`、`load_suite` 与原 schema。旧 r2 数据逐行原样保留，只追加 10 条 pending 和 9 个真实来源短摘录。数据版本合计 50 cases / 50 replay inputs / 48 source snapshots。人工审核入口是该目录的 `human_review_pack.md`；机器可读候选、审核问题和来源上下文分别在 `pending_cases.jsonl`、`review_checklist.json`、`source_audit.json`。

| relation_type | 新 negation negative | 新 conflict proposal | 新 forward control | 新 reverse negative | 新 pending 合计 | 已有 approved real |
|---|---:|---:|---:|---:|---:|---:|
| Era | 1 | 1 | 0 | 0 | 2 | 4 |
| Scene | 1 | 1 | 0 | 0 | 2 | 4 |
| Influence | 1 | 1 | 2 | 2 | 6 | 4 |
| Overall | 3 | 3 | 2 | 2 | 10 | 12 |

已有人工 Gold 仍为 12 条，synthetic 仍为 28 条；原 4 条 REJECT 仍排除。新 10 条全部 `pending / pending_human`，没有 reviewer、reviewed_at、decision 或新 receipt。已审核来源不自动批准本轮新 query。

## 来源与标签边界

| 候选 | 所检验的真实证据 / 范围 | 需要人工关注 |
|---|---|---|
| `real-era-r3-let-it-be-negation` | Beatles 官方 George 原话，原专辑直到 1970 年 5 月才发行；所询 1960s 实体对为 negative | 专辑与歌曲、录制与正式发行分开；输入如实保留 1970/1970s，不能用 1969 录制代替发行 |
| `real-scene-r3-alice-punk-negation` | Steve Turner 原创访谈，否定 Alice in Chains 属于早期 Seattle punk scene | 参与者回忆、代词与上文场景范围；不否定 Seattle grunge 关系 |
| `real-influence-r3-paik-migung-negation` | Hwang Byungki 原创访谈明确否定 Nam June Paik 对 Migung 的影响 | 仅限该作品；保留 passive claim 方向；现有 song 类型承载音乐作品需要人工确认 |
| `real-era-r3-giant-steps-release-conflict` | Rhino 历史首发段落给出 1960，Jeff Pifher 教育文章明确写发行于 1959 | 弱来源可能混淆录制年，但原文确实断言 release；仍须核对地域/版本，不能靠投票确定历史事实 |
| `real-scene-r3-blur-britpop-conflict` | BRIT Awards 外部历史场景归类，与 EL PAÍS 成员回顾访谈拒绝 Britpop 身份 | 可能在拒绝政治品牌而非 scene membership；不同语义范围应 REJECT，不能强行确认为冲突 |
| `real-influence-r3-stockhausen-cluster-conflict` | Guardian 原创访谈报道作者的早期音乐借鉴归因，与 Indie-eye 原创采访成员的限定否定 | 作者解释与成员自述分开；意大利文、主语上下文、早期音乐范围均须审核，不移植 Kluster 的断言 |
| `real-influence-r3-pixies-nirvana-forward / reverse` | 复用 Rock Hall 的既有真实 Influence 列表；同一原始 claim 和 documents | 正向 Pixies → Nirvana；反向拒绝仅限给定 evidence，不断言现实中无相反影响 |
| `real-influence-r3-pixies-cobain-forward / reverse` | 复用 Cobain 访谈既有真实创作启发证据；同一原始 claim 和 documents | 正向 Pixies → Cobain；反向拒绝仅限给定 evidence，不混淆乐队与个人 |

conflict 的 proposed positive 表示需要保留争议，不表示已确认关系为事实。Era 使用两条带 `temporal_conflict` 的互斥年代候选，每条关联双方 claim，年份为 null、精度 unknown；这是需审核的表示方案，不能解释为两次确定首发。Scene / Influence 要求 `attribution_conflict=true` 且保留双方 evidence。Scene 目前没有专门处理归因冲突的既有逻辑；本轮保留评测要求，后续运行时再暴露实际失败，不修改算法或降低标签标准。

所有新 exact span 均来自打开核对的真实原文，保留原语言、标点及偏移；中文说明、翻译与来源上下文不作为 Gold evidence。source hash 与 offsets 验证本地摘录冻结，不能代替完整网页/PDF审核。Pifher 文档的 2011 来自版权标识，不能当作已验证的精确上线日期；无精确出版日期的来源不补造日期。来源 locator 指向具体段落或 PDF 页，审核包提供可点击 URL。

`research_exclusions.json` 记录未纳入的线索：Queen 成立与阵容定型、Migung 创作与首演、不同地区的发行日期，以及视觉启发与音乐影响差异，均不人为合成为 conflict。

## 冻结与输入隔离

`gold.jsonl`、`inputs.jsonl`、`sources.jsonl` 保留 r2 字节前缀；`schema.json`、`approved_gold.jsonl`、`excluded_cases.jsonl` 与 r2 完全一致。16 份历史 receipts 与 r1/r2 审核记录保留，旧数据与旧 failure 报告共 78 个文件的 hash 记录在 `protected_artifacts.json`。manifest 绑定父版本和所有新审核材料。

提交前额外核对 Git HEAD：旧 v1 的 7 份历史报告在当前 Windows checkout 为 CRLF、Git blob 为 LF，已确认除换行外完全相同，文件保持不动。保护清单记录两种已核实 byte hash，仅这 7 份历史报告允许对应 checkout 差异；Gold、input、source 与其它冻结文件仍要求精确字节一致。

Replay 不包含 Gold label、expected relation、review 或人工决定。否定段落中的 candidate annotation 表达被否定的命题，否定词仍在原文中；没有伪造来源支持性。反向 query 不翻转输入 claim，两条配对的 documents 完全相同。Graph/SearchPlan 仍使用既有离线身份接口。未运行线上 retrieval、自动抽取、LLM judge 或 checkpoint Evaluation，也未读写运行时索引/生产数据。

## 已运行验证

以下命令均在 `ai-service` 下执行。pytest 设置 `OPENBLAS_NUM_THREADS=1`。

```powershell
.\.venv\Scripts\python.exe -m pytest evals/tests/test_semantic_relations_review.py evals/tests/test_semantic_relations_review_r2.py evals/tests/test_semantic_relations_review_r3.py -q
# 38 passed in 7.28s
.\.venv\Scripts\python.exe -m pytest tests/test_worldline_era_expansion.py tests/test_worldline_scene_expansion.py tests/test_worldline_influence_expansion.py tests/test_worldline_session.py -q
# 74 passed in 10.40s
.\.venv\Scripts\python.exe -m ruff check evals/tests/test_semantic_relations_review_r3.py
# All checks passed
.\.venv\Scripts\python.exe -m compileall -q evals/tests/test_semantic_relations_review_r3.py
# exit 0
```

新增 16 项数据检查覆盖 pending 门禁、按类型覆盖、旧字节/receipt/排除记录保护、来源元数据与 exact span、审核包 label hash、冲突双方/旗标/空年份、正反方向配对、passive 方向、Gold/input 隔离，以及 rehash 后仍拒绝伪造 span、input content、source hash 和无 receipt 晋升。通过 `load_suite` 校验本批完整性时禁止导入 runner / metrics，不生成关系分数。

共 112 项测试通过。没有运行会执行 `run_suite` 的 `test_semantic_relations_evaluation.py`，也没有执行最终评测命令；这是本轮用户明确约束，不是环境阻塞。没有前端或生产代码改动，因此不跑前端构建或部署。完整项目中无关服务/集成路径未重新验证，本轮验证范围为数据完整性与直接相关 Worldline 既有功能。环境未阻止上述验证。

## 本轮结论与下一步

1. **指标 baseline：本轮未测，不能报告新分数。** formal Gold 的数量仍为 Era/Scene/Influence 各 4；negation、conflict 和反向负例尚在 pending，不能计入 approved baseline。旧诊断结果保持原样。
2. **当前门槛：10 条待人工审核；没有测试或环境 blocker。** 三个冲突还需确认同一范围与表示方式；若不成立须 REJECT 并在后继版本另建候选，不能改本轮冻结标签。
3. **下一步是审核本包并登记结果，尚不能据此进入真实失败修复。** 收到明确结论后，新版本绑定原 label hash 创建 receipt，随后再按用户指令统一 Evaluation；先诊断再修复，不以候选或源码推测作为已测 failure categories。
4. **Final Worldline Regression 前须完成：** 真正审核通过的三类否定/冲突及反向方向覆盖、冲突语义的人工确认、统一 harness 的 overall + relation_type baseline 与 trace，以及完整 checkpoint/persistence 语义连续性评测。两个正反配对及旧正例有相关性，不能充当独立随机覆盖或总体质量置信区间。

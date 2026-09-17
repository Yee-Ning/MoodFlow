# Gold V4 candidate 审计报告

- 冻结 Gold V3 SHA256：`5d2888542042af16bf38d3ae63f45c956c8e37f658397e2f5d3a78c3759236e9`；未写入 V3。
- 冻结来源：`knowledge-v2-8608ffc2fa72e441`；Parent artifact `sha256:8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。
- 案例：30；outcome 分布：{'answerable': 15, 'insufficient_evidence': 12, 'needs_input': 3}；变更案例：27。
- 已替换/移除接近 350 字的 span：29；V4 span 总数：70。
- forbidden_claims 新增条数：8。
- `partially_answerable` 是局部事实可证但原问题不能完整回答；为保持既有评测 outcome 合同，expected_outcome 仍为 insufficient_evidence。
- 026 有两组逐字重复的 Parent ID；evidence_groups 仅记录可接受的替代 ID，不按 Trace 倒推标签。旧确定性评测器仍按 relevant_parent_ids 逐 ID 计分，需连同 group-aware 诊断解释结果。

## 优先案例

- 024：问题按作词人举例回答；冻结曲目表证明除方文山外还有周杰伦、黄俊郎，不能声称完整名单。
- 038：阶段论 Parent 把《七里香》及《听妈妈的话》的年表混写，不能作为可靠风格变化证据；仅保留发行事实。
- 036：2005 Parent 只证明发行日期；无足够证据描述 2000—2005 的完整风格演变，改为部分可证。
- 040：逐个年份核对时间锚点；移除不支持时间线的 DEMO 与重复电影 Parent，新增明确发行证据。
- 034：和声研究使用 may be identical，保留可能性，缩短截断段落。
- 042：网站导航与托管地址不能作为英文资料内容或作者归属的证据；仅保留英文正文。
- 044：将“不要现场版”明确写为禁止输出约束，使用非现场版新闻证据。
- 026：两组曲目/奖项 Parent 分别存在逐字相同的另一 ID；登记 duplicate group，避免将替代 ID 当检索错误。

## 所有 insufficient_evidence 复核

- 005：partially_answerable；冻结语料只能确认《晴天》是周杰伦挑选公开展示的DEMO之一，并说明其早期DEMO通常完成度较高、保留创作时最初的画面感与哼唱旋律；但语料没有提供足够的《晴天》专属创作背景，因此不应进一步编造具体创作缘起。
- 008：partially_answerable；冻结语料可以说明《东风破》的作品信息和风格背景：它由周杰伦演唱、方文山作词，收录于《叶惠美》，使用二胡、琵琶等民族乐器，被视为中国风代表作之一，歌名还与古代“曲破”形式有关。但这些证据没有直接交代这首歌的具体创作动机、灵感来源或创作过程，因此严格按“创作背景”口径应判为证据不足。
- 009：insufficient_evidence；现有冻结语料能说明《双截棍》的创作灵感与风格特征，例如周杰伦提到许多快歌受到动作片启发，方文山也把《双截棍》作为画面感和独特写法的例子；但这些材料不足以回答“《双截棍》产生了什么影响”，因此该问题应判为证据不足。
- 025：insufficient_evidence；当前冻结语料没有提供足够的直接证据说明周杰伦与蔡依林具体合作过哪些作品或项目。候选材料中的同榜单出现、媒体提及或方文山分别为两人写词，都不能作为二人实际合作的证明，因此应判为证据不足。
- 031：partially_answerable；现有资料能描述周杰伦与中国风音乐的关系，也提到林俊杰等音乐人参与中国风创作，但没有提供足够的林俊杰独立音乐风格证据。因此无法在冻结语料范围内可靠比较两人的音乐风格。
- 032：insufficient_evidence；冻结语料能确认《晴天》和《夜曲》的专辑/曲目信息，但没有给出足够的曲风、编曲、节奏或情绪特征描述，因此不能据此可靠回答两首歌的风格差异。
- 033：insufficient_evidence；现有材料主要支持《叶惠美》和《七里香》的发行及销量/参考信息，没有足够的制作风格、编曲方向或制作团队差异证据，因此无法可靠比较两张专辑的制作风格。
- 035：insufficient_evidence；当前冻结语料没有足够的、可对齐的王力宏与周杰伦音乐风格描述，因此无法可靠概括二人的风格异同。奖项同榜或共同出现在参考列表中不能替代风格证据。
- 036：partially_answerable；冻结语料可证实2000年《Jay》的多元融合、2003年《东风破》的中国风乐器使用，以及《十一月的萧邦》在2005年的发行；这些节点不足以回答2000到2005年整体音乐风格如何演变。
- 038：partially_answerable；冻结语料可确认《叶惠美》在2003年7月31日发行；用于描述其前后风格变化的阶段论材料含明显年表混淆，不能据此可靠概括风格演变。
- 039：partially_answerable；冻结语料仅能提供部分节点：同一来源记载2011年 DejaVu 餐厅、2012年西安 KTV 等商业活动；这些记录不能完整回答2008至2012年有哪些变化，2010年榜单条目还带有需更好来源的提示。
- 045：partially_answerable；现有语料可以提供《晴天》的榜单反响、DEMO和MV相关资料，但没有足够的专业或明确“乐评”文本来评价其旋律、编曲、演唱或审美风格。因此即使排除翻唱版，仍应判为证据不足。

## 仍需人工复核

- 003：中国风兴起与广泛文化影响的概括仍依赖跨来源语义判断。
- 034：和声研究的 may be identical 仅是可能性，比较结论需人工复核。
- 036：冻结语料没有足以概括 2005 风格演变的直接材料。
- 038：剔除错误年表 Parent 后，缺少可靠的《叶惠美》前后风格转变资料。
- 039：商业时间线主要由同一 Wikipedia 来源提供，来源可靠性无法仅凭冻结语料确认。
- 042：英文资料的作者/机构归属不能凭冻结 Parent 正文确认。

## 校验与限制

- 校验 Parent 存在、来源 ID 一致、span 原文定位、answerable 有证据、partial 与 outcome 对齐、group ID/Parent 有效且 duplicate 文本完全相同、没有 350 字机械截断或图片/导航片段。
- 自动校验不对来源事实真伪和跨句语义作最终判定；需人工复核事项见 manual_review JSONL。
- 指标回放需使用同一历史 Trace 和现有 evaluator；本构建脚本不读取 Trace，也不改检索链路。
- Warning: Semantic alignment and source reliability require human review for the listed manual cases.

## 同 Trace 确定性指标回放

| 历史 Trace / Gold | 可完整回答例数 | Recall@40 | Rerank Recall@12 | Final Context Precision |
| --- | ---: | ---: | ---: | ---: |
| Fallback V3 | 18 | 66.73% | 41.67% | 18.06% |
| Fallback V4 | 15 | 68.86% | 39.17% | 13.33% |
| Nav/Context6 V3 | 18 | 69.51% | 41.67% | 17.78% |
| Nav/Context6 V4 | 15 | 68.86% | 39.17% | 15.78% |

- 两次回放分别使用同一份历史 Trace；只换 Gold。Fallback Trace SHA256：`7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e`；Nav/Context6 Trace SHA256：`9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967`。
- 同一 evaluator 重新计算 V3 与 V4；V3 数值与原历史报告相同。V4 可完整回答案例从 18 降为 15，分母及 relevant Parent 集合已变；指标变化只能解释为 Gold 修订。
- 旧 evaluator 按 Parent ID 计分，尚不消费 V4 evidence_groups。026 的 duplicate group 是审计解释依据，不能将仅命中替代 ID 自动判为检索错误；表中仍报告旧 evaluator 的原始定义。

## 变更规模与文件

- 实质变更案例：27 条，ID：001, 002, 003, 004, 005, 006, 007, 008, 009, 023, 024, 025, 026, 031, 032, 033, 034, 035, 036, 037, 038, 039, 040, 042, 043, 044, 045。046—048 仅随新 schema 读入默认空字段，没有改变标签。
- 旧 span 总数 68；V4 span 总数 70；替换或移除旧 span 40 条；新增 V4 span 42 条。29 条原先接近 350 字的 span 均已替换或移除。
- forbidden_claims 净新增 8 条。
- 全面枚举 V4 relevant Parent 后仅发现 026 的两组逐字重复 Parent；没有把内容不同但仅主题相近的 Parent 自动归为 equivalent。026 曲目表与奖项表分别支持合作曲目和提名，是互补证据。
- Gold V4 candidate：`evals/datasets/rag_eval_gold_v4.candidate.jsonl`；变更记录：`evals/reports/gold_v4_changes.jsonl`；人工复核：`evals/reports/gold_v4_manual_review.jsonl`。
- 同 Trace 评测报告：`gold_v4_same_trace_v3_fallback_20260917.json`、`gold_v4_fixed_replay_fallback_20260917.json`、`gold_v4_same_trace_v3_nav_20260917.json`、`gold_v4_fixed_replay_nav_20260917.json`。
- V4 仍为 candidate：6 个案例需人工语义/来源复核；当前 30 例少于配置要求的 50 例，baseline_ready=false。

## 验证

- `python run_rag_gold_v4_audit.py --validate-only`：30/30 验证通过，V3 与 Parent SHA256 匹配。
- `python run_rag_eval.py validate-dataset --dataset evals/datasets/rag_eval_gold_v4.candidate.jsonl`：30/30 approved，baseline_ready=false。
- `python -m pytest tests/test_rag_gold_v4_audit.py tests/test_rag_evaluation_service.py -q`：16 passed。
- `python -m pytest tests -q`：734 passed，2 skipped，128 subtests passed。
- `python -m ruff check app/rag/evaluation/models.py run_rag_gold_v4_audit.py tests/test_rag_gold_v4_audit.py`：通过。相关 Python 文件 compileall 通过。
- 两份历史 Trace 的 V3/V4 deterministic evaluate 均成功。未运行任何在线检索、精排或生成。
- 全仓 `git diff --check` 命中原有未提交文件 `gold_review_v0_blind_pack.md` 的尾随空格；本次未修改该文件，提交前按本次文件范围检查。
- `.\.venv\Scripts\python.exe -m pytest -q` 在收集可选 `evals/tests/test_judges.py` 时因主环境缺少 `deepeval` 中止；并非断言失败。
- 改用现成评测环境运行 `.\.venv-eval\Scripts\python.exe -m pytest evals/tests/test_judges.py -q`：4 passed。

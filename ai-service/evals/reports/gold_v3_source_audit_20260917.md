# Gold V2 可回答案例冻结来源审计与 V3 修订候选（2026-09-17）

## 审计边界与结论

- 基线：`evals/datasets/rag_eval_gold_v2.ai_assisted_source_review.jsonl`，共 30 例，其中 `answerable` 18 例。此文件**未修改**；本报告和 `gold_v3_source_audit_candidates.jsonl` 都不是可直接用于评测的 Gold V3。
- 只读来源：`data/knowledge/v2/knowledge_release_manifest.json` 指定的 `knowledge-v2-8608ffc2fa72e441`，Parent artifact SHA256 为 `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`。加载时校验整个 artifact 哈希，再按 Parent 粒度核对 `parent_id`、`source_id` 与逐字 span。未使用在线 Retrieval/Rerank 的 rank、score、Final Context 或旧 Silver Top3 决定修订。
- 现有 18/18 可回答案例的 relevant Parent 均存在于冻结来源，全部现有 supporting span 均为相应 Parent 的原文，`source_id` 也匹配。只有 040 存在 1 个已标相关 Parent 没有 supporting span。**逐字匹配仅证明数据指向正确，不证明原文事实真实或足以支持每条 required fact。**
- 本轮对 6 例给出来源可追溯的候选修订：002、006、026、040、042、043；其中新增 10 段 span（涉及 5 个此前未标的 Parent）、修订 3 例的 required facts / reference answer。036、039 保留待核实，不做假定性修正。其余 10 例初步未发现本轮检查范围内的明确矛盾，不等于已完成外部事实核查或穷尽所有替代证据。
- 本轮没有重跑评测，也没有新的 Recall / Precision 数字；若将来冻结 V3，必须在同一固定 Trace 上重算基线，再评价 Retrieval/Rerank，不能把标签改变造成的数值变化称为模型提升。

## 18 例逐项审计

| Case | 本轮来源检查结果 | 处置 |
| --- | --- | --- |
| 001 | 相关 Parent / span 指向及原文匹配；未见明确标签矛盾 | 暂保留 V2 |
| 002 | 英文研究文本 `jc-v1-025` 另有两段关于多元融合、中国风与流行节奏结合的证据；两段属同一来源，且不能替代“音乐空间与想象力”的采访证据 | 候选补两个 Parent / span |
| 003 | 相关 Parent / span 指向及原文匹配；未见明确标签矛盾 | 暂保留 V2 |
| 004 | 相关 Parent / span 指向及原文匹配；未见明确标签矛盾 | 暂保留 V2 |
| 006 | `parent-d3a5bad1fa461976a1129b06` (`jc-v1-007`) 已在 Gold，但现有 span 未覆盖《七里香》MV 日本乡间取景的 required fact | 候选补同 Parent 逐字 span |
| 007 | 相关 Parent / span 指向及原文匹配；未见明确标签矛盾 | 暂保留 V2 |
| 023 | 相关 Parent / span 指向及原文匹配；未见明确标签矛盾 | 暂保留 V2 |
| 024 | 相关 Parent / span 指向及原文匹配；未见明确标签矛盾 | 暂保留 V2 |
| 026 | Gold 的“只确认一个合作点”与同一冻结来源的四首歌曲编曲署名冲突；奖项表只能另证《蓝色风暴》提名 | 候选改事实和答案，补曲目表 Parent / span |
| 034 | 相关 Parent / span 指向及原文匹配；未见本轮新增明确标签矛盾；比较类事实仍需按组合证据理解 | 暂保留 V2 |
| 036 | 四个现标 Parent 未直接证明“到 2005 仍延续个人化、中国风与多元表达”；搜索到的 2005 专辑发行文字只证明时间点，不能推出风格 | **待核实**，不自动改写该事实 |
| 037 | 相关 Parent / span 指向及原文匹配；未见明确标签矛盾 | 暂保留 V2 |
| 038 | 相关 Parent / span 指向及原文匹配；未见明确标签矛盾 | 暂保留 V2 |
| 039 | 2009–2012 荣誉、餐厅及 KTV 事实目前依赖 `jc-v1-030` 同一 Wikipedia 来源的两个 Parent；其中若干荣誉旁带 `[需要較佳来源]` | **待核实**，不当作双来源确认 |
| 040 | 现标 `parent-531672f53462b6956869e0eb` (`jc-v2-007`) 无 span；其“这一年”不能单独锚定 2007。另有明确写出 2007 年电影上映的 Parent，以及英文论文附录中的年份 / 片名 | 候选补 2019 span 和两处 2007 电影 Parent / span；导演身份仍仅有单一来源 |
| 042 | `jc-v1-025` 的英文 PDF 托管于 University of Puget Sound 网站，但封面及元数据不足以证明该大学是论文作者或授位机构 | 候选校正来源归属措辞 |
| 043 | `parent-396d8237183dea741ebce052` (`jc-v2-007`) 是人物文章转述 CNN 采访，非冻结语料中的 CNN 原始采访稿 | 候选校正来源层级措辞 |
| 044 | 相关 Parent / span 指向及原文匹配；未见明确标签矛盾 | 暂保留 V2 |

## 关键证据、修订边界

1. **026：署名 ≠ 提名，也不是两个独立来源。** `jc-v1-036` 的 `parent-cfb9f049a0cfdf86866257fd` 曲目表逐行署名洪敬堯为《藍色風暴》《四面楚歌》《浪漫手機》《麥芽糖》编曲；`parent-a734651048b7159210d5c4a6` 奖项表记载《藍色風暴》的最佳编曲人提名。原问题为“周杰伦和洪敬尧的编曲合作”，故四首署名是更完整的可核实例子。两 Parent 属同一 `source_id`，不得宣称独立交叉验证，也不得据此推断全部合作目录。
2. **040：时间锚点必须在证据内。** `jc-v2-007` 的 `parent-531672f53462b6956869e0eb` 明确写“2019年9月16日”与《说好不哭》，可补其缺失 span；该 Parent 另一个“这一年”片段不能单独证明 2007。`jc-v1-030` 的 `parent-a83673d2b967ec913e6b0ef3` 逐字写“2007年”电影《不能說的·秘密》上映，英文文本 `jc-v1-025` 的 `parent-20baa3294417e60fb93bf1df` 附录独立列出该影片及 2007 年份，与原 Gold 的 2007 专辑年表组合支撑时间线中的跨界叙述；但“自编自导自演”目前仍只见 Wikipedia Parent，不能说这一身份已独立核实。
3. **002、006：补的是事实覆盖，不是按检索名次补标。** 002 两个新 Parent 均来自同一英文学术文本，分别描述多风格融合和中国古典元素与 R&B / hip hop 的混合；其余 Gold 事实不因此被覆盖。006 的 MV 原文补在已标 Parent，不增加相关 Parent 数。
4. **042、043：来源归属要降到语料实际证明的级别。** 网站托管不等于机构署名，媒体转述不等于原始采访。候选仅修正 Gold 文案，不擅自变更 outcome。
5. **036、039：不能以“可在来源中找到字句”代替事实确认。** 036 的 2005 风格演变结论需要直接、时间明确的来源支撑，或收窄为可证实的时间线描述；039 的争议荣誉与商业事实需要更可靠的原始 / 独立来源。冻结语料内本轮没有足够依据将它们定为已解决；不要通过在线命中候选反向选择新 Gold。

## 可执行后续

- `gold_v3_source_audit_candidates.jsonl` 每行均带 `case_id`、`review_status=candidate_not_finalized`、新增 Parent / 来源 / 精确原文，以及需要替换的事实和答案字段；null 表示维持 V2 原字段。该文件是审核提案，不是新评测集。
- 要冻结 V3，先解决 036 的 2005 结论及 039 的来源质量：有可追溯的合格原始材料则补证据；若没有，则收窄 required facts / reference answer 或按评测集政策剔除不可靠断言。再逐条核对候选与完整 Parent 上下文，生成独立版本的 Gold，保留 V2 不变，并对生成版运行 schema、精确 span、来源一致性及固定 Trace 回放。
- 本轮证据质量结论只覆盖这 18 个 answerable case 的冻结语料审计，未评估 12 个非 answerable case 的 outcome 正确性，也未做外部网页事实核验、在线链路调参或 Judge 调用。

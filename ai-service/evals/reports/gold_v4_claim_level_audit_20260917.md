# Gold V4 claim-level 标注审计

证据仅来自冻结 Parent release `knowledge-v2-8608ffc2fa72e441`（Parent artifact SHA-256 `8d4e0b3bb3ea8ea8a456fcfbb97b348d0e4af1252d06b5923cbabc5864289c5a`）。逐 case 的完整 claim、Parent、最小 evidence 文本、support level、group 关系与被替换 span 见 [JSONL 审计记录](gold_v4_claim_level_audit_20260917.jsonl)。每条记录都对应一个 Gold case；“可支持”只表示冻结原文直接陈述该来源归属或作品事实，不把来源评价升级成独立事实。

## 处理结果

- 修改前：65 个评分单位，其中 32 个显式 claim、31 个无 claim 的 span 代理单位、2 个 evidence group；具体有 36 条无 claim 正向 span（其中 5 条已经处于 026 的两个组内）。
- 修改后：65 个评分单位，其中 63 个显式 claim、2 个 evidence group；**无 claim 正向 span 为 0**。新增 36 条 span claim 标签：31 个原代理单位变为显式 claim，另外 5 个是 026 组内的原子事实标签。所有 21 条 full/partial case 都有显式 `supported_claims`；8 条 partial case 各有明确 `unsupported_claims`。
- 没有添加新的 Parent 或 evidence group；没有为了评分删除正向单位。Gold V3、Trace 和 evaluator 评分规则未改。

| Case | 新标 span | 证据边界与处理 |
| --- | ---: | --- |
| 001 | 2 | 杰威尔早期 DEMO 完成度；2003 HitFM 榜单。另把两个旧 span 缩到完整可定位的叙述分句。 |
| 002 | 6 | 首专多曲风、受访者对想象空间的表述、来源所述中国风背景、英文研究对混合风格的描述；均保留来源归属。 |
| 004 | 3 | 母亲命名《叶惠美》、杨峻荣的录音室回忆、报道所述首专推动过程。 |
| 005 | 1 | 《晴天》列于 DEMO 清单；不推出专属创作缘起。 |
| 006 | 2 | 官方专辑命名过程和《七里香》MV 取景；不推出歌曲写作动机。 |
| 007 | 2 | 方文山关于青花瓷文化识别度的看法、学术资料的中国风归类。 |
| 008 | 2 | 研究所述《东风破》与中国风术语的关系、歌曲署名和收录信息；不推出具体创作动机。 |
| 023 | 2 | 报道列举的合作歌曲、采访所述先曲后词合作方式。 |
| 024 | 1 | 冻结资料记载的方文山早期合作例子；仍非完整作词人名录。 |
| 026 | 5 | 四首曲目的编曲署名和一项提名各有 span claim；继续由原有两个 duplicate evidence group 计分。 |
| 031 | 1 | 研究将林俊杰列于中国风创作者中；不据此比较其独立风格。 |
| 037 | 3 | 《叶惠美》发行、2003 榜单、来源记载的《时代》亚洲版封面。 |
| 040 | 1 | 索尼复刻商品资料标示的《十一月的萧邦》原发行日期；不作为风格演变证据。 |
| 043 | 2 | 访谈中电影激发灵感；将原来仅含 CNN 采访提问的 span 换成同一 Parent 内含回答的短段。该回答仍是人物文章转述。 |
| 044 | 2 | 非现场版 DEMO 官方新闻、人物报道中的发片间隔。 |
| 045 | 1 | 只保留《晴天》的 HitFM 名次，移除同一榜单中别的歌曲名次；同步把 reference answer 和 required facts 限定到该名次与“乐评仍不足”。 |

003、034、036、039 的正向 claim 数量分别为 **3、4、5、2**，与既有人工作出的部分可回答边界一致；各自的 unsupported claim 现已单独列出，且没有 supporting span。005、008、031、045 同样只把局部可支持事实计入正向证据。

## 仍需输入的 3 条

046、047 的 Gold 备注记录了已批准的“裸标题先澄清”规则，048 缺少“这首歌”的指代。冻结语料无法代替用户决定对象，因此这三条保留 `needs_input`。所需澄清、可见证据和不足之处见 [人工复核说明](needs_input_manual_review.md)。

## 结构限制

026 的四个曲目行各有原子 span claim，但原 evidence group 把同一对逐字重复 Parent 中的四行合为一个合作事实组。当前 scorer 不允许同一 Parent 同时属于四个组；本轮保持原组和评分规则，并由 validator 对这一组合给 warning。该组计 **1** 个证据单位，不能把它解释为四个独立评分 claim。若以后要逐曲评分，须单独设计组与评分语义并重做兼容性检查。

同 Trace 复算及前后数值见 [评分报告](gold_v4_claim_level_same_trace_20260917.md)。

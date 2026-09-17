# Gold V4 / 026：claim-scoped duplicate evidence 验收

- Gold V4 SHA-256：`75dc8910eab396ac7815ec303a126c14dc44b85bf97924f416f1278eee4ee038`；Gold V3 SHA-256：`5d2888542042af16bf38d3ae63f45c956c8e37f658397e2f5d3a78c3759236e9`。
- 仅使用冻结的 fallback (`7e85baf64ff79163093e1712ba889cbb221c41023609c5c6b3d5fed657bd0c8e`) 和 nav_context6 (`9beacd474a7257253cec4cfa0655a8348e71943200456abc662d84928c40f967`) Trace；未重新运行 Retrieval/Rerank。

## 026 Gold 结构

| Claim | Group | acceptable_parent_ids |
| --- | --- | --- |
| 洪敬尧凭《蓝色风暴》获最佳编曲人提名 | `026-award` | `parent-a734651048b7159210d5c4a6`, `parent-287a536a7936d114db25a509` |
| 《蓝色风暴》的编曲署名为洪敬尧 | `026-track-1` | `parent-cfb9f049a0cfdf86866257fd`, `parent-d0c34fdc20c0a8d545deea06` |
| 《四面楚歌》的编曲署名为洪敬尧 | `026-track-2` | 同上 |
| 《浪漫手机》的编曲署名为洪敬尧 | `026-track-3` | 同上 |
| 《麦芽糖》的编曲署名为洪敬尧 | `026-track-4` | 同上 |

五组关系均为 `duplicate`，四首歌曲仍是四个独立 atomic claims；没有合并曲目表组。现有 `EvidenceGroup.claim` 标识所属 claim，`acceptable_parent_ids` 定义该 claim 的等价证据，不需要新增顶层 schema 字段。V4 评分每组投影为一个证据单位，任一 Parent 命中该单位，分母仍为五个单位；旧 Parent-ID 口径的分母仍为相关 Parent 数。重复使用 Parent 不会生成额外单位或增加分母。非重叠组及 `equivalent`/`complementary` 评分语义保持原样。

## SAME TRACE 三项 V4-aware 指标

| Trace | Evidence Recall@40 | Evidence Rerank Recall@12 | Final Context Precision | 证据单位总数 |
| --- | ---: | ---: | ---: | ---: |
| fallback | 0.6413832200 | 0.4426303855 | 0.1190476190 | 68 |
| nav_context6 | 0.6413832200 | 0.4426303855 | 0.1396825397 | 68 |

对同一份 V4 数据只临时移除新加的四个歌曲组、保留奖项组作修改前投影比较，上表三项值和单位总数逐值不变。026 本身的 Top40 / Top12 为 `5/5` / `4/5`；fallback final context precision 为 `0.25`，nav_context6 为 `1/6`，均与修改前相同。逐 claim 检查两份 Trace：**无 MISS→HIT**；歌曲原始锚点本来已命中，替代 Parent 的能力由 A/B 单独命中测试验证。

旧 eligible-case 三项 V4 ID-only / group-aware 两份 Trace 均逐值相同：Recall@40 `0.6868131868`，Rerank Recall@12 `0.3827838828`，Final Context Precision 分别 `0.1153846154` / `0.1307692308`；逐 case/stage 无差异。Gold V3 legacy 与 compatibility 的逐 case/stage 回放核对通过，见 [同 Trace 兼容性报告](gold_v4_evidence_groups_same_trace_20260917.md)。

校验：V4 30 条冻结 Parent/原文/claim 结构验证通过；定向测试覆盖 A、B 分别命中两个独立 claim，重复组不合并、不加分母，以及无匹配 claim 的拒绝路径。旧 `run_rag_v4_answerability_replay.py` 的完整报告再生成在 045 的既存 `unsupported-claim note is not in Gold` 校验处中止；本报告三项 V4-aware 数值由相同 `evaluate_v4_answerability` 直接读取冻结 Trace 计算，不修改 045 Gold 或评测公式。

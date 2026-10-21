# Gold V5 release 文本对齐（Gate 1）

此目录是独立的离线对齐工具，不导入或恢复旧 `app/rag/evaluation` 脚手架。
West 已在当前会话确认“进 Gate 1”；输出仍是 `pending_west` 建议，不能作为 Gate 2 冻结或签字。

## 输入与保护

- 冻结 Gold：`ai-service/evals/datasets/rag_eval_gold_v4.frozen.jsonl`。每次运行开始、输出前及输出后校验 SHA256 为 `1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`，不一致立即停止。
- `workspace/moodflow-review/gold-v5-Gate0-inventory.json` 固定本次运行目录、release 和 178 个输入产物哈希；任何漂移均停止。
- 新 release：`knowledge-v2-e40ab37ffcc7bcf0`，再次核对 691 projection / 413 Parent / 22 source / 57 SUPPORTED claim。
- `review_plan.json` 是 Codex 编写的逐 fact 建议映射，包含新增的原文锚、来源、表头约束、修改理由与备选方案。它不是 West 的人工签字。
- 仅读取本地 JSON/JSONL/Markdown；没有网络、数据库、模型、索引写入或生产配置操作，没有新增依赖。
- 输出只允许新建，或验证相同字节。若已有输出被人工修改，工具拒绝覆盖；`--check` 不写入任何内容。

## 对齐与标签语义

原 `supporting_spans[].exact_text` 折叠空白后在新 Parent/Child `content` 中查找。原 Parent/Child ID 和 evidence_groups.acceptable_parent_ids 不参与定位；修改这些旧 ID 不影响结果，有集成测试验证。

同一 Parent 的 Parent/Child 是同一证据位置；一个锚对应多个不同 Parent 时保留 ambiguous。原锚指定的 source_id 在多个同文来源中有唯一位置时，优先使用该来源，同时完整记录其他候选。source_id 改变会显式记录。表格新增锚同时检查具体行和父文本表头。

claim 检索同时查看 Parent 文本和 knowledge claim.statement，词面相似候选仅作审阅线索，不自动获得 matched。无原文锚的 fact 也执行线索检索并保留未解决状态。`review_plan.json` 用显式 AND/OR 依赖将经过阅读的新文本与 required_facts 对应；这部分语义判断仍需 West 终审。

逐题 evidence_status 表示正向 fact 的定位状态；matched 不等于 full。例如一个比较题的背景发行日期能够匹配，但仍可能不足以比较风格。ambiguous 也包含旧问题证据锚缺失、无法唯一认定的情形，JSON 的 evidence_status_reason 说明原因。

source_absent 的原文继续在 34 篇隔离 normalized Markdown 中查找。找到记 recoverable_by_unquarantine；找不到记“题超纲候选”。隔离文档不拥有本次运行的 chunk/enrichment，恢复线索不会生成虚假的 Parent/Child，也不计入 release 的事实覆盖。找到原文不代表可自动解除隔离。

full/partial/insufficient 按正向 fact 的完整覆盖降级，并以旧标签为上限；不自动升级旧 insufficient。每条事实的各组成锚必须全部满足，不能用同一题其他已命中的事实补齐缺口。范围限定、背景不足或“没有找到证据”等判断单列为 scope_limits，不作为正向事实计数或全语料不存在的证明。

## 特殊题与 candidate

- 001、024：基于新 release 起草新答案和 required_facts；024 按作词表头核查，并补充宋健彰、刘畊宏示例。
- 009：原 supporting_spans 实际为空，旧快歌灵感/画面感要求与“影响”不对应；新建直接评论与电影传播使用的证据链。保守保留 insufficient，并列出 partial 备选。
- 042、043、044：建议 needs_input，保留 partial/剔除备选及待澄清问题，等待 West 决定。
- 040、049：以当前 release 的文本替换缺失来源中的发行年/获奖事实，并显式保留来源替换记录；049 不冒充旧官方原页。
- 046、047、048、068：原题干、空答案和空 required_facts carry over。Ragas 中用空字符串表示原 null 参考答案。

candidate 严格只有四列：`question`、`ground_truth`、`contexts`、`answer`。每题 question 保持原 query；contexts=[]、answer=""。7 个建议 needs_input 的 ground_truth 为空，其中 4 个是原设计，另外 3 个待 West 批准。其他 ground_truth 保留已完整对齐的原答案，或收窄为本次已找到证据的部分；原参考答案和 required_facts 始终保存在 sidecar 比较。

case_id、序号、建议标签、候选 required_facts、scope_limits、各锚的新 ID、原文偏移、隔离路径、原/新答案及修改记录均放在审计 JSON 和 Markdown 中。candidate 的行号与审计 number（1–50）一一对应，不向 Ragas 四列混入附加字段。

## 运行与验证

在仓库根目录执行：

```powershell
python -B -X utf8 -m unittest discover -s ai-service/tools/gold_release_alignment_v5 -p "test_*.py" -v
python -B -X utf8 ai-service/tools/gold_release_alignment_v5/align_gold_v5.py
python -B -X utf8 ai-service/tools/gold_release_alignment_v5/align_gold_v5.py --check
```

无字节码落盘的静态语法检查：

```powershell
python -B -X utf8 -c "from pathlib import Path; paths=list(Path('ai-service/tools/gold_release_alignment_v5').glob('*.py')); [compile(p.read_text(encoding='utf-8'), str(p), 'exec') for p in paths]; print('syntax PASS:', len(paths), 'files')"
```

完整测试覆盖正常文本定位、跨 Child、重复位置歧义、来源消歧、空原文拒绝、表头误用、隔离恢复/未恢复、text_changed 相似候选不升格、事实组合与降级、needs_input、冻结哈希拒绝、原 case 契约、不同输出拒绝覆盖，以及真实 50 题四列/顺序/新证据偏移/特殊修正。测试只运行本工具，不启动应用或调用外部服务。

2026-10-17 本次执行结果：30 项 unittest 全部通过；2 个 Python 文件的静态语法检查通过；真实输入生成与 `--check` 逐字节重算均通过。另用独立读取校验了 50 行顺序、四列契约、4 道原 needs_input、3 道新增建议、全部已接受原文的新 chunk 偏移及未修改的 Gold SHA256。

本次建议分布为 full 18 / partial 11 / insufficient 14 / needs_input 7。33 个 source_absent 锚在隔离原文中找到恢复线索，涉及18题；没有将这些线索算作当前 release 的证据。4 题缺少原问题的直接证据锚，保留 ambiguous 待人工审阅。没有应用运行时或前后端改动，因此未启动服务、前端构建或整仓应用测试；真实检索及 Ragas 运行留待后续评测步骤。

## 输出与审阅

- `ai-service/evals/datasets/rag_eval_gold_v5.candidate.jsonl`
- `workspace/moodflow-review/gold-v5-对齐审计表.md`
- `workspace/moodflow-review/gold-v5-alignment-audit.json`

完成后停止，等待 West 审阅审计表与 candidate。未生成 frozen V5；冻结属于单独派发的 Gate 2。

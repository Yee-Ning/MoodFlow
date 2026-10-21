# Gold V5 Gate 2 冻结

只执行 West 在本次用户指令中已签字的7项决定。`west_signoff.json` 明确记录该指令为授权来源、终审答案与 required_facts、Gate 1 源哈希和最终50个标签；它是签字决定的执行记录，不冒充数字签名。

## 格式与来源

- 冻结集：`ai-service/evals/datasets/rag_eval_gold_v5.frozen.jsonl`，50行、50唯一 case_id。
- 严格视图：`ai-service/evals/datasets/rag_eval_gold_v5.ragas.jsonl`，只含 question / ground_truth / contexts / answer。
- 冻结报告：`workspace/moodflow-review/gold-v5-冻结报告.md`。
- SHA256 与逐行 case_id 映射：`workspace/moodflow-review/gold-v5-freeze-manifest.json`。
- 已签字 sidecar：`workspace/moodflow-review/gold-v5-alignment-audit.json`。

JSONL 不添加第51行“文件头”。release_id、schema_version、frozen_by、frozen_at 是每个记录开头的四个字段；冻结清单也集中记录它们。冻结记录还保留 required_facts、old_label、changes、新 Parent/Child 及精确引用和偏移。

Ragas 四列中没有 case_id；必须按冻结文件原顺序使用。工具逐行比较四列值，并在 freeze-manifest 的 row_map 中记录 line、case_id、答案哈希和问题哈希，不往严格视图混入附加列。

relevant_parent_ids 只从 matched 锚的 accepted_locations 去重汇总。source_absent/ambiguous 题中其他已匹配锚仍可保留；没有 matched 锚则是空数组。所有 needs_input 都强制清空证据与 ground_truth。隔离原文、词面候选、未接受的多位置候选都不会变成冻结证据。

每条引用须在新 release 的 Parent/Child 正文中按位置逐字复现；Child 必须属于记录的 Parent。终审 #1/#11 答案与 required_facts 必须逐字匹配签字记录；只允许 #9 相比 Gate 1 改标签。最终分布不为18/12/13/7时停止。

## 运行和验证

从仓库根目录执行：

```powershell
python -B -X utf8 -m unittest discover -s ai-service/tools/gold_release_freeze_v5 -p "test_*.py" -v
python -B -X utf8 -m unittest discover -s ai-service/tools/gold_release_alignment_v5 -p "test_*.py" -v
python -B -X utf8 -c "from pathlib import Path; paths=list(Path('ai-service/tools/gold_release_freeze_v5').glob('*.py')); [compile(p.read_text(encoding='utf-8'), str(p), 'exec') for p in paths]; print('syntax PASS:', len(paths), 'files')"
python -B -X utf8 ai-service/tools/gold_release_freeze_v5/freeze_gold_v5.py
python -B -X utf8 ai-service/tools/gold_release_freeze_v5/freeze_gold_v5.py --check
```

全部操作离线，不引入依赖，不启动模型、数据库、服务或隔离恢复流水线。没有前后端改动，不执行应用构建或模型评测；contexts=[]、answer="" 是此步骤的明确约定。

2026-10-17 本次结果：Gate 2 测试26项与 Gate 1 回归测试30项全部通过（合计56项）；两个新Python文件的语法检查通过；创建冻结产物和冻结后 `--check` 均通过。另有独立读取校验50行/唯一ID、仅#9标签变更、#1/#11原文采用、逐行四列投影与case_id映射、全部文件SHA256和Windows只读属性。V4任务前后SHA256保持规定值。没有因环境原因缺失本次要求的验证。

## 冻结保护

每次开始、输出前及输出后校验 Gold V4 SHA256，必须为 `1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e`。同时校验 Gate 0 中的178个输入指纹、原 candidate 指纹和 Gate 1 sidecar 原快照。

新产物仅可新建或核对相同字节；任何已有冻结文件或其他新输出不同都会停止，不提供 force 选项。唯一获准的原文件更新是签字后的 alignment sidecar，并在写入前再次检查外部编辑。Gate 1 notes/alternatives 原样保留作历史，最新决定以 gate2_resolution、suggested_label 和冻结文件为准。

冻结后给 v5.frozen.jsonl 设置本机只读属性；`--check` 不写文件或属性，并要求该只读属性仍有效。Git 不存储 Windows 只读位，迁移到新工作副本后可用创建/核验命令重新设置属性；它不改变已存在冻结文件的内容。datasets/.gitattributes 仅固定两个V5文件为LF，保证checkout不改变SHA256，不改变V4的属性。

任何内容调整必须另建新版本；不解除只读后覆写V5。Gate 1 生成器仍可重算历史待审稿，但其 `--check` 不用于验证已签字 sidecar；冻结后使用本目录的 `--check`。

Gate 2 完成后停下，等待 West 确认冻结完成。34篇隔离文档恢复走独立 pipeline track，不阻塞本次冻结。

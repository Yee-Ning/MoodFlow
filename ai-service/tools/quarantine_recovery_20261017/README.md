# 34 篇隔离文档恢复：2026-10-17 独立 track

只处理已经批准的 Gate 0 计划中的 34 篇。本轮在独立 run 中完成文档 QA、增量 Clean/Chunk/Enrichment/Knowledge 和新 QA 包；新 release 与 collection 要等 West 对新样本的实际签字。

- Gate 0 计划：`workspace/moodflow-review/quarantine-recovery-Gate0-plan.md`。
- 当前 run：`workspace/moodflow-review/quarantine-recovery-Gate1-run.json`。
- 逐篇决定、局部 Clean 配置、QA 样本副本：`workspace/moodflow-review/quarantine-recovery-knowledge-qa/`。
- 执行报告：`workspace/moodflow-review/quarantine-recovery-Gate1-执行报告.md`。

所有命令从项目根目录运行，用现有 `ai-service/.venv/Scripts/python.exe -B -X utf8`。不新增依赖，不读取 `.env`，不用付费 LLM/远程模型。以下运行根是本轮已经物化并核验的独立目录：

```text
ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
```

工具使用已经存在的 execution-manifest、原始数据副本与逐篇 QA 决定，不自动产生人工批准。`guard_run` 在每个阶段前后核对 Gate 0 的 573 个保护输入。历史目录、Gold V4/V5、旧配置与 registry 原件不写入。

## 已执行的阶段

```powershell
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py preview --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
# 完成原文/删除 diff/PDF 版面检查，记录实际 reviewer=Codex 的逐篇决定；不是 West 的 release 签字。
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py draft-clean --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
# 复核最终文本和精确告警，记录 cleaned_text_sha256 后才可执行 clean。
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py clean --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py chunk --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py enrich --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py knowledge --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py assemble-review --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py packet --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py vector-preflight --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
```

Chunk 阶段拒绝重做已经存在的 source chunk 产物。Enrichment/Knowledge 的已存在 delta 走 validate-only，旧 22 篇不会进入处理调用。合并保留旧 1104 行的原始字节前缀，以已有 writer/reporter 生成新的聚合清单和质量报告。

26 篇文档通过本地文档 QA；新增 276 Parent、579 Child、855 enriched/knowledge chunks、18 SUPPORTED claims。合并为 48 篇、689 Parent、1270 Child、75 SUPPORTED claims，机器质量门禁通过。

7 篇超长原子 Wikidata record 继续隔离；jc-v2-048 的报纸栏序/广告混排未解决，继续隔离。Sony 的已核对正文在模板去重时用精确 document/block SHA256 保护，原 56 篇统计上下文与阈值不变；其余去重仍调用原实现。删除比例告警保留，只有最终文本 hash、告警列表与逐篇决定完全一致才能下游准入。

## 人工签字之后的入口（本轮尚未执行）

新 QA 范围为 75 条 claim、100 条 entity，以及 11 个新文档覆盖补充项。canonical 样本保持 pending 原文；West 的实际结论写入新 run 的 `data/knowledge/review/qa_review_attestation.json`。模板在同目录，reviewer=null、reviewed_count=0；不能把它直接当作批准。

实际 attestation 必须绑定两个 sample hash、release_selection_sha256、补充项 hash，填写实际审阅数、West 的批准/退回结论与真实签字来源。旧 4 条 known issues 仅沿袭精确的历史 ID/依据，须内嵌历史记录；新错误不得自动接受。有退回则修复/隔离并重新生成抽样及签字，不能仅把 rejected_sample_ids 清空。

```powershell
# 仅在真实 West 签字已经记录后执行。
ai-service/.venv/Scripts/python.exe -B -X utf8 ai-service/tools/quarantine_recovery_20261017/recovery.py publish --run ai-service/data/reruns/jay-chou-quarantine-recovery-20261017/gate1-20261017-152344
```

该入口使用原 KnowledgeReleaseBuilder finalize，生成内容寻址的新 `knowledge-v2-<hash>`；只在 localhost 开发端点创建 `music_knowledge_recovery_20261017_<hash>_v1_v2_v4`，不改 alias 或生产配置，prune_stale=false。旧缓存使用 SQLite `mode=ro&immutable=1`；逐项核对 dense_text、完整 dense 配置、pooling、维度/有限值/归一化后，将旧向量按新 input_hash 写入新缓存。若旧 chunk/向量不等价或缺失就停，编码器也拒绝默默重新计算旧正文。仅新增 delta 需要本地 embedding。

最终 release/indexing 的实际集成运行尚未执行，不能据此声明新 collection 已就绪。签字前不连接 Qdrant、不加载模型；此阶段的环境就绪和最终 point/payload 验收仍待执行。

## 验证

```powershell
ai-service/.venv/Scripts/python.exe -B -X utf8 -m unittest discover -s ai-service/tools/quarantine_recovery_20261017 -p test_recovery.py
ai-service/.venv/Scripts/python.exe -B -m ruff check ai-service/tools/quarantine_recovery_20261017
```

结果：30 个恢复门禁测试通过；Ruff 通过。另用 `pytest.main(['--noconftest', '-p', 'no:cacheprovider', '-q', ...])` 运行以下现有纯单元回归，39 passed：

- `tests/test_clean_stage_quarantine_regression.py`
- `tests/test_rag_manual_review_quarantine.py`
- `tests/test_chunk_oversize_quarantine.py`
- `tests/test_knowledge_release_quarantine.py`

完整合并产物通过原 Enrichment/Knowledge 的 validate-only（1959 行）；691 个旧向量通过只读复用预检；旧 22 篇逐文件及逐行字节一致，Gold V4/V5 和旧目录 542 文件树摘要一致。没有业务服务或前端改动，不运行应用构建/全量应用测试。部署、生产切换、push、Gold 增量对齐均不在本轮执行范围内。

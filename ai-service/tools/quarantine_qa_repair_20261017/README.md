# QA 退回修复，2026-10-17

West已批准Gate0报告中的档1+档2，以及撤下C034/C063、收窄C074、10存疑退回采样、72已过claims不动。

此工具只构建新的派生Knowledge及QA审阅包。没有publish、index、签字命令，不修改生产配置。旧候选与冻结release/Gold只读，执行前后校验全树/文件SHA256；目标必须在专用新namespace且不存在。

在`ai-service`目录执行（末级目录必须新命名）：

```powershell
.venv/Scripts/python.exe -B tools/quarantine_qa_repair_20261017/repair.py build --output data/reruns/jay-chou-quarantine-qa-repair-20261017/gate1-NEW
```

对应校验：

```powershell
.venv/Scripts/python.exe -B tools/quarantine_qa_repair_20261017/repair.py validate --output data/reruns/jay-chou-quarantine-qa-repair-20261017/gate1-NEW
```

测试（同在ai-service目录）：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_grounded_entity_qa.py -q
.venv/Scripts/python.exe -B -m unittest discover -s tools/quarantine_qa_repair_20261017 -p test_repair.py -q
```

`GroundedEntityLinker`是新增的可注入实现，复用原EntityRegistry与消歧辅助函数，只有显式v6/rule-only配置可构造。未替换既有v5实现及默认pipeline配置。文档分类先从实际正文识别电影infobox，再以`film_documents`传给linker；因此Child也能继承真实作品类型冲突，不能从错误采集标签推断“歌曲”。日文新别名未加入registry。

`GroundedKnowledgeQASampler`复用既有sample ID、分层/稳定哈希算法，先校验正文quote/offset/解析路径，再抽样。Document metadata只展示上下文，不计作局部提及。补充样本同样适用；无合格样本如实记覆盖缺口。

派生Knowledge保留所有原始chunk正文/ID/metadata。每个实体链接的决定另存hash绑定sidecar；claims流不重新抽取，72个VerifiedClaim对象及其QA行逐项相等。少数已审claim的argument若未被v6解析成局部实体，另列claim-argument-associations，不偷偷补回局部链接或改claim，交West复核。

新包路径为输出目录下`qa/knowledge-review-packet-v2.md`，只有模板`qa_review_attestation.v2.template.json`，没有真实签字。审核该新包后，冻结与新collection仍需后续明确授权。

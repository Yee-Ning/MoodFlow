# MoodFlow Memory 完善验收

日期：2026-10-09，Asia/Shanghai。基线：`ea2b50c2da971786dfd6d68d973fead57f1e0967`。

在独立工作树 `C:/Users/33994/.codex/worktrees/memory-hardening/MoodFlow` 的 `codex/memory-hardening` 分支按用户批准的优先级实施。原工作区的报告修改、删除与未跟踪数据不覆盖、不回退。没有修改生产 .env、默认图、灰度开关或 Guard 阈值，没有部署或 push，没有新增依赖。

## 按顺序完成的提交

| 优先级 / 项目 | commit | 实现 |
| --- | --- | --- |
| P0 画像一致性 | 381e69c | Memory 主表与待同步 revision 同事务提交；画像只作为有界投影，失败保留待办，后续读取重试；CAS 冲突重试，保留独立人工条目；超过 64 条 / 16KiB 的事实保留主表，记录投影遗漏数。 |
| P0 消息幂等与证据 | df4cb7f | 用可信请求 ID 区分消息；同消息同事实不增加确认，原文 / 候选改变报冲突；记录证据引用、原文 quote、来源消息及线程。事实、证据、同步标记共同回滚；旧无消息 ID 调用保留兼容限制。 |
| P0 入库门 | fe1282d | 主表前统一敏感文本、类别、置信度、原文、事实主体、假设 / 引用及否定校验；敏感消息不交给提取模型；引用保持逐字空白。拒绝理由不含原文。 |
| P0 格式偏好路由 | ec81a96 | format / recommendation_feedback 映射到音乐任务；验证“不要现场版”通过画像路径进入歌曲、工具及 Worldline 上下文。 |
| P1 场景 / 有效期 | 6edb1b2 | 全局与场景分别构造事实键；明确有效期，过期不召回；确认不隐式延长；场景推断多义时返回未定，仅使用全局事实；兼容同场景旧键。 |
| P1 检索质量 / 去重 | 228cc1d | 相关候选优先进入候选池、置信度参与排序、相关性门槛、画像 / 向量分别降级；以事实键去重两种画像来源；迁移标记已有事实供投影重建。 |
| P1 文本 / 向量对应 | 26c1810 | 确认同一事实时一起刷新摘要与向量；验证向量维度、有限值及非零，不将无效向量写入。 |
| P1 用户管理 | 7f1a9d6 | 登录用户自己的分页查看、纠错、忘记、暂停 / 恢复 API 与 /memory 页面；跨用户 404、请求校验、纠错幂等、代次隔离、防止旧任务复活已忘记事实；历史画像版本通过撤销索引过滤，原不可变触发器不改变；缓存记忆在组装前重验。 |
| P2 异步提取 / 评测 | 7a4803e | 主回答只入队；PG 持久队列、租约、两次派发上限、有限存储重试、已验证结果保存与重试；沿用原 MODEL_USAGE 预留 / 结算及截止时间；无持久预算证明时不在重启后创建余额；添加离线评测与不含身份 / 原文的生命周期日志。 |
| P2 任务收尾补充 | 50f4912 | 已终止任务释放内存中的原账本对象引用；未知用量的原预留仍保留，不猜费用、不退款；存储重试保留对象，且不重复调用模型。 |
| P1 画像故障时读门补充 | 9dc30f2 | ProfileSource 在注入前复用主表核验，投影同步失败也不能复活撤销 / 替换的记忆；主表不可读时拒绝需要核验的记忆投影。 |

## 数据流与运行边界

1. 用户身份仍由 Spring session 解析，不接受请求 JSON 中的 user_id。
2. 新消息先按用户、消息 ID 校验处理摘要；只对符合入库门的用户事实写入。profile 与 episodic 仍由现有 MemoryStore 保存，不改变 Journal 或 checkpoint saver。
3. Memory 主表保持事实源，ProfileDocument 是受容量上限约束的投影。新事实、确认、撤销和暂停写同步待办；同步完成才清除该 revision。已有人工画像条目保留。
4. 召回保留用户隔离、场景与时效约束、token 预算和不可信数据区段。主表查询优先命中明确主题；向量失败不丢掉可用画像，画像失败也不阻止可用历史体验；相同事实不会两次注入。
5. 原回答完成前仅保存提取任务，提取后台执行。租约领取和派发次数在 PG 中保存；有结果但落库失败可重试该结果，不重复调用模型。完成 / 拒绝 / 阻断后清除队列中的原消息与临时提取结果。
6. 当原请求存在累计账本时，入队先在原 MODEL_USAGE 保留额度，后台核验该预留，仍使用原 run_id、agent、call_id 结算。没有调用模型时可结算 0；有派发而无完整用量回执时保留估算预留，不猜实际费用。派发总次数不超过 2，原截止时间过期不新派发。无持久预算的 volatile 任务重启后被阻断；没有累计执行上下文的独立调用使用既有单请求预算边界。
7. 用户暂停 / 忘记会推进代次，旧任务无法再写入；未执行任务立即清除输入。忘记删除同主题的主表版本与关联证据，画像撤销索引使旧版本也不再被正常读取。原对话及历史画像原始存储仍按原保留策略存在，本实现不宣称物理抹除全部历史、日志或备份。

## 迁移与使用

新增迁移 008–014（Memory 可选迁移只在 include_memory=true 时执行；013 画像撤销表即使 Memory 关闭也需要）。部署前需通过既有入口在批准的数据库执行：

```powershell
python -m scripts.migrate_agent_postgres
```

本任务仅对一次性本机 pgvector 测试容器执行迁移，没有连接或迁移生产数据库。Memory 持久性仍由 MEMORY_BACKEND 决定；memory 模式的事实、待办、队列及偏好只在当前进程中存在。生产需要 postgres 模式，启动后台 worker 和已有 backend 初始化共用应用生命周期。

新增 API：

- GET /api/ai/memory/records（after_id、limit，最多 50 条）
- GET / PATCH /api/ai/memory/preferences
- PATCH /api/ai/memory/records/{memory_id}（第一人称更正 text + request_id）
- DELETE /api/ai/memory/records/{memory_id}

页面入口：桌面 / 手机导航“记忆”，路径 /memory。暂停期间仍可查看和忘记已保存条目；恢复后可继续召回未撤销事实。页面显示原文依据、更正表单、忘记确认、操作失败与同步待完成状态。需要同主题更正；更换主题需忘记旧条目后用新消息表达。

## 验证记录

Python 使用原项目已安装 .venv 的解释器，在本工作树运行 `-X utf8 scripts/validate_journal_recovery.py`，隔离生产 .env 与凭据。真实 PG 为本机回环地址的一次性 pgvector 容器，端口 60763；每项 PG 测试用独立数据库。

| 检查 | 结果 |
| --- | --- |
| 画像 / 原 Memory / 入口回归 | 44 passed |
| 画像待办、PG 重启 / 回滚及入口 | 32 passed |
| 消息幂等及 Context Loop 回归 | 85 passed |
| 入库门 / 原 Memory / 入口 | 56 passed |
| format 音乐路由 | 43 passed |
| 场景、时效、实际 PG 与 Agent Pack | 94 passed |
| 检索 / Agent Pack | 96 passed；分路降级、Jev 补充 50 passed |
| 摘要 / 向量一致性 | 46 passed |
| 用户控制 / 原 Memory / 画像 / PG | 80 passed |
| 最终异步队列、用户控制、API、Context Loop / PG | 76 passed |
| 队列及 PG 租约 / 重启 / 预算专项 | 11 passed |
| 队列收尾补充及实际 PG | 14 passed；两项新增测试分别覆盖未知用量终止、缺用量回执且存储重试。 |
| 画像故障时主表核验 / Memory / Context / PG | 96 passed；四个新增用例在修复前均失败，修复后覆盖撤销、暂停、替换及主表不可用。 |
| 画像核验后的 Agent Pack / 原 Memory / 预算上下文 | 96 passed |
| 首轮全量 pytest（7a4803e，夹具未齐） | 2235 passed、13 skipped、99 failed、17 errors；228 subtests passed，2384.36 秒。失败详情保留在本地日志 / JUnit，归因为冻结文件换行符和缺失的既有 RAG 夹具。 |
| 最终全量 pytest（9dc30f2，含真实 PG） | 2351 passed、12 skipped、3 failed、0 errors；288 subtests passed，3086.78 秒。三项失败均为既有 Gold 冻结数据字节摘要；恢复其原始换行字节后按下行复测。未声称这次全量调用零失败。 |
| Gold 冻结数据与图基础复测 | 35 passed、0 failed、0 skipped；56 subtests passed，51.20 秒。其中 Gold 三个受影响模块全部 20 项通过，包含原先失败的三项；图基础 15 项通过。没有遗留未通过的失败用例。 |
| 修改文件 Ruff | All checks passed |
| 全仓 Ruff | 45 项既有问题（E402=40、F401=4、F841=1）；全部 18 个问题文件与基线 ea2b50c 相同，本任务新增问题为 0，未顺手修复。 |
| Python compileall（app、评测及所有 Memory 测试文件） | 通过 |
| npm run build | 通过；保留既有大 bundle 提示 |
| npm run lint | 通过 |
| npm run test:memory | 请求、鉴权错误、变更与匿名 UI 检查通过 |
| npm run test:search | 现有搜索回归通过 |
| 浏览器关键交互 / 响应式 | 模拟账户桌面、390×844 窄屏；暂停、恢复、更正、忘记确认与空状态已验证，截图随报告。没有操作真实账户数据。 |

中间失败如实记录：新增音乐路由测试一度误用不存在的 select 方法，改为实际 build 接口且保留三路断言；新增 PG 测试插入位置导致一次 collection 语法错误，修复结构后保留全部重启 / 回滚断言；异步导入暴露 RAG 的 eager import 环，改为延迟加载 embedding 实现；首次预算专项的假响应 usage 对象与真实 SDK 不同，修正为可报告用量的响应并增加实际账本 tokens=20 断言；UI SSR 测试需要既有 Audio mock，补齐后通过。未删除、skip 或弱化失败测试。后续最终验证如有失败在本报告追加。

Windows 工作树首轮全量验证暴露冻结文件的 LF → CRLF 转换问题。独立复现 `tests/test_handoff_entry_rules.py -x --tb=short` 为 1 failed，异常为 `Router Capability Contract differs from V3 freeze`。冻结配置要求摘要 `238d373ff43db0eea72dae17525525276a666c3819f959de8925206b0915a97c`；Git 基线原始字节计算值一致，CRLF checkout 的值为 `17bd911d3e5d16cc138b8391fe13e8a15225895011dfd31069d5ac442fbdb3e5`。去掉 CRLF 后字节与基线完全相同。复验恢复工作树中的 Git 原始字节，不修改契约内容、冻结摘要、策略阈值或测试断言。

首轮失败的 99 项包含：85 项直接报 Router Contract 摘要冲突、由同一冲突导致的 shadow 日志断言 1 项、其他冻结数据摘要断言 / 门禁 6 项、缺历史 trace 7 项；17 项 errors 均因缺失 `data/knowledge/v2/knowledge_release_manifest.json`。最终工作树从 Git 原始字节恢复 839 个已提交的评测 / 数据文件；从原工作区复制六个命名的公开测试夹具（知识 release manifest、被冻结的 296MB Parent 语料、两份历史 trace、clean / normalized 文档），知识语料、trace、Router Contract 四项摘要全部匹配既有冻结值。没有连接新外部服务，夹具不提交 Git。原工作区没有因此被修改。

账本对象清理新增测试的第一批为 12 passed、2 failed：测试误将 StructuredOutputService 已转为 rejected 的 provider 异常当作可重试存储异常。测试改为实际存储 ConnectionError 路径，并保留未知用量原预留不变、重试期间引用保留、只调用一次模型、终止后引用释放的断言；最终 14 项通过。

最终代码复核发现旧画像来源尚未核对 Memory 主表：投影同步不可用时，撤销 / 暂停 / 替换后的旧画像仍可能进入候选。新增四个故障测试先得到 4 failed，复现这一实际缺口。修复复用 `current_memory_items`，主表不可用时排除需要核验的记忆投影；显式注入测试 MemoryStore 的既有正向测试仍保留原断言。与 Memory、Context 和实际 PG 的 96 项回归通过。为验证这次补丁，50f4912 上已经运行但未结束的全量批次被停止，其日志 / JUnit 留存为 `pytest-full-before-source-fix.*`，不作为最终验收数字。

最终全量只剩以下三个失败：`test_v3_remains_frozen_and_v4_validates`、`test_seed_is_byte_identical_prefix_and_new_cases_are_unique`、`test_frozen_gold_matches_reviewed_candidate_exactly`。冻结摘要绑定原工作区的 CRLF / 混合换行字节，Git 已将这些文件规范化为 LF；统一恢复 Git LF 后，JSON 内容相同，原始字节摘要却不同。对 V3、V4 seed、V4 frozen、expanded candidate 四个文件，先确认原工作区字节与既有冻结摘要一致，再确认去除 CRLF 后与 HEAD blob 逐字节相同，才恢复工作树中的原始字节。没有修改原工作区、RAG 代码、冻结摘要、门禁阈值或测试断言；这四个文件无 Git 内容变更，不纳入提交。

单独选择三个 Gold 模块的第一次复测为 collection 1 error：既有 RAG eager import 环依赖导入顺序。加入现有 `test_graph_foundation.py`、沿用全量测试中图基础先于 Gold 的导入顺序后，35 项全部通过。产品代码在最终全量与复测之间没有变化；只复测受到夹具字节修复影响的全部模块，没有再次重复 51 分钟全量运行。全量与复测的日志 / JUnit 保存在 `ai-service/.dev/memory-hardening/pytest-full-final.*`、`pytest-frozen-recheck.*`、`pytest-frozen-recheck-ordered.*`；原始字节核验过程保存在同目录的 `check_gold_bytes.py`、`restore_gold_bytes.py`。冻结文件在不同换行策略 checkout 下的可移植性和单模块导入环仍属既有 RAG 限制，未扩展本任务处理范围。

最终全量的 12 项 skipped 均为既有外部 / 特定重启验收：

- 4 项：`RUN_LIVE_POSTGRES_SMOKE=1` 未启用（开发数据库 smoke、真实 Qwen embedding 等）。
- 3 项：`RUN_WORLDLINE_POSTGRES_RESTART=1` 未启用。
- 2 项：`QDRANT_INTEGRATION_URL` 未配置真实 Qdrant。
- 2 项：未配置可供重启的专用 PG 容器测试参数。
- 1 项：`JEV_LIVE_TEST=1` 未启用真实 TypeSafe API。

没有新增 skip、删除测试或弱化断言。普通 PG / pgvector、队列租约和 Memory 持久重启测试已在本次隔离测试数据库运行；上述特定外部验收与真实模型质量仍未验证。交付收尾时停止本任务专用测试容器；复制的两个未跟踪知识夹具移入被忽略的 `.dev/memory-hardening/fixtures/v2` 留存，其余公开测试夹具同样不提交 Git。重新全量验证需先按上述冻结字节要求准备夹具并启动新的隔离 PG。

主要复现命令（在 ai-service / soul-music-room 对应目录运行）：

```powershell
& 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe' -X utf8 scripts/validate_journal_recovery.py --postgres-port 60763 -q tests --junitxml=.dev/memory-hardening/pytest-full-final.xml
& 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe' -X utf8 scripts/validate_journal_recovery.py --postgres-port 60763 -q tests/test_graph_foundation.py tests/test_rag_gold_v4_audit.py tests/test_rag_gold_v4_expand.py tests/test_rag_gold_v4_frozen_analysis.py --junitxml=.dev/memory-hardening/pytest-frozen-recheck-ordered.xml
& 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe' -m ruff check . --output-format json
& 'D:/code/ProjectsAll/MoodFlow/ai-service/.venv/Scripts/python.exe' -m compileall -q app scripts/evaluate_memory.py tests/test_memory_v1.py tests/test_memory_hardening.py tests/test_memory_hardening_postgres.py tests/test_memory_controls.py tests/test_memory_jobs.py
npm run build
npm run lint
npm run test:memory
npm run test:search
```

桌面最终布局（模拟账户完成忘记后的空状态）：

![桌面记忆管理页](C:/Users/33994/.codex/worktrees/memory-hardening/MoodFlow/workspace/user/files/memory-hardening/desktop.png)

390×844 手机布局（合成记忆数据）：

![手机记忆管理页](C:/Users/33994/.codex/worktrees/memory-hardening/MoodFlow/workspace/user/files/memory-hardening/mobile.png)

## 离线评测与观测

运行 `python -m scripts.evaluate_memory --output ../workspace/user/files/memory-hardening/evaluation.json`。15 个合成入库样例：误存率 0、漏存率 0；3 个合成召回查询 hit@3=1；矛盾覆盖准确率=1。该评测检查静态校验和召回逻辑，不调用真实提取模型，不代表线上准确率；真实用户纠错率尚需有足够线上样本。生命周期日志只含 operation/outcome/count/latency_ms，可用于汇总纠错、撤销、队列阻断及延迟，无用户 ID、消息、记忆正文或证据原文。

## 已知限制

- 入库语义门是保守规则与模型结构输出结合，不能证明任意自然语言推理正确；复杂指代、昵称、隐含否定可能被拒绝，真实模型效果尚未测量。
- 画像与主表仍是两个存储边界，outbox 让失败可检测、可重试，不宣称跨存储原子提交；可见性由现有 Source/撤销校验保护。
- 忘记控制 Memory 主表、画像投影和缓存中的该记忆引用；原始历史画像、原对话与备份不在本次物理清除范围内。原对话仍可参与既有会话 / 历史上下文，因此本功能不等同于对话级遗忘或全部历史删除。
- 旧调用没有稳定消息 ID 时无法区分真实重复与重试；正式入口现在传 request_id。
- 任务受原预算、截止时间、派发上限及用户代次约束，可能被阻断；持久队列不会据此生成新预算或复活撤销事实。memory 后端不保证重启持久化。
- 完整生产流量、真实 TypeSafe/Qdrant、受限环境重启等额外验收以最终 pytest 的 skip 原因列出；本任务未部署。

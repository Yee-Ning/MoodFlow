# Handoff 修复复验报告

日期：2026-10-01；开始基线 `390dfcb`。本轮完成搜歌预算修复、世界线检索排查与实体过滤修复、真实 Explorer→Curator 组合用例，以及关闭的 10% 会话配置。改动限于 Python AI 服务、测试、验收脚本和文档。[实现与复现说明](../../architecture/handoff-repair-and-canary.md)。阶段 6 原失败记录保留。

**原搜歌用例通过；两轮固定 HTTP 验收分别 13/13 通过；真实组合路径通过。周杰伦的知识覆盖仍未通过，灰度保持关闭。** 世界线成功返回目录图谱，不等于知识库已具备周杰伦资料，也不等于可以切生产流量。

## 原请求与修复证据

| 请求 | 最终结果 | 验证依据 |
|---|---|---|
| 搜索周杰伦的晴天，不要播放 | 两轮原输入通过，零播放/收藏动作 | active 与 shadow 均 completed/all_tasks_done；Curator 分别 8722、8856 token；另存原请求公开回复 |
| 探索周杰伦的音乐世界线 | 两轮协议通过，目录降级，无无关知识来源 | root 为 netease:artist:6452；9 nodes/8 edges；来源仅 music-api:artist:6452；明确未检索到补充音乐知识 |
| 先探索周杰伦的音乐世界线，然后暂停播放 | 两轮 SSE 通过；真实 Explorer→Curator | 同一 HTTP 线程的 checkpoint 记录 accepted G→C→E→C，两个任务成功、控制调用/回复成对、恰好一次 pause_player，final/done 完整 |
| 先暂停播放，然后陪我聊聊 | 原输入两轮通过 | 明确顺序执行 tool_calling→general_chat，不再把“先”升级为额外澄清 |

完整输入仍固定在[验收 CLI](../../../ai-service/scripts/handoff_acceptance.py)，保留原 8 例并新增真实组合例。每轮包含 9 个业务请求、健康检查、3 个 422 校验。[入口生效记录](http-active-final.json)、[入口 Shadow 记录](http-shadow-final.json)、[数据库路径证据](checkpoint-evidence.json)、[原搜歌公开回复](search-final.json)、[世界线公开结果](worldline-original.json)。公开结果导出时去除重复的提供方歌手长简介，保留图谱、来源、生成叙事和动作。

Curator 之前为一次只读搜歌绑定整套操作与详情工具，第一轮 prompt 有 4406 input token。目录返回多为翻唱/变体，模型接着查询实体，已有 9943 token 后下一次预算预留被拒绝，尚未生成最终回复。修复后纯明确搜索只绑定两个目录搜索工具，限制补充查询并要求如实报告版本；同一观察方式下第一轮 input 降到 1559，约减少 65%，整次完成时 Curator 使用 7949 token。这是单例观察，不能外推 P95 成本。混合动作、歌词/详情和当前歌曲指代保留原工具能力；未授权操作的拒绝语义保留。[修复前](search-before.json)、[修复后](search-after-scope.json)。预算未提高，仍为单 Agent 16000、整轮 32000。

组合用例还暴露“先探索”前缀进入歌手搜索名，以及语义路由未填直接操作子意图的情况。修复前失败分别保留于 [实体解析失败](combo-initial.json) 与 [暂停预算失败](combo-fixed.json)。当前任务完全匹配既有直接操作规则时才复用零模型调用路径；否定、条件、混合和未覆盖文本不会误执行。[最终组合 JSON](combo-final.json)。`combo-direct.json` 是服务重载前的额外失败，不计作最终修复验证。

## 世界线索引、实体和排序

当前 manifest 为 `data/indexing/worldline/final-native-20260927/v4/index_build_manifest.json`，集合 `worldline_knowledge_v4_daa965c10984eefa_v1_v2_v4`。本地 projection 和真实 Qdrant count 均为 41 points、35 parents；41 points 包含 artist:nirvana，40 包含 artist:kurt_cobain，**artist:jay_chou 为 0**。默认 v3 registry 能将周杰伦解析为 artist:jay_chou，名称解析存在，当前集合没有相应知识资料。[只读索引审计](index-audit.json)。

原路径缺少根实体的强制过滤，dense/BM25/RRF 召回的无关候选可能被上下文充分性判断接收。现在在原过滤条件外 AND 已解析根实体知识 ID，并在 rerank 前再过滤候选；没有映射时停止全库召回，有映射但没有候选时返回空知识上下文。测试包括相关/无关混排、无关高分候选、空结果、原筛选条件与 plan identity。没有调大召回池或更改排序权重，也没有通过排序“补出”缺失资料。

**已修复无关知识混入；未补充周杰伦语料、未重建/切换索引。** 后续需要可信来源采集、实体绑定与发布审查，再用同一周杰伦请求验证知识来源及具体内容。当前目录数据来自真实音乐服务，降级说明是预期结果；知识质量门槛仍未通过。

## 验证命令与结果

应用命令在 `ai-service` 执行，前端命令在 `soul-music-room`。普通全量在独立子进程使用 memory checkpoint、SQLite persistence、disabled memory、空 DATABASE_URL、占位 LLM 配置、关闭 live 开关和单线程 OpenBLAS；实际 `.env` 不改动。真实 HTTP 使用正常模型/JEV、当前 Java JAR 与 Netease、独立 PostgreSQL 和现有 Qdrant，未替换服务或业务结果。

| 命令 | 最终结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests -q -ra --tb=short` | **1671 passed，14 skipped，286 subtests passed**；退出 0，143.30 秒 |
| `.venv/Scripts/python.exe -m pytest evals/tests --ignore=evals/tests/test_judges.py -q -ra --tb=short` | **194 passed**；退出 0 |
| `.venv-eval/Scripts/python.exe -m pytest evals/tests/test_judges.py -q -ra --tb=short` | **4 passed**；退出 0；评测框架共 198 项 |
| `.venv/Scripts/python.exe -m scripts.handoff_acceptance --base-url http://127.0.0.1:8001 --output <report>` | 入口生效、Shadow 各 **13/13**；均退出 0 |
| `npm run test:ai-layout`、`test:ai-stream`、`test:music-assets`、`test:search`、`test:ai-trigger`、`test:worldline` | 六个命令通过，均退出 0 |
| `npm run lint`、`npm run build` | 通过；保留既有 >500 KB chunk warning |
| 修改的 Python 文件 Ruff check；10 个新建/验收文件 format check | 通过 |
| `.venv/Scripts/python.exe -m compileall -q app tests evals scripts`、`-m pip check` | 通过；No broken requirements found |
| `.venv/Scripts/python.exe -m ruff check . --output-format json` | 退出 1；**45 项既有问题**，path/line/code/message 与阶段 0 完全一致 |
| 本轮暂存文件 `git diff --cached --check` | 通过；未暂存用户原有修改 |

最终全量增加 49 项测试（含参数化场景）。此前完整运行有 5 项失败：测试进程显式关闭子开关覆盖了配置测试的预期 3 项，知识 fixture 混用目录/知识 ID 1 项，授权错误诊断被工具范围先行校验遮盖 1 项。修正测试进程隔离方式、对齐 fixture 的真实实体字段且新增根过滤断言、恢复授权诊断后，最终完整集合通过。没有删除、skip 或弱化原断言。另有首轮 native 内存错误与非隔离全量启动被终止的记录；不计为通过。命令结果及历史日志 hash 见[命令证据](command-evidence.json)，不导出捕获的私有正文。

14 项 skip 为显式 JEV/PostgreSQL/Qdrant/WorldLine restart 用例；本轮真实 HTTP 和只读数据库/Qdrant 检查另有证据，不能等同于运行全部 live 专项。没有重跑 Java 测试或浏览器视觉验收，Java/前端源码没有改变；前端既有脚本、构建和静态检查已执行。

## 灰度状态和未验证项

实际运行配置读取到 foundation、HANDOFF_CANARY_ENABLED=false、比例 10，JEV 转交护栏仍 Shadow。[配置状态](configuration-state.json)。10000 个合成会话的分桶稳定性、0/100% 边界、无 thread_id、关闭回退、非法配置及 API SSE 分派均有测试。另选取一个“若开关开启应命中 handoff”的会话，在关闭的真实 Foundation `/chat` 服务上验证，最终 checkpoint 属于 Foundation，没有 handoff run。[关闭开关真实验证](closed-canary.json)、[checkpoint](closed-canary-checkpoint.json)。诊断期间显式 handoff 进程不代表生产灰度已启用。

**本轮不会开闸。** 周杰伦知识覆盖待完成；JEV 护栏生效阻断仍未验收（Shadow 中也出现低于 0.8 的转交分数），不能将 Shadow 成功当作生效护栏通过。还未验证登录态音频播放、账户写操作、浏览器动作 ACK/恢复重放、并发负载、SLA 和生产回滚。比例/salt 改变会改变会话分配，两个图的历史不会自动合并。

本轮启动的服务及隔离测试数据库已停止，数据库保留；原有容器和 Qdrant 未操作。[清理记录](cleanup.json)。用户原有报告修改、移动、知识数据和图片保持原状态。[工作区保护记录](protected-workspace.json)。各逻辑改动独立 commit，没有 push；最终产物 hash 与机器门槛见 [evidence.json](evidence.json)。

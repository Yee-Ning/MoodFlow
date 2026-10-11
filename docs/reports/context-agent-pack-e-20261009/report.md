# Agent Pack A→E 综合实现报告

日期：2026-10-09。实现分支：`codex/context-agent-pack-impl-e`。设计版本：`agent-pack-design/v1`，设计提交 `8b88dc2ee8ea992ea75fd5d721620116472ee914`。

E 新增三 Agent／四边／恢复回归、离线软配额驱动及统计报告。真实业务调用的 Pack、Envelope 消费、来源选择和 usage 引用已在合并代码上验证。**生产 relay 仍使用旧 ContextPack 路径，缺少 AgentContextManifest；不能把本报告解读为设计全部落地或上线批准。** 本文分别记录已经通过的验证、继承的实现缺口和外部环境限制。

## 1. 基线、合并与变更纪律

从指定 D `1f75b0588f053ea0890c5b55f940ec22df285327` 创建 E，正常合入 C `1286f5f669d6700a26a790470848915a80b1275d`。合并提交 `d7aff7053ca25fcc70d32786c7c57a105a7389e5` 的两个父提交分别为指定 D、C；共同 B 为 `3e09b919570d9491a34e9a729fe2aa12235d6e80`。C 35 个改动文件、D 27 个，重叠仅 `context_engineering.py`、`context_policy.json`，ort 自动合并，无深层冲突。

设计按用户指定命令读取：

```text
git show codex/context-agent-pack-design:docs/architecture/context-agent-pack-design-20261008.md
```

C 已有修复完整继承：`274e0cc` 旧 smoke 的 session_handoff 断言；`451844a` 迁移 CLI 解耦；`6621d93` 本机 Spring 请求代理隔离；`1286f5f` 最新累计预算和输出预留。D 已有正式 RAG 合并与审计修复 `f734049` 同时保留。

首次合并兼容测试为 205 通过、3 失败：一个旧断言仍要求 L2/L3 16000/32000；D 的两个夹具 total=4000 小于新输出预留4096。用户指示“阈值改成最新的就行”后，E 仅同步旧测试预期为 64000/128000，并把 D 的夹具改为 `reserved_output + safety_margin + 1244`，保持原输入压力和全部裁剪／审计断言；修复提交 `356bd69`。兼容重跑 210 项通过。

相对合并提交，E **没有修改 app、脚本、生产配置、prompt 或任何来源阈值**。只新增测试、eval 适配／统计和报告，另有上述明确获准的两个旧测试同步。不修改 A/B 接口、DTO、Guard、源断言的安全性或其他分支；不 cherry-pick、rebase、push、部署。原工作区的未提交改动保持原状。

## 2. A→E 交付物

| 阶段／提交 | 交付与实际调用范围 | 专项用例 | 归档依据 |
| --- | --- | ---: | --- |
| A `55ee1fb` | 共用 AgentPackInvocation／Builder、11 个现有静态 prompt 锚点、可选 strict agent_context、旧格式投影；复用既有 pipeline，system 计一次 | 47 | `context-agent-pack-impl-a-20261008/report.md` |
| B `3e09b91` | 已提交当前 Hop 快照；received_envelope／session_view；四边和 relay 字段选择；完整 digest、owner/run/task/session/lease 检查、EventPointer | 29 | `context-agent-pack-impl-b-20261008/report.md` |
| C `27d2e76` 及其修复 | GeneralChat answer/read_observation；Curator tool_step/tool_observation/song_analysis；按 owner/run/task/user 隔离私有历史，快捷路径保留 | 30 | `context-agent-pack-impl-c-20261008/validation.md`；旧 smoke 结论见本报告 §8 |
| 正式 RAG `041392a`／合并 `f734049` | 24 个有出处候选、实际 embedding/rerank 用量、Child 投影、逐 chunk SourceManifest、拒绝透传和 PG 账本恢复 | 25 Source + 2 PG + 9 manifest | `context-rag-source-20261008/report.md` |
| D `1f75b05` | Explorer query_analysis/narrative/branches 真实 renderer 接入；正式与过渡 RAG 共享池；重试、出处闭包、framing 和成本后重选 | 35 | `context-agent-pack-d-20261008/report.md` |
| E `ab17bb1`／`0759792` | 三 Agent 四边网络和恢复；离线 quota 回放，数据、分母、原因分类和审查建议；本报告及最终验证数据 | 7 网络 + 16 eval | 本目录 |

各阶段专项和全量存在重叠，**不能相加为项目独立测试总数**。历史全量口径：A 合并补验2379普通用例、B2408、D2479，均另有288 subtests及12原环境 skip；C 的旧报告早于 smoke 修复和最新预算修复，最终以本轮重新运行结果为准。

## 3. 三 Agent、四边与 manifest

新网络用例执行 GeneralChat → Curator → Explorer → Curator → GeneralChat，四次转交，保持原 hop 限制。使用真实 handoff 图、WorldLine 图、ContinuityCoordinator、SQLite Store、RequestLedger、业务 Pack、正式 RagSource 和 SDK 请求适配；外部音乐目录、编码／重排模型和 SDK 响应使用离线替身。

每个网络案例断言业务调用的 owner/phase 顺序：GeneralChat answer → Curator tool_step → Curator tool_observation → Explorer query_analysis → Explorer narrative → Explorer branches → GeneralChat answer。查询分析不拉正式 RAG；叙事和分支分别 Collect，来源有出处。三例覆盖 empty、hybrid、unavailable。

首跳 envelope=null 合法。后续业务 Pack 记录同一真实已提交 Envelope 的 commit_ref 与选中字段；JournalReader 可解析所有模型调用的 MODEL_USAGE 引用，token 与 CallManifest 一致。Curator 工具调用／回执成对；最后 GeneralChat 不带 Curator 的 ToolMessage。转交模型只绑定 transfer_to_* 工具。prompt 锚点均有 SHA-256。重开 Store、重放同一已完成 request 时，响应一致，不增加模型调用、RAG 来源读取、History 或预算消费。

另四例逐边重开 Store，重复组装同一 Hop Pack，验证完整 payload digest、提交指针、字段、只读 History、无 reserve 和无伪造 call_manifest。实际网络中的转交调用仍有旧 CallManifest 和 usage_ref 可追溯；**其配方、prompt 和选中 Envelope 字段审计缺失，属于设计未完成部分**，见 §9。

## 4. 与设计文档的符合度

| 设计项 | 当前实现／验证 | 结论 |
| --- | --- | --- |
| §1/§9 原始 Source → 同一 pipeline → 临时 Pack；Pack/manifest 不反哺 Collect | A/B/C/D 专项与 E 恢复验证；不使用上一 Pack 作为来源 | 已验证业务路径 |
| §3–5 按职责和 phase、私有历史隔离、既有工具权限 | GeneralChat/Curator/Explorer 业务接入；未知私有归属排除，协议配对 | 已验证；Store 元数据缺口保留 |
| §6 Envelope 四边、已提交回执、完整 digest 和授权边界 | B 的完整性拒绝矩阵、D 真实 Hop、E 网络和逐边恢复 | 已验证业务接收 |
| §6 relay 最小包、排除画像/RAG/业务结果、统一 recipe | B 提供 relay 构建能力；choose_transfer 尚未调用该能力，仍可能带入口候选 | **未完全实现** |
| §7 L1/L2/L3、成本后重新检查、最终 reserve 唯一权威 | 既有预算／恢复／PG 测试与合并兼容；E 重组不扣账 | 已验证现有边界；不是并发账本重新设计 |
| §8 strict DTO、prompt 版本、旧文档兼容、RAG 审计不被覆盖 | A/D/RAG 专项；E JournalReader、SHA 和来源选择验证 | 业务调用已验证；relay 审计缺口 |
| §10 C emotion_state 可选只读证据槽 | 没有找到独立 emotion_state 来源/槽位；已有 mood 类画像不等同该实现 | **未单独落地** |
| §10/§11 E 回归、离线 quota、报告 | 7网络、16驱动测试；382离线案例；完整回归数据单独归档 | 本轮交付；外部验证不冒充通过 |

设计 §7 的16000/32000为当时基线值；C 后续获准预算修复已改为64000/128000。D 历史报告的输出2500、WorldLine total12288也早于 C 合并，不能作为本轮现值。

## 5. 冻结的现行预算和软配额

| 范围 | 当前值 | E 行为 |
| --- | --- | --- |
| L2/L3 | 64000 / 128000 | 继承 C；没有修改 |
| 默认输出／JEV输出／安全余量 | 4096 / 256 / 256 | 继承 C；没有修改 |
| GeneralChat、CurrentSong L1 total / 输入上限 | 12288 / 7936 | 输出和余量已扣除；SDK framing 仍受硬门 |
| ToolCalling、WorldLine L1 total / 输入上限 | 16384 / 12032 | 同上 |
| received_envelope Explorer | 24候选 / 1280 token | 仅离线评测 |
| received_envelope Curator | 24 / 1024 | 同上 |
| received_envelope GeneralChat | 16 / 768 | 同上 |
| session_view | 8 / 600 | 同上 |
| user_profile | 16 / 240，120字符摘录 | 同上 |
| history_memory | 24 / 600，400字符摘录 | 同上 |
| 正式 RAG | 最多24个有出处重排输入；5000 token，原top_k=12 | 同上；D仍与过渡证据共享5000池 |

离线驱动运行前后检查生产 policy 字节和 POLICIES 内容相等；policy SHA-256 为 `fc856d3be97c4a5d9d5b7ec7e92a7d451d58759856893652a41e7b242d11624e`。所有建议 `applied=false`。本轮获准的测试预期更新不属于生产调参。

## 6. 离线评测数据、方法与分母

总计382例，来源适配与合成压力分组统计：context Gold30例；memory_eval_v2的130例分别重放实际 UserProfileSource 和 HistoryMemorySource，得到260例；RAG Gold v4及冻结检索／重排日志50例；Envelope/session三owner × 两source × 7档候选数量，42例。合成档位为0/1/4/8/16/24/32，使用旧 handoff_projection 的公开短文本种子；不是已验证的生产回执或真实访问分布。

RAG 先按真实 Source 出处／有限分数门过滤，再取原检索顺序前24个，限定到冻结分数后按原top_k12输出 Child。不扩展 Parent，不发 embedding/rerank/LLM 请求，不重新标注 Gold；缺必要分数硬报错，不虚构观测。Memory 使用实际有界来源与120/400字符摘录规则，但将冻结候选适配为公开画像／历史事件；不声称完成真实数据库检索质量评估。

每例最终选择调用原 AgentPackBuilder／Collector／Selector／Budget／Assembler，保留strict manifest；只做单来源竞争，保留该owner的现有L1。token采用生产Unicode启发式计数器，不等于某在线模型的账单token。Memory统一绑定GeneralChat做来源额度回放，不能据此验证其他owner的画像分类或职责选择。**不模拟完整业务 Pack 中 required 图、其他来源、过渡 RAG 与 provider framing 的竞争。**

指标口径：可用重要项保留率＝最终保留的重要ID／进入L1的重要ID；总标签捕获率＝最终保留的重要ID／Gold全部重要ID；dropped频率＝有Pack dropped_items的案例／非空候选案例。候选窗口遗漏、上游/重排缺失、去重和软配额/总预算误裁分别计数。Supporting span先解码Source的JSON转义，再计同ID保留与任意存活ID文本保留；只有本来在Child中的精确span进入分母。无标签或空分母为N/A。画像/历史重要项摘录截取单独计数，缺关键事实span标注不能证明语义完整。

资产为现有evals目录下6个冻结文件，完整SHA-256保存在 `quota/quota-eval.json`：context_gold_audit_v3、memory_eval_v2、synthetic_memories_v2（仅记录语料来源哈希，实际使用memory_eval候选）、rag_eval_gold_v4.frozen、20260917 retrieval日志及rerank_diagnostics。结果只保存ID、数字与原因，没有复制候选正文。

## 7. 评测结果与建议值（只评不调）

| 来源／owner | 样本 | 可用重要ID保留 | Pack dropped频率 | 重要ID丢弃／配额误裁 | token审查建议 |
| --- | --- | ---: | ---: | ---: | --- |
| history_memory，Context Gold | 30 | 24/24，100% | 0% | 0/0 | 600维持 |
| history_memory，冻结Memory | 130（非空30） | 30/30，100% | 0% | 0/0 | 600维持 |
| user_profile，冻结Memory | 130（非空100） | 44/44，100% | 100% | 0/0 | 240维持 |
| RAG，冻结Child与分数 | 50（非空45） | 37/38，97.4% | 62.2% | 1/0 | 5000维持 |
| Envelope GeneralChat，合成压力 | 7（非空6） | 49/61，80.3% | 50% | 12/12 | 768→1024，向上审查 |
| Envelope Curator，合成压力 | 7（非空6） | 63/77，81.8% | 33.3% | 14/14 | 1024→1536，向上审查 |
| Envelope Explorer，合成压力 | 7（非空6） | 71/77，92.2% | 33.3% | 6/6 | 1280→1536，向上审查 |
| session_view，每owner合成压力 | 各7（各非空6） | 各37/37，100% | 0% | 0/0 | 600维持 |

Envelope 的误裁均来自来源软额；可用重要项总需求P95为GeneralChat932、Curator/Explorer1440。建议向上取整到128得到1024/1536/1536。**仅作为下一轮候选审查值，7档合成样本不足以授权生产调参。** 候选上限16/24分别额外漏24/8个合成重要项，不能仅加token解决；暂维持上限，先收集真实每Hop字段数量及重要性分布。

Session 在8项窗口内的需求P95为451，600足够；窗口外每owner另漏48个合成重要项，总标签捕获37/85=43.5%。压力来自候选窗口而非token。真实设计本就只取相关公开进度，所有32项同时“重要”是压力假设，不能据此直接扩大8项或缩小600。

Profile 裁掉500个候选而44个Gold重要项全部保留，重要需求P95仅69，最终P95192；当前样本下240没有证据偏紧，但也不能证明真实多画像/长文本分布足够。历史Memory重要需求P9567，最终P95515；Context Gold重要需求P9589、最终P9595。600对该短样本较宽，建议维持，缺少真实长历史和多来源竞争依据，不建议下调240/600或16/24。

RAG 最终token P953655、最大3730，5000在本回放中有余量，未因token配额裁重要项。1个重要Parent ID在去重时被删除；同ID精确span保留38/40，任意存活ID文本保留40/40，因此不是关键span文字丢失，也不能靠加token解决。Gold总捕获37/70=52.9%，32个重要ID在进入L1之前已经缺失；24候选窗口丢615个候选，其中4个Gold重要ID，窗口丢弃频率88.9%（以45个非空L1例为分母）。建议5000/24保持，优先检查上游命中、窗口覆盖和重排，而非增大L1软额；如评估候选24→更大值，需另跑实际重排与成本/延迟评测，本轮不外推冻结分数到新生产阈值。

## 8. 验证记录及旧 smoke 核销

最终全量 `tests evals/tests`：**2559普通用例通过、288子测试通过、0失败、12环境skip、201警告**；pytest 905.33秒，驱动含归档906.64秒，退出码0。普通用例为主测试2343、eval测试216；按文件统计和运行前后相同的源码SHA全部保存在 `validation-results.json`。12个skip节点与D验收完全相同，没有新增skip。

| 验证 | 普通用例通过 | 子测试通过 | 失败／skip | 结果依据 |
| --- | ---: | ---: | --- | --- |
| 合并兼容 A/B/C/D/RAG及最新预算 | 210 | 0 | 0／0 | compatibility |
| E 三Agent四边及恢复 | 7 | 0 | 0／0 | network |
| E 离线驱动边界和统计 | 16 | 0 | 0／0 | quota_tests |
| 冻结字节与缺资产补验 | 50 | 22 | 0／0 | environment_repair |
| C 旧跨进程smoke额外补验 | 1 | 0 | 0／0 | legacy_smoke |
| 最终全量（覆盖以上专项） | 2559 | 288 | 0／12 | full |

默认skip分组：真实TypeSafe API 1项；live PostgreSQL smoke 4项；真实Qdrant 2项；专用PG容器重启修复2项；WorldLine真实PG重启3项。4个live smoke中的跨进程checkpoint/history/handoff一项已在本轮额外隔离运行通过；其余11个不同场景仍未验证，不能把默认skip当作通过。

初次全量实际退出码1：pytest显示2452普通用例通过、110失败、284子测试通过、13 skip、196警告，1074.93秒；失败统计含4个子测试报告，旧外部计数器误把子测试也计入passed，归档时以pytest原始摘要为准，不使用2736作为普通用例数。原因是新worktree统一CRLF改变旧冻结文件的物理SHA，以及缺两份历史RAG trace与Clean样本；没有改现有失败断言。恢复与D逐文件相同的验收字节，包括Gold v4原有混合换行，而非统一转LF；补齐只读资产。相关补验50普通用例及22子测试通过。quota重新回放后，全部382例统计、配额和建议与修复前完全相等，仅更新4个文本资产的哈希绑定；提交 `9e8c9f6`。

已完成：合并兼容210通过；E网络7通过；eval驱动16通过；额外单项旧smoke1通过。各次均无新增skip，来源哈希检查相同。

旧 smoke 修复已在 C `274e0cc` 将事件期望改为真实 `session_handoff`；本轮没有再次改变它的断言。为核销 C 旧报告的失败，在本轮自己的loopback pgvector数据库创建所需checkpoint／continuity表后，只启用 `test_checkpoint_history_and_handoff_survive_process_restart`：**1 passed，18.58秒**，实际跨子进程create/recover，验证session数、artifact、checkpoint区分、上下文预算、continued_steps及公开History事件。初次在空数据库启动因缺checkpoint schema失败，补齐隔离测试迁移后通过；没有使用生产数据库或放宽断言。

本次5个实现／测试Python文件和归档验证驱动的Ruff通过；`compileall -q app tests evals/tests evals/agent_pack_quota.py`及驱动编译通过；任务diff空白检查通过。全仓 `ruff check app tests evals/agent_pack_quota.py evals/tests` 仍有与基线相同的5项问题：agent/foundation/routing.py两个F401、parse_normalize/pdf/adapters.py一个F841、source_ingestion/auto_service.py及service.py各一个F401；四个文件的Git blob逐一与B相同，未擅自修复任务外文件。

## 9. 已知缺口、适用范围与后续决策

1. **relay recipe未接入生产转交调用**：`handoff/agents.py::choose_transfer`继续 `build_context_async`，缺少 `agent_context`，也没有使用B的relay最小字段选择；入口候选可能沿旧路径进入转交Pack。已存在真实call usage与提交回执，但不满足设计§6/§8所有调用统一recipe审计。E获准范围只新增测试/eval/报告，未跨范围修改；需单独完成迁移及回归后，才能核销此设计缺口。
2. **History Store缺agent私有归属字段/索引**：C按现有消息metadata做作用域过滤，未知私有记录排除；不能补造owner或把整个Store声称为按agent原生查询。后续需独立数据模型和迁移设计。
3. **emotion_state独立证据槽未落地**：未找到对应来源/槽位。已有mood类别仅作为只读画像与intent映射，不能推断实时情绪或宣称这一设计项完成。
4. **生产分布与效果未验证**：382例为固定已有资产和42组合成压力；单来源保留率不能代替线上检索／回答正确率、成本、延迟或多来源竞争。正式RAG网络测试为合成索引／模型替身，冻结评测没有重新运行在线重排。
5. **外部opt-in和全仓lint债务**：原12项环境skip及5项lint问题单独记录；没有增加skip或以删除／弱化测试规避失败。旧smoke过期断言已经修复且本轮跨进程补验通过，应从C未解决问题中核销，不代表其余live场景全部通过。

软配额建议只提交给West审查；本报告不授权生产阈值、部署、Redis或长程分支修改。

## 10. 可复现命令与归档清单

工作目录为独立E worktree的 `ai-service`，使用现有 `.venv` Python；本机没有新安装依赖。归档驱动 `../docs/reports/context-agent-pack-e-20261009/reproduce-validation.py` 根据所在目录定位E，追加调用解释器所在service的既有 `.venv-eval/Lib/site-packages`，显式载入pytest_asyncio。测试环境固定UTF-8、JEV关闭、RAG empty、Memory/Checkpoint为memory、Continuity为sqlite；`DATABASE_URL`仅指向本轮 `127.0.0.1:15483/agent_pack_e_test` 独立pgvector16容器，PG fixture自建随机测试库。驱动拒绝含.env的worktree，使用明确离线占位设置。离线知识、意图模型/数据、两份旧RAG trace及Clean样本只读沿用D验收资产，逐文件校验SHA，未提交模型或近297MB语料。**先复用D验收资产原始字节再运行；Windows新检出不能用全CRLF或全LF代替历史冻结格式。** 该物理哈希可移植性问题为已有评测资产限制，本轮不修改冻结值、类别标签或测试断言。

运行后核对9份知识复制文件的SHA和全部目录清单，将它们移入本工作区忽略的 `.dev/agent-pack-e-20261009/accepted-knowledge-v2` 保留；其他模型、trace和Clean复制资产仍在忽略目录中。它们不是Git交付物，文件清单和哈希见验证JSON的 `test_asset_inventory`。重新运行全量前需将这9份副本复制回 `ai-service/data/knowledge/v2`，或从D复用相同字节，并重新准备仅绑定loopback的独立测试数据库；测试驱动不会替用户启动数据库。

```text
python -B -X utf8 ../docs/reports/context-agent-pack-e-20261009/reproduce-validation.py <compatibility.json> <A/B/C/D/RAG兼容测试文件> -q --tb=short -ra
python -B -X utf8 ../docs/reports/context-agent-pack-e-20261009/reproduce-validation.py <network.json> tests/test_agent_pack_impl_e.py -q --tb=short --show-capture=no
python -B -X utf8 ../docs/reports/context-agent-pack-e-20261009/reproduce-validation.py <quota-tests.json> evals/tests/test_agent_pack_quota.py -q --tb=short --show-capture=no
python -B -X utf8 -m evals.agent_pack_quota --output <全新结果目录>
python -B -X utf8 ../docs/reports/context-agent-pack-e-20261009/reproduce-validation.py <full.json> tests evals/tests -q --tb=short --show-capture=no -ra
python -B -X utf8 -m ruff check tests/test_context_rag_source.py tests/test_agent_pack_impl_d.py tests/test_agent_pack_impl_e.py evals/agent_pack_quota.py evals/tests/test_agent_pack_quota.py ../docs/reports/context-agent-pack-e-20261009/reproduce-validation.py
python -B -X utf8 -m compileall -q app tests evals/tests evals/agent_pack_quota.py
git diff --check
```

quota CLI不覆盖已有输出目录；policy／资产SHA和各例ID/数字/原因见 `quota/quota-eval.json`，中文摘要见 `quota/quota-eval.md`。验证驱动的第一个参数为独立结果JSON路径，其余为pytest参数；额外旧smoke仅可用 `--legacy-smoke-only` 加该单一节点，它只迁移本轮测试库，不启用真实embedding等其他live项。合并边界见 `merge-validation.md`，网络证明和限制见 `network-regression.md`；最终full计数、按文件结果、原skip节点、额外smoke及源码冻结证明见 `validation-results.json`。本轮没有前端变更，未把后端测试当作UI视觉验收。测试容器仅用于本轮，结束后只清理该确切实例，E工作区和归档文件保留供审查。

# Relay 生产迁移验收报告

日期：2026-10-09。基线：E `70cec051b0f5f0213719333dd6397a36f89ca190`；工作分支：`codex/agent-pack-relay-migration`；经验证的实现提交：`0b01e444be388153b6e77ed20b5226678c42b959`。

## 1. 结果与边界

`handoff/agents.py::choose_transfer` 已使用 Agent Pack 生成真实转交调用输入。正式调用复用 B 的 `build_hop_pack`，统一 `handoff/v1` recipe，以 `phase=relay` 区分业务阶段；原工具绑定、模型派发和错误契约保持原语义。真实 CallManifest 包含 `agent_context`、owner/route、prompt 版本及 SHA-256、选中来源字段和原预算快照。E 报告 §9.1 的生产 relay 未接入缺口在本轮核销；历史 E 报告保留作为原验收记录。

生产代码仅修改 `ai-service/app/agent/handoff/agents.py`。新增 22 项测试并适配 E 网络测试的 phase 分类。C 的业务测试探针按 phase 采集业务调用，原 48 个 assert 的 AST 与 E 完全相同；没有修改 A/B/C/D 接口与既有断言。Guard、四条转交边、授权、Journal 归档、5 hop 上限、账本及三个 Agent 的业务阶段未修改。`task_view` 与 `execute_owned_task` 的 AST 与 E 完全相同。未 push、部署、修改生产配置、Redis或长程任务分支，未新增依赖。

## 2. 最小输入与兼容策略

| 场景 | 组装入口与字段 | 审计 |
| --- | --- | --- |
| 已提交接收 Envelope | B relay Source，仅 `route_path`：当前 task ID、route、owner、下一跳；runtime task query | 原 proposal/commit 引用与 digest；`selected_fields=(route_path,)`，原 4 候选/256 token 配额 |
| 首跳或业务完成后 Envelope 已清除 | B 原空来源路径；runtime task query 和固定 prompt | received_envelope empty、envelope null，不伪造转交 |
| 无 Journal 的旧调用或直接调用 | A `AgentPackBuilder.build`；相同 runtime 最小路径和 query | 记录 recipe/prompt/选中字段；envelope/session null，不声明已观察 Journal commit |
| run/owner/task/digest/lease 或提交证明异常 | 原可信快照/Source 完整性校验硬拒绝 | 不退回兼容路，不派发模型，不伪造 CallManifest |

所有路径排除原始整轮输入、业务摘要、实体、工具结果、业务工具对象、画像、历史和 RAG。仅绑定原转交工具。固定 prompt 原文和原 feedback 条件附加文本不变，query 不覆盖 runtime task。正式路径保持完整 Envelope 的原完整性核对，但只有最小 route_path 进入模型；Journal 的完整性读取不等同于读取业务历史。

局部 `_RelayCollector` 委托现有 Collect，显式只允许 query/system、runtime 最小路径及 B 注册候选。该边界防止累计预算 `stop_sources` 预处理注入连续性字段后，通用 collector 意外带入 ambient revalidation；原 Select/Assembler、来源策略和降级阈值均保留。

## 3. 验证结果

| 检查 | 普通用例通过 | 子测试通过 | 失败 / skip |
| --- | ---: | ---: | --- |
| 新 Relay 22 项 + E 7 项 + C 30 项专项 | 59 | 0 | 0 / 0 |
| 原 handoff、A～D、context-loop 行为与兼容回归（全量中的子集） | 638 | 0 | 0 / 0 |
| 全量 `tests evals/tests` | 2581 | 288 | 0 / 12 |

全量耗时 1912.66 秒；201 条警告；原 handoff 测试在全量中通过 438 项。普通用例与子测试分别统计，不重复相加冒充用例数。最终专项与全量的源码运行前后哈希均一致，逐文件计数、命令、节点和源码 SHA 见 `validation-results.json`。

新增测试覆盖首跳/接收/业务完成清除/返回中转与 retry 组合、无 Journal 两类调用、run/owner/task/digest/lease/提交证明六类拒绝、owner 预算耗尽、来源停拉后连续性隔离以及原无效模型响应错误。实际 provider fixture 边界断言最小模型输入、无画像/历史/RAG读取、prompt 原文与摘要、只绑定转交工具和账本只结算一次。E 网络回归额外断言真实四次 relay CallManifest 的 owner/route、recipe、来源及选中字段，原七次业务调用、工具协议、Journal、恢复和重放断言保留。

改动 Python 文件与归档驱动的 Ruff、源码编译和 `git diff --check` 通过。全仓 Ruff 仍有原来的 5 项：routing.py 两个 F401、pdf/adapters.py 一个 F841、source_ingestion/auto_service.py 与 service.py 各一个 F401；对应四个文件 Git blob 与 E 相同。未为通过检查调整任务外文件。

初次原行为回归为 636 passed / 2 failed，失败来自 C 业务测试探针把新增 relay 混入业务 Pack 和业务 manifest 序列。只在探针采集处按 phase 分类，原阶段、prompt、调用数、来源配额、协议等 48 个 assert 原样保留；最终 C 30 项和全量原回归子集 638 项通过。已启动的初次全量在 2% 时中止再修探针，未声称中止运行完成或通过。

开发初期新增夹具的 run/任务不一致、entry owner 默认值以及原 Assembler framing 期望不完整导致专项失败，均在新增测试中纠正并完整重跑；曾有一次 psycopg 导入暂态系统 I/O 导致 collection exit 2，后续成功载入。最终结果来自成功的完整执行，没有删除、跳过或弱化失败断言。诊断结果单独记录，不能当作验收通过记录。

## 4. 配额与验证限制

生产 policy SHA-256 为 `fc856d3be97c4a5d9d5b7ec7e92a7d451d58759856893652a41e7b242d11624e`，与 E 完全相同。received_envelope relay 4/256、业务三 owner 配额，以及 session/profile/history/RAG、L1/L2/L3、输出预留、安全余量、Guard/hop 阈值均未调整。原静态 prompt 和绑定 Git blob 相同，边界证明见归档 JSON。

原 12 项环境 skip 的节点集合与 E 精确一致：真实 TypeSafe API 1、live PostgreSQL smoke 4、真实 Qdrant 2、专用 PG 容器重启 2、WorldLine 真实 PG 重启 3。本轮没有新增 skip；这些外部 opt-in 场景未验证。模型行为测试使用离线替身和真实组装/派发/记账/Journal流程，不能代替线上模型质量、成本或延迟验收。无前端改动，本轮未做 UI 视觉验收。

E 的 History Store 缺 agent 字段、emotion_state 独立来源及生产配额分布验证等其余缺口继续保留，未扩大本轮范围。旧 smoke 断言在 C 已修复并由 E 额外补验，本轮不重新改断言或据此宣布其余 live 测试通过。

## 5. 环境与复现

复用 E 的冻结评测资产原始字节，保留既有混合换行，不把 Windows 新检出统一换行当作等价物理哈希；逐文件检验 774 条资产记录，其中包含 34 项 E 验收资产及既有 tracked eval/intent 资产。没有将约 297 MB 知识语料或意图模型提交到 Git。9 份知识副本运行后保留在本 worktree 忽略的 `.dev/relay-migration-20261009/accepted-knowledge-v2`；重新运行需按 inventory 的原路径复制回 `ai-service/data/knowledge/v2`。其他忽略资产保留在 inventory 记录的位置。

数据库只使用本轮独立 pgvector16 容器 `moodflow-relay-migration-test-20261009`，标签 `moodflow.task=relay-migration-20261009`，绑定 `127.0.0.1:15484`、数据库 `relay_migration_test`；fixture 自建随机测试库。Docker 曾因失效零字节 IPC socket 无法启动。工具两次删除尝试被自动策略拒绝，包括用户授权后的尝试，拒绝操作均未执行。随后引擎恢复，核对标签/端口后只启动本轮容器；最终测试在就绪数据库上执行，结束后仅清理此确切容器及其测试卷。未 reset Docker、修改其配置、读取线上 env 或连接生产数据库。

归档驱动定位本 worktree，使用现有 `.venv` 与 `.venv-eval` 路径，拒绝带 live `.env` 的 service；固定 UTF-8、单线程数、离线占位 provider、memory checkpoint、SQLite continuity、JEV false、RAG empty。启动同样的独立测试数据库并恢复 inventory 资产后，在 `ai-service` 执行：

```text
python -B -X utf8 ../docs/reports/agent-pack-relay-migration-20261009/reproduce-validation.py <unit.json> tests/test_agent_pack_relay_migration.py tests/test_agent_pack_impl_e.py tests/test_agent_pack_impl_c.py -q --tb=short --show-capture=no -ra
python -B -X utf8 ../docs/reports/agent-pack-relay-migration-20261009/reproduce-validation.py <full.json> tests evals/tests -q --tb=short --show-capture=no -ra
python -B -m ruff check app/agent/handoff/agents.py tests/test_agent_pack_relay_migration.py tests/test_agent_pack_impl_e.py tests/test_agent_pack_impl_c.py ../docs/reports/agent-pack-relay-migration-20261009/reproduce-validation.py
python -B -m compileall -q app tests evals/tests evals/agent_pack_quota.py
git diff --check
```

本报告、`validation-results.json` 和 `reproduce-validation.py` 共同归档实现、证据与复现入口。历史 E 报告保持原样。

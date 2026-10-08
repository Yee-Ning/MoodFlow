# Agent Pack C 实现与验证

日期：2026-10-08。分支：`codex/context-agent-pack-impl-c`。

基线：`codex/context-agent-pack-impl-b`，`3e09b919570d9491a34e9a729fe2aa12235d6e80`。

当前状态：C 范围代码与调用链、隔离、配额验证已完成；最终验收待处理额外启用的旧 PostgreSQL smoke 基线失败。下文不将该失败计为通过。

## 实现范围

- GeneralChat `answer/read_observation`、Curator `tool_step/tool_observation/song_analysis` 的实际模型输入改用 Agent Pack；Foundation 和 handoff 两条入口均接入。
- recipe 为 `general_chat/v1`、`curator/v1`，复用已有 prompt 引用与 SHA-256 摘要，不修改 prompt 文本。
- 有 Journal 的 hop 使用 B 的 `build_hop_pack`。没有 Journal 的原兼容路径通过 A 的 `build`，不消费不可验证的 Envelope，也不生成提交回执。
- 私有历史在 Source 层按 `owner_agent/run_id/route_task_id` 和可用的 `user_id` 检查。未知、跨 owner、跨 task/run/user 及不完整工具协议被排除；直接越权读取硬拒绝。
- 仅实际模型/工具生产者给本次新消息携带当前执行身份；不推断 node/route/text/位置，不回填旧历史。普通公开会话保持共享。
- 画像和历史从已有来源重新读取一次；阶段选择按当前 task route 执行。成对工具协议仍经过原 collector、requiredness、预算及 assembler。
- 配额及来源用 `agent_context.source_selection.references` 中的 `quota:` / `source_scope:` JSON 引用表达，不保存内容原文、不改变 A/B DTO 或签名。
- 原配置保持 `history_memory=24候选/600 token/400字符摘录`、`user_profile=16/240/120字符`、`session_view=8/600`。Envelope 为 GeneralChat `16/768`、Curator `24/1024`。超 token 配额保留原 dropped_items/events。
- 无模型快捷路径、工具 allowlist、转交边及实际记账入口保持原行为。

## 验证结果

命令工作目录为 `ai-service`，常规 Python 为 `.venv/Scripts/python.exe`。

| 验证 | 命令/方式 | 结果 |
| --- | --- | --- |
| C 专项 | `python -m pytest tests/test_agent_pack_impl_c.py -q --tb=short`；也被全量测试再次覆盖 | 30 passed，0失败 |
| 全量主测试 | `python -m pytest tests -q --tb=short -ra` | 2202 passed，48环境skip，288 subtests passed，0失败 |
| evals 全量 | 同一 Python 的 `sys.path` 追加已有 `.venv-eval/Lib/site-packages`，运行 `pytest evals/tests -q --tb=short -ra` | 200 passed，0失败；未安装新依赖 |
| 隔离 PostgreSQL | 临时 localhost-only `postgres:16`，仅测试子进程设置 `DATABASE_URL`；补跑七个 PostgreSQL/恢复测试文件 | 最初30 passed、3 skip；5项子进程中文输出解码失败 |
| 进程恢复重跑 | 子进程固定 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`，重跑 `tests/test_recovery_selfhealing_process.py` | 5 passed、2原环境skip；上述5项失败消除，未修改测试 |
| 额外旧 smoke | 启用并运行 `test_live_postgres_smoke.py::LivePostgresSmokeTests::test_checkpoint_history_and_handoff_survive_process_restart` | 1失败：旧断言要求 `handoff` |
| B 基线复现 | 从 B ref 导出未修改源码及旧 smoke/worker，在同一隔离数据库重跑旧 smoke | 同一行116断言失败，证实不是 C 引入 |
| 改动文件 Ruff | `python -m ruff check`，覆盖全部 C 源码和新测试 | 通过 |
| 全仓 Ruff | `python -m ruff check app tests evals/tests --statistics` | 5项既有错误，涉及4个文件；文件 Git blob 均与 B 一致 |
| Python 编译 | `python -m compileall -q app tests evals/tests` | 通过 |
| Prompt 不变 | AST 提取三个 assistant 模块全部 `_PROMPT` 常量，与 B 逐字比较 | 全部一致 |
| 范围检查 | 与 B 比较 A/B接口、prompt_bindings、hop_context、Explorer、WorldLine、RAG | 无差异 |
| Diff 检查 | 仅检查本任务相关路径的 `git diff --check` | 通过；用户原有报告空白问题保留 |

七个 PostgreSQL补跑文件：`test_context_budget_postgres.py`、`test_context_contracts_postgres.py`、`test_context_entry_postgres.py`、`test_context_loop_postgres_payload_integrity.py`、`test_context_loop_postgres_recovery.py`、`test_postgres_persistence.py`、`test_recovery_selfhealing_process.py`。

主测试与 PostgreSQL补跑按用例去重后：2237项通过、13项仍需环境。其中1项旧 smoke 额外启用后失败，因此当前不能声称达到“全量0失败”的最终验收。其余12项为：JEV live 1、Qdrant live 2、其他 PostgreSQL live smoke 3、WorldLine重启3、专用固定容器重启2、pgvector 1。

旧 smoke 的 worker及持久化执行链与 B 相同。现有 rollover 使用 `HistoryEventType.SESSION_HANDOFF`，旧 smoke 却断言 `{"route_decision", "retrieval", "handoff"}`；没有更改旧测试或 A/B 契约规避失败。

## 限制与边界

- History Store 没有 agent 私有作用域查询字段/索引。本轮在 Source 层过滤现有归属，并拒绝未知私有历史，不增加表、迁移或猜测归属。这是后续数据模型缺口。
- 真实 JEV、Qdrant、pgvector及其余 opt-in live验证仍受环境约束；没有使用生产数据库或模型服务补验。
- 专用重启测试硬编码了既有容器名及端口；该容器已存在，不重启、重命名或修改它来满足本轮测试。
- 全仓5项既有Ruff问题：`routing.py` 两项F401、RAG PDF adapter一项F841、RAG source ingestion两个文件各一项F401。本轮未修改这些文件。
- 保留用户既有未提交/未跟踪文件；不 push、不发布应用、不改生产配置，不改 Explorer、RAG 或 Redis 分支。

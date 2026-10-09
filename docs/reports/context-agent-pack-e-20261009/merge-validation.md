# Agent Pack E 合并兼容验证

日期：2026-10-09。分支：`codex/context-agent-pack-impl-e`。

经 Gate 0 明确确认，从 D `1f75b0588f053ea0890c5b55f940ec22df285327` 创建独立工作区，正常合入 C `1286f5f669d6700a26a790470848915a80b1275d`。合并提交 `d7aff7053ca25fcc70d32786c7c57a105a7389e5` 的两个父提交即为上述 D、C；未 cherry-pick、rebase 或 push。设计从 `codex/context-agent-pack-design:docs/architecture/context-agent-pack-design-20261008.md` 读取。

相对于共同 B 基线 `3e09b91`，C 当前 HEAD 修改 35 个文件，D 修改 27 个文件。两者重叠仅为 `context_engineering.py` 和 `context_policy.json`，由 Git ort 自动合并。D 的正式 RAG 配额、审计和策略钩子与 C 的最新累计预算及输出预留同时保留。

首次联合兼容验证为 205 passed、3 failed。RAG 一项断言固定要求旧 L2/L3 16000/32000；D 两个参数化用例的固定 L1 total=4000 已小于新输出预留4096。用户随后明确指示“阈值改成最新的就行”。本次仅将旧默认值断言同步为 64000/128000，并把 D 的夹具 total 改为 `reserved_output + safety_margin + 1244`，保留原输入压力、重新裁剪、禁止二次 Collect、RAG 用量/逐 chunk 审计和 trace 身份的全部断言。

没有调整任何生产阈值、来源软配额或 prompt，没有修改 A/B 接口、DTO 或测试。其余 C/D 原断言未变。

验证使用现有 Python 环境，外部任务运行器设置离线模型替身和仅本机回环可访问的独立 pgvector/PostgreSQL 16 数据库。不读取生产 `.env`，不调用在线模型，不接触现有数据库、Redis 或其他特性分支。运行器记录原 app/tests 文件运行前后 SHA-256，一致。

从 `ai-service` 执行：

```text
python -X utf8 <task-validation-driver> <result.json> \
  tests/test_agent_pack_impl_a.py tests/test_agent_pack_impl_b.py \
  tests/test_agent_pack_impl_c.py tests/test_agent_pack_impl_d.py \
  tests/test_context_rag_source.py tests/test_context_rag_postgres.py \
  tests/test_context_source_manifest_compat.py tests/test_context_contracts_phase3.py \
  tests/test_budget_defaults.py -q --tb=short -ra
```

运行器显式加载 `pytest_asyncio.plugin`。结果：**210 passed、0 failed、0 skipped、4 warnings**，pytest 43.44 秒。警告为既有无 checkpointer 时 durability 不生效。静态检查包括本次两个测试文件的 `ruff check`、`compileall` 和暂存 diff 空白检查。

这只确认合并兼容性，不代表 E 的新增端到端回归、软配额评测或最终全量验收已经完成。后续成果单独归档，生产数值以确认继承的 C/D 合并基线为准。

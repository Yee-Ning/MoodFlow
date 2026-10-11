# Agent Pack D：RAG 合并与 SourceManifest 兼容修复

本报告记录 D 实施前的合并兼容检查，不作为 Explorer 三阶段接入的完成报告。

## 基线与合并

- B 基线：`3e09b919570d9491a34e9a729fe2aa12235d6e80`。
- 已验收 RAG：`041392a788660483f591ec6be495bb409dab7975`。
- 分支：`codex/context-agent-pack-impl-d`，独立 Git worktree；主工作区和其他特性分支未修改。
- 使用正常双亲 merge，不 cherry-pick、不 rebase、不 push。
- `context_policy.json` 保留 B 的 received_envelope/session_view 配额和 RAG 的 5000 token / 24 候选配额；L2/L3 阈值不变。
- `context_sources.py` 保留 SourceItems 的审计、状态，并同时穿透 SourceIntegrityError、RagBudgetRefusal、RagAccountingFailure；未将完整性或记账失败降级为 unavailable。

初次联合回归为 116 passed、2 failed，均由旧 SourceManifest 序列化补入新增 RAG 默认字段造成。按已确认边界暂停并报告后，West 批准以下兼容修复。

## 修复边界

SourceManifest 仅新增 wrap serializer：对 chunks、model_usage、collection 逐字段检查，只有原文未提供该字段且目前没有审计内容时才省略。显式空列表/null、实际审计内容及验证后追加的非空审计均保留。只处理序列化结果，不改输入文档。

candidate_tokens、final_tokens、events、dedup_dropped 仍必填；extra=forbid、frozen、校验和统计含义不变。AgentPackBuilder/build_hop_pack 的实现与调用签名相对 B 无 diff；A/B 原测试文件相对 B 无 diff，两项原失败断言未改。

新增 9 项对照测试：旧 Phase 2 文档在 python/json 模式下精确 round-trip；四种显式空审计字段形状；真实 RagSource → SourceRegistry → Select 输出的 chunk/usage/collection 保留；缺席字段后续追加内容；必填与额外字段拒绝。真实审计比较保留原嵌套 DTO 对可选身份/usage_ref 补 null 的既有语义，不另改嵌套契约。

## 验证

在隔离 worktree 的 ai-service 执行，使用原项目 Python 3.12.10、离线测试环境及本轮独立 loopback PostgreSQL。测试环境变量由仓库外验证运行器设置，未读取生产 .env 或提交凭证。

联合回归命令（前缀为仓库外环境/计数运行器）：

```text
python -X utf8 <validation-driver> <result.json> -p xdist.plugin -n 2 --dist loadfile tests/test_agent_pack_impl_a.py tests/test_agent_pack_impl_b.py tests/test_context_rag_source.py tests/test_context_rag_postgres.py tests/test_context_contracts_phase3.py tests/test_context_source_manifest_compat.py -q --disable-warnings --tb=short
```

结果：127 passed、0 failed、0 skipped，pytest 35.68 秒。A 47、B 29、RAG 25、RAG PostgreSQL 2、Phase 3 15、新兼容测试 9。

全量命令：

```text
python -X utf8 <validation-driver> <result.json> -q --disable-warnings --tb=short
```

结果：2444 passed、288 subtests passed、12 skipped、185 warnings、0 failed、0 errors，pytest 1187.50 秒。12 项 skip 与既有基线逐项一致；运行前后 app/tests 的 Python/Lua 源码哈希一致。

主机资源不足时，首次 4 worker 全量出现 MemoryError/SQLite I/O 错误并被中断，只停止了本轮验证进程。验收采用随后完整串行运行的上述零失败结果，没有新增 skip、修改失败断言或删除测试。

静态检查：ruff check（本次代码与新增测试）、compileall（同范围）、git diff --check / --cached --check 均通过。

## 限制与后续范围

12 项既有 skip 仍涉及在线 JEV、显式启用的真实 Qwen/Qdrant/进程重启环境；不声称这些环境路径已经验证。本次真实 RAG 审计测试执行实际 Source/Registry/Selector，检索和模型替身使用合成索引，不调用线上模型。

本提交仅完成获准的 RAG 合并与 SourceManifest 兼容修复；Explorer 的阶段 recipe、共享可选证据配额及生成器规则审计尚未接入，不声称 D 已完成。

# 保留旧暂停会话继续 003 / 004：已批准方案

保持 9 月 29 日这条暂停聊天的原状，执行已经批准的兼容 schema 迁移，不先恢复或删除会话。请求方是项目开发和全程负责人；已在本轮聊天明确回复“允许保留这条暂停会话并继续 003/004（推荐）”，批准原“无 pending 执行”条件的一条例外，无需重复确认。

限定对象是 [inline 核对结果](gate3-inline-control-result.json) 中指纹 `ce287fbf81eca90fce8a14548837d6ec` 的唯一控制候选，目标 `general_chat_subgraph`、inline None、版本为字符串、没有 seen 状态。[Session 核对](gate3-session-readonly-result.json) 确认它是 Session 主图的最新 checkpoint，保存时间 `2026-09-29T02:47:54.115908+00:00`，Session / Task 存在且开放。存储 namespace 非空本身不表示子图，保存时间早也不能单独证明会话无效。

最终脚本仅允许上述一条保存状态保留。任何额外 pending、未知状态、活跃 run / 请求、最新 pending writes 或 Session 身份变化仍停止。备份完整性与恢复验证、固定镜像与 SQL 哈希、完整低峰观测、事务内复查、固定 5s / 60s 超时和失败回滚保持。在 PostgreSQL 内计算该 namespace 全部 checkpoint / blob / writes 及其 Task / Session 的完整行 SHA-256，正文不离开 SQL；六次观测、事务内、提交前及提交后摘要必须保持。业务恢复、灰度、默认图、`.env`、部署及 push 均不在本方案内。

003 增加历史 event_key / event_seq 及索引；004 新建 agent_execution。两份 SQL 均不更新 checkpoint、Session、Task，不恢复 graph。实际隔离 PostgreSQL 测试以 synthetic 暂停主图状态执行完整两份 SQL，逐表比较 checkpoint、blob、写记录、Session、Task 的完整 JSONB 行内容，全部保持；回滚后 ledger 恢复为 001 / 002。该测试证明 DDL 的内容兼容性，不代表生产当前无运行请求，也不能替代即时门控。

验证：`python -m unittest discover -s docs/reports/context-loop-p1-production-migration-20261002 -p test_production_runner.py -v`，17 tests / 55.241 s 全部通过。覆盖 Session 主图与持久子图区分、兼容保留、无例外批准的 pending 停止、额外 pending / 状态变化 / 非法摘要停止、实际事务内状态变化回滚、批准例外后的正常提交和 §3 复验及内容摘要保持、重复调用停止。没有应用源码或迁移 SQL 变更，生产迁移及 §3 输出尚未执行，真实业务写测试依约跳过。

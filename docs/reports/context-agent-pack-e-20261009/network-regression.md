# E 三 Agent 网络回归

基线：D `1f75b05` 正常合入 C `1286f5f`，合并提交 `d7aff705`。

新增 `ai-service/tests/test_agent_pack_impl_e.py`：7 项通过，0 失败、0 skip，12 项既有 LangGraph durability 警告，pytest 37.38 秒。使用真实 handoff / WorldLine 图、业务 Pack、正式 RAG Source、SDK 调用适配、RequestLedger 和 SQLite Journal；SDK、检索模型与音乐目录在外部边界使用离线替身，未调用在线服务。

3 项网络案例分别采用正式 RAG empty / hybrid / unavailable。执行 GeneralChat → Curator → Explorer → Curator → GeneralChat 四边路径，验证 7 次业务 Pack 调用的 owner / phase、首跳空 Envelope、工具协议归属、静态 prompt 摘要、同一已提交 Envelope 的字段和 EventPointer、MODEL_USAGE 引用可解析；重开 Store 后重放同一已完成请求，不新增调用、来源读取或 History。

另 4 项逐边案例重开 Store，从已提交回执重新取得当前 Hop，在原预算作用域内重复组装 Pack：提交引用和完整载荷摘要相同、字段非空、不扣模型 token、不产生预留或伪造 call_manifest、不写 History。

现状限制：生产 `handoff/agents.py::choose_transfer` 仍调用旧 `build_context_async`。原转交工具、Guard、记账与回执可验证，但真实 relay 调用没有 `agent_context` 配方审计扩展；不能据此声称 relay 已全面迁入 Agent Pack。此项属于继承的实现缺口，E 不修改 A/B/C/D 的生产调用接口。业务接收方的 Pack 与四边消费已经接入并通过本轮验证。

命令：在隔离 worktree 的 `ai-service` 中，以离线测试环境运行 `python -B -X utf8 <validation-driver> <result.json> tests/test_agent_pack_impl_e.py -q --tb=short --show-capture=no`。驱动固定 UTF-8、载入 pytest_asyncio、使用本轮独立测试数据库；运行前后原 app/tests 源码哈希相同。

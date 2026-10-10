# MoodFlow AI 音乐助手项目总结

## 技术栈

React、Spring Boot、FastAPI、LangGraph、LangChain、ReAct、RAG、MCP、Redis、PostgreSQL / pgvector、Qdrant、Docker。

## 具体业务描述

- 智能策展：按场景、情绪、年代，融合目录曲库、知识关联与用户偏好生成歌单，补充关联小众曲目和选曲导语，由 Curator → GeneralChat 承接说明。
- 聆听旅程 Listening Journey：Explorer 生成有序探索路径，代表歌曲映射为播放队列，Curator 播放、GeneralChat 随节点讲解；关系图作为详情，串起“知识 → 播放 → 对话”。
- 个性化推荐：红心／播放历史、知识关联、场景标签三因素加权，策略可配置、可 A/B；以离线红心命中率、在线播放完成率和收藏转化率评估效果。

## Agent Harness 技术亮点

- 路由识别：RuleGuard → JEV → TaskOrchestrator 三级级联，Choice 单意图、Noul 多意图；保留否定、任务顺序与依赖，低置信度回退 Hybrid，结合 Guard、Shadow 与灰度验证。
- 工具治理：ToolSpec 注册表与 execute_tool 网关统一契约、授权、幂等和执行策略，串联 Journal、执行、对账与 Receipt；持久化 ACK Outbox 仅补传回执、超时标未知，读操作退避／熔断、未知写入只读对账，业务 Saga 显式补偿。
- Context Engineering：Agent Pack 按 Agent／阶段隔离、选源、去重和预算压缩；Budgeted Handoff 传目标、约束、进度及 Artifact Ref，以 Immutable History、Session Rollover／Lineage、按需 Drill-down 和 Revalidation 支撑长程任务。
- 断点续跑：Checkpoint + Journal + 幂等 + RecoveryPlanner 恢复进度，核对 Handoff 提交、业务回执与预算；支持 Journal 重建检查点，避免重复副作用。
- Structured Output：统一 Pydantic 契约，以 JSON Schema／Function Calling 约束生成；语法、Schema、业务语义三层校验，确定性 Repair + 有界 Retry，最终 Fail Closed，校验通过才进入下游或执行工具。
- Memory：短期 State／Checkpoint 与长期 Profile／Episodic Memory 分层，按用户隔离、版本化和冲突消解，pgvector 召回后纳入上下文预算。
- Token 与 Redis：L1 调用、L2 Agent、L3 任务三级预算，预留、实耗结算与恢复对账；Redis Lua 原子预留、限流和派生缓存，PostgreSQL 保存权威账本。
- RAG：Query Understanding → Dense + BM25／RRF 混合召回 → Rerank → Evidence Selection，以 Parent／Child、实体绑定和来源引用支撑知识关系、选曲与讲解。
- 评测与观测：Harness 覆盖路由、工具、Handoff 和故障恢复，RAG 分层评测；统一 Trace／Manifest，追踪结构化输出首轮成功率、修复率、重试率、校验失败率及延迟。
- Prompt 版本化：以 ID／Version／Variant／SHA-256 固定提示词与调用清单，支持回放追溯、版本对比和灰度。

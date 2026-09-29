# Jev Router V4 Shadow 与生产验收状态

## 当前状态

生产 MainGraph 仍只派发现有 Hybrid 的 `RouteDecision`。V4 候选入口默认关闭（`JEV_ROUTER_V4_SHADOW_ENABLED=false`）；本阶段没有关闭 `JEV_SHADOW_MODE`、没有删除 Hybrid、没有改 Subgraph 业务接口或生产数据。真实用户流量的 V4 shadow 数量为 **0（未启用，未采集）**。本文的数字来自冻结的人工离线请求和真实 Jev/Hybrid Router 调用，不能称为真实 shadow traffic。

若未来显式开启该 opt-in 且保留 `JEV_ENABLED=true`、`JEV_SHADOW_MODE=true`，`intent_route` 并行获得 Hybrid 与 Jev V4 的决策，始终返回 Hybrid 给 MainGraph。候选仅分类与建任务，不调用 Subgraph；同一个 Hybrid future 在 Jev 回退时复用。单元测试用可能产生播放器动作的候选任务验证：MainGraph 最终只返回 Hybrid 的澄清，候选未派发。候选自身异常只记录错误类别，不中断 Hybrid；生产真实负载与并发性能尚待验证。

## 匿名 telemetry

新增 `jev.v4_shadow` 日志事件包含 `router_source`、`fallback_reason`、实际模型版本、Router 延迟、API 调用、token usage、Choice confidence、四路概率、两侧 route 列表、任务数、依赖边数、clarify 标志与 route agreement。异常事件只记录异常类型。日志不包含 query、任务片段、Song ID、用户 ID、trace ID、API key 或 SDK 原始异常文本；单元测试检查原始私密片段不会出现在日志。现有日志基础设施可承载此摘要，尚未配置线上聚合、保留周期或监控阈值，因此没有真实分布数据可报告。

## Shadow 离线对照

V4 首轮 blind 24 条：Hybrid/Jev route Exact 为 11/23，完整任务 Exact 为 5/20；Jev 有 1 次超时回退和 1 次 route/fragment 冲突回退。修复后同集复测：11/24 对 24/24 route、5/24 对 23/24 完整任务；剩余冲突回退仍把两条 ToolCalling 动作合并。详情与首轮/复测区分见 `jev_router_v4_e2e_report.md`，原始决策、模型概率、延迟与 token 在两个结果 JSONL 中。

## 切换前必须补齐的证据

1. 新的独立人工审核 E2E Gold，特别覆盖无显式连接词、多段同 route 动作、否定和同名实体；不能继续用已见 V4 集作调优集。
2. 真实匿名 shadow 流量下的 route、任务、clarify、超时与回退分布，并检查敏感数据不进入日志；统计多天和高并发时的 P95/P99、API 调用与实际成本。
3. `v4-24` 类型的 Noul/片段冲突和 Hybrid 合并动作需有不会错误执行的处理策略，并由独立数据验证。
4. Jev 决策进入 MainGraph 后的受控 Subgraph E2E：请求校验、成功响应、失败/needs_input、ToolCalling 排序和浏览器动作结果确认。当前 Shadow 保证候选不执行副作用，因而不能提供这项验收。
5. 明确无依赖任务的调度语义：当前 MainGraph 串行执行，RouteDecision 中无依赖只表示业务独立。

保持 Shadow 与 Hybrid；本阶段不发布切换结论。

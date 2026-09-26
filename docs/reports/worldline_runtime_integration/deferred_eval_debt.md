# Deferred evaluation debt

登记日期：2026-09-26。状态：deferred / recorded_not_repaired。

此清单只引用上一轮已冻结的 failure diagnosis 与用户确认的 baseline，**不代表本轮重新评测**。工程 integration 测试通过数不替代这些质量指标。

| 类别 | 冻结债务 | Case records | 归属 |
| --- | --- | --- | --- |
| Influence Direction | 4 个真实漏检 | real-influence-2-positive；real-influence-3-positive；real-influence-r3-pixies-nirvana-forward；real-influence-r3-pixies-cobain-forward | 后续整体 Agent Eval System |
| Scene evidence mismatch | 1 个真实漏检 | real-scene-1-positive | 后续整体 Agent Eval System |
| Synthetic regression | 20/28 passed，8 未通过 | 沿用上一轮冻结 snapshot，未新运行/刷分 | 后续整体 Agent Eval System |

本轮未修改对应 validator/direction/scoring 规则，未修 Neko Case，未新增 Gold。机器可读版本及其原 diagnosis SHA 见 deferred_eval_debt.json。下一阶段只做 Online E2E 与 PostgreSQL persistence/restart 的准备及验收，不将以上债务偷偷合入 engineering repair。

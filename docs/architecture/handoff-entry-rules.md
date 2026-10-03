# Handoff 入口规则层：V5 主路与 Hybrid 兜底

实现基线为 `f97d5d259cfcf153185581905ca7abb0aca33b50`（ACK 命名空间修复）；开始工作时 HEAD 为其文档后代 `982d321b981de69f23394cd252b969e45937e275`。本轮只改 handoff 入口、对应测试及本文，未修改 Foundation、V5 内部、转交 Guard、转交边、Journal/digest 或 ACK。

## 入口顺序

```text
输入 → Preflight → RuleRouter → V5 → Legacy Hybrid → 澄清
         ↓拒绝       ↓完整命中    ↓成功    ↓成功         ↓
      needs_input   RouteDecision RouteDecision RouteDecision final
```

1. **Preflight**：检查非空字符串及 4000 字上限；复用 V5 `RuleGuard.inspect/preflight_question` 和已有语法分段，排除否定片段，检查既有外部预订/通信与音乐反事实能力边界。全部操作被否定或预检拒绝时，直接生成澄清计划，无分类器、JEV 或 LLM 调用。
2. **RuleRouter 快判**：对每个肯定片段执行既有规则；只有所有片段完整覆盖、各片段只有一个高精度意图时才短路。复用 `TaskOrchestrator.build(IntentPlan)` 生成任务；保留原文证据、重复路由任务、顺序依赖，并标注 `source=rule` 和原 `sub_intent`。当前歌曲缺失等上下文问题沿用 TaskOrchestrator 的澄清检查。
3. **V5 主路**：快判未命中时调用原 `JevRouterV5.route_with_trace`，内部 RuleGuard、Single Choice / Multi Noul + 片段 Choice、TaskOrchestrator 均保持不变，阈值沿用已有配置。
4. **Hybrid 兜底**：V5 的超时、低置信度、解析/编排失败沿用原兜底分支；入口补接原分支未覆盖的普通异常。每次请求只调用一次 Hybrid。Hybrid 继续使用自身“规则 → 本地分类器 → 必要时 LLM”流程；没有完整非空计划、返回值不合法或普通异常时转澄清。
5. **出口核对**：Hybrid 意图证据必须位于预检保留的原文片段内；无法提供意图证据的任务只允许保留完整单片段输入。防止 Legacy 的否定词识别差异在兜底时重新加入已否定动作；异常日志不输出用户原文或异常正文。

“零成本”指本地层没有供应商调用，仍有本地解析和匹配耗时；本轮不宣称物理零延迟。Preflight 继承已有已知能力边界规则，不等同于识别所有自然语言边界或替代转交 Guard。

## 配置、预算与澄清

- `configured_entry_router` 在 JEV 关闭时仍套用 Preflight 和快判；未命中走 Hybrid，保留原有可用能力。
- V5 实际选择计划仍要求已有 `JEV_ENABLED=true`、`JEV_HANDOFF_ROUTER_ENABLED=true`、`JEV_HANDOFF_ROUTER_SHADOW_MODE=false`。本文说明配置条件，未改任何 `.env`、默认图或灰度开关。
- Shadow 保持既有含义：快判命中时直接使用规则计划；未命中时先执行一次 Hybrid，再观察 V5，最终执行 Hybrid 计划。V5 观察失败复用已有计划，不重复调用 Hybrid。
- Hybrid 延迟到实际需要时构造，规则命中和 V5 主路成功均不加载本地分类器。Foundation 的工厂与入口保持原样。
- `RuntimeLimitError` 和任务取消不转为普通分类失败，保留 parent ledger/deadline 的终止语义；预算耗尽或父请求超时后不会追加 Hybrid 调用。本轮不添加 V5 重试或新的超时配置。
- 合法输入的拒绝/澄清复用 `new_run` 的 `needs_input → final_response`。空白、非字符串或超长输入无法存入严格 `HandoffRun.UserInput`，图入口直接设置 `handoff_needs_input=true` 并终止，不创建无效 run，也不调用 Agent。
- HTTP DTO 的现有输入校验保持不变；图级澄清不会改变现有 HTTP 非法参数响应。

## 代码与验证

| 位置 | 职责 |
| --- | --- |
| `ai-service/app/agent/handoff/routing.py`：`input_question`、`HandoffEntryRouter.rule_plan/route` | 本地预检、快判、V5 选择 |
| 同文件：`HybridFallback.route`、`LazyHybridRouter.route` | 兜底澄清、原文核对、延迟构造 |
| `ai-service/app/agent/handoff/graph.py`：`build_handoff_graph` 的 `prepare/route/final` | 默认入口接线及非法图输入澄清 |
| `ai-service/tests/test_handoff_entry_rules.py` | 短路、边界、依赖、失败、预算、默认图入口 |
| `ai-service/tests/test_handoff_jev_routing.py` | 原有 Shadow/主路/兜底测试改用规则未覆盖输入，继续验证 V5 路径 |

本轮使用既有进程级离线包装器 `.dev/context-loop-p0-20261002/validate.py`；不读取/输出凭据、不调用真实供应商、不访问测试账号，不开启灰度。

```powershell
# 工作目录：ai-service
.venv/Scripts/python.exe ../.dev/context-loop-p0-20261002/validate.py -m pytest tests -q
.venv/Scripts/python.exe -m ruff check app/agent/handoff/routing.py app/agent/handoff/graph.py tests/test_handoff_entry_rules.py tests/test_handoff_jev_routing.py
git diff --check -- app/agent/handoff/routing.py app/agent/handoff/graph.py tests/test_handoff_entry_rules.py tests/test_handoff_jev_routing.py
```

| 检查 | 结果 |
| --- | --- |
| 定向 `pytest`：入口规则、入口 JEV、V5、序列路由 | 77 passed；随后补充的 3 项用例由全量回归覆盖 |
| 全量业务 `pytest tests -q` | **1879 passed、38 skipped、286 subtests passed**，336.25s；161 条 LangGraph 无 checkpointer 时 durability 无效的警告 |
| 本轮 4 个 Python 文件 `ruff check` | 通过 |
| 本轮文件 `git diff --check` | 通过 |

可选环境测试的既有跳过条件未调整；47 个新增参数化用例全部通过。覆盖预检无调用、完整快判与子意图、部分匹配保留原请求、重复路由和依赖、Shadow 与 V5 主路、原始异常与已处理异常兜底、Hybrid 无计划/失败澄清、否定片段出口隔离、实际 Legacy Hybrid 规则/任务构造、父预算与取消、4000 字边界、默认 handoff 图接线。

测试边界：多片段兜底计划必须提供各自的原文证据；把整段组合请求伪装成单个意图会被出口核对拒绝。V5 的既有语法审查可能直接把“暂停播放”判为待分解而进入 Hybrid，因此远程异常用例采用“暂停一下”，确保确实到达 JEV 后再注入异常。

未进行真实 JEV/DeepSeek 联调、生产 canary 或供应商账单核对；本轮结果不能替代这些验收。全量业务命令仅运行 `tests/`；独立 `evals/` 未运行，当前虚拟环境确认未安装其所需的 `deepeval`，本轮未新增依赖。前端未改动，未重复播放器真实验收。

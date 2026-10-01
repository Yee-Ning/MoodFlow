# Handoff 复验修复与关闭的会话灰度

本轮修复范围为 Python AI 服务、对应回归和验收脚本。前端协议、Java API、真实 `.env`、现有知识数据和线上索引均不改动。复验结果见[报告](../reports/handoff-repair-20261001/validation.md)。

纯目录搜歌任务通过既有规则和操作授权校验识别后，仅绑定 `search_music`、`search_entities_tool`。带播放、收藏、歌词、详情或当前歌曲指代的任务继续使用原工具域。搜索提示要求一次查询通常足够，找不到指定版本时最多补充一次，并如实区分原唱、翻唱和其他版本。模型与执行器使用相同工具范围，未授权操作仍返回 `unauthorized_action`；预算保持单 Agent 16000、整轮 32000。

WorldLine 将已解析的目录根实体绑定到 SearchPlan 中的知识实体 ID，在原 metadata filter 外追加 AND 根实体条件；候选池在 rerank 前再校验一次根实体 ID。没有知识映射则不进行全库召回；映射存在但索引没有资料时，返回空知识上下文，由原有目录降级路径处理。原始查询、其他筛选条件与排除项保留，改变筛选范围时重新计算 plan ID。排序只调整相关候选的顺序，不能弥补缺失的语料。

JEV 的语义路由可能只有 tool_calling 域而没有子意图。直接操作节点只对当前分配的任务文本重新执行既有 RuleRouter；完整覆盖且全部属于既有直接操作时才执行。历史和 Explorer 输出不能授权写操作。明确顺序请求中的“先”不再单独进入分类模型；“陪我聊聊”新增高精度规则。世界线实体解析也移除请求前缀中的“先”，引号中的真实歌名保留。

## 10% 会话配置

当前模板和实际运行配置均保持关闭：

```dotenv
AI_AGENT_GRAPH_MODE=foundation
HANDOFF_CANARY_ENABLED=false
HANDOFF_CANARY_PERCENTAGE=10
HANDOFF_CANARY_SALT=moodflow-handoff-v1
```

只有基础模式为 foundation 且开关为 true 时，`/api/ai/chat` 才对客户端提供的 thread_id 分桶。SHA-256(salt + NUL + thread_id) 的前 8 字节转为整数，取模 10000；小于 1000 的会话进入 handoff，其余仍为 foundation。无 thread_id 的请求保留 foundation。`/foundation`、`/handoff` 的显式诊断接口和 legacy/handoff 原模式保留原行为。

同一 thread_id 在配置不变时跨请求、实例和重启保持一致。比例和 salt 必须在一次观察窗口内固定；该实现没有持久化每个会话的分配。部署环境变量更新后需重启服务，使缓存配置生效。关闭开关可停止新的 handoff 路由，但既有 Foundation/Handoff checkpoint 命名空间各自独立，历史不会自动合并。生产回滚时的界面与历史连续性仍需专项演练。

此提交不自动开闸。复验报告通过并得到后续切流指令后，才修改实际部署开关；不得把显式 `/handoff` 诊断请求计为已经启用 10% 生产流量。JEV 转交护栏仍为 Shadow，阈值保持 0.8/0.2；护栏的生效阻断也需独立验收。

## 真实组合用例

在既有验收 CLI 中新增 `explorer_to_curator_sse`：`先探索周杰伦的音乐世界线，然后暂停播放`，使用明确标注的合成播放器上下文。必须得到顺序为 worldline、tool_calling 的成功结果、来源与引用、恰好一次 pause_player，以及完整 final/done。该用例验证服务端指令生成，不代表浏览器已播放音频或执行 ACK。

使用当前服务、真实模型与 JEV、独立 PostgreSQL 及当前 Qdrant 索引运行：

```powershell
# 在 ai-service，正常配置仅交给进程，不打印凭据。
.venv/Scripts/python.exe -m scripts.handoff_acceptance `
  --base-url http://127.0.0.1:8001 `
  --output ../docs/reports/handoff-repair-20261001/http-replay.json
```

保留原 8 个业务输入，加上新增组合，共 9 个业务请求；每轮另有健康检查及 3 个非法请求校验。入口 Shadow 和隔离入口生效分别复验。服务端 checkpoint 中的 accepted Explorer→Curator 转交与同一请求的最终动作、成功状态共同构成路径证据；仅看到两个路由名称不作为真实转交证明。

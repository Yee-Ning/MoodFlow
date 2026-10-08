# AI 聊天离线故障修复记录（2026-10-08）

## 原因与修复范围

本地 AI 进程的 `/health` 返回 200，但聊天在初始化会话执行锁时抛出 `continuity execution schema missing`。运行数据库 `moodflow_agent` 只登记了 001、002 两个迁移，缺少后续代码需要的表、历史事件列和索引。

使用既有迁移工具补齐 003、004、006、007，未添加自动启动迁移。执行前完成 PostgreSQL 完整备份，并用 `pg_restore --list` 验证备份格式；迁移前后的 task、session、history、artifact、handoff、memory 数据条数一致。完整备份及结构快照保存在忽略目录 `.dev/ai-chat-repair-20261008/`，未提交数据库内容。

迁移 CLI 原本因导入 Memory/Retrieval/Agent 图产生循环导入，无法独立运行。将轻量的 `memory_backend()` 配置读取移至 `app.config.settings`，原 `app.agent.memory.runtime` 导入路径继续可用。补充开发文档，说明 PostgreSQL 模式更新代码后需手动执行迁移。

另外，三个 Spring HTTP 适配器默认读取系统代理，导致本地请求误走代理并返回 502。将用户身份、歌曲上下文和音乐工具客户端设置为直接连接配置的 Spring 服务。LLM 客户端及外部模型代理配置未改动。

清理了重复运行的两份本地 AI 服务，并通过既有 `start-dev.ps1` 恢复单份服务。验证期间为避免 Windows 提交内存不足，使用 `OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`。未更改系统内存设置、依赖或用户现有未提交文件。

## 验证结果

命令从对应项目目录执行，Python 使用既有 `.venv/Scripts/python.exe`。

| 检查 | 结果 |
| --- | --- |
| `python -m scripts.migrate_agent_postgres` | 补齐四个迁移；重复执行返回 `Business migrations are current` |
| `python -m pytest tests -q --tb=short` | 2210 passed、48 skipped、288 subtests passed；188 warnings |
| `python -m pytest tests/test_spring_proxy_isolation.py tests/test_runtime_environment.py -q --tb=short` | 最终版本 11 passed，包含真实回环 HTTP 请求、错误响应、迁移入口独立导入、配置边界和缺少数据库配置 |
| PostgreSQL `tests/test_postgres_persistence.py` | 独立临时 pgvector/PostgreSQL 16 容器内 7 passed，覆盖迁移幂等、事务回滚、持久化、用户隔离及进程重启恢复 |
| `ruff check` 本次修改的 Python 文件 | 通过 |
| `python -m compileall -q` 本次修改的 Python 文件 | 通过 |
| `npm run test:ai-stream` | 通过 |
| `npm run lint` | 通过 |
| `npm run build` | 通过，保留既有较大 chunk 提示 |
| 经前端代理 POST `/api/ai/chat`，空白输入 | HTTP 422，正常校验失败 |
| 经前端代理 POST `/api/ai/chat`，没有当前歌曲 | 正常返回 `needs_input`、`final`、`done`，提示先播放歌曲 |
| 服务恢复后的真实普通聊天 | HTTP 200、`status=success`、`final`、`done`，没有 SSE error |
| 服务恢复后的原始请求“根据现在这首歌推荐”，当前歌曲《晴天》（186016） | HTTP 200、`status=success`，返回完整文字推荐、`final`、`done`，没有 SSE error |

全量测试执行期间补充了迁移缺配置的失败路径测试，因此最终 11 项定向测试包含全量测试快照之后新增的一项测试。

业务库凭据文件无法匹配新建的测试库，首次 PostgreSQL 补测在认证阶段退出；后续在仅回环地址可访问、使用同一 pgvector 镜像的独立容器通过全部 7 项。首次失败创建的空测试库已按名称、创建时间和空结构确认后清理，临时容器也已移除，现有认证设置未修改。

## 限制与后续风险

- 全量测试的 48 项依赖既有 opt-in 条件，包括 PostgreSQL 进程/重启验收、Qdrant 集成及真实 JEV 调用；本次单独补跑了 7 项 PostgreSQL 持久化测试，其他 opt-in 验收未完整执行。
- 在线验证中，部分其他推荐请求曾触发既有 token 预算上限，或需要进一步澄清。预算和路由策略未修改；这类回答仍可能出现，不能保证任意推荐请求都成功。
- 原始《晴天》请求成功返回文字推荐，未生成播放器推荐卡片；本次未修改前端呈现或执行浏览器、登录会话及响应式视觉验证。
- 保留了既有 LangGraph、Qdrant 兼容性检查和 JEV 降级日志提示；没有将这些提示作为离线故障修复。

运行证据保存在 `.dev/ai-chat-repair-20261008/`：完整备份、迁移前后结构快照、自动化测试日志、SSE 响应及 `final-live-summary.json`。该目录不会提交。

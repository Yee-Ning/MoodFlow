# MoodFlow 本地开发启动

MoodFlow 的本地环境包含四个独立服务：

| 服务 | 目录 | 端口 |
| --- | --- | ---: |
| 网易云音乐 API | `netease-music-api` | 3000 |
| Spring Boot 后端 | `music-backend` | 8080 |
| Python AI Agent | `ai-service` | 8001 |
| React 前端 | `soul-music-room` | 5173 |

## 一键启动

在项目根目录打开 PowerShell：

```powershell
.\start-dev.ps1
```

脚本会检查 JDK、Maven、Node.js、Python 虚拟环境和各模块依赖，然后依次启动尚未运行的服务。端口已经被占用时，脚本会认为对应服务由 IDE 或其他终端管理，并跳过该服务。

启动成功后访问 <http://localhost:5173>。请使用 `localhost`，以匹配 Spring Boot 当前允许的前端开发来源。

如果 `ai-service/.env` 启用了 PostgreSQL，首次启动和更新代码后应先手动补齐数据库迁移：

```powershell
cd ai-service
.\.venv\Scripts\python.exe -m scripts.migrate_agent_postgres
cd ..
```

迁移命令读取现有环境配置，只执行尚未登记的迁移。服务启动和 `/health` 成功不代表数据库结构已与聊天代码同步；缺少迁移时聊天仍可能返回“AI 暂时离线”。

如果 Windows 阻止执行本地脚本，可以只对本次命令放开策略：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-dev.ps1
```

## AI token 预算

默认每个 Agent 累计上限为 64,000 token，单次任务（含 JEV 路由、工具循环和辅助模型调用）合计上限为 128,000 token。累计额度包括模型输入与输出；额度是上限，不会强制用满。

普通模型单次输出最多 4096 token，JEV 决策调用保持 256。当前歌曲与普通聊天为单次输入、输出及安全余量合计预留 12,288 token；工具与 WorldLine 调用预留 16,384 token。其中输出预留均为 4096，安全余量保持 256，既有来源配额不变。

已有 `ai-service/.env` 不会被示例配置自动覆盖。若仍配置 `LLM_MAX_OUTPUT_TOKENS=1024`，请将该项改为 `4096` 并重启 AI 服务，否则 SDK 仍使用较小的单次输出上限。显式设置更小额度仍会生效；从检查点恢复的旧任务继续保留原始任务额度及已记录用量，重新发送请求会使用新默认值。

## 停止

```powershell
.\stop-dev.ps1
```

停止脚本只关闭由 `start-dev.ps1` 启动并记录的进程，不会关闭启动前已经存在的 IDE 或终端进程。

## 日志与检查

运行日志保存在 `.dev/logs`，该目录不会提交到 Git。

只检查本地环境、不启动服务：

```powershell
.\start-dev.ps1 -ValidateOnly
```

检查启动脚本：

```powershell
.\scripts\test-dev-scripts.ps1
```

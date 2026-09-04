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

如果 Windows 阻止执行本地脚本，可以只对本次命令放开策略：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-dev.ps1
```

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

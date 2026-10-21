# MoodFlow

MoodFlow 是一个音乐播放与 AI 音乐助手项目，包含 React 前端、Spring Boot 后端、FastAPI / LangGraph AI 服务，以及网易云音乐 API 子模块。

| 模块 | 目录 | 默认开发端口 |
| --- | --- | ---: |
| React / TypeScript / Vite 前端 | `soul-music-room` | 5173 |
| Spring Boot 后端 | `music-backend` | 8080 |
| Python AI 服务 | `ai-service` | 8001 |
| 网易云音乐 API | `netease-music-api` | 3000 |

## 获取项目

```bash
git clone --recurse-submodules https://github.com/Yee-Ning/MoodFlow.git
cd MoodFlow
```

如果已经克隆项目，补齐子模块：

```bash
git submodule update --init --recursive
```

## 本地开发

需要 JDK 21、Maven、Node.js、npm 和 Python。先安装各模块依赖：

```powershell
npm --prefix soul-music-room ci
npm --prefix netease-music-api install
python -m venv ai-service/.venv
ai-service/.venv/Scripts/python.exe -m pip install -r ai-service/requirements.txt
```

参考根目录和 `ai-service` 中的 `.env.example` 配置本地环境，实际密钥与密码只保存在本地。AI 服务的模型、数据库和检索配置见 [AI 服务说明](ai-service/README.md)。启用 PostgreSQL 时，启动前按 [开发说明](DEVELOPMENT.md) 执行数据库迁移。模型权重、向量数据库、原始网页语料、生产部署数据和真实账号配置需另外准备。

在项目根目录用 PowerShell 启动或停止服务：

```powershell
.\start-dev.ps1
.\stop-dev.ps1
```

前端入口为 <http://localhost:5173>。环境检查和启动说明见 [DEVELOPMENT.md](DEVELOPMENT.md)，生产部署说明见 [部署文档](docs/ecs-ubuntu2204-deployment.md)。

## 验证

```powershell
python scripts/test-repo-hygiene.py
npm --prefix soul-music-room run build
npm --prefix soul-music-room run lint
mvn -f music-backend/pom.xml verify
cd ai-service
.venv/Scripts/python.exe -m pytest tests evals/tests
```

前端另有 `package.json` 中的 `test:*` 交互契约测试。部分历史评测依赖本地语料、模型或历史运行产物，执行前需要准备对应数据。

## 仓库内容

仓库保留源码、测试、依赖清单、必要配置示例、数据集与技术文档。开发报告、截图、工作区审查材料、运行日志、依赖安装目录和真实环境配置不纳入版本控制；生成报告的程序仍保留在源码中。

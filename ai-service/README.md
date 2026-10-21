# MoodFlow AI Service

FastAPI / LangGraph 音乐助手。在线入口为 `app/main.py`，提供聊天、WorldLine 音乐探索、记忆和进度事件 API。

## 模块

- `app/agent/foundation`：意图路由、上下文、任务执行与持续会话。
- `app/agent/handoff`：GeneralChat、Curator、Explorer 之间的协作。
- `app/agent/decision/jev/config`：在线路由所需的能力契约与策略配置。
- `app/rag`：语料处理、知识发布、索引和在线检索。
- `migrations`：PostgreSQL 与记忆数据迁移。
- `tests`：当前功能的回归测试；必要样本在 `tests/fixtures`。

## 本地开发

从项目根目录进入 `ai-service`：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

Linux / macOS 的虚拟环境 Python 路径为 `.venv/bin/python`。将 `.env.example` 复制为 `.env`，填写自己的模型服务配置。实际环境文件不提交到 Git。

```powershell
.venv/Scripts/python.exe -m scripts.migrate_agent_postgres
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

数据库迁移用于启用了 PostgreSQL 的环境。模型、数据库和知识索引需在相应功能启动前准备好；`/health` 返回成功只表示 HTTP 服务可访问。

## 检查

在 `ai-service` 目录中运行：

```powershell
.venv/Scripts/python.exe -m pytest tests
```

真实数据库、Redis 和模型测试使用独立的测试环境；不要将验收测试指向已有业务数据库。

## 历史研究代码

旧 Gold/Ragas/JEV 对比评测、人工审阅版本和按日期编写的修复工具，连同其专用测试，归档在 Git 标签 `archive/research-20261021`。需要复现时，在独立目录检出该版本并准备当时的外部数据。当前在线路由不再从历史评测目录读取配置。

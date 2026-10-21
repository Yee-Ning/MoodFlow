# 开发与测试

推荐先按 [README](README.md) 通过 Docker 运行完整环境。固定版本为 Python 3.12.10、Node.js 22.22.0 和 Java 21；Python 依赖在 `ai-service/requirements.lock.txt`，前端依赖在 `package-lock.json`。

## 自动化验证

前端：

```bash
npm --prefix soul-music-room ci
npm --prefix soul-music-room run build
npm --prefix soul-music-room run lint
npm --prefix soul-music-room run test:ai-layout
npm --prefix soul-music-room run test:ai-stream
npm --prefix soul-music-room run test:music-assets
npm --prefix soul-music-room run test:search
npm --prefix soul-music-room run test:memory
npm --prefix soul-music-room run test:ai-trigger
npm --prefix soul-music-room run test:worldline
```

后端：

```bash
mvn -f music-backend/pom.xml verify
```

Python：在虚拟环境内安装 `ai-service/requirements-dev.txt`。Linux CPU 环境先按 CI 中的步骤安装经过 SHA256 校验的 PyTorch wheel，再安装锁定依赖。Windows 开发环境可使用 PyTorch 官方对应平台的 wheel。

```bash
python -m venv ai-service/.venv
# 激活虚拟环境后执行：
python -m pip install -r ai-service/requirements-dev.txt
docker compose -f compose.test.yaml up -d --wait
```

测试数据库与 Redis 仅绑定本机的 55432 和 56379 端口，数据库存于临时内存文件系统。设置测试变量后，在仓库根目录运行：

```powershell
$env:DATABASE_URL='postgresql://postgres@127.0.0.1:55432/postgres'
$env:BUDGET_TEST_REDIS_URL='redis://127.0.0.1:56379/0'
python -m pytest -q
python scripts/test-repo-hygiene.py
python -m ruff check scripts/setup.py scripts/smoke.py scripts/tests deploy/local_runtime.py deploy/tests
docker compose -f compose.test.yaml down
```

Linux / macOS 使用 `export DATABASE_URL=...` 和 `export BUDGET_TEST_REDIS_URL=...`。测试不会读取开发机真实 `.env`；需要模型输出的单元测试使用受控替身。在线端到端验证另行使用 `scripts/smoke.py --chat`。

## 分服务调试

| 服务 | 开发入口 | 默认端口 |
| --- | --- | ---: |
| 网易云 API | `npm start`，在子模块内运行 | 3000 |
| Java 后端 | `mvn spring-boot:run`，在 `music-backend` 内运行 | 8080 |
| AI 服务 | `python -m uvicorn app.main:app --port 8001`，在 `ai-service` 内运行 | 8001 |
| 前端 | `npm run dev`，在 `soul-music-room` 内运行 | 5173 |

原有 `start-dev.ps1` / `stop-dev.ps1` 可管理 Windows 上的四个开发进程；它们要求已安装依赖和准备好数据库、模型、索引。根目录 `.env` 用于 Compose，分服务调试使用 `ai-service/.env.example` 配置 AI 服务，不要提交真实配置。PostgreSQL 迁移入口为 `python -m scripts.migrate_agent_postgres`。

## 本地初始化约定

`compose.yaml` 先执行数据库迁移，再按当前检索配置建立 `moodflow_local` 集合，最后启动 API 和前端。数据库密码通过连接参数传递，支持特殊字符。索引及 Hugging Face 下载缓存保存于 Docker volumes，后续启动会复用完整索引。

仅检查启动前提可运行 `python scripts/setup.py --check`。排查启动问题可查看 `docker compose ps -a` 和对应服务日志；不要删除 volume 来替代问题诊断。

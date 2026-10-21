# MoodFlow

音乐播放与 AI 音乐助手。React 前端、Spring Boot 音乐后端、FastAPI / LangGraph AI 服务通过同一个网页入口运行，支持音乐搜索、播放、对话和音乐关系探索。

## 本地运行

需要 Git、[Git LFS](https://git-lfs.com/)、Python 3.12 和支持 Linux 容器的 Docker Compose。建议至少为 Docker 分配 8 GB 内存，并预留 15 GB 磁盘空间。

```bash
git lfs install
git clone --recurse-submodules https://github.com/Yee-Ning/MoodFlow.git
cd MoodFlow
python scripts/setup.py
```

脚本会获取子模块和必要的分类模型，校验模型文件，并在缺少 `.env` 时从示例创建。已有 `.env` 会保留。

编辑根目录 `.env`，填写自己的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 和 `LLM_GENERATION_MODEL`。模型服务需要同时支持 OpenAI 兼容的 **Chat Completions** 和 **Responses API（JSON Schema 结构化输出）**。仅支持 Chat Completions 的服务不足以运行全部 AI 功能。

```bash
docker compose up --build -d
docker compose ps -a
```

全部服务就绪后访问 [localhost:8088](http://localhost:8088)。首次运行会下载 CPU PyTorch 和 Qwen 检索模型，并从仓库内的审核语料建立本地索引；根据网络和 CPU 性能可能需要较长时间：

```bash
docker compose logs -f prepare-index ai-service
python scripts/smoke.py
```

`python scripts/smoke.py --chat` 会额外发送一次真实 AI 请求，使用你配置的模型服务。网易云登录、曲库和歌曲可播放性取决于账号、地区和上游服务。

```bash
docker compose down
```

此命令停止服务并保留数据库、知识索引和模型缓存。配置、日志、缓存和安装依赖不会提交到 Git。更新代码后重新运行 `python scripts/setup.py` 和启动命令即可。

## 目录

| 目录 | 内容 |
| --- | --- |
| `soul-music-room/` | React / TypeScript / Vite 前端 |
| `music-backend/` | Java 21 / Spring Boot 音乐 API |
| `ai-service/app/` | 对话、路由、工具和知识检索 |
| `ai-service/tests/` | 当前功能测试与必要回归样本 |
| `ai-service/artifacts/intent_model/` | 分类模型推理文件与校验清单，权重使用 Git LFS |
| `ai-service/data/` | 知识语料、审核清单及数据处理测试样本 |
| `netease-music-api/` | 固定版本的网易云 API 子模块 |
| `deploy/`、`scripts/` | 容器构建、本地初始化和验证工具 |

开发和测试命令见 [DEVELOPMENT.md](DEVELOPMENT.md)，AI 模块说明见 [ai-service/README.md](ai-service/README.md)。历史评测、一次性审查工具及旧部署方案保存在 Git 标签 `archive/research-20261021`，不参与当前启动。

## 许可与来源

项目使用 [GPL-3.0-only](LICENSE)。第三方代码、模型和知识来源保留各自的许可与署名：分类编码器基于 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)，其 MIT 许可随模型保存；检索模型使用 [Qwen/Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)。前端原有来源说明见 [前端 README](soul-music-room/README.md)，网易云 API 的许可见其子模块。

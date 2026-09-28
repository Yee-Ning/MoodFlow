# MoodFlow AI Production Image：CPU Torch wheel 离线构建修复报告

日期：2026-09-28。范围仅为 `ai-service` Production Docker image 的依赖获取、构建和只读离线模型 smoke；未执行服务器部署、数据恢复、Worldline 修改、Eval 或 Production Compose cold boot。

## 结果与根因边界

此前在线构建从 PyTorch CPU CDN 得到的 Torch 内容 SHA256 为 `81e37ec35d7237dcf9fe5323739534abe5e47e16783dc308a71c55450f786975`，不等于冻结值 `a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260`；程序化宿主与 Docker build 网络后来还都收到 403、17 字节文本响应。原失败构建没有保存那次错误内容的完整 HTTP 元数据，因此无法证明具体是哪一层代理/CDN/传输节点造成历史 mismatch。可确定的是构建网络路径没有可靠提供正式 wheel；本次用户通过浏览器取得的官方 wheel 经本机再次计算 SHA 后与冻结值完全一致。离线构建不再请求 PyTorch CDN，原阻塞已解除。

| 对象 | SHA256 / 结果 |
| --- | --- |
| 冻结 expected / 官方 CPU index fragment | `a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260` |
| 此前在线构建 received | `81e37ec35d7237dcf9fe5323739534abe5e47e16783dc308a71c55450f786975`，被 pip 拒绝 |
| 本地官方 wheel 再校验 | `a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260`，196,253,793 字节，通过 |
| 镜像 dependency stage 再校验 | `sha256sum -c -` 输出 `OK` |

历史链路证据保留于 [此前构建失败](docs/deployment-preparation/ai_image_build_failure.json)、[宿主诊断](docs/deployment-preparation/torch_wheel_host_diagnostic.json)和 [Docker build 诊断](docs/deployment-preparation/torch_wheel_docker_diagnostic.json)；没有用历史错误 SHA 替换 expected，也没有安装错误文件。

## 可信离线安装结构

- [Production Compose](docker-compose.production.yml) 使用 `additional_contexts.wheelhouse: ./deployment-artifacts/wheelhouse`。该 BuildKit 本地 context 仅被 `COPY --from=wheelhouse` 读取，不在默认源码 context 中。`deployment-artifacts/` 仍由 `.gitignore` 忽略，`git ls-files -- deployment-artifacts/wheelhouse` 为 0；wheel 未进入 Git。
- [AI Dockerfile](deploy/ai.Dockerfile) 的依赖阶段固定 `python:3.12.10-slim-bookworm` 原 digest，复制精确的 `cp312/cp312/manylinux_2_28_x86_64` CPU wheel；先执行 SHA256 校验，再使用 `pip --isolated install --no-index --no-deps --find-links=/tmp/wheelhouse --require-hashes` 安装 `torch==2.14.0+cpu`。安装其他包前后均未从 PyTorch CDN 下载 Torch。
- [Production requirements](deploy/requirements.production.txt) 移除了旧的 PyTorch `--extra-index-url`，避免后续 resolver 再访问该 CDN；**文件来源配置有改动，所有包版本 pin 均未改动**，`ai-service/requirements.txt` 未改动。其余锁定依赖只从显式 PyPI index 安装。Torch 保留两层 SHA 验证（`sha256sum` 与 pip `--require-hashes`），没有使用 `--no-hash`、`--trusted-host` 或修改 expected。
- 最终 runtime stage 只复制安装结果 `/usr/local`，不复制临时 wheel archive；容器检查 `/tmp/wheelhouse` 和临时 Torch requirement 文件均不存在。现有生产依赖文件是版本锁，并非为每个非 Torch 包提供独立 SHA 的全量 hash lock；本轮未把这种既有约束误报为全包 `--require-hashes`。

## 完整构建与容器校验

`docker compose --env-file .env.example -f docker-compose.production.yml build ai-service` **成功**，生成 `moodflow/ai-service:worldline-v1-fe24b04`。构建日志中本地 wheel 校验为 `OK`、本地 pip 安装成功，PyTorch CDN URL 命中数和远程 `Downloading torch` 行数均为 0；dependency stage 与最终 runtime stage 的 `pip check` 都为 `No broken requirements found.`

| 成品容器检查（`--network none`） | 结果 |
| --- | --- |
| `python --version` | `Python 3.12.10` |
| `import torch; torch.__version__` | `2.14.0+cpu` |
| `torch.cuda.is_available()` | `False` |
| `python -m pip check` | `No broken requirements found.` |
| 逐项核对 production requirements | 104 条 pin 全部匹配已安装版本；0 个缺失/冲突 |
| CUDA 相关发行包 | 0 个 `nvidia-*` / `triton` |

因此 `production_dependency_lock_verified=true` 表示当前 Linux 镜像中所有 104 个版本 pin 已逐项核对并通过 pip 依赖完整性检查；不表示现有非 Torch 依赖已拥有逐文件 SHA 锁。

## 冻结资产离线加载

用冻结 production 资产的只读 bind mounts，在 AI 容器 `--network none` 下运行 smoke；镜像的 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，retrieval 配置的 `local_files_only=true`、`device=cpu` 均已断言。没有下载新模型、运行推理业务链或执行 Semantic Eval。冻结 manifest 校验通过：Qwen 缓存 6 个文件，Worldline/intent/index 资产 20 个文件，release identity 为 `knowledge-v2-daa965c10984eefa`。其中 Qwen `model.safetensors` SHA 为 `0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd`，intent `classifier_head.pt` SHA 为 `2ca187ac89e8fd269ac20da7cc3564c91c9d0413e98f20b622aa890faac55b84`，均与冻结 manifest 一致。

| 模型 | 结果 | 加载耗时 |
| --- | --- | ---: |
| Qwen/Qwen3-Embedding-0.6B，revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | tokenizer + model 加载成功 | 5.453 s |
| intent classifier | encoder + head 加载成功 | 0.337 s |

整个 smoke 含 SHA 验证耗时 31.704 s；Linux 进程峰值 RSS 672,010,240 字节（约 641 MiB）。这是本次本机容器 smoke 的观测值，并非服务器容量保证。

## 五个生产镜像

以下为本地 `docker image inspect` 返回的 Linux/amd64 content-addressed image ID / RepoDigest；尚未 push 到 registry。

| 镜像 tag | 本地 image ID / digest |
| --- | --- |
| `moodflow/frontend:worldline-v1-fe24b04` | `sha256:8ee212427b6a51a595785ce75819c1b74660831a8799434b61ca585805dff888` |
| `moodflow/netease:worldline-v1-fe24b04` | `sha256:6051bbdfd1ad7b1534d85d0a62ff3f82a029a5f78a87e90fec34344033ece7cf` |
| `moodflow/java:worldline-v1-fe24b04` | `sha256:14c50f6b0447cf4a577b57e5609b2cccb488c507b3589b46f2db5a32cff33e82` |
| `moodflow/qdrant:worldline-v1-fe24b04` | `sha256:d55a1602d079ded592bb0199bac55a49accbb94918c429dc5e29890b197ca1d6` |
| `moodflow/ai-service:worldline-v1-fe24b04` | `sha256:6bc9b8d18244037c213deeba6fcf5bafb7f48f7dbe1a06ed753c5ddd120d869e` |

## 验证与边界

`ai-service/.venv/Scripts/python.exe -m pytest deploy/tests -q`：37 passed，包含新增离线 Torch build 合约检查；`ruff check`：通过；`docker compose --env-file .env.example -f docker-compose.production.yml config --quiet`：通过。另用独立临时 wheelhouse 中的损坏小文件执行一次 `docker build --target dependencies --no-cache` 失败路径验证：镜像内 `sha256sum` 在 pip 安装前报 `FAILED`，构建退出码 1，原正式 wheel 与已成功的 AI image 未被修改。用户已有的无关工作区修改未触碰。未执行服务器部署、Production Compose cold boot、数据库/Qdrant restore、Worldline 功能修改或 Eval。

```text
torch_wheel_integrity_pass=true
ai_image_build_pass=true
production_dependency_lock_verified=true
offline_model_load_pass=true
all_production_images_built=true
```

按本轮停止条件，五项全部通过后立即停止。

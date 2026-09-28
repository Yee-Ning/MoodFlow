# MoodFlow AI Production Image：Torch wheel 完整性阻塞报告

日期：2026-09-28。范围仅为 `ai-service` 生产镜像的 CPU Torch wheel 获取与完整性验证；未执行服务器部署、数据恢复、Production Compose cold boot、Worldline 修改或 Eval。

## 结论与唯一阻塞

**当前无法可信获取冻结的 CPU Torch wheel。** 宿主机与 Linux Docker build 容器分别从 [PyTorch 正式 CPU index](https://download.pytorch.org/whl/cpu/torch/) 解析出完全相同的目标链接；对 `download-r2.pytorch.org` 发起的 HTTPS GET 均得到 **HTTP 403、`text/plain; charset=UTF-8`、17 字节**，没有重定向，也没有 wheel 内容。两个响应的 `Content-Length` 与实际大小一致，因此这次复现是访问被拒绝，不是传输截断。17 字节错误正文 SHA256 为 `2938e9f1284180959e33ab1718d0793a72ff6e4cdb8108c34dcd14e69446de5c`；它不是 wheel，更不能当作可接受的新 hash。

此前完整 AI build 的 pip 报错是 expected `a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260`、received `81e37ec35d7237dcf9fe5323739534abe5e47e16783dc308a71c55450f786975`。该历史 received 值不同于本次 17 字节错误正文的 SHA。原失败构建未保存响应头与原始文件，**不能仅凭现有证据断言历史 SHA mismatch 究竟由代理、CDN、缓存或传输错误造成**。已证实的当前根因/单一部署阻塞是正式 wheel URL 在宿主和 Docker build 网络路径上均返回 403；在取得匹配正式 SHA 的 wheel 之前，不执行安装或完整 AI build。

| 对象 | SHA256 | 解释 |
| --- | --- | --- |
| 冻结预期 / 正式 index fragment | `a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260` | 唯一可接受的 wheel SHA |
| 此前 pip 构建收到的内容 | `81e37ec35d7237dcf9fe5323739534abe5e47e16783dc308a71c55450f786975` | 历史 mismatch，未安装 |
| 本次两侧 HTTP 403 正文 | `2938e9f1284180959e33ab1718d0793a72ff6e4cdb8108c34dcd14e69446de5c` | 17 字节文本错误响应，非 wheel |

可复现原始证据：[宿主诊断](docs/deployment-preparation/torch_wheel_host_diagnostic.json)、[Docker build 容器诊断](docs/deployment-preparation/torch_wheel_docker_diagnostic.json)、[此前 pip 失败记录](docs/deployment-preparation/ai_image_build_failure.json)。诊断脚本为 [deploy/fetch_torch_wheel.py](deploy/fetch_torch_wheel.py)：只接受正式 HTTPS index 中带冻结 SHA fragment 的精确 wheel/tag，只允许 PyTorch 官方下载域名；下载到 `.partial` 后计算 SHA，只有匹配才提升为 `.whl`。诊断 URL 会剔除 userinfo/query，不记录代理凭据。该脚本**尚未接入生产 Dockerfile**，因为正式 artifact 无法通过验证；不存在绕过校验的“修复”。

复现入口为 `python deploy/fetch_torch_wheel.py --output-dir <干净临时目录>/host --report <干净临时目录>/host.json`。容器诊断在临时 Dockerfile 中使用同一 `python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db` 基础镜像，`COPY` 同一脚本并运行相同参数，然后用 `docker build --no-cache --platform linux/amd64 --progress=plain --output type=local` 导出诊断 JSON；未执行 pip install。宿主首次返回 403 后，为补足状态码/长度/正文 SHA 复核了小型错误响应；未反复下载完整 wheel。

## 依赖链与网络审计

- 当前 [AI Dockerfile](deploy/ai.Dockerfile) 的基础镜像为 digest 固定的 `python:3.12.10-slim-bookworm`，目标平台是 Linux x86_64。当前生产 requirements pin 为 `torch==2.14.0+cpu`，正式 CPU extra index 是 `https://download.pytorch.org/whl/cpu`；`ai-service/requirements.txt` 的范围约束为 `torch>=2.5,<3.0`。目标文件明确是 `torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl`。没有切换到 CUDA、Windows、macOS、ARM wheel，也没有修改 Torch/Python pin。
- Compose 的 AI build 没有自定义 build args；Dockerfile 没有 `ARG`、`PIP_INDEX_URL` 或 `PIP_EXTRA_INDEX_URL` override。宿主进程中未发现 `HTTP(S)_PROXY`、`PIP_INDEX_URL`、`PIP_EXTRA_INDEX_URL`、`PIP_TRUSTED_HOST` 环境变量。宿主 Python 3.12.10 / pip 25.0.1；本次 build 诊断容器 Python 3.12.10、Linux x86_64。容器 pip 版本尚未复核，因未进入安装流程。
- 宿主 WinINET 系统代理启用，Python 系统代理发现 `http/https/ftp` 项；WinHTTP 为 direct。Docker daemon 报告 HTTP/HTTPS/NO_PROXY 配置存在，但诊断 build 进程没有显式代理环境变量。两条路径的底层转发是否由同一代理/CDN 节点承担，现有证据无法确定；未读取或输出代理地址/认证信息。
- 诊断请求使用标准 TLS 校验、`Cache-Control: no-cache`、`Pragma: no-cache`、`Accept-Encoding: identity`，无 `Range`/resume 请求。两侧正式 index 请求成功；wheel 请求在目标 `download-r2.pytorch.org` 上直接 403，redirect chain 为空。当前响应不是 HTML，也不是 wheel；不能把它归因于 HTTP range 或 connection reset。
- 原 AI Dockerfile 使用 `pip install --no-cache-dir`，失败的 RUN 层不会成为成功镜像层。本次诊断使用 Docker `--no-cache`，且下载器不使用 pip cache。没有证据表明旧 pip/Docker cache 导致当前 403，故没有执行全局或定向 prune；PostgreSQL/Qdrant volume 与其他用户数据均未触碰。

## 构建、模型和镜像状态

未修改 `deploy/requirements.production.txt` 或 `ai-service/requirements.txt`，未改 expected SHA，未使用 `--no-hash`、`--trusted-host`，未安装不匹配内容。现有 pip hash validation 仍启用；新增诊断脚本也 fail closed。由于正式 wheel GET 在宿主与 build 容器均失败，按本轮停止条件**未重复运行完整 `docker compose ... build ai-service`**，也未运行 AI 容器内 `torch`、`pip check`、冻结 Qwen3-Embedding-0.6B / intent classifier 离线加载、artifact SHA/内存/延时 smoke。它们均为**未验证**，不能宣称通过。冻结模型资产及 manifest 未改动。

已存在的四个生产镜像按本地 content-addressed image ID 记录如下；这是本地镜像 digest，不冒充 registry RepoDigest。AI image 不存在，故没有可锁定的 AI digest。

| 镜像 tag | 本地 image ID |
| --- | --- |
| `moodflow/frontend:worldline-v1-fe24b04` | `sha256:8ee212427b6a51a595785ce75819c1b74660831a8799434b61ca585805dff888` |
| `moodflow/netease:worldline-v1-fe24b04` | `sha256:6051bbdfd1ad7b1534d85d0a62ff3f82a029a5f78a87e90fec34344033ece7cf` |
| `moodflow/java:worldline-v1-fe24b04` | `sha256:14c50f6b0447cf4a577b57e5609b2cccb488c507b3589b46f2db5a32cff33e82` |
| `moodflow/qdrant:worldline-v1-fe24b04` | `sha256:d55a1602d079ded592bb0199bac55a49accbb94918c429dc5e29890b197ca1d6` |
| `moodflow/ai-service:worldline-v1-fe24b04` | **未生成** |

## 验证与后续入口

`ai-service/.venv/Scripts/python.exe -m pytest deploy/tests -q`：36 passed，覆盖正式链接/host/hash 校验、正确 wheel 提升、错误 hash 和 HTTP 403 拒绝以及诊断 URL 去敏。`python -m compileall -q deploy/fetch_torch_wheel.py`：通过。`docker compose --env-file .env.example -f docker-compose.production.yml config --quiet`：通过。无服务器部署或 Compose 启动。

后续唯一前提是在可访问正式 PyTorch CPU artifact 的可信网络路径上，执行一次同版本同目标的下载并得到上述冻结 SHA；再考虑将已验证 wheel 的获取/校验/本地安装纳入 Docker dependency-fetch 阶段。若网络路径仍返回 403，应先由网络/CDN/代理提供方排查该正式 URL 的访问策略，不改变 lock 或放宽校验。本轮已停止重复下载和安装。

```text
torch_wheel_integrity_pass=false
ai_image_build_pass=false
production_dependency_lock_verified=false
offline_model_load_pass=false
all_production_images_built=false
```

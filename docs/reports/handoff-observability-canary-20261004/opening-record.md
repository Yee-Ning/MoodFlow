# 最终双开关重开记录

West 已授权双开关翻转。用户随后明确取消“两小时或 100 请求”的等待，本次只做重开后的短硬验收；日志接收器继续运行。

- 生产代码 HEAD：`6667b70ecc1ad27786559bacb389f6f65d42098b`；应用镜像未改变。
- 北京时间 2026-10-04 15:49:20，用既有 `handoff-control.py open` 设置 `JEV_HANDOFF_ROUTER_ENABLED=true`、`HANDOFF_CANARY_ENABLED=true`；15:49:54 healthy。
- 方式：root 私有 `/opt/moodflow-runtime/handoff-canary-reopen-20261004/switches.yml` 的两个 AI environment 键；既有 Compose base + override，以 `up -d --no-deps --no-build --force-recreate ai-service` 加载相同镜像。
- Scope 检查：其他 Compose 配置、服务镜像、挂载、排除两键后的环境指纹未变；其他六服务容器未变。foundation、10% session、router shadow true、Guard threshold 0.8 未变；secrets 未改。
- 15:51:36–15:52:06：新 thread 的 Single / Multi / non-canary 三条验收全绿；有效 V5 候选各 1，Guard 2，accepted 跨 agent 转交 2，trace 17；对照为 Foundation+Hybrid。
- 15:52:54：双开关 true；六常驻 healthy，migrate exited 0；源码与生产 Git hash 一致。

本次重开前发生观察驱动异常，15:41:10 已自动回滚。原因是主机 Python 3.10.12 不支持驱动所用 Docker 九位小数时间戳解析格式；不是业务阈值超标。原驱动退出后未重启；采集服务仍 active。

证据：[开闸配置](open-final.json)、[硬验收](hard-gates-final.json)、[最终状态](final-snapshot.json)、[自动回滚](automatic-rollback.json)、[驱动异常](watch-failure.json)、[取消观察](observation-stop.json)。

回滚仍使用 `/opt/moodflow-runtime/handoff-canary-reopen-20261004/rollback.sh`，同时关闭双开关、同镜像加载，并复核 healthy 与 Foundation+Hybrid。有限观察驱动不再执行自动阈值回滚；持续采集及 fallback 文件告警保持运行。

没有 push。本次未完成两小时统计观察，真实 Replay 样本不足，详见最终报告。

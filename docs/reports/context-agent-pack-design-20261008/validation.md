# Per-Agent ContextPack 设计轮验证记录

日期：2026-10-08。分支：`codex/context-agent-pack-design`。基线：`d1ff818c9318b532dfe5de81fb0c5cf2865a789e`。

主交付：[Per-Agent ContextPack 设计](D:/code/ProjectsAll/MoodFlow/docs/architecture/context-agent-pack-design-20261008.md:1)。

## 1. 交付范围与 Gate

Gate 0 先只读审计、输出五项表并停止；West 随后确认按实际现状细化设计。新增文件仅为主设计文档和本验证记录，没有实现代码、测试代码、配置或原契约修改。

设计包含三份 recipe 的来源、配额和代码 Select 策略，四条边的 Envelope 消费与生命周期，L2 组装检查／原 reserve 边界，prompt 版本和严格 manifest 兼容设计，与 entry pack 的复用关系，以及 5 个后续实现 prompt 和验收矩阵。

## 2. 已运行检查

| 命令／检查 | 实际结果 |
| --- | --- |
| `git branch --show-current`、`git rev-parse HEAD`（提交前） | 新设计分支；HEAD 与指定 Phase 3 基线完全相同 |
| `ai-service/.venv/Scripts/python.exe -`（PowerShell 传入内存校验脚本，未写入仓库） | 下述 12 项文档校验全部通过 |
| Python AST 对照原 `ContextPolicy`／`POLICIES` | 四类 L1 total、output reserve、safety、input 上限完全匹配；未把模型输出上限误当 Pack 原预留 |
| Python JSON 对照原 `context_policy.json` | L2=16000／L3=32000，user_profile=240、history_memory=600，与设计引用一致 |
| MarkdownIt CommonMark＋table 解析 | 主设计 16 张表、1 个 Mermaid 源码块；没有未闭合代码围栏 |
| 主设计链接存在性和行号检查 | 12 个本地证据链接全部存在，行号有效 |
| `git diff --cached --check` | 本任务两份文档通过空白检查；未把无关工作树差异纳入提交 |
| `git diff --cached --name-status`／暂存 diff 审阅 | 暂存只含本任务两份新增 Markdown 文档 |
| 原用户修改保护核对 | 原 5 项 tracked 工作树差异保持；已存在文件 SHA-256 未变，原删除状态未变 |
| `git for-each-ref --format='%(refname) %(objectname)' 'refs/heads/codex/redis*'` 对照快照 | Redis 五个特性分支及 infra 分支的 ref 均未改变 |

内存文档校验的 12 项为：UTF-8 无 BOM、无尾随空白、无 TODO/TBD／未填写标记、围栏配对、交付章节齐全、证据链接有效、L1 AST 数值／算术匹配、L2/L3 与原来源配额匹配、四条转交边齐全、依赖／范围明确、Markdown 表格解析通过、Mermaid 源码围栏存在。

主设计检查时为 339 行、24,297 字符。字符／行数仅记录文档版本，不代表功能测试数量。Mermaid 检查覆盖源码围栏和 Markdown 解析，没有运行图像渲染。

## 3. 未运行与后续限制

本轮没有代码或运行配置改动，因此未执行 pytest、前端构建、Ruff 或功能／视觉回归；不引用其他分支的通过数量作为本轮功能验收。没有环境阻止文档校验，没有安装依赖、访问线上模型／索引、push 或部署。

新来源软配额是有基线量级依据的设计初值，仍需后续离线 eval。运行时正确性，包括 Envelope 消费、私有历史归属、strict manifest 新旧读取、L2 降级／恢复，以及正式 RAG 接口与成本边界，均须在后续实现轮用测试验证。主设计第 10、11 节已经给出实现切分和完整验收要求。

工作树原有无关报告修改／删除和未跟踪资料保留。未通过扩大暂存范围、修正无关空白或回退文件取得“干净工作树”。

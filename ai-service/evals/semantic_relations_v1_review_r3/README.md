# Semantic Relations v1 review r3 — pending coverage expansion

新增 10 条待审核真实来源候选：Era/Scene/Influence negation 各 1，conflict 各 1；Influence 两组 forward/reverse 各 2 条。复用当前 v1 schema、来源快照契约、ReplayInput 和 load_suite。schema.json 与 r2 完全相同，未改 runtime、algorithm、metric 或 runner 默认路径。

- 人工审核入口：human_review_pack.md；待审核视图：pending_cases.jsonl。
- source_audit.json：9 个新来源的作者/说话人、日期、scope、上下文与核验限制；两项旧来源复用。
- review_checklist.json：绑定冻结 label hash 的机器可读待审清单，没有人工决定或 receipt。
- gold.jsonl = r2 原 40 行逐字保留 + 新 10 pending；inputs.jsonl / sources.jsonl 同样只追加。approved_gold.jsonl 仍为原 12 条。Gold/replay/source 均为 evaluation-only，未写入 runtime index / production data。
- receipts、excluded_cases、r1/r2 审核记录原样保留；protected_artifacts.json 锁定旧数据及旧 failure 报告。
- manifest.json 冻结核心文件及所有审核材料；新 pending 不计入 approved_real_baseline，synthetic 不晋升。

冲突正例意味着保留争议与来源归因，不意味着事实定案。Era 原始 release 1959/1960 跨 decade，用两条带 temporal_conflict 的互斥候选表达，两条都需要双方证据，年份=null、temporal_precision=unknown；这是待审核的表示方案。Blur 候选可能是政治品牌与社会场景的范围差异，必须由人核对后决定 APPROVE 或 REJECT。Cluster 候选保留意大利原文及作者判断/成员限定否认的区别。不得生成或翻译替换 exact span 以帮助算法通过。

反向 Influence negative 只拒绝所询方向，不推断全局无反向影响；正向控制复用旧 evidence，同对 documents 完全相同。配对和旧正例有相关性，不是独立随机样本。冻结 replay 是人工 annotation，不是线上检索/自动抽取评测；当前没有新的 performance baseline。

本轮只做数据完整性检查与既有功能回归，evaluation_run=false。不要运行最终 Evaluation、优化算法或覆盖旧 freeze。后继版本仅在收到本包明确人工决定后新增绑定 label hash 的 receipt，复用原 label/input/source，并继续保留 REJECT 历史。

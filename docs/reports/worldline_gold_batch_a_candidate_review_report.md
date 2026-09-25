# WorldLine Gold Dataset V1 Batch A Candidate Review Preparation Report

## 1. 本阶段结果

Batch A 候选审核工作流已经建立。当前共有 14 个 discovery candidates：

| 区域 | Candidate 数量 | Primary | Backup |
|---|---:|---:|---:|
| 华语 | 5 | 4 | 1 |
| 欧美 | 5 | 3 | 2 |
| 日本 | 2 | 2 | 0 |
| 韩国 | 2 | 1 | 1 |
| 合计 | 14 | 10 | 4 |

全部候选满足：

- `candidate_status=pending_review`。
- `is_gold=false`。
- `relation_type=associated_with`。
- target genre 来自 Registry V2。
- 没有 accepted/rejected 标签。
- 没有自动填写 evidence、catalog ID 或最终 outcome。

本阶段没有创建正式 `worldline_gold_v1.jsonl`，也没有创建 approved Gold case。

## 2. 新增文件

| 文件 | 用途 |
|---|---|
| `ai-service/evals/worldline_gold_v1/README.md` | 工作区边界、目录结构和命令 |
| `ai-service/evals/worldline_gold_v1/candidate_review/candidate_pool.jsonl` | 14 个 pending discovery candidates |
| `ai-service/evals/worldline_gold_v1/candidate_review/candidate_review_workbook.md` | 生成的人工审核工作包 |
| `ai-service/evals/worldline_gold_v1/candidate_review/candidate_exclusions.jsonl` | 初始为空的人工排除日志 |
| `ai-service/evals/worldline_gold_v1/source_review/source_review_checklist.md` | Source 和 evidence 审核清单 |
| `ai-service/evals/worldline_gold_v1/identity_review/identity_review_checklist.md` | knowledge → catalog identity 审核清单 |
| `ai-service/evals/worldline_gold_v1/templates/worldline_gold_candidate_template.json` | 单个候选的 pending 审核模板 |
| `ai-service/evals/worldline_gold_v1/templates/candidate_exclusion_template.json` | 排除记录模板 |
| `ai-service/evals/worldline_gold_v1/candidate_review_tools.py` | 工作区、记录、span 和 hash 校验；工作包生成 |
| `ai-service/evals/tests/test_worldline_gold_candidate_review.py` | 候选准备工作流测试 |

所有实现都位于 Evaluation-only 目录，没有修改 WorldLine、Semantic Expansion、Genre Executor、RAG、Knowledge Base 或数据库。

## 3. Candidate Workflow

```text
candidate_pool.jsonl
  ↓
generate-workbook
  ↓
人工 Source Review
  ↓
人工 Identity Review
  ↓
validate-record
  ↓
仍保持 pending_review / final_outcome=null
  ↓
等待后续 Owner Review 指令
```

工作包的每个候选包含：

- Candidate ID、名称、待核验 aliases 和区域。
- Registry V2 Genre hypothesis。
- Source review 空白项。
- Identity review 空白项。
- `pending` review status。
- 明确的“Gold status not assigned”。

## 4. Candidate Pool

### 4.1 华语

- 周杰伦：C-pop hypothesis，primary。
- 蔡依林：C-pop hypothesis，primary。
- G.E.M. 邓紫棋：C-pop hypothesis，primary。
- 陈奕迅：C-pop hypothesis，primary。
- MC HotDog：Hip-Hop hypothesis，backup。

### 4.2 欧美

- Kendrick Lamar：Hip-Hop hypothesis，primary。
- Nas：Hip-Hop hypothesis，primary。
- Beyoncé：R&B hypothesis，primary。
- Alicia Keys：R&B hypothesis，backup。
- Common：Hip-Hop hypothesis，backup。

### 4.3 日本

- Nujabes：Jazz Hip-Hop hypothesis，primary。
- 宇多田光：R&B hypothesis，primary。

### 4.4 韩国

- Epik High：Hip-Hop hypothesis，primary。
- Dynamic Duo：Hip-Hop hypothesis，backup。

这些关系全部是 discovery hypotheses，不能作为音乐事实或 Gold 标签引用。

## 5. Review 流程

### 5.1 生成和校验

在 `ai-service` 目录运行：

```powershell
.\.venv\Scripts\python.exe evals\worldline_gold_v1\candidate_review_tools.py validate
.\.venv\Scripts\python.exe evals\worldline_gold_v1\candidate_review_tools.py generate-workbook
```

人工填写单条记录后运行：

```powershell
.\.venv\Scripts\python.exe evals\worldline_gold_v1\candidate_review_tools.py validate-record `
  --record evals\worldline_gold_v1\candidate_review\records\<candidate>.json
```

工具会检查：

- Candidate pool 必须正好 14 条且 ID 唯一。
- 所有候选必须保持 `pending_review` 和 `is_gold=false`。
- Relation 必须是 `associated_with`。
- Genre ID、canonical name 和 Registry version 必须匹配。
- 单条记录不能包含 final outcome。
- 已填写 Evidence 的 exact span、offset 和 SHA256 必须一致。
- Resolved identity 必须同时具有 knowledge ID 和 catalog reference。
- Exclusion reason 必须来自允许枚举，并具有 reviewer 和 review time。

### 5.2 人工审核顺序

1. 从 primary candidates 开始定位来源。
2. Relation Reviewer 使用 Source checklist 核对直接证据。
3. Identity Reviewer 独立核对 knowledge Artist 和 catalog Artist。
4. 工具校验来源快照、span、hash 和结构。
5. 不符合当前范围的候选由人工写入 exclusion log。
6. Backup candidates 用于替换证据或身份无法通过的 primary candidates。
7. 所有记录在本阶段继续保持 pending，等待后续 Owner Review。

## 6. Source 审核规则

允许优先级：

1. 可由 manifest 和 hash 复原的项目 Knowledge release 来源。
2. Artist、唱片公司、发行方和音乐机构官方页面。
3. 有编辑责任的音乐媒体、机构或音乐百科。
4. 公开知识数据库用于 identity 辅助核验。

明确禁止：

- 搜索摘要或 knowledge panel。
- 模型回答。
- 无出处聚合页。
- 社交媒体短句作为唯一证据。
- 从 WorldLine 当前输出反推 Gold。
- 把 Song/Album Genre 自动提升为 Artist Genre。

来源必须可冻结为有限上下文，exact span 必须逐字存在，SHA256 必须基于未归一化的 UTF-8 evidence text。

## 7. Identity 审核规则

Identity Reviewer 必须核对：

- Canonical name 和经过来源确认的 aliases。
- Artist 地区或市场信息。
- 至少一项代表作品。
- 另一个可区分字段，如专辑、组合身份或简介。
- Provider Artist 页面和 raw catalog ID。
- 同名 Artist、个人/组合及成员混淆。
- Node ID 是否等于 `<provider>:artist:<entity_id>`。

Preparation identity status 只允许：

- `pending`。
- `resolved`。
- `ambiguous`。
- `unresolved`。

不得因为候选排名或概率选择 catalog ID，不得创建临时 catalog ID。

## 8. Candidate Exclusion Log

`candidate_exclusions.jsonl` 当前为空。只有人工审核后才能增加记录，允许原因：

- `genre_not_registered`。
- `weak_source`。
- `no_direct_evidence`。
- `identity_ambiguous`。
- `catalog_missing`。

Exclusion 只是准备期工作记录，不进入 Gold Dataset 或 Real Gold Metrics。

## 9. 测试与验证

执行结果：

```text
pytest evals/tests/test_worldline_gold_candidate_review.py -q
5 passed

ruff check evals/worldline_gold_v1/candidate_review_tools.py \
  evals/tests/test_worldline_gold_candidate_review.py
All checks passed

candidate_review_tools.py validate
candidate_count=14, pending_count=14, exclusion_count=0
```

测试覆盖：

- 14 个指定候选和四个区域覆盖。
- 全部候选保持 pending。
- 提前写入 accepted 状态会被拒绝。
- 工作包不会产生 accepted/rejected 结论。
- Exact span 与 hash 校验。
- 已填写 review record 仍必须保持 `final_outcome=null`。

## 10. 当前限制

- 尚未采集任何外部真实来源。
- 尚未冻结 Source Ledger 内容。
- 尚未核验任何 catalog ID。
- 尚未确认任何 Artist–Genre relation。
- Existing Knowledge V2 只作为后续 source discovery 种子，本阶段没有把其中 claim 自动提升为 Gold。
- Candidate aliases 也属于待审核信息，不能直接写入正式 Gold。
- Exclusion log 为空，不代表所有候选都会通过。
- 当前工具用于 preparation validation，不负责生成正式 Gold JSONL。

本阶段已停止。下一步应由人工开始 Source Review 和 Identity Review；在得到明确审核结果前，不生成 Gold Dataset。

# WorldLine Gold Dataset V1 Batch A Review Workspace

> 本工作区只用于人工审核 14 个 discovery candidates。所有 relation、evidence 和 identity 状态均为 pending；本文不包含 Gold 标签或最终 evidence。

## 1. 使用方式

每个 candidate 依次完成：

1. 按 Source Collection Template 记录可复现来源。
2. 回答该 candidate 的 relation review questions。
3. 按 Identity Review Template 核对 knowledge Artist 与 catalog Artist。
4. 更新 `candidate_review_tracking.jsonl` 中三个状态和 notes。
5. 本阶段不填写 accepted/rejected，不填写最终 Gold outcome。

## 2. Candidate Summary

| Candidate ID | Artist | Region | Proposed Genre | Relation | Priority |
|---|---|---|---|---|---|
| `wl-candidate-v1-jay-chou` | 周杰伦 | cjk_chinese | C-pop | associated_with | primary |
| `wl-candidate-v1-jolin-tsai` | 蔡依林 | cjk_chinese | C-pop | associated_with | primary |
| `wl-candidate-v1-gem` | G.E.M. 邓紫棋 | cjk_chinese | C-pop | associated_with | primary |
| `wl-candidate-v1-eason-chan` | 陈奕迅 | cjk_chinese | C-pop | associated_with | primary |
| `wl-candidate-v1-mc-hotdog` | MC HotDog | cjk_chinese | Hip-Hop | associated_with | backup |
| `wl-candidate-v1-kendrick-lamar` | Kendrick Lamar | western | Hip-Hop | associated_with | primary |
| `wl-candidate-v1-nas` | Nas | western | Hip-Hop | associated_with | primary |
| `wl-candidate-v1-beyonce` | Beyoncé | western | R&B | associated_with | primary |
| `wl-candidate-v1-alicia-keys` | Alicia Keys | western | R&B | associated_with | backup |
| `wl-candidate-v1-common` | Common | western | Hip-Hop | associated_with | backup |
| `wl-candidate-v1-nujabes` | Nujabes | japanese | Jazz Hip-Hop | associated_with | primary |
| `wl-candidate-v1-hikaru-utada` | 宇多田光 | japanese | R&B | associated_with | primary |
| `wl-candidate-v1-epik-high` | Epik High | korean | Hip-Hop | associated_with | primary |
| `wl-candidate-v1-dynamic-duo` | Dynamic Duo | korean | Hip-Hop | associated_with | backup |

## 3. Candidate Review Sheets

### 3.1 周杰伦

- Candidate ID: `wl-candidate-v1-jay-chou`
- Region: `cjk_chinese`
- Proposed Genre: `genre:c_pop / C-pop`
- Relation Type: `associated_with`

Expected review questions:

- 当前 Knowledge V2 原文是否直接描述周杰伦本人属于华语流行音乐，而不只是其作品或市场影响？
- `C-pop` mapping 是否来自原文语义，而不是仅因 Artist 使用中文演唱？
- `周杰伦`、`周杰倫`、`Jay Chou` 是否能定位到同一 knowledge identity？
- Catalog Artist 页面是否能通过代表专辑或歌曲唯一确认？

### 3.2 蔡依林

- Candidate ID: `wl-candidate-v1-jolin-tsai`
- Region: `cjk_chinese`
- Proposed Genre: `genre:c_pop / C-pop`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接把蔡依林本人归入 C-pop，而不只是称其为华语市场歌手？
- 来源是否具有明确 publisher 和可冻结上下文？
- `蔡依林` 与 `Jolin Tsai` 是否由身份来源明确关联？
- Catalog 页面是否与其代表作品一致且不存在同名 Artist 冲突？

### 3.3 G.E.M. 邓紫棋

- Candidate ID: `wl-candidate-v1-gem`
- Region: `cjk_chinese`
- Proposed Genre: `genre:c_pop / C-pop`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接支持 Artist–C-pop 关系，而不只描述流行度或语言？
- `G.E.M.`、`邓紫棋`、`鄧紫棋`、`Gloria Tang` 是否全部有可靠 alias 依据？
- Catalog 中 stage name 与本名是否对应同一 Artist 页面？
- 名称标点和空格差异是否会造成重复 identity？

### 3.4 陈奕迅

- Candidate ID: `wl-candidate-v1-eason-chan`
- Region: `cjk_chinese`
- Proposed Genre: `genre:c_pop / C-pop`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否明确描述陈奕迅本人及其 Genre？
- Cantonese/Mandarin repertoire 是否被错误当作 Genre 证据？
- `陈奕迅`、`陳奕迅`、`Eason Chan` 是否属于同一 identity？
- Catalog 页面是否通过代表作品和地区信息唯一确认？

### 3.5 MC HotDog

- Candidate ID: `wl-candidate-v1-mc-hotdog`
- Region: `cjk_chinese`
- Proposed Genre: `genre:hip_hop / Hip-Hop`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接把 MC HotDog 描述为 Hip-Hop Artist？
- `姚中仁` 是否由权威来源明确关联到 stage name？
- Catalog 页面是否区分 Artist、歌曲和包含“热狗”名称的其他结果？
- 该候选的 evidence 是否足以支持 executable Hip-Hop 测试，而不依赖模型常识？

### 3.6 Kendrick Lamar

- Candidate ID: `wl-candidate-v1-kendrick-lamar`
- Region: `western`
- Proposed Genre: `genre:hip_hop / Hip-Hop`
- Relation Type: `associated_with`

Expected review questions:

- 机构或编辑来源是否直接称其为 Hip-Hop Artist/rapper？
- Evidence 是否描述 Artist 本人，而不是某张专辑的 Genre？
- Catalog Artist 页面是否与代表作品及完整姓名一致？
- Source 是否为原始页面而不是搜索摘要或转载？

### 3.7 Nas

- Candidate ID: `wl-candidate-v1-nas`
- Region: `western`
- Proposed Genre: `genre:hip_hop / Hip-Hop`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接支持 Nas 与 Hip-Hop 的 Artist 级关系？
- `Nas` 的短名称是否可能对应其他 Artist 或 catalog 结果？
- `Nasir Jones` alias 是否有明确身份来源？
- Catalog mapping 是否能通过代表作品和地区唯一确认？

### 3.8 Beyoncé

- Candidate ID: `wl-candidate-v1-beyonce`
- Region: `western`
- Proposed Genre: `genre:r_and_b / R&B`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否明确把 Beyoncé 本人关联到 R&B，而不是只描述单曲或早期组合？
- `Beyoncé` 与无重音 `Beyonce` 是否映射到同一 Artist？
- Catalog 页面是否为 solo Artist，而不是 Destiny's Child 页面？
- Evidence 是否保留了跨 Genre 描述中的限定语？

### 3.9 Alicia Keys

- Candidate ID: `wl-candidate-v1-alicia-keys`
- Region: `western`
- Proposed Genre: `genre:r_and_b / R&B`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接支持 Alicia Keys 与 R&B 的关系？
- Source 是否为机构或具有编辑责任的 Artist profile？
- Catalog 页面能否通过代表作品和姓名唯一确认？
- 关系描述是否包含需要保留的 soul/pop 等并列限定？

### 3.10 Common

- Candidate ID: `wl-candidate-v1-common`
- Region: `western`
- Proposed Genre: `genre:hip_hop / Hip-Hop`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接描述 Common 为 Hip-Hop Artist？
- 普通英文词 `Common` 是否造成明显的搜索和 catalog identity 冲突？
- `Lonnie Rashid Lynn` 是否有可靠 alias 来源？
- Catalog mapping 是否通过作品和人物信息唯一确认，而不是按排名选择？

### 3.11 Nujabes

- Candidate ID: `wl-candidate-v1-nujabes`
- Region: `japanese`
- Proposed Genre: `genre:jazz_hip_hop / Jazz Hip-Hop`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接使用 Jazz Hip-Hop 或 Registry 中的一一对应 alias？
- “融合 Jazz 元素”是否被错误提升为 Jazz Hip-Hop Genre？
- `Nujabes` 与 `瀬葉淳` 是否有明确身份关联来源？
- Catalog 页面是否能通过代表作品唯一确认？

### 3.12 宇多田光

- Candidate ID: `wl-candidate-v1-hikaru-utada`
- Region: `japanese`
- Proposed Genre: `genre:r_and_b / R&B`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接描述宇多田光本人和 R&B 关系？
- `宇多田光`、`Hikaru Utada`、`Utada Hikaru` 是否是同一 Artist 的正式名称形式？
- Catalog 页面是否区分日文发行和英文名下的记录？
- Evidence 是否把作品级描述误作 Artist 级关系？

### 3.13 Epik High

- Candidate ID: `wl-candidate-v1-epik-high`
- Region: `korean`
- Proposed Genre: `genre:hip_hop / Hip-Hop`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接把 Epik High 组合关联到 Hip-Hop？
- `Epik High` 与 `에픽하이` 是否由可靠来源确认？
- Catalog 页面是否为组合，而不是某个成员或同名作品？
- 代表作品和成员信息是否共同支持唯一 catalog mapping？

### 3.14 Dynamic Duo

- Candidate ID: `wl-candidate-v1-dynamic-duo`
- Region: `korean`
- Proposed Genre: `genre:hip_hop / Hip-Hop`
- Relation Type: `associated_with`

Expected review questions:

- 来源是否直接把 Dynamic Duo 组合关联到 Hip-Hop？
- 英文通用名称是否造成同名 Artist 冲突？
- `Dynamic Duo` 与 `다이나믹 듀오` 是否有明确 alias 来源？
- Catalog 页面是否通过成员或代表作品确认是目标韩国组合？

## 4. Tracking Rules

`candidate_review_tracking.jsonl` 中：

- `relation_status` 保持 `pending`，直到人工完成 relation review。
- `identity_status` 保持 `pending`，直到人工完成 identity review。
- `evidence_status` 保持 `pending`，直到 source、span、offset 和 hash 都已核验。
- `reviewer_notes` 只记录审核观察，不填写 accepted/rejected。

本阶段没有 final outcome 字段。Tracking 文件不是 Gold Dataset，也不能进入 Real Gold Metrics。

## 5. Stop Point

审核人员现在可以使用本工作区逐条收集来源并核对 Identity。在人工记录返回之前，不创建正式 evidence，不生成 Gold Dataset，不运行 Real Gold Baseline。

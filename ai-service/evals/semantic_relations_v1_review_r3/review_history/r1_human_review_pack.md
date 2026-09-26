# Semantic Relations v1 review r1 — 弱证据复审包

本包仅包含 4 条新建 pending 候选；尚未运行该数据版本的 Evaluation。
请打开完整来源，核对实体、上下文和所询 predicate；不能根据模型输出审核标签。
proposed negative 只表示给定证据不支持所询关系，不断言现实中关系不存在。
exact span 是真实网页摘录；换行用于字段分隔。来源上下文与候选关系注释严格区分。
supported_claims 中的所询关系是刻意不充分的安全性探针，不是来源已证实的断言。
批准只晋升本包对应的冻结标签；新结论仍需创建新版本，不修改本次 freeze。

## real-era-r1-flying-representative-weak

Query: Flying 是否可仅凭发行记录判为 1960s 的时代代表作品（representative_of_era）？
relation_type: `era`; proposed label: **negative**
Source → target: `eval:song:flying` → `era:1960s`
Proposed final relations: `[]`（对本 query 拒绝输出关系）
Rationale: 发行日期可支持 belongs_to_era → 1960s；所询明确是 representative_of_era。仅发行于该年代（以及邻近的体裁/署名记录）不足以证明该曲是该年代的代表作品。negative 只拒绝本 query 的代表性关系，不否认其发行年代。新 query、实体和来源均不同于旧 Love Me Do 样本；需人工确认代表性判断没有被页面上下文明确支持。

Source: [Flying](https://www.thebeatles.com/flying) — Apple Corps / The Beatles
Retrieved at: 2026-09-26; locator: Song heading Flying and Release date field; adjacent table identifies instrumental, album, genre and credits but does not assert era representativeness. No lyrics captured.
Source ID: `source:r1-beatles-flying`; content SHA256: `e360edf02c5758e1c6ce6de4d0f88601ac75ec088f74f170639ee5e5c5f43dc4`
Exact span: Unicode code-point offsets [0, 37)
Document / chunk / claim: `real-era-r1-flying-representative-weak` / `chunk:real-era-r1-flying-representative-weak` / `claim:real-era-r1-flying-representative-weak`

```text
Flying
Release date: 27 November 1967
```

候选探针 predicate: `representative_of_era`; subject: `song:flying`; object: `1960s`
候选置信度为固定测试注释，不代表来源支持度；不得写入生产索引。

Label SHA256（排除 review）: `24acf822f2eaf6d535da459f9b2d060a2a7beb52139968a5fe2f1a7e5a875cd1`

核对重点：该记录实际支持了什么；它是否明确支持所询关系及其方向；是否遗漏了会改变标签的来源上下文。

```text
reviewer:
reviewed_at: YYYY-MM-DD
case_id: real-era-r1-flying-representative-weak
decision: APPROVE / REJECT
reason:
```

## real-scene-r1-dylan-venue-weak

Query: Bob Dylan 是否可仅凭 Carnegie Hall 演出记录判为 New York folk scene 成员（belongs_to_scene）？
relation_type: `scene`; proposed label: **negative**
Source → target: `eval:artist:bob_dylan` → `scene:new_york_folk_scene`
Proposed final relations: `[]`（对本 query 拒绝输出关系）
Rationale: 官方巡演记录将主体识别为 Bob Dylan，明确支持特定日期在纽约演出。演出地点及曲目是 Scene 的间接线索，但不明确陈述 New York folk scene 成员归属；单场地点不能升级为 belongs_to_scene。negative 是证据相对判断，不否认 Dylan 现实中的场景关系；须人工打开完整页面核对上下文。

Source: [Carnegie Hall | The Official Bob Dylan Site](https://www.bobdylan.com/date/1963-10-26-carnegie-hall/) — Bob Dylan / Sony Music Entertainment
Retrieved at: 2026-09-26; locator: Concert date, New York location heading, Carnegie Hall venue; page is Bob Dylan tour archive. Following set list documents songs, not named scene membership.
Source ID: `source:r1-dylan-carnegie-hall`; content SHA256: `e015db28c17ba34051df3f231d512ad4484cd09c630f03254fc781271c1c7cae`
Exact span: Unicode code-point offsets [0, 41)
Document / chunk / claim: `real-scene-r1-dylan-venue-weak` / `chunk:real-scene-r1-dylan-venue-weak` / `claim:real-scene-r1-dylan-venue-weak`

```text
Oct 26, 1963

New York, NY

Carnegie Hall
```

候选探针 predicate: `belongs_to_scene`; subject: `artist:bob_dylan`; object: `New York folk scene`
候选置信度为固定测试注释，不代表来源支持度；不得写入生产索引。

Label SHA256（排除 review）: `ca5bcc52e362ac40f754482e050b334571d6534e22deb3905db14ca09964d3f4`

核对重点：该记录实际支持了什么；它是否明确支持所询关系及其方向；是否遗漏了会改变标签的来源上下文。

```text
reviewer:
reviewed_at: YYYY-MM-DD
case_id: real-scene-r1-dylan-venue-weak
decision: APPROVE / REJECT
reason:
```

## real-influence-r1-bowie-eno-cowrite-weak

Query: David Bowie 是否对 Brian Eno 构成音乐影响（David Bowie → Brian Eno，influenced）？仅以 Warszawa 共同作曲署名为证。
relation_type: `influence`; proposed label: **negative**
Source → target: `eval:artist:david_bowie` → `eval:artist:brian_eno`
Proposed final relations: `[]`（对本 query 拒绝输出关系）
Rationale: 共同作曲署名明确支持合作/共同创作，未陈述 David Bowie 对 Brian Eno 的因果或音乐影响。不能从对称的共同署名推出定向 influenced。全文还谈到 Brian 的 ambient compositions；那也不能转换为所询 Bowie → Eno。两个端点独立且与 query/candidate 同向；需人工核实署名及全文方向上下文。

Source: [ANCIANT Album Focus: Low](https://www.davidbowie.com/blog/2017/8/23/anciant-album-focus-low) — David Bowie official site
Retrieved at: 2026-09-26; locator: LOW FACT FILE, songwriting credit at end of All songs written paragraph. Opening Tony Visconti introduction describes collaboration and Brian ambient compositions; it does not attribute Bowie → Eno influence. The captured credit clause is verbatim, not the whole paragraph.
Source ID: `source:r1-bowie-warszawa-credit`; content SHA256: `40054a223fde3d0718629eacfad65e9ea8c0abc6c53bed09f5ff34fced9f2e78`
Exact span: Unicode code-point offsets [0, 48)
Document / chunk / claim: `real-influence-r1-bowie-eno-cowrite-weak` / `chunk:real-influence-r1-bowie-eno-cowrite-weak` / `claim:real-influence-r1-bowie-eno-cowrite-weak`

```text
‘WARSZAWA’ written by DAVID BOWIE and BRIAN ENO.
```

候选探针 predicate: `influenced`; subject: `artist:david_bowie`; object: `artist:brian_eno`
候选置信度为固定测试注释，不代表来源支持度；不得写入生产索引。

Label SHA256（排除 review）: `2489abcc55ccbac39da9650ed4552ea764f34be1fc4851350bcae3892ff7e22d`

核对重点：该记录实际支持了什么；它是否明确支持所询关系及其方向；是否遗漏了会改变标签的来源上下文。

```text
reviewer:
reviewed_at: YYYY-MM-DD
case_id: real-influence-r1-bowie-eno-cowrite-weak
decision: APPROVE / REJECT
reason:
```

## real-influence-r1-baez-dylan-guest-weak

Query: Joan Baez 是否对 Bob Dylan 构成创作启发（Joan Baez → Bob Dylan，inspired）？仅以 Dylan 客串 Baez 演出的记录为证。
relation_type: `influence`; proposed label: **negative**
Source → target: `eval:artist:joan_baez` → `eval:artist:bob_dylan`
Proposed final relations: `[]`（对本 query 拒绝输出关系）
Rationale: 真实客串记录建立了 Bob Dylan 与 Joan Baez 的演出联系，未陈述 Joan Baez 启发 Dylan 的创作，也不提供影响方向。客串/同台不能直接升级为 inspired。候选方向 Joan Baez → Bob Dylan 与 query/Gold probe 一致，非自环。negative 不断言现实中两人无影响关系；请人工核对页面主体和影响归因。

Source: [Forest Hills Tennis Stadium-Guest appearence at a Joan Baez concert | The Official Bob Dylan Site](https://www.bobdylan.com/date/1963-08-17-forest-hills-tennis-stadium-guest-appearence-joan-baez-concert/) — Bob Dylan / Sony Music Entertainment
Retrieved at: 2026-09-26; locator: Concert date/location and venue label in Bob Dylan archive; preserve original spelling appearence. Following two-song set list has no influence attribution. Bob Dylan is the archive subject, Joan Baez the concert artist.
Source ID: `source:r1-dylan-baez-guest`; content SHA256: `cbe57c63160fcc8f1c645e16e1cf819ef8f79b2fb9d0225e2dc7b8d63686f5e9`
Exact span: Unicode code-point offsets [0, 95)
Document / chunk / claim: `real-influence-r1-baez-dylan-guest-weak` / `chunk:real-influence-r1-baez-dylan-guest-weak` / `claim:real-influence-r1-baez-dylan-guest-weak`

```text
Aug 17, 1963

New York, NY

Forest Hills Tennis Stadium-Guest appearence at a Joan Baez concert
```

候选探针 predicate: `inspired`; subject: `artist:joan_baez`; object: `artist:bob_dylan`
候选置信度为固定测试注释，不代表来源支持度；不得写入生产索引。

Label SHA256（排除 review）: `e3c7c94b4f0bb79656dc1335ba01a433be46e679487eb3122c41743bdee2dc67`

核对重点：该记录实际支持了什么；它是否明确支持所询关系及其方向；是否遗漏了会改变标签的来源上下文。

```text
reviewer:
reviewed_at: YYYY-MM-DD
case_id: real-influence-r1-baez-dylan-guest-weak
decision: APPROVE / REJECT
reason:
```

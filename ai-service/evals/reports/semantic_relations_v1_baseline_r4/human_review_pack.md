# Semantic Relations v1 — 人工审核包

每条真实样本的审核状态见下方；pending 的 provisional labels 尚未经人工核定。
请逐条查看完整来源上下文，核对实体、relation predicate、方向、证据强度、否定作用域和冲突。
negative 表示给定证据不足以支持所询关系，不代表全世界不存在该关系。
review receipt 必须包含 case_id、reviewer、reviewed_at、status=approved 和完整标签 payload 的 label_sha256。
新增审核结论需新版本数据与 manifest；不要修改本次冻结 Gold 来消除失败。

## real-era-0-positive

Query: Love Me Do 的发行年代
Family: era; provisional label: positive
Source → target: `eval:song:love_me_do` → `era:1960s`
Proposed relations: `[{"source_entity": "eval:song:love_me_do", "target_entity": "era:1960s", "predicate": "belongs_to_era", "evidence_claim_ids": ["claim:real-era-0-positive"], "fields": {}, "conflict_flags": []}]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Love Me Do](https://www.thebeatles.com/love-me-do) — Apple Corps / The Beatles
Locator: Song heading and Release date field; no lyrics captured
Source SHA256: `d79f452b87c77ad12eacc0e413c886e991fa8852394ebaadd22e553857a7be5e`
Exact span offsets: [0, 40); claim: `claim:real-era-0-positive`
> Love Me Do / Release date: 05 October 1962

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: 官方 Beatles 页面明确给出 Love Me Do 的 Release date = 05 October 1962，可直接支持 belongs_to_era → 1960s。

## real-era-0-confusable

Query: Love Me Do 的音乐场景归属
Family: scene; provisional label: negative
Source → target: `eval:song:love_me_do` → `era:1960s`
Proposed relations: `[]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Love Me Do](https://www.thebeatles.com/love-me-do) — Apple Corps / The Beatles
Locator: Song heading and Release date field; no lyrics captured
Source SHA256: `d79f452b87c77ad12eacc0e413c886e991fa8852394ebaadd22e553857a7be5e`
Exact span offsets: [0, 40); claim: `claim:real-era-0-confusable`
> Love Me Do / Release date: 05 October 1962

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: 当前证据仅支持发行时间/年代，不支持音乐场景关系，作为 cross-relation isolation negative 合理。

## real-scene-1-positive

Query: Neko Case 的音乐场景归属
Family: scene; provisional label: positive
Source → target: `eval:artist:neko_case` → `scene:vancouver_indie_scene`
Proposed relations: `[{"source_entity": "eval:artist:neko_case", "target_entity": "scene:vancouver_indie_scene", "predicate": "belongs_to_scene", "evidence_claim_ids": ["claim:real-scene-1-positive"], "fields": {}, "conflict_flags": []}]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Puget Sound Stories - Neko Case](https://www.mopop.org/experience/puget-sound-stories) — Museum of Pop Culture
Locator: Neko Case paragraph; speaker/entity and city coreference require full page review
Source SHA256: `2a9b3671bc0d5460052ab38160d6674328cb5bf37c18bfa2c060ec06089b49d9`
Exact span offsets: [0, 80); claim: `claim:real-scene-1-positive`
> Case moved to Vancouver, where she became part of the city's vibrant indie scene

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: MoPOP 原文明确表述 Neko Case moved to Vancouver and became part of the city's vibrant indie scene，可直接支持 belongs_to_scene → Vancouver indie scene。

## real-scene-1-confusable

Query: Neko Case 的活跃年代
Family: era; provisional label: negative
Source → target: `eval:artist:neko_case` → `scene:vancouver_indie_scene`
Proposed relations: `[]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Puget Sound Stories - Neko Case](https://www.mopop.org/experience/puget-sound-stories) — Museum of Pop Culture
Locator: Neko Case paragraph; speaker/entity and city coreference require full page review
Source SHA256: `2a9b3671bc0d5460052ab38160d6674328cb5bf37c18bfa2c060ec06089b49d9`
Exact span offsets: [0, 80); claim: `claim:real-scene-1-confusable`
> Case moved to Vancouver, where she became part of the city's vibrant indie scene

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: 当前 evidence 支持 Vancouver indie scene，但不能据此推出所询“活跃年代”，作为 Era/Scene query isolation negative 合理。

## real-influence-2-positive

Query: The Pixies 对 Nirvana 的音乐影响
Family: influence; provisional label: positive
Source → target: `eval:artist:pixies` → `eval:artist:nirvana`
Proposed relations: `[{"source_entity": "eval:artist:pixies", "target_entity": "eval:artist:nirvana", "predicate": "influenced", "evidence_claim_ids": ["claim:real-influence-2-positive"], "fields": {}, "conflict_flags": []}]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame
Locator: Influence > Influences list ending with central Nirvana node; distinguish following Influenced list
Source SHA256: `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`
Exact span offsets: [0, 111); claim: `claim:real-influence-2-positive`
> Influences /  / R.E.M. /  / The Pixies /  / The Melvins /  / The Raincoats /  / Bam Bam /  / Sonic Youth /  / Fugazi /  / The Gap Band /  / Nirvana

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: Rock & Roll Hall of Fame 的 Nirvana 页面将 The Pixies 明确列于 Influences，而 Nirvana 为中心实体，因此方向 Pixies → Nirvana 合理。

## real-influence-2-confusable

Query: Nirvana 的活跃年代
Family: era; provisional label: negative
Source → target: `eval:artist:nirvana` → `eval:artist:nirvana`
Proposed relations: `[]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame
Locator: Influence > Influences list ending with central Nirvana node; distinguish following Influenced list
Source SHA256: `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`
Exact span offsets: [0, 111); claim: `claim:real-influence-2-confusable`
> Influences /  / R.E.M. /  / The Pixies /  / The Melvins /  / The Raincoats /  / Bam Bam /  / Sonic Youth /  / Fugazi /  / The Gap Band /  / Nirvana

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: Influence 列表不能支持 Nirvana 活跃年代关系，作为 Influence→Era cross-relation negative 合理。

## real-influence-3-positive

Query: Pixies 对 Kurt Cobain 的创作启发
Family: influence; provisional label: positive
Source → target: `eval:artist:pixies` → `eval:artist:kurt_cobain`
Proposed relations: `[{"source_entity": "eval:artist:pixies", "target_entity": "eval:artist:kurt_cobain", "predicate": "inspired", "evidence_claim_ids": ["claim:real-influence-3-positive"], "fields": {}, "conflict_flags": []}]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke
Locator: Answer to But you must have had a good time writing it; referent is Smells Like Teen Spirit
Source SHA256: `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`
Exact span offsets: [0, 74); claim: `claim:real-influence-3-positive`
> I was basically trying to rip off the Pixies. I have to admit it [smiles].

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: Kurt Cobain 明确表示创作 Smells Like Teen Spirit 时在尝试模仿 Pixies，并进一步说明使用了 Pixies 的 dynamics，可支持 Pixies → Kurt Cobain 的 inspired/influence 方向。

## real-influence-3-confusable

Query: Kurt Cobain 的音乐场景归属
Family: scene; provisional label: negative
Source → target: `eval:artist:kurt_cobain` → `eval:artist:kurt_cobain`
Proposed relations: `[]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke
Locator: Answer to But you must have had a good time writing it; referent is Smells Like Teen Spirit
Source SHA256: `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`
Exact span offsets: [0, 74); claim: `claim:real-influence-3-confusable`
> I was basically trying to rip off the Pixies. I have to admit it [smiles].

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: Pixies 创作影响证据不能支持 Kurt Cobain 的 scene membership，作为 Influence→Scene isolation negative 合理。

## real-era-r1-flying-representative-weak

Query: Flying 是否可仅凭发行记录判为 1960s 的时代代表作品（representative_of_era）？
Family: era; provisional label: negative
Source → target: `eval:song:flying` → `era:1960s`
Proposed relations: `[]`
Rationale: 发行日期可支持 belongs_to_era → 1960s；所询明确是 representative_of_era。仅发行于该年代（以及邻近的体裁/署名记录）不足以证明该曲是该年代的代表作品。negative 只拒绝本 query 的代表性关系，不否认其发行年代。新 query、实体和来源均不同于旧 Love Me Do 样本；需人工确认代表性判断没有被页面上下文明确支持。
Source: [Flying](https://www.thebeatles.com/flying) — Apple Corps / The Beatles
Locator: Song heading Flying and Release date field; adjacent table identifies instrumental, album, genre and credits but does not assert era representativeness. No lyrics captured.
Source SHA256: `e360edf02c5758e1c6ce6de4d0f88601ac75ec088f74f170639ee5e5c5f43dc4`
Exact span offsets: [0, 37); claim: `claim:real-era-r1-flying-representative-weak`
> Flying / Release date: 27 November 1967

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: 来源明确支持 Flying 于 1967 年发行，因此可支持 belongs_to_era → 1960s；但发行于某年代本身不足以证明 representative_of_era。当前 evidence 未明确陈述其为 1960s 代表作品，因此作为 weak-evidence negative 合理。

## real-scene-r1-dylan-venue-weak

Query: Bob Dylan 是否可仅凭 Carnegie Hall 演出记录判为 New York folk scene 成员（belongs_to_scene）？
Family: scene; provisional label: negative
Source → target: `eval:artist:bob_dylan` → `scene:new_york_folk_scene`
Proposed relations: `[]`
Rationale: 官方巡演记录将主体识别为 Bob Dylan，明确支持特定日期在纽约演出。演出地点及曲目是 Scene 的间接线索，但不明确陈述 New York folk scene 成员归属；单场地点不能升级为 belongs_to_scene。negative 是证据相对判断，不否认 Dylan 现实中的场景关系；须人工打开完整页面核对上下文。
Source: [Carnegie Hall | The Official Bob Dylan Site](https://www.bobdylan.com/date/1963-10-26-carnegie-hall/) — Bob Dylan / Sony Music Entertainment
Locator: Concert date, New York location heading, Carnegie Hall venue; page is Bob Dylan tour archive. Following set list documents songs, not named scene membership.
Source SHA256: `e015db28c17ba34051df3f231d512ad4484cd09c630f03254fc781271c1c7cae`
Exact span offsets: [0, 41); claim: `claim:real-scene-r1-dylan-venue-weak`
> Oct 26, 1963 /  / New York, NY /  / Carnegie Hall

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: 来源仅明确支持 Bob Dylan 于 1963-10-26 在 New York Carnegie Hall 演出。单次演出地点不能推出 belongs_to_scene → New York folk scene，当前 evidence 未明确陈述 scene membership，因此作为 weak-evidence negative 合理。

## real-influence-r1-bowie-eno-cowrite-weak

Query: David Bowie 是否对 Brian Eno 构成音乐影响（David Bowie → Brian Eno，influenced）？仅以 Warszawa 共同作曲署名为证。
Family: influence; provisional label: negative
Source → target: `eval:artist:david_bowie` → `eval:artist:brian_eno`
Proposed relations: `[]`
Rationale: 共同作曲署名明确支持合作/共同创作，未陈述 David Bowie 对 Brian Eno 的因果或音乐影响。不能从对称的共同署名推出定向 influenced。全文还谈到 Brian 的 ambient compositions；那也不能转换为所询 Bowie → Eno。两个端点独立且与 query/candidate 同向；需人工核实署名及全文方向上下文。
Source: [ANCIANT Album Focus: Low](https://www.davidbowie.com/blog/2017/8/23/anciant-album-focus-low) — David Bowie official site
Locator: LOW FACT FILE, songwriting credit at end of All songs written paragraph. Opening Tony Visconti introduction describes collaboration and Brian ambient compositions; it does not attribute Bowie → Eno influence. The captured credit clause is verbatim, not the whole paragraph.
Source SHA256: `40054a223fde3d0718629eacfad65e9ea8c0abc6c53bed09f5ff34fced9f2e78`
Exact span offsets: [0, 48); claim: `claim:real-influence-r1-bowie-eno-cowrite-weak`
> ‘WARSZAWA’ written by DAVID BOWIE and BRIAN ENO.

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: “WARSZAWA written by DAVID BOWIE and BRIAN ENO”明确支持共同创作关系，但共同署名是对称合作事实，不能推出 David Bowie → Brian Eno 的定向 influenced 关系。当前 evidence 不支持目标 predicate 及方向，因此作为 weak-evidence negative 合理。

## real-influence-r1-baez-dylan-guest-weak

Query: Joan Baez 是否对 Bob Dylan 构成创作启发（Joan Baez → Bob Dylan，inspired）？仅以 Dylan 客串 Baez 演出的记录为证。
Family: influence; provisional label: negative
Source → target: `eval:artist:joan_baez` → `eval:artist:bob_dylan`
Proposed relations: `[]`
Rationale: 真实客串记录建立了 Bob Dylan 与 Joan Baez 的演出联系，未陈述 Joan Baez 启发 Dylan 的创作，也不提供影响方向。客串/同台不能直接升级为 inspired。候选方向 Joan Baez → Bob Dylan 与 query/Gold probe 一致，非自环。negative 不断言现实中两人无影响关系；请人工核对页面主体和影响归因。
Source: [Forest Hills Tennis Stadium-Guest appearence at a Joan Baez concert | The Official Bob Dylan Site](https://www.bobdylan.com/date/1963-08-17-forest-hills-tennis-stadium-guest-appearence-joan-baez-concert/) — Bob Dylan / Sony Music Entertainment
Locator: Concert date/location and venue label in Bob Dylan archive; preserve original spelling appearence. Following two-song set list has no influence attribution. Bob Dylan is the archive subject, Joan Baez the concert artist.
Source SHA256: `cbe57c63160fcc8f1c645e16e1cf819ef8f79b2fb9d0225e2dc7b8d63686f5e9`
Exact span offsets: [0, 95); claim: `claim:real-influence-r1-baez-dylan-guest-weak`
> Aug 17, 1963 /  / New York, NY /  / Forest Hills Tennis Stadium-Guest appearence at a Joan Baez concert

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: 来源支持 Bob Dylan 客串 Joan Baez 演出的事实，但共同演出/客串不能推出 Joan Baez → Bob Dylan 的 inspired 创作启发关系。当前 evidence 未提供影响归因或方向，因此作为 weak-evidence negative 合理。

## real-era-r3-let-it-be-negation

Query: Let It Be 专辑的首次正式发行是否 belongs_to_era → 1960s？只返回该实体对。
Family: era; provisional label: negative
Source → target: `eval:album:let_it_be` → `era:1960s`
Proposed relations: `[]`
Rationale: 明确 not released until May 1970 排除原专辑在 1960s 正式发行。Replay 如实保留 1970/1970s 观察，不把 1969 录制改成发行；负例只要求所询 1960s 实体对无输出。
Source: [The Beatles Anthology](https://www.thebeatles.com/beatles-anthology-0) — The Beatles / Apple Corps
Locator: George quotation after the 30 January 1969 rooftop-performance account; clause beginning The album Get Back
Source SHA256: `7190805b41861b6665b9a94d65273e38d1dd19eb33369b26c6444f12d4f3737d`
Exact span offsets: [0, 79); claim: `claim:real-era-r3-let-it-be-negation`
> The album Get Back (or Let It Be, as it became) was not released until May 1970

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: The Beatles 官方来源明确说明 Get Back/Let It Be “was not released until May 1970”。实体为原 Let It Be 专辑，predicate 为首次正式发行年代；该证据明确排除 belongs_to_era → 1960s。1969 录制时间不能替代发行时间，因此 negative 标注正确。

## real-scene-r3-alice-punk-negation

Query: Alice in Chains 在早期 Seattle punk scene 中是否 belongs_to_scene？只返回该实体对。
Family: scene; provisional label: negative
Source → target: `eval:artist:alice_in_chains` → `scene:seattle_punk_scene`
Proposed relations: `[]`
Rationale: Steve Turner 明确否定 Alice in Chains 属于早期 punk scene；Seattle 范围来自同一回答上文。否定的是 punk 社群根源，不是否定 grunge 归属。Replay 候选 predicate 表示被否定的命题，exact span 的否定不被去除。
Source: [Mudhoney intervista](https://www.rocknation.it/interviews/mudhoney-intervista/) — Rock Nation / Francesca Cecconi
Locator: English version; Steve Turner answer about his book, following discussion of the Seattle music scene before international breakthrough
Source SHA256: `89bb38ab28881953e4f138c854bbdd765eb26e07114fee059e64b79810be8a0a`
Exact span offsets: [0, 113); claim: `claim:real-scene-r3-alice-punk-negation`
> Like all the Grunge bands except Alice in Chains, nothing against them, but they were not part of the punk scene.

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: Steve Turner 的完整上下文明确讨论 Seattle music scene，并指出除 Alice in Chains 外其他 grunge bands 来自 Punk Rock，随后明确说明 Alice in Chains “were not part of the punk scene”且青少年时期并不参与 punk shows。该否定限定于早期 Seattle punk scene，不否定其 Seattle/grunge 身份，因此 negative scope 正确。

## real-influence-r3-paik-migung-negation

Query: Nam June Paik → Migung 的 influenced 创作关系是否成立？只返回该实体对。
Family: influence; provisional label: negative
Source → target: `eval:artist:nam_june_paik` → `eval:song:migung`
Proposed relations: `[]`
Rationale: 作曲者明确否定 Nam June Paik 对 Migung 的影响。被动 influenced_by 的输入方向为 Migung → Paik，规范关系应为 Paik → Migung；否定与原方向均保留。song 是既有模型对音乐作品的承载类型，需人工确认。
Source: [The Persistent Influence of Korean Musician Hwang Byungki and His “Migung”](https://daily.redbullmusicacademy.com/2018/02/hwang-byungki-migung-interview/) — Red Bull Music Academy Daily / Hasan Hujairi
Locator: Hwang answer to question about conversations with Nam June Paik at the time of composing Migung
Source SHA256: `b7f0e81b0451aac9fe6e082402250a5e86f8ce33acf5e6de29da0a2a287d8bdc`
Exact span offsets: [0, 42); claim: `claim:real-influence-r3-paik-migung-negation`
> Migung is not influenced by Nam June Paik.

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: Hwang Byungki 在被问及创作 Migung 时与 Nam June Paik 的交流后明确表示“Migung is not influenced by Nam June Paik”。作品身份和 influence scope 明确，规范化方向为 Nam June Paik → Migung；该方向被直接否定，因此 negative 标注正确。

## real-era-r3-giant-steps-release-conflict

Query: Giant Steps 原专辑首次发行的年代归属（1950s / 1960s）有什么来源冲突？保留双方，不作确定裁决。
Family: era; provisional label: positive
Source → target: `eval:album:giant_steps` → `era:1960s`
Proposed relations: `[{"source_entity": "eval:album:giant_steps", "target_entity": "era:1960s", "predicate": "belongs_to_era", "evidence_claim_ids": ["claim:real-era-r3-giant-steps-release-conflict-1960", "claim:real-era-r3-giant-steps-release-conflict-1959"], "fields": {"end_year": null, "start_year": null, "temporal_precision": "unknown"}, "conflict_flags": ["temporal_conflict"]}, {"source_entity": "eval:album:giant_steps", "target_entity": "era:1950s", "predicate": "belongs_to_era", "evidence_claim_ids": ["claim:real-era-r3-giant-steps-release-conflict-1960", "claim:real-era-r3-giant-steps-release-conflict-1959"], "fields": {"end_year": null, "start_year": null, "temporal_precision": "unknown"}, "conflict_flags": ["temporal_conflict"]}]`
Rationale: Rhino 的历史发行段落声称 early 1960；Jeff Pifher 2011 教育文章明确写 released in 1959。两者使用同一 release 谓词，不把录制日期与发行日期直接相比较。proposed positive 指需要返回带 temporal_conflict 的两个互斥年代候选；每个候选关联双方证据且年份为空，不是断言两次首次发行。较弱来源可能有误，仍须保留归因分歧并由人判断地区/版本范围。
Source: [Coltrane: 1960-1964 Mono (Rhino High Fidelity)](https://media.rhino.com/press-release/coltrane-1960-1964-mono-rhino-high-fidelity) — Rhino Media
Locator: Historical Giant Steps paragraph, immediately after description of first Atlantic sessions; NBSP after comma retained
Source SHA256: `cc38602829665b8f4d57e0d9edef02e1cd7bd925921336a6d152deec660c798c`
Exact span offsets: [0, 70); claim: `claim:real-era-r3-giant-steps-release-conflict-1960`
> Released in early 1960, Giant Steps introduced the “Coltrane Changes,”
Source: [Cycles And Symmetric Division Of The Octave In The Works Of John Coltrane](https://static1.squarespace.com/static/5b4756089772aefb8c13401c/t/5c36518e0e2e72da3b5fbdd6/1547063701367/Jeff%2BPifher%2BAcademic%2BSeries-Coltrane%2BCycles.pdf) — Jeff Pifher (authored educational essay)
Locator: PDF page 7 (zero-based page 6); Countdown and Giant Steps section, opening sentence, before footnote 21
Source SHA256: `7501aa06ba0cd982888371456c616c614dc4c1206bb11518e35b455368e5c2d2`
Exact span offsets: [0, 50); claim: `claim:real-era-r3-giant-steps-release-conflict-1959`
> Coltrane's album Giant Steps was released in 1959.

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: 两个来源对同一 Giant Steps 原专辑 release predicate 给出互斥年份：Rhino 明确写 released in early 1960，另一教育文章写 released in 1959。Gold 不应自行裁定真实性，也不能把两个年代作为两个确定事实；保留 1950s/1960s 两个候选、双方 evidence、temporal_conflict=true，并将年份置空和 precision=unknown，符合 conflict case 设计。

## real-scene-r3-blur-britpop-conflict

Query: Blur 的历史 Britpop 场景归属是否存在外部归类与成员自述的分歧？保留双方，不作确定裁决。
Family: scene; provisional label: positive
Source → target: `eval:artist:blur` → `scene:britpop_movement`
Proposed relations: `[{"source_entity": "eval:artist:blur", "target_entity": "scene:britpop_movement", "predicate": "belongs_to_scene", "evidence_claim_ids": ["claim:real-scene-r3-blur-britpop-conflict-external", "claim:real-scene-r3-blur-britpop-conflict-self"], "fields": {}, "conflict_flags": ["attribution_conflict"]}]`
Rationale: BRIT Awards 将 Blur 归为 Britpop scene 的领军者；2023 回顾访谈成员拒绝 Britpop 身份。proposed relation 必须含双方证据和 attribution_conflict，不能抹去自述。仍可能只是拒绝政治包装而非社会场景归属，需人工决定同一语义范围；若不同范围，应 REJECT 本候选。
Source: [Blur at the BRITs: 1995 - 2012](https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/) — The BRIT Awards
Locator: Paragraph on the 2012 Outstanding Contribution award; historical Britpop scene description
Source SHA256: `b6b853efe0dddb40c89ea9782d79cebfea99e278b64ad1dd9135c2a8301210d8`
Exact span offsets: [0, 106); claim: `claim:real-scene-r3-blur-britpop-conflict-external`
> One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.
Source: [Blur: ‘We’re not AC/DC. We’ve never been basic. We play too many chords to be basic’](https://english.elpais.com/culture/2023-07-21/blur-were-not-acdc-weve-never-been-basic-we-play-too-many-chords-to-be-basic.html?outputType=amp) — EL PAÍS / Álex Vicente
Locator: Interview paragraph about Blair, England and political appropriation; final two sentences of the answer following Coxon adds
Source SHA256: `257f424837d1b688167b6a5ca344cc55c5655b079dbe919739f0b9b29a9b562b`
Exact span offsets: [0, 54); claim: `claim:real-scene-r3-blur-britpop-conflict-self`
> We never really did ‘Britpop.’ We’re not part of that.

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: BRIT Awards 明确将 Blur 描述为 Britpop frontrunner 并称其 survived the scene；Blur 成员在后来的直接访谈中则表示“We never really did ‘Britpop.’ We’re not part of that.” 两者均针对 Blur 与历史 Britpop 的归属/认同，虽自述带有政治文化品牌语境，但足以形成外部归类与成员自我认同之间的 attribution conflict。应保留双方 evidence，不裁定单一事实。

## real-influence-r3-stockhausen-cluster-conflict

Query: Karlheinz Stockhausen → Cluster 的早期音乐 influenced 归因是否有争议？保留作者判断与成员否定。
Family: influence; provisional label: positive
Source → target: `eval:artist:karlheinz_stockhausen` → `eval:artist:cluster`
Proposed relations: `[{"source_entity": "eval:artist:karlheinz_stockhausen", "target_entity": "eval:artist:cluster", "predicate": "influenced", "evidence_claim_ids": ["claim:real-influence-r3-stockhausen-cluster-conflict-report", "claim:real-influence-r3-stockhausen-cluster-conflict-denial"], "fields": {}, "conflict_flags": ["attribution_conflict"]}]`
Rationale: Guardian 原创访谈报道作者将 Stockhausen 作为 Cluster 早期电子音乐的借鉴来源；2010 原创采访中 Moebius 对 Stockhausen 影响他们的提问回答 direi di no。作者解释与成员的限定否定不可静默合并。原文 Italian 和上下文依赖保留，不将翻译或补写的英文句子作为 Gold，不把 Kluster 的评论移植到 Cluster。
Source: [An awfully big adventure](https://www.theguardian.com/music/2005/apr/08/popandrock) — The Guardian / Dorian Lynskey
Locator: Historical Cluster paragraph starting Music critics have explained Krautrock; excerpt of the second sentence, whose grammatical subject is Cluster
Source SHA256: `a798a652865f566e6cd6ebfe22b54fb892e49d79c29c34f63d5b943819128261`
Exact span offsets: [0, 130); claim: `claim:real-influence-r3-stockhausen-cluster-conflict-report`
> tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.
Source: [Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010](https://www.indie-eye.it/recensore/generi/experimental/cluster-lintervista-di-indie-eye-it.html) — Indie-eye / Federico Fragasso and Michele Baldini
Locator: Moebius answer immediately after question whether Stockhausen influenced them as he did other Krautrock artists
Source SHA256: `9268f84e46dbb53d3e88cebfcc472609eb7eb7bb80cfea5967e57fdcce9f80a4`
Exact span offsets: [0, 85); claim: `claim:real-influence-r3-stockhausen-cluster-conflict-denial`
> Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: Guardian 完整句的主语明确为 Cluster，并将 Stockhausen 与其他来源一起描述为其转化进电子音乐的参考/影响来源；Dieter Moebius 在被直接问及 Stockhausen 是否影响他们时回答“direi di no”，构成对同一历史音乐影响归因的限定否定。因此应保留双方 attribution，并标记 attribution_conflict，而不是静默选择其中一方。

## real-influence-r3-pixies-nirvana-forward

Query: Pixies → Nirvana 的 influenced 音乐关系是否被所给证据支持？只返回该实体对。
Family: influence; provisional label: positive
Source → target: `eval:artist:pixies` → `eval:artist:nirvana`
Proposed relations: `[{"source_entity": "eval:artist:pixies", "target_entity": "eval:artist:nirvana", "predicate": "influenced", "evidence_claim_ids": ["claim:real-influence-2-positive"], "fields": {}, "conflict_flags": []}]`
Rationale: 同一冻结证据与 claim ID 成对复用，不因 query 反向而翻转 replay claim。正向支持 Pixies → Nirvana；反向负例仅表示该证据不支持 Nirvana → Pixies，不主张现实中绝无反向影响。正向控制与既有 approved 样本关联，不是独立随机样本；新 query 的标签仍待人工审核。
Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame
Locator: Influence > Influences list ending with central Nirvana node; distinguish following Influenced list
Source SHA256: `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`
Exact span offsets: [0, 111); claim: `claim:real-influence-2-positive`
> Influences /  / R.E.M. /  / The Pixies /  / The Melvins /  / The Raincoats /  / Bam Bam /  / Sonic Youth /  / Fugazi /  / The Gap Band /  / Nirvana

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: Rock & Roll Hall of Fame 的 Nirvana Influence 页面将 The Pixies 明确列在 Nirvana 的 Influences 下，因此证据支持规范方向 Pixies → Nirvana influenced。source、target、predicate 和方向均正确。

## real-influence-r3-pixies-nirvana-reverse

Query: Nirvana → Pixies 的 influenced 音乐关系是否被所给证据支持？只返回该实体对。
Family: influence; provisional label: negative
Source → target: `eval:artist:nirvana` → `eval:artist:pixies`
Proposed relations: `[]`
Rationale: 同一冻结证据与 claim ID 成对复用，不因 query 反向而翻转 replay claim。正向支持 Pixies → Nirvana；反向负例仅表示该证据不支持 Nirvana → Pixies，不主张现实中绝无反向影响。正向控制与既有 approved 样本关联，不是独立随机样本；新 query 的标签仍待人工审核。
Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame
Locator: Influence > Influences list ending with central Nirvana node; distinguish following Influenced list
Source SHA256: `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`
Exact span offsets: [0, 111); claim: `claim:real-influence-2-positive`
> Influences /  / R.E.M. /  / The Pixies /  / The Melvins /  / The Raincoats /  / Bam Bam /  / Sonic Youth /  / Fugazi /  / The Gap Band /  / Nirvana

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: 与正向 case 使用同一冻结 evidence，但该 evidence 只支持 Pixies → Nirvana，不支持查询中的 Nirvana → Pixies。该 negative 为 evidence-relative，不断言现实中绝无反向影响，因此反方向控制样本设计正确。

## real-influence-r3-pixies-cobain-forward

Query: Pixies → Kurt Cobain 的 inspired 音乐关系是否被所给证据支持？只返回该实体对。
Family: influence; provisional label: positive
Source → target: `eval:artist:pixies` → `eval:artist:kurt_cobain`
Proposed relations: `[{"source_entity": "eval:artist:pixies", "target_entity": "eval:artist:kurt_cobain", "predicate": "inspired", "evidence_claim_ids": ["claim:real-influence-3-positive"], "fields": {}, "conflict_flags": []}]`
Rationale: 同一冻结证据与 claim ID 成对复用，不因 query 反向而翻转 replay claim。正向支持 Pixies → Kurt Cobain；反向负例仅表示该证据不支持 Kurt Cobain → Pixies，不主张现实中绝无反向影响。正向控制与既有 approved 样本关联，不是独立随机样本；新 query 的标签仍待人工审核。
Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke
Locator: Answer to But you must have had a good time writing it; referent is Smells Like Teen Spirit
Source SHA256: `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`
Exact span offsets: [0, 74); claim: `claim:real-influence-3-positive`
> I was basically trying to rip off the Pixies. I have to admit it [smiles].

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: Kurt Cobain 明确表示自己创作时“trying to rip off the Pixies”，完整上下文还说明使用了 Pixies 的 dynamics，因此证据明确支持 Pixies → Kurt Cobain 的 inspired/influence 方向。实体、predicate 和方向正确。

## real-influence-r3-pixies-cobain-reverse

Query: Kurt Cobain → Pixies 的 inspired 音乐关系是否被所给证据支持？只返回该实体对。
Family: influence; provisional label: negative
Source → target: `eval:artist:kurt_cobain` → `eval:artist:pixies`
Proposed relations: `[]`
Rationale: 同一冻结证据与 claim ID 成对复用，不因 query 反向而翻转 replay claim。正向支持 Pixies → Kurt Cobain；反向负例仅表示该证据不支持 Kurt Cobain → Pixies，不主张现实中绝无反向影响。正向控制与既有 approved 样本关联，不是独立随机样本；新 query 的标签仍待人工审核。
Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke
Locator: Answer to But you must have had a good time writing it; referent is Smells Like Teen Spirit
Source SHA256: `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`
Exact span offsets: [0, 74); claim: `claim:real-influence-3-positive`
> I was basically trying to rip off the Pixies. I have to admit it [smiles].

Human decision: approved; reviewer: human-review-WT; reviewed_at: 2026-09-26; notes: 同一证据明确表达 Cobain 从 Pixies 获取创作启发，只支持 Pixies → Kurt Cobain，不支持 Kurt Cobain → Pixies。作为与 forward case 配对的 evidence-relative reverse negative 合理，不应输出反向 inspired relation。

# Semantic Relations v1 — r3 人工审核包

本包仅包含 10 条新增 pending：明确否定 3 条、冲突 3 条、Influence 正反方向配对 4 条。既有 12 条人工 Gold 未变，原 4 条 REJECT 未恢复。尚未运行本版本 Evaluation。

请打开完整来源后审核实体、时间/地域/版本、predicate 和方向；不要参考模型输出或分数。source_audit.json 中的上下文说明与中文释义不是 Gold evidence；Gold 仅使用下列原文短摘录，offset 为 Unicode 字符偏移 [start,end)。hash 证明本地摘录冻结，不能代替对完整真实来源的核验。

proposed positive 在 conflict case 中表示必须返回携带冲突语义的 relation，不表示争议已被判为确定事实。Era 两条结果是互斥候选；必须保留双方证据、temporal_conflict=true、空年份和 unknown 精度。Scene/Influence 必须保留 attribution_conflict=true。当前接口能否满足要求留给后续评测揭示，本轮不据此降低 Gold。

反向 negative 为 evidence-relative；不等于全局否定反向影响。配对共用同一 documents/claim 数据，仅 query、query identities 与 Gold 方向变化。supported_claims 为人工构造的离线 replay annotation：否定段落上的 predicate 表示被否定的命题，并非已成立关系。未运行实际检索或生产抽取，不能用此批推断线上 retrieval 质量。

请给每条 case_id 独立 APPROVE / REJECT 与理由。没有回复即保持 pending，不推断批准。若要改 scope/实体/标签，拒绝当前冻结候选，再建新版本。

## real-era-r3-let-it-be-negation

Query: Let It Be 专辑的首次正式发行是否 belongs_to_era → 1960s？只返回该实体对。
relation_type: `era` · proposed label: `negative` · status: **pending**
所询 source → target: `eval:album:let_it_be` → `era:1960s`
Predicate: `belongs_to_era`
Label SHA256: `e6d960827bc40e44479801eed5a106f2c8084d193a6865ab6b8698f5207d6c7c`
Pair: —

Proposed expected relations:
```json
[]
```

标注依据与限制：明确 not released until May 1970 排除原专辑在 1960s 正式发行。Replay 如实保留 1970/1970s 观察，不把 1969 录制改成发行；负例只要求所询 1960s 实体对无输出。

Source: [The Beatles Anthology](https://www.thebeatles.com/beatles-anthology-0) — The Beatles / Apple Corps
Locator: George quotation after the 30 January 1969 rooftop-performance account; clause beginning The album Get Back
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:r3:beatles-let-it-be-not-until` / `real-era-r3-let-it-be-negation` / `chunk:real-era-r3-let-it-be-negation` / `claim:real-era-r3-let-it-be-negation`
Source content SHA256: `7190805b41861b6665b9a94d65273e38d1dd19eb33369b26c6444f12d4f3737d`
Exact span offsets: `[0, 79)`; Gold stance: `contradiction`

```text
The album Get Back (or Let It Be, as it became) was not released until May 1970
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-era-r3-let-it-be-negation",
  "subject_entity_id": "album:let_it_be",
  "predicate": "belongs_to_era",
  "object_entity_id": null,
  "object_value": "1970s",
  "time": {
    "year": 1970
  },
  "source_chunk_id": "chunk:real-era-r3-let-it-be-negation",
  "evidence_span": "The album Get Back (or Let It Be, as it became) was not released until May 1970",
  "extraction_confidence": 0.96,
  "verification_confidence": 0.96
}
```

上下文说明（非 evidence）：Original Let It Be album release, not the song, recording sessions, bootlegs or later reissues.
审核注意：Not until is a release-time exclusion. Do not interpret 1969 recording as a 1960s release.

人工核对点：

- 是否确为原专辑而非同名单曲、录制事件或重版？
- not until May 1970 是否足以否定所询 1960s 发行归属？

Decision: **pending**
Reason: 待人工填写

## real-scene-r3-alice-punk-negation

Query: Alice in Chains 在早期 Seattle punk scene 中是否 belongs_to_scene？只返回该实体对。
relation_type: `scene` · proposed label: `negative` · status: **pending**
所询 source → target: `eval:artist:alice_in_chains` → `scene:seattle_punk_scene`
Predicate: `belongs_to_scene`
Label SHA256: `03ca4d5f94fd9034998bd909acd661bec0dfe12b976c7c3da897b260e52a256b`
Pair: —

Proposed expected relations:
```json
[]
```

标注依据与限制：Steve Turner 明确否定 Alice in Chains 属于早期 punk scene；Seattle 范围来自同一回答上文。否定的是 punk 社群根源，不是否定 grunge 归属。Replay 候选 predicate 表示被否定的命题，exact span 的否定不被去除。

Source: [Mudhoney intervista](https://www.rocknation.it/interviews/mudhoney-intervista/) — Rock Nation / Francesca Cecconi
Locator: English version; Steve Turner answer about his book, following discussion of the Seattle music scene before international breakthrough
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:r3:turner-alice-punk-denial` / `real-scene-r3-alice-punk-negation` / `chunk:real-scene-r3-alice-punk-negation` / `claim:real-scene-r3-alice-punk-negation`
Source content SHA256: `89bb38ab28881953e4f138c854bbdd765eb26e07114fee059e64b79810be8a0a`
Exact span offsets: `[0, 113)`; Gold stance: `contradiction`

```text
Like all the Grunge bands except Alice in Chains, nothing against them, but they were not part of the punk scene.
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-scene-r3-alice-punk-negation",
  "subject_entity_id": "artist:alice_in_chains",
  "predicate": "belongs_to_scene",
  "object_entity_id": null,
  "object_value": "Seattle punk scene",
  "time": null,
  "source_chunk_id": "chunk:real-scene-r3-alice-punk-negation",
  "evidence_span": "Like all the Grunge bands except Alice in Chains, nothing against them, but they were not part of the punk scene.",
  "extraction_confidence": 0.96,
  "verification_confidence": 0.96
}
```

上下文说明（非 evidence）：Alice in Chains membership in the early Seattle punk community.
审核注意：They refers to Alice in Chains. Denial is about punk-scene roots, not Seattle grunge membership; an attributed recollection.

人工核对点：

- they 指代 Alice in Chains、punk scene 范围为 Seattle 是否明确？
- 是否同意将这段参与者回忆作为所询场景关系的否定证据？

Decision: **pending**
Reason: 待人工填写

## real-influence-r3-paik-migung-negation

Query: Nam June Paik → Migung 的 influenced 创作关系是否成立？只返回该实体对。
relation_type: `influence` · proposed label: `negative` · status: **pending**
所询 source → target: `eval:artist:nam_june_paik` → `eval:song:migung`
Predicate: `influenced`
Label SHA256: `d4dd4a1a9788dd5cc35e60160292be453c5fe126d24d80a486f5477acd94ac56`
Pair: —

Proposed expected relations:
```json
[]
```

标注依据与限制：作曲者明确否定 Nam June Paik 对 Migung 的影响。被动 influenced_by 的输入方向为 Migung → Paik，规范关系应为 Paik → Migung；否定与原方向均保留。song 是既有模型对音乐作品的承载类型，需人工确认。

Source: [The Persistent Influence of Korean Musician Hwang Byungki and His “Migung”](https://daily.redbullmusicacademy.com/2018/02/hwang-byungki-migung-interview/) — Red Bull Music Academy Daily / Hasan Hujairi
Locator: Hwang answer to question about conversations with Nam June Paik at the time of composing Migung
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:r3:hwang-migung-paik-denial` / `real-influence-r3-paik-migung-negation` / `chunk:real-influence-r3-paik-migung-negation` / `claim:real-influence-r3-paik-migung-negation`
Source content SHA256: `b7f0e81b0451aac9fe6e082402250a5e86f8ce33acf5e6de29da0a2a287d8bdc`
Exact span offsets: `[0, 42)`; Gold stance: `contradiction`

```text
Migung is not influenced by Nam June Paik.
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-influence-r3-paik-migung-negation",
  "subject_entity_id": "song:migung",
  "predicate": "influenced_by",
  "object_entity_id": "artist:nam_june_paik",
  "object_value": null,
  "time": null,
  "source_chunk_id": "chunk:real-influence-r3-paik-migung-negation",
  "evidence_span": "Migung is not influenced by Nam June Paik.",
  "extraction_confidence": 0.96,
  "verification_confidence": 0.96
}
```

上下文说明（非 evidence）：Nam June Paik influencing the composition Migung; not all of Hwang’s career.
审核注意：The composition uses the existing song node type. Check work identity and the limited influence scope.

人工核对点：

- 是否认可 Migung 的作品身份及现有 song 节点映射？
- 否定是否仅限此作品的创作影响，且规范方向为 Paik → Migung？

Decision: **pending**
Reason: 待人工填写

## real-era-r3-giant-steps-release-conflict

Query: Giant Steps 原专辑首次发行的年代归属（1950s / 1960s）有什么来源冲突？保留双方，不作确定裁决。
relation_type: `era` · proposed label: `positive` · status: **pending**
所询 source → target: `eval:album:giant_steps` → `era:1960s`
Predicate: `belongs_to_era`
Label SHA256: `4af0c20000bbaf8678de3ea4e6840f1ce0519de5c50c0ea6a6d90d2d8bba83fe`
Pair: —

Proposed expected relations:
```json
[
  {
    "source_entity": "eval:album:giant_steps",
    "target_entity": "era:1960s",
    "predicate": "belongs_to_era",
    "evidence_claim_ids": [
      "claim:real-era-r3-giant-steps-release-conflict-1960",
      "claim:real-era-r3-giant-steps-release-conflict-1959"
    ],
    "fields": {
      "start_year": null,
      "end_year": null,
      "temporal_precision": "unknown"
    },
    "conflict_flags": [
      "temporal_conflict"
    ]
  },
  {
    "source_entity": "eval:album:giant_steps",
    "target_entity": "era:1950s",
    "predicate": "belongs_to_era",
    "evidence_claim_ids": [
      "claim:real-era-r3-giant-steps-release-conflict-1960",
      "claim:real-era-r3-giant-steps-release-conflict-1959"
    ],
    "fields": {
      "start_year": null,
      "end_year": null,
      "temporal_precision": "unknown"
    },
    "conflict_flags": [
      "temporal_conflict"
    ]
  }
]
```

标注依据与限制：Rhino 的历史发行段落声称 early 1960；Jeff Pifher 2011 教育文章明确写 released in 1959。两者使用同一 release 谓词，不把录制日期与发行日期直接相比较。proposed positive 指需要返回带 temporal_conflict 的两个互斥年代候选；每个候选关联双方证据且年份为空，不是断言两次首次发行。较弱来源可能有误，仍须保留归因分歧并由人判断地区/版本范围。

Source: [Coltrane: 1960-1964 Mono (Rhino High Fidelity)](https://media.rhino.com/press-release/coltrane-1960-1964-mono-rhino-high-fidelity) — Rhino Media
Locator: Historical Giant Steps paragraph, immediately after description of first Atlantic sessions; NBSP after comma retained
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:r3:rhino-giant-steps-1960` / `real-era-r3-giant-steps-release-conflict-1960` / `chunk:real-era-r3-giant-steps-release-conflict-1960` / `claim:real-era-r3-giant-steps-release-conflict-1960`
Source content SHA256: `cc38602829665b8f4d57e0d9edef02e1cd7bd925921336a6d152deec660c798c`
Exact span offsets: `[0, 70)`; Gold stance: `support`

```text
Released in early 1960, Giant Steps introduced the “Coltrane Changes,”
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1960",
  "subject_entity_id": "album:giant_steps",
  "predicate": "belongs_to_era",
  "object_entity_id": null,
  "object_value": "1960s",
  "time": {
    "year": 1960
  },
  "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1960",
  "evidence_span": "Released in early 1960, Giant Steps introduced the “Coltrane Changes,”",
  "extraction_confidence": 0.96,
  "verification_confidence": 0.96
}
```

上下文说明（非 evidence）：Historical original album release, not the 2025 boxed-set release.
审核注意：Release Date at the top describes the 2025 box. Historical paragraph explicitly states early 1960.

Source: [Cycles And Symmetric Division Of The Octave In The Works Of John Coltrane](https://static1.squarespace.com/static/5b4756089772aefb8c13401c/t/5c36518e0e2e72da3b5fbdd6/1547063701367/Jeff%2BPifher%2BAcademic%2BSeries-Coltrane%2BCycles.pdf) — Jeff Pifher (authored educational essay)
Locator: PDF page 7 (zero-based page 6); Countdown and Giant Steps section, opening sentence, before footnote 21
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:r3:pifher-giant-steps-1959` / `real-era-r3-giant-steps-release-conflict-1959` / `chunk:real-era-r3-giant-steps-release-conflict-1959` / `claim:real-era-r3-giant-steps-release-conflict-1959`
Source content SHA256: `7501aa06ba0cd982888371456c616c614dc4c1206bb11518e35b455368e5c2d2`
Exact span offsets: `[0, 50)`; Gold stance: `contradiction`

```text
Coltrane's album Giant Steps was released in 1959.
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-era-r3-giant-steps-release-conflict-1959",
  "subject_entity_id": "album:giant_steps",
  "predicate": "belongs_to_era",
  "object_entity_id": null,
  "object_value": "1950s",
  "time": {
    "year": 1959
  },
  "source_chunk_id": "chunk:real-era-r3-giant-steps-release-conflict-1959",
  "evidence_span": "Coltrane's album Giant Steps was released in 1959.",
  "extraction_confidence": 0.96,
  "verification_confidence": 0.96
}
```

上下文说明（非 evidence）：Explicit album release-year assertion, not the preceding 1959 recording-year statement.
审核注意：This weaker source may contain a release/recording error. Preserve its attributed claim; do not conclude the dates are equally credible or settle truth by vote.

人工核对点：

- 两处 released 是否指同一原专辑首发及地域/版本范围？
- 是否认可用两个标记 temporal_conflict 的互斥年代候选表达争议，而非两个确定事实？
- 若认为两来源不构成同范围冲突或现有表示不合适，请 REJECT；不可删掉较弱来源以迎合分数。

Decision: **pending**
Reason: 待人工填写

## real-scene-r3-blur-britpop-conflict

Query: Blur 的历史 Britpop 场景归属是否存在外部归类与成员自述的分歧？保留双方，不作确定裁决。
relation_type: `scene` · proposed label: `positive` · status: **pending**
所询 source → target: `eval:artist:blur` → `scene:britpop_movement`
Predicate: `belongs_to_scene`
Label SHA256: `37965125710d230cf198665d316fd46d17aca3a530972df9dbb151ba60c05a50`
Pair: —

Proposed expected relations:
```json
[
  {
    "source_entity": "eval:artist:blur",
    "target_entity": "scene:britpop_movement",
    "predicate": "belongs_to_scene",
    "evidence_claim_ids": [
      "claim:real-scene-r3-blur-britpop-conflict-external",
      "claim:real-scene-r3-blur-britpop-conflict-self"
    ],
    "fields": {},
    "conflict_flags": [
      "attribution_conflict"
    ]
  }
]
```

标注依据与限制：BRIT Awards 将 Blur 归为 Britpop scene 的领军者；2023 回顾访谈成员拒绝 Britpop 身份。proposed relation 必须含双方证据和 attribution_conflict，不能抹去自述。仍可能只是拒绝政治包装而非社会场景归属，需人工决定同一语义范围；若不同范围，应 REJECT 本候选。

Source: [Blur at the BRITs: 1995 - 2012](https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/) — The BRIT Awards
Locator: Paragraph on the 2012 Outstanding Contribution award; historical Britpop scene description
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:r3:brits-blur-membership` / `real-scene-r3-blur-britpop-conflict-external` / `chunk:real-scene-r3-blur-britpop-conflict-external` / `claim:real-scene-r3-blur-britpop-conflict-external`
Source content SHA256: `b6b853efe0dddb40c89ea9782d79cebfea99e278b64ad1dd9135c2a8301210d8`
Exact span offsets: `[0, 106)`; Gold stance: `support`

```text
One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-scene-r3-blur-britpop-conflict-external",
  "subject_entity_id": "artist:blur",
  "predicate": "belongs_to_scene",
  "object_entity_id": null,
  "object_value": "Britpop movement",
  "time": null,
  "source_chunk_id": "chunk:real-scene-r3-blur-britpop-conflict-external",
  "evidence_span": "One of the frontrunners of Britpop, Blur survived the scene and continued recording into the 21st Century.",
  "extraction_confidence": 0.96,
  "verification_confidence": 0.96
}
```

上下文说明（非 evidence）：External classification of Blur within the historical Britpop movement.
审核注意：Britpop must be reviewed as a music scene/movement here, not merely a genre label.

Source: [Blur: ‘We’re not AC/DC. We’ve never been basic. We play too many chords to be basic’](https://english.elpais.com/culture/2023-07-21/blur-were-not-acdc-weve-never-been-basic-we-play-too-many-chords-to-be-basic.html?outputType=amp) — EL PAÍS / Álex Vicente
Locator: Interview paragraph about Blair, England and political appropriation; final two sentences of the answer following Coxon adds
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:r3:elpais-blur-denial` / `real-scene-r3-blur-britpop-conflict-self` / `chunk:real-scene-r3-blur-britpop-conflict-self` / `claim:real-scene-r3-blur-britpop-conflict-self`
Source content SHA256: `257f424837d1b688167b6a5ca344cc55c5655b079dbe919739f0b9b29a9b562b`
Exact span offsets: `[0, 54)`; Gold stance: `contradiction`

```text
We never really did ‘Britpop.’ We’re not part of that.
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-scene-r3-blur-britpop-conflict-self",
  "subject_entity_id": "artist:blur",
  "predicate": "belongs_to_scene",
  "object_entity_id": null,
  "object_value": "Britpop movement",
  "time": null,
  "source_chunk_id": "chunk:real-scene-r3-blur-britpop-conflict-self",
  "evidence_span": "We never really did ‘Britpop.’ We’re not part of that.",
  "extraction_confidence": 0.96,
  "verification_confidence": 0.96
}
```

上下文说明（非 evidence）：Retrospective self-identification concerning historical Britpop, not solely current 2023 membership.
审核注意：May reject a political/national branding rather than deny membership in a broad scene. Human must decide whether this is the same predicate/scope as the external classification.

人工核对点：

- Britpop 在两来源中是同一历史音乐 scene/movement 吗？
- Coxon 的话是在否定 scene membership，还是只在拒绝政治/文化品牌？
- 是否认可 attribution_conflict 表示外部归类与自我认同的争议？现有 Scene 未专门建模此旗标，不能因此降低 Gold 标准。

Decision: **pending**
Reason: 待人工填写

## real-influence-r3-stockhausen-cluster-conflict

Query: Karlheinz Stockhausen → Cluster 的早期音乐 influenced 归因是否有争议？保留作者判断与成员否定。
relation_type: `influence` · proposed label: `positive` · status: **pending**
所询 source → target: `eval:artist:karlheinz_stockhausen` → `eval:artist:cluster`
Predicate: `influenced`
Label SHA256: `74550d6859572fa98c1a8949d3cf1bd812e5209a86522e9d5b1feecc00b0afe8`
Pair: —

Proposed expected relations:
```json
[
  {
    "source_entity": "eval:artist:karlheinz_stockhausen",
    "target_entity": "eval:artist:cluster",
    "predicate": "influenced",
    "evidence_claim_ids": [
      "claim:real-influence-r3-stockhausen-cluster-conflict-report",
      "claim:real-influence-r3-stockhausen-cluster-conflict-denial"
    ],
    "fields": {},
    "conflict_flags": [
      "attribution_conflict"
    ]
  }
]
```

标注依据与限制：Guardian 原创访谈报道作者将 Stockhausen 作为 Cluster 早期电子音乐的借鉴来源；2010 原创采访中 Moebius 对 Stockhausen 影响他们的提问回答 direi di no。作者解释与成员的限定否定不可静默合并。原文 Italian 和上下文依赖保留，不将翻译或补写的英文句子作为 Gold，不把 Kluster 的评论移植到 Cluster。

Source: [An awfully big adventure](https://www.theguardian.com/music/2005/apr/08/popandrock) — The Guardian / Dorian Lynskey
Locator: Historical Cluster paragraph starting Music critics have explained Krautrock; excerpt of the second sentence, whose grammatical subject is Cluster
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:r3:guardian-cluster-stockhausen` / `real-influence-r3-stockhausen-cluster-conflict-report` / `chunk:real-influence-r3-stockhausen-cluster-conflict-report` / `claim:real-influence-r3-stockhausen-cluster-conflict-report`
Source content SHA256: `a798a652865f566e6cd6ebfe22b54fb892e49d79c29c34f63d5b943819128261`
Exact span offsets: `[0, 130)`; Gold stance: `support`

```text
tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-report",
  "subject_entity_id": "artist:karlheinz_stockhausen",
  "predicate": "influenced",
  "object_entity_id": "artist:cluster",
  "object_value": null,
  "time": null,
  "source_chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-report",
  "evidence_span": "tried to translate their spirit, along with musique concrete, psychedelia and Stockhausen, into radical, free-flowing electronica.",
  "extraction_confidence": 0.96,
  "verification_confidence": 0.96
}
```

上下文说明（非 evidence）：Stockhausen’s influence on early Cluster experimental music.
审核注意：The short excerpt relies on the preceding subject Cluster. Review full sentence. This is author interpretation, not a direct admission by Roedelius.

Source: [Cluster: l’intervista a Dieter Moebius e Hans-Joachim Roedelius, marzo 2010](https://www.indie-eye.it/recensore/generi/experimental/cluster-lintervista-di-indie-eye-it.html) — Indie-eye / Federico Fragasso and Michele Baldini
Locator: Moebius answer immediately after question whether Stockhausen influenced them as he did other Krautrock artists
Language: `it`; retrieved_at: `2026-09-26`
IDs: `source:r3:indieeye-cluster-stockhausen-denial` / `real-influence-r3-stockhausen-cluster-conflict-denial` / `chunk:real-influence-r3-stockhausen-cluster-conflict-denial` / `claim:real-influence-r3-stockhausen-cluster-conflict-denial`
Source content SHA256: `9268f84e46dbb53d3e88cebfcc472609eb7eb7bb80cfea5967e57fdcce9f80a4`
Exact span offsets: `[0, 85)`; Gold stance: `contradiction`

```text
Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-influence-r3-stockhausen-cluster-conflict-denial",
  "subject_entity_id": "artist:karlheinz_stockhausen",
  "predicate": "influenced",
  "object_entity_id": "artist:cluster",
  "object_value": null,
  "time": null,
  "source_chunk_id": "chunk:real-influence-r3-stockhausen-cluster-conflict-denial",
  "evidence_span": "Dieter Moebius: direi di no, Stockhausen era troppo intellettuale per i nostri gusti.",
  "extraction_confidence": 0.96,
  "verification_confidence": 0.96
}
```

上下文说明（非 evidence）：Cluster’s historical musical influence attribution to Stockhausen; interview recorded 2010-03-19.
审核注意：Italian original is retained. Direi di no is qualified denial (I would say no). Do not expand it into an absolute denial by every member or all Krautrock bands.

人工核对点：

- Guardian 摘录的主语是否为 Cluster，是否明确构成 influence 而非仅相似性？
- Italian 回答是否是对同一早期音乐影响归因的限定否定？
- 是否认可作者解释与成员自述构成 attribution_conflict，且不是时期不同或只否定某个子风格？

Decision: **pending**
Reason: 待人工填写

## real-influence-r3-pixies-nirvana-forward

Query: Pixies → Nirvana 的 influenced 音乐关系是否被所给证据支持？只返回该实体对。
relation_type: `influence` · proposed label: `positive` · status: **pending**
所询 source → target: `eval:artist:pixies` → `eval:artist:nirvana`
Predicate: `influenced`
Label SHA256: `b054981d582ce133f3cacef46ff4ae6e922df3160db10d8fcae0d511415a513a`
Pair: `pixies-nirvana`

Proposed expected relations:
```json
[
  {
    "source_entity": "eval:artist:pixies",
    "target_entity": "eval:artist:nirvana",
    "predicate": "influenced",
    "evidence_claim_ids": [
      "claim:real-influence-2-positive"
    ],
    "fields": {},
    "conflict_flags": []
  }
]
```

标注依据与限制：同一冻结证据与 claim ID 成对复用，不因 query 反向而翻转 replay claim。正向支持 Pixies → Nirvana；反向负例仅表示该证据不支持 Nirvana → Pixies，不主张现实中绝无反向影响。正向控制与既有 approved 样本关联，不是独立随机样本；新 query 的标签仍待人工审核。

Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame
Locator: Influence > Influences list ending with central Nirvana node; distinguish following Influenced list
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:rockhall-nirvana-influences` / `real-influence-2-positive` / `chunk:real-influence-2-positive` / `claim:real-influence-2-positive`
Source content SHA256: `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`
Exact span offsets: `[0, 111)`; Gold stance: `support`

```text
Influences

R.E.M.

The Pixies

The Melvins

The Raincoats

Bam Bam

Sonic Youth

Fugazi

The Gap Band

Nirvana
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-influence-2-positive",
  "evidence_span": "Influences\n\nR.E.M.\n\nThe Pixies\n\nThe Melvins\n\nThe Raincoats\n\nBam Bam\n\nSonic Youth\n\nFugazi\n\nThe Gap Band\n\nNirvana",
  "extraction_confidence": 0.96,
  "object_entity_id": "artist:nirvana",
  "object_value": null,
  "predicate": "influenced",
  "source_chunk_id": "chunk:real-influence-2-positive",
  "subject_entity_id": "artist:pixies",
  "time": null,
  "verification_confidence": 0.96
}
```

人工核对点：

- 来源给出的 influencer / influenced 是否明确？
- 反向 negative 是否仅限给定证据，并且 source/target 与 query 一致？
- 是否认可两条 query 的 predicate、作品/乐队/个人 scope 与方向配对？

Decision: **pending**
Reason: 待人工填写

## real-influence-r3-pixies-nirvana-reverse

Query: Nirvana → Pixies 的 influenced 音乐关系是否被所给证据支持？只返回该实体对。
relation_type: `influence` · proposed label: `negative` · status: **pending**
所询 source → target: `eval:artist:nirvana` → `eval:artist:pixies`
Predicate: `influenced`
Label SHA256: `c68ab1af3715433976e4566509715bae9ebaaab6b81194673cf91f2175a4f487`
Pair: `pixies-nirvana`

Proposed expected relations:
```json
[]
```

标注依据与限制：同一冻结证据与 claim ID 成对复用，不因 query 反向而翻转 replay claim。正向支持 Pixies → Nirvana；反向负例仅表示该证据不支持 Nirvana → Pixies，不主张现实中绝无反向影响。正向控制与既有 approved 样本关联，不是独立随机样本；新 query 的标签仍待人工审核。

Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame
Locator: Influence > Influences list ending with central Nirvana node; distinguish following Influenced list
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:rockhall-nirvana-influences` / `real-influence-2-positive` / `chunk:real-influence-2-positive` / `claim:real-influence-2-positive`
Source content SHA256: `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`
Exact span offsets: `[0, 111)`; Gold stance: `context`

```text
Influences

R.E.M.

The Pixies

The Melvins

The Raincoats

Bam Bam

Sonic Youth

Fugazi

The Gap Band

Nirvana
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-influence-2-positive",
  "evidence_span": "Influences\n\nR.E.M.\n\nThe Pixies\n\nThe Melvins\n\nThe Raincoats\n\nBam Bam\n\nSonic Youth\n\nFugazi\n\nThe Gap Band\n\nNirvana",
  "extraction_confidence": 0.96,
  "object_entity_id": "artist:nirvana",
  "object_value": null,
  "predicate": "influenced",
  "source_chunk_id": "chunk:real-influence-2-positive",
  "subject_entity_id": "artist:pixies",
  "time": null,
  "verification_confidence": 0.96
}
```

人工核对点：

- 来源给出的 influencer / influenced 是否明确？
- 反向 negative 是否仅限给定证据，并且 source/target 与 query 一致？
- 是否认可两条 query 的 predicate、作品/乐队/个人 scope 与方向配对？

Decision: **pending**
Reason: 待人工填写

## real-influence-r3-pixies-cobain-forward

Query: Pixies → Kurt Cobain 的 inspired 音乐关系是否被所给证据支持？只返回该实体对。
relation_type: `influence` · proposed label: `positive` · status: **pending**
所询 source → target: `eval:artist:pixies` → `eval:artist:kurt_cobain`
Predicate: `inspired`
Label SHA256: `56722573eff250d241dbe7824f630acd971eb56027f6fd9d120233830618e1a2`
Pair: `pixies-cobain`

Proposed expected relations:
```json
[
  {
    "source_entity": "eval:artist:pixies",
    "target_entity": "eval:artist:kurt_cobain",
    "predicate": "inspired",
    "evidence_claim_ids": [
      "claim:real-influence-3-positive"
    ],
    "fields": {},
    "conflict_flags": []
  }
]
```

标注依据与限制：同一冻结证据与 claim ID 成对复用，不因 query 反向而翻转 replay claim。正向支持 Pixies → Kurt Cobain；反向负例仅表示该证据不支持 Kurt Cobain → Pixies，不主张现实中绝无反向影响。正向控制与既有 approved 样本关联，不是独立随机样本；新 query 的标签仍待人工审核。

Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke
Locator: Answer to But you must have had a good time writing it; referent is Smells Like Teen Spirit
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:rollingstone-cobain-pixies` / `real-influence-3-positive` / `chunk:real-influence-3-positive` / `claim:real-influence-3-positive`
Source content SHA256: `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`
Exact span offsets: `[0, 74)`; Gold stance: `support`

```text
I was basically trying to rip off the Pixies. I have to admit it [smiles].
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-influence-3-positive",
  "evidence_span": "I was basically trying to rip off the Pixies. I have to admit it [smiles].",
  "extraction_confidence": 0.96,
  "object_entity_id": "artist:kurt_cobain",
  "object_value": null,
  "predicate": "inspired",
  "source_chunk_id": "chunk:real-influence-3-positive",
  "subject_entity_id": "artist:pixies",
  "time": null,
  "verification_confidence": 0.96
}
```

人工核对点：

- 来源给出的 influencer / influenced 是否明确？
- 反向 negative 是否仅限给定证据，并且 source/target 与 query 一致？
- 是否认可两条 query 的 predicate、作品/乐队/个人 scope 与方向配对？

Decision: **pending**
Reason: 待人工填写

## real-influence-r3-pixies-cobain-reverse

Query: Kurt Cobain → Pixies 的 inspired 音乐关系是否被所给证据支持？只返回该实体对。
relation_type: `influence` · proposed label: `negative` · status: **pending**
所询 source → target: `eval:artist:kurt_cobain` → `eval:artist:pixies`
Predicate: `inspired`
Label SHA256: `ff3879dea3a2d196413b6262f800cd69ee5ad6173283d3d3879501acb1e695b0`
Pair: `pixies-cobain`

Proposed expected relations:
```json
[]
```

标注依据与限制：同一冻结证据与 claim ID 成对复用，不因 query 反向而翻转 replay claim。正向支持 Pixies → Kurt Cobain；反向负例仅表示该证据不支持 Kurt Cobain → Pixies，不主张现实中绝无反向影响。正向控制与既有 approved 样本关联，不是独立随机样本；新 query 的标签仍待人工审核。

Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke
Locator: Answer to But you must have had a good time writing it; referent is Smells Like Teen Spirit
Language: `en`; retrieved_at: `2026-09-26`
IDs: `source:rollingstone-cobain-pixies` / `real-influence-3-positive` / `chunk:real-influence-3-positive` / `claim:real-influence-3-positive`
Source content SHA256: `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`
Exact span offsets: `[0, 74)`; Gold stance: `context`

```text
I was basically trying to rip off the Pixies. I have to admit it [smiles].
```

Replay annotation（独立输入，无 Gold label/review）：
```json
{
  "claim_id": "claim:real-influence-3-positive",
  "evidence_span": "I was basically trying to rip off the Pixies. I have to admit it [smiles].",
  "extraction_confidence": 0.96,
  "object_entity_id": "artist:kurt_cobain",
  "object_value": null,
  "predicate": "inspired",
  "source_chunk_id": "chunk:real-influence-3-positive",
  "subject_entity_id": "artist:pixies",
  "time": null,
  "verification_confidence": 0.96
}
```

人工核对点：

- 来源给出的 influencer / influenced 是否明确？
- 反向 negative 是否仅限给定证据，并且 source/target 与 query 一致？
- 是否认可两条 query 的 predicate、作品/乐队/个人 scope 与方向配对？

Decision: **pending**
Reason: 待人工填写

## 回复格式

```text
reviewer: human-review-WT
reviewed_at: YYYY-MM-DD

case_id: <本包 case_id>
decision: APPROVE 或 REJECT
reason: <原文、实体、predicate、scope、方向或冲突表示的审核理由>
```

请分别审查 10 条，不以两个配对的控制样本重复证明独立覆盖率。r3 不产生 review receipt；收到明确人工结论后再在独立后继版本登记。

# Semantic Relations v1 — r5 Influence confusable 人工审核包

本包仅含 3 条新 pending：Scene→Influence 混淆 2 条、Era→Influence 混淆 1 条。22 条 approved Gold 与 r4 baseline 完全保留，尚未运行 r5 Evaluation。
至少 1 条经明确人工 APPROVE 并在后续独立版本登记 receipt 后，足以补齐现有 Influence confusable coverage；不代表质量失败自动消失。

所有 query relation_type=influence，proposed label=negative，expected_relations=[]。Negative 仅表示本包冻结 evidence 不支持所询 source→target/predicate，不表示现实世界绝无该影响。
请独立打开原始 URL 核对完整上下文；没有回复即保持 pending。若要改实体/scope/predicate/标签，请 REJECT 当前冻结候选，再创建新版本。

Gold span 与 source snapshot 是原文/原始字段，不是中文解释或生成文本。offset 为保留 excerpt 的 Unicode 字符偏移 [start,end)，不是完整 HTML 偏移。
完整 metadata 和 capture fragments 见 sources.jsonl / source_audit.json；HTTP response hash 只作采集 provenance，完整 HTML 未归档。
inputs.jsonl 复用既有 supported_claims：真实非 Influence source fact 与所询 Influence safety hypothesis 同时作为候选输入；后者不是来源已证实的事实、线上抽取结果或 Gold label。
0.96 是沿用的可控候选输入值，不能替代 evidence 强度或解释为影响关系成立的概率。Gold label/expected/review 不传入 runtime input。

Ten 页面正文动态渲染；本包直接捕获官网 HTML script#data 的原始 album.name/released 字段，未凭缓存摘要或生成 UI 文本作 Gold。
Pearl Jam 两个非连续字段用两 LF 分隔，Gold span 只取 released 字段；另一个 name 字段与 locator / verified_album_identity 固定原专辑身份。

请逐条返回 case_id、APPROVE / REJECT 和理由，并给 reviewer / reviewed_at。审核提问在每条结尾。

## real-influence-r5-oasis-blur-britpop-confusable

Query: 现有证据是否支持 Oasis → Blur 的 influenced 音乐影响关系？只返回该实体对。
relation_type: influence；proposed label: negative；review.status: pending
Source → target: `eval:artist:oasis` → `eval:artist:blur`
Candidate predicate: `influenced`
Proposed expected relations: []

Label SHA256: `588e02a26a9264082262b2318af2e3b789a26fa553d25ab6abaadd47e303c69d`
Replay input line SHA256: `a12a4de2f87b608392fb8b4e0d54305b9b3325dd1704147c49d6025894314d5d`

混淆依据与 negative scope：两支乐队在同一 Britpop/BRIT Awards 语境中被描述为竞争者，实体同时出现且音乐相关；场景归类或奖项竞争不足以推出 Oasis 对 Blur 的定向音乐影响。 负例仅表示本次冻结证据不支持所询 Influence，不表示现实中不存在该影响。
Evidence 实际支持的 family：scene

Source: [Blur at the BRITs: 1995 - 2012](https://www.brits.co.uk/news/2012/blur-at-the-brits-1995-2012/) — BRIT Awards / BPI
Locator: Body paragraph describing 1995 awards; continuous excerpt begins Blur at the BRIT Awards and ends at Britpop rivals Oasis.
Language: en；capture_method: verbatim_web_excerpt；retrieved_at: 2026-09-26T07:10:22.267912+00:00
publication_date: unknown（未用事件/发行年冒充）；speaker/author: not stated
IDs: `source:r5:brits-blur-oasis-britpop` / `real-influence-r5-oasis-blur-britpop-confusable:brits` / `chunk:real-influence-r5-oasis-blur-britpop-confusable:brits`
Source content SHA256: `70547a89adcf1b244d65eb057e9bd0cff93ff8e7418a6d45cf4a74ebace40161`

Source excerpt（原文/原始 JSON 字段）：
```text
Blur at the BRIT Awards. The indie boys took home an impressive four awards at the show, triumphing over Britpop rivals Oasis
```

Gold context claim: `claim:real-influence-r5-oasis-blur-britpop-confusable:britpop-association`；exact span offsets: [0, 125)；stance=context
Exact span SHA256: `70547a89adcf1b244d65eb057e9bd0cff93ff8e7418a6d45cf4a74ebace40161`

Source fact annotations（支持非 Influence 关系）：
```json
[
  {
    "claim_id": "claim:real-influence-r5-oasis-blur-britpop-confusable:britpop-association",
    "subject_entity_id": "artist:blur",
    "predicate": "scene_association",
    "object_entity_id": "artist:oasis",
    "object_value": null,
    "time": null,
    "source_chunk_id": "chunk:real-influence-r5-oasis-blur-britpop-confusable:brits",
    "evidence_span": "Blur at the BRIT Awards. The indie boys took home an impressive four awards at the show, triumphing over Britpop rivals Oasis",
    "extraction_confidence": 0.96,
    "verification_confidence": 0.96,
    "scene_name": "Britpop"
  }
]
```

Requested Influence candidate hypotheses（不是已证实 source fact）：
```json
[
  {
    "claim_id": "claim:real-influence-r5-oasis-blur-britpop-confusable:influence-hypothesis",
    "subject_entity_id": "artist:oasis",
    "predicate": "influenced",
    "object_entity_id": "artist:blur",
    "object_value": null,
    "time": null,
    "source_chunk_id": "chunk:real-influence-r5-oasis-blur-britpop-confusable:brits",
    "evidence_span": "Blur at the BRIT Awards. The indie boys took home an impressive four awards at the show, triumphing over Britpop rivals Oasis",
    "extraction_confidence": 0.96,
    "verification_confidence": 0.96
  }
]
```

Human decision: pending；reviewer: —；reviewed_at: —；reason: —

- 完整原始网页是否支持实体身份与非 Influence 事实？
- predicate、source→target 与 influence query family 是否明确？
- 同场景/同年代事实有真实混淆风险，但是否缺少该方向 Influence 归因？
- negative 是否仅限当前 evidence，而非现实影响不存在？
- 原文 span/offset/locator/hash 是否可核对？若不支持标签，请 REJECT 冻结候选，不修改本版本。

## real-influence-r5-soundgarden-nirvana-grunge-confusable

Query: 现有证据是否支持 Soundgarden → Nirvana 的 shaped_style 风格塑造关系？只返回该实体对。
relation_type: influence；proposed label: negative；review.status: pending
Source → target: `eval:artist:soundgarden` → `eval:artist:nirvana`
Candidate predicate: `shaped_style`
Proposed expected relations: []

Label SHA256: `635d6d7ee1a38c54d8941419df8366560e42e298ccdf8ca2a8203d6070852d28`
Replay input line SHA256: `1901029b46412aeeda17f037a655cd580e5a8dc518865e6acba0fb6cc330bd56`

混淆依据与 negative scope：两支乐队同在 Seattle/grunge 与 Sub Pop 历史语境中；原文把集体作用归于 Sub Pop，不能转移为 Soundgarden 塑造 Nirvana 风格的定向断言。 负例仅表示本次冻结证据不支持所询 Influence，不表示现实中不存在该影响。
Evidence 实际支持的 family：scene

Source: [Puget Sound Stories](https://www.mopop.org/experience/puget-sound-stories) — Museum of Pop Culture
Locator: Sub Pop Records section; first sentence beginning Through artists including Nirvana, Mudhoney, Soundgarden, and TAD.
Language: en；capture_method: verbatim_web_excerpt；retrieved_at: 2026-09-26T07:10:23.894417+00:00
publication_date: unknown（未用事件/发行年冒充）；speaker/author: not stated
IDs: `source:r5:mopop-subpop-grunge-artists` / `real-influence-r5-soundgarden-nirvana-grunge-confusable:mopop` / `chunk:real-influence-r5-soundgarden-nirvana-grunge-confusable:mopop`
Source content SHA256: `c70ef361c017712f3a36ac7f519597ce057c6b67dc03601f059a1f61ffed6f31`

Source excerpt（原文/原始 JSON 字段）：
```text
Through artists including Nirvana, Mudhoney, Soundgarden, and TAD, Sub Pop helped define grunge while introducing Seattle to audiences around the world.
```

Gold context claim: `claim:real-influence-r5-soundgarden-nirvana-grunge-confusable:grunge-association`；exact span offsets: [0, 152)；stance=context
Exact span SHA256: `c70ef361c017712f3a36ac7f519597ce057c6b67dc03601f059a1f61ffed6f31`

Source fact annotations（支持非 Influence 关系）：
```json
[
  {
    "claim_id": "claim:real-influence-r5-soundgarden-nirvana-grunge-confusable:grunge-association",
    "subject_entity_id": "artist:soundgarden",
    "predicate": "scene_association",
    "object_entity_id": "artist:nirvana",
    "object_value": null,
    "time": null,
    "source_chunk_id": "chunk:real-influence-r5-soundgarden-nirvana-grunge-confusable:mopop",
    "evidence_span": "Through artists including Nirvana, Mudhoney, Soundgarden, and TAD, Sub Pop helped define grunge while introducing Seattle to audiences around the world.",
    "extraction_confidence": 0.96,
    "verification_confidence": 0.96,
    "scene_name": "Seattle grunge"
  }
]
```

Requested Influence candidate hypotheses（不是已证实 source fact）：
```json
[
  {
    "claim_id": "claim:real-influence-r5-soundgarden-nirvana-grunge-confusable:influence-hypothesis",
    "subject_entity_id": "artist:soundgarden",
    "predicate": "shaped_style",
    "object_entity_id": "artist:nirvana",
    "object_value": null,
    "time": null,
    "source_chunk_id": "chunk:real-influence-r5-soundgarden-nirvana-grunge-confusable:mopop",
    "evidence_span": "Through artists including Nirvana, Mudhoney, Soundgarden, and TAD, Sub Pop helped define grunge while introducing Seattle to audiences around the world.",
    "extraction_confidence": 0.96,
    "verification_confidence": 0.96
  }
]
```

Human decision: pending；reviewer: —；reviewed_at: —；reason: —

- 完整原始网页是否支持实体身份与非 Influence 事实？
- predicate、source→target 与 influence query family 是否明确？
- 同场景/同年代事实有真实混淆风险，但是否缺少该方向 Influence 归因？
- negative 是否仅限当前 evidence，而非现实影响不存在？
- 原文 span/offset/locator/hash 是否可核对？若不支持标签，请 REJECT 冻结候选，不修改本版本。

## real-influence-r5-ten-nevermind-era-confusable

Query: 现有证据是否支持 Pearl Jam 的 Ten → Nirvana 的 Nevermind 的 inspired 创作启发关系？只返回该专辑对。
relation_type: influence；proposed label: negative；review.status: pending
Source → target: `eval:album:ten` → `eval:album:nevermind`
Candidate predicate: `inspired`
Proposed expected relations: []

Label SHA256: `a2d57b9a859369a18148a33ed6f7015abda56b36f519588c96c448d7a6260ad6`
Replay input line SHA256: `5f28952e5db3cfe925fb4f46998a57ed96ed04deecd1649da5fd44ea3b65b439`

混淆依据与 negative scope：两张相近音乐领域的专辑在同一 1991/1990s 发行窗口内；发行时间建立 Era 关系，却不是创作/录制先后，不能证明 Ten 启发 Nevermind。 负例仅表示本次冻结证据不支持所询 Influence，不表示现实中不存在该影响。
Evidence 实际支持的 family：era

Source: [Pearl Jam - Ten](https://pearljam.com/music/album/ten) — Pearl Jam official website
Locator: HTML script[type=application/json]#data, album object id 18592 / name Ten / slug ten; verbatim name and released fields.
Language: en；capture_method: verbatim_web_excerpt；retrieved_at: 2026-09-26T07:10:26.780622+00:00
publication_date: unknown（未用事件/发行年冒充）；speaker/author: not stated
IDs: `source:r5:pearljam-ten-release` / `real-influence-r5-ten-nevermind-era-confusable:pearljam` / `chunk:real-influence-r5-ten-nevermind-era-confusable:pearljam`
Source content SHA256: `868b0dc2b1b07d2f49680ed8446ba48ef389d025b2cf150b3ea01948a53c8238`

Source excerpt（原文/原始 JSON 字段）：
```text
"name": "Ten"

"released": "Aug. 27, 1991"
```

Gold context claim: `claim:real-influence-r5-ten-nevermind-era-confusable:pearljam-release`；exact span offsets: [15, 42)；stance=context
Exact span SHA256: `88a6d1b4c11390838f31f831bf647f998cee9cdc1f2e4efc7da5f9e079359710`

Source fact annotations（支持非 Influence 关系）：
```json
[
  {
    "claim_id": "claim:real-influence-r5-ten-nevermind-era-confusable:pearljam-release",
    "subject_entity_id": "album:ten",
    "predicate": "released",
    "object_entity_id": null,
    "object_value": null,
    "time": {
      "year": 1991
    },
    "source_chunk_id": "chunk:real-influence-r5-ten-nevermind-era-confusable:pearljam",
    "evidence_span": "\"released\": \"Aug. 27, 1991\"",
    "extraction_confidence": 0.96,
    "verification_confidence": 0.96
  }
]
```

Requested Influence candidate hypotheses（不是已证实 source fact）：
```json
[
  {
    "claim_id": "claim:real-influence-r5-ten-nevermind-era-confusable:influence-hypothesis",
    "subject_entity_id": "album:ten",
    "predicate": "inspired",
    "object_entity_id": "album:nevermind",
    "object_value": null,
    "time": null,
    "source_chunk_id": "chunk:real-influence-r5-ten-nevermind-era-confusable:pearljam",
    "evidence_span": "\"released\": \"Aug. 27, 1991\"",
    "extraction_confidence": 0.96,
    "verification_confidence": 0.96
  }
]
```

Source: [Nevermind - Nirvana](https://www.nirvana.com/releases-archive/nevermind/) — Nirvana official website / UME
Locator: Nevermind release archive, heading Nevermind and adjacent visible Released: 1991 field.
Language: en；capture_method: verbatim_web_excerpt；retrieved_at: 2026-09-26T07:10:25.510770+00:00
publication_date: unknown（未用事件/发行年冒充）；speaker/author: not stated
IDs: `source:r5:nirvana-nevermind-release` / `real-influence-r5-ten-nevermind-era-confusable:nirvana` / `chunk:real-influence-r5-ten-nevermind-era-confusable:nirvana`
Source content SHA256: `5af19aaca45ee3c14f0e4ee5ad27acb0b0a63c077fe146597a03f3753fc8ec67`

Source excerpt（原文/原始 JSON 字段）：
```text
Released: 1991
```

Gold context claim: `claim:real-influence-r5-ten-nevermind-era-confusable:nirvana-release`；exact span offsets: [0, 14)；stance=context
Exact span SHA256: `5af19aaca45ee3c14f0e4ee5ad27acb0b0a63c077fe146597a03f3753fc8ec67`

Source fact annotations（支持非 Influence 关系）：
```json
[
  {
    "claim_id": "claim:real-influence-r5-ten-nevermind-era-confusable:nirvana-release",
    "subject_entity_id": "album:nevermind",
    "predicate": "released",
    "object_entity_id": null,
    "object_value": null,
    "time": {
      "year": 1991
    },
    "source_chunk_id": "chunk:real-influence-r5-ten-nevermind-era-confusable:nirvana",
    "evidence_span": "Released: 1991",
    "extraction_confidence": 0.96,
    "verification_confidence": 0.96
  }
]
```

Requested Influence candidate hypotheses（不是已证实 source fact）：
```json
[]
```

Human decision: pending；reviewer: —；reviewed_at: —；reason: —

- 完整原始网页是否支持实体身份与非 Influence 事实？
- predicate、source→target 与 influence query family 是否明确？
- 同场景/同年代事实有真实混淆风险，但是否缺少该方向 Influence 归因？
- negative 是否仅限当前 evidence，而非现实影响不存在？
- 原文 span/offset/locator/hash 是否可核对？若不支持标签，请 REJECT 冻结候选，不修改本版本。

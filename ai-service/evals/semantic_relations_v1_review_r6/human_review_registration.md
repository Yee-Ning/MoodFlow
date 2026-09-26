# Semantic Relations v1 — r6 人工审核登记

Reviewer: human-review-WT；reviewed_at: 2026-09-26；provenance: explicit_user_review。

本轮仅登记用户明确批准的 r5 三条 Influence query-family confusable negative。新 r6 只改变 review 元数据；query、实体对、predicate、label、expected_relations=[]、证据、span、输入与来源均未改变。

Negative 仅表示当前冻结 evidence 不支持所询方向与 predicate，不否认现实世界可能存在其他影响。APPROVE 不代表该 Influence 关系成立，也不代表算法已经通过该 case。

静态清点显示 Influence confusable approved 覆盖为 3，Era / Scene / Influence 的现有必需 categories 均已覆盖。没有运行新 baseline，r4 正式 machine gate 和已发现 failures 保留原状。

## real-influence-r5-oasis-blur-britpop-confusable

Decision: **APPROVE**
Relation type: influence；label: negative；expected_relations=[]
Query: 现有证据是否支持 Oasis → Blur 的 influenced 音乐影响关系？只返回该实体对。
Source → target: eval:artist:oasis → eval:artist:blur
Candidate predicate: `influenced`

原始证据明确将 Blur 与 Oasis 置于同一 Britpop/BRIT Awards 竞争语境，并称 Oasis 为 Britpop rivals。该证据能够支持两者存在同一音乐场景/竞争语境关联，但没有提供 Oasis → Blur 的定向音乐影响归因，也没有 influenced 的因果或创作影响语义。因此 expected_relations=[] 合理。该 negative 仅表示当前 evidence 不支持所询 Influence，不否认现实世界可能存在其他影响关系。

Label SHA256: `588e02a26a9264082262b2318af2e3b789a26fa553d25ab6abaadd47e303c69d`
Parent manifest SHA256: `3fa02d3c1111cdf9ddf9c98849619b7703ffe0c1331749da6f56cb77ebe08d11`
Parent case line SHA256: `392ff554423320f809249d679031dca6a3370a57a88b1d8cb79e39f22c50b06b`
Replay input line SHA256: `a12a4de2f87b608392fb8b4e0d54305b9b3325dd1704147c49d6025894314d5d`
Receipt SHA256: `bd0d9906ed94536f4a6beda2cf8869400aabb93e929edbe0d79d32ceeab2ca75`
Receipt: [real-influence-r5-oasis-blur-britpop-confusable.json](receipts/real-influence-r5-oasis-blur-britpop-confusable.json)

## real-influence-r5-soundgarden-nirvana-grunge-confusable

Decision: **APPROVE**
Relation type: influence；label: negative；expected_relations=[]
Query: 现有证据是否支持 Soundgarden → Nirvana 的 shaped_style 风格塑造关系？只返回该实体对。
Source → target: eval:artist:soundgarden → eval:artist:nirvana
Candidate predicate: `shaped_style`

MoPOP 原文同时列出 Nirvana、Soundgarden 等艺人，并将“helped define grunge”的作用明确归于 Sub Pop 通过这些 artists 所形成的整体历史语境。该证据支持 Seattle/grunge 场景关联，但不能把群体共现场景关系转换为 Soundgarden → Nirvana 的 shaped_style 定向影响。因此当前 evidence 不支持所询 Influence，expected_relations=[] 合理。

Label SHA256: `635d6d7ee1a38c54d8941419df8366560e42e298ccdf8ca2a8203d6070852d28`
Parent manifest SHA256: `3fa02d3c1111cdf9ddf9c98849619b7703ffe0c1331749da6f56cb77ebe08d11`
Parent case line SHA256: `472d3a8031f8a2f4fdd5ad64b2c67f1d21fc276c7aecb8072d9a8c41712e8b8c`
Replay input line SHA256: `1901029b46412aeeda17f037a655cd580e5a8dc518865e6acba0fb6cc330bd56`
Receipt SHA256: `646c6325205d806105cb07b6007d17de43d24cc015bd15a5d8e2a89a1af0226e`
Receipt: [real-influence-r5-soundgarden-nirvana-grunge-confusable.json](receipts/real-influence-r5-soundgarden-nirvana-grunge-confusable.json)

## real-influence-r5-ten-nevermind-era-confusable

Decision: **APPROVE**
Relation type: influence；label: negative；expected_relations=[]
Query: 现有证据是否支持 Pearl Jam 的 Ten → Nirvana 的 Nevermind 的 inspired 创作启发关系？只返回该专辑对。
Source → target: eval:album:ten → eval:album:nevermind
Candidate predicate: `inspired`

Pearl Jam 官方来源支持 Ten 于 1991 年发行，Nirvana 官方来源支持 Nevermind 于 1991 年发行，因此证据能够建立两张专辑的 Era/发行时间背景。共同或相近发行年代本身不构成 Ten → Nevermind 的 inspired 创作启发关系，而且发行时间也不能替代创作、录制或影响方向证据。因此当前 evidence 不支持所询 Influence，expected_relations=[] 合理。

Label SHA256: `a2d57b9a859369a18148a33ed6f7015abda56b36f519588c96c448d7a6260ad6`
Parent manifest SHA256: `3fa02d3c1111cdf9ddf9c98849619b7703ffe0c1331749da6f56cb77ebe08d11`
Parent case line SHA256: `6666d2143d76a93f00f21c33245e49335b6a9134bdb7eaeac440e2a0522a8cf1`
Replay input line SHA256: `5f28952e5db3cfe925fb4f46998a57ed96ed04deecd1649da5fd44ea3b65b439`
Receipt SHA256: `8ff8cf9fcb6e5b309b656dc7c766ec386a99e4cc0acbbd3329e7463b60350301`
Receipt: [real-influence-r5-ten-nevermind-era-confusable.json](receipts/real-influence-r5-ten-nevermind-era-confusable.json)

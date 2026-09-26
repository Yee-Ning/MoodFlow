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

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

## real-era-0-weak_evidence

Query: Love Me Do 的发行年代
Family: era; provisional label: negative
Source → target: `eval:song:love_me_do` → `era:1960s`
Proposed relations: `[]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Love Me Do](https://www.thebeatles.com/love-me-do) — Apple Corps / The Beatles
Locator: Song heading and Release date field; no lyrics captured
Source SHA256: `d79f452b87c77ad12eacc0e413c886e991fa8852394ebaadd22e553857a7be5e`
Exact span offsets: [0, 40); claim: `claim:real-era-0-weak_evidence`
> Love Me Do / Release date: 05 October 1962

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

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

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

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

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

## real-scene-1-weak_evidence

Query: Neko Case 的音乐场景归属
Family: scene; provisional label: negative
Source → target: `eval:artist:neko_case` → `scene:vancouver_indie_scene`
Proposed relations: `[]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Puget Sound Stories - Neko Case](https://www.mopop.org/experience/puget-sound-stories) — Museum of Pop Culture
Locator: Neko Case paragraph; speaker/entity and city coreference require full page review
Source SHA256: `2a9b3671bc0d5460052ab38160d6674328cb5bf37c18bfa2c060ec06089b49d9`
Exact span offsets: [0, 80); claim: `claim:real-scene-1-weak_evidence`
> Case moved to Vancouver, where she became part of the city's vibrant indie scene

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

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

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

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

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

## real-influence-2-weak_evidence

Query: The Pixies 对 Nirvana 的音乐影响
Family: influence; provisional label: negative
Source → target: `eval:artist:nirvana` → `eval:artist:nirvana`
Proposed relations: `[]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Nirvana](https://rockhall.com/inductees/nirvana/) — Rock & Roll Hall of Fame
Locator: Influence > Influences list ending with central Nirvana node; distinguish following Influenced list
Source SHA256: `aecb9646407b1ee3b0ba3fa1846208bb837afe37953b2b9c1ff17cfb94b1294f`
Exact span offsets: [0, 111); claim: `claim:real-influence-2-weak_evidence`
> Influences /  / R.E.M. /  / The Pixies /  / The Melvins /  / The Raincoats /  / Bam Bam /  / Sonic Youth /  / Fugazi /  / The Gap Band /  / Nirvana

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

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

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

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

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

## real-influence-3-weak_evidence

Query: Pixies 对 Kurt Cobain 的创作启发
Family: influence; provisional label: negative
Source → target: `eval:artist:kurt_cobain` → `eval:artist:kurt_cobain`
Proposed relations: `[]`
Rationale: Provisional source-grounded annotation; human must verify full URL context, identity, direction and scope. Gold positive describes the stated relation; weak/confusable query tests a stronger or different unsupported relation. Input claims are evaluation-only annotations, not production extractions. Rejection is evidence-relative, not a global claim of nonexistence.
Source: [Kurt Cobain, the Rolling Stone interview: Success doesn't suck](https://www.rollingstone.co.uk/music/kurt-cobain-the-rolling-stone-interview-anniversary-death-38446/) — Rolling Stone UK / David Fricke
Locator: Answer to But you must have had a good time writing it; referent is Smells Like Teen Spirit
Source SHA256: `c741061de474f204bd0df29c6a339a35561977b44fcd8c574ca7fcbbfbeb2bf8`
Exact span offsets: [0, 74); claim: `claim:real-influence-3-weak_evidence`
> I was basically trying to rip off the Pixies. I have to admit it [smiles].

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

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

Human decision: pending; reviewer: —; reviewed_at: —; notes: No human approval is inferred.

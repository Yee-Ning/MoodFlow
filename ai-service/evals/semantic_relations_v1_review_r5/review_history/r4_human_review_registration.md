# Semantic Relations v1 — r4 人工审核登记

Reviewer: `human-review-WT`; reviewed_at: `2026-09-26`。

本文件逐项登记用户提供的 10 条明确 APPROVE，绑定 r3 冻结的标签、父版本与 replay 行。仅在后继 r4 改变 review 元数据，原 query、entity、label、expected relation、conflict flag、exact span、输入与来源全部保留。尚未运行 Evaluation。

冲突 APPROVE 确认的是保留双方归因/互斥候选的 Gold 设计，不裁定历史事实，也不把不确定关系转为确定事实。reverse negative 仍为 evidence-relative。

## real-era-r3-let-it-be-negation

Decision: **APPROVE**
Relation type: `era`; label: `negative`
Query: Let It Be 专辑的首次正式发行是否 belongs_to_era → 1960s？只返回该实体对。
Source → target: `eval:album:let_it_be` → `era:1960s`

The Beatles 官方来源明确说明 Get Back/Let It Be “was not released until May 1970”。实体为原 Let It Be 专辑，predicate 为首次正式发行年代；该证据明确排除 belongs_to_era → 1960s。1969 录制时间不能替代发行时间，因此 negative 标注正确。

Label SHA256: `e6d960827bc40e44479801eed5a106f2c8084d193a6865ab6b8698f5207d6c7c`
Parent case line SHA256: `fa3852136f410eb70b7ebc0ef9a40d0fd5e6ec2910e90b51405ac1cb05d27190`
Replay input line SHA256: `1e71f4b115ba99b4e619c53d06bc5a154ce141be6fec4088a24d3856ae339b0c`
Receipt: [real-era-r3-let-it-be-negation.json](receipts/real-era-r3-let-it-be-negation.json)

## real-scene-r3-alice-punk-negation

Decision: **APPROVE**
Relation type: `scene`; label: `negative`
Query: Alice in Chains 在早期 Seattle punk scene 中是否 belongs_to_scene？只返回该实体对。
Source → target: `eval:artist:alice_in_chains` → `scene:seattle_punk_scene`

Steve Turner 的完整上下文明确讨论 Seattle music scene，并指出除 Alice in Chains 外其他 grunge bands 来自 Punk Rock，随后明确说明 Alice in Chains “were not part of the punk scene”且青少年时期并不参与 punk shows。该否定限定于早期 Seattle punk scene，不否定其 Seattle/grunge 身份，因此 negative scope 正确。

Label SHA256: `03ca4d5f94fd9034998bd909acd661bec0dfe12b976c7c3da897b260e52a256b`
Parent case line SHA256: `beab884c0c6fd617185f98aa2d06328a3ec33a6f3714028e5af7a27224138db0`
Replay input line SHA256: `cc2d7664d7d021bb05902512a6b03c18838dee981b992fe876e18f8575be3679`
Receipt: [real-scene-r3-alice-punk-negation.json](receipts/real-scene-r3-alice-punk-negation.json)

## real-influence-r3-paik-migung-negation

Decision: **APPROVE**
Relation type: `influence`; label: `negative`
Query: Nam June Paik → Migung 的 influenced 创作关系是否成立？只返回该实体对。
Source → target: `eval:artist:nam_june_paik` → `eval:song:migung`

Hwang Byungki 在被问及创作 Migung 时与 Nam June Paik 的交流后明确表示“Migung is not influenced by Nam June Paik”。作品身份和 influence scope 明确，规范化方向为 Nam June Paik → Migung；该方向被直接否定，因此 negative 标注正确。

Label SHA256: `d4dd4a1a9788dd5cc35e60160292be453c5fe126d24d80a486f5477acd94ac56`
Parent case line SHA256: `c50c25bbb3e0ca329ca35a1f002d729ad3fa1a74720b03dc115383724da5d8b4`
Replay input line SHA256: `cb5c7c1880099f52166cf6844b3625423bdbde40d15bbe7f932697ecfd9e2afc`
Receipt: [real-influence-r3-paik-migung-negation.json](receipts/real-influence-r3-paik-migung-negation.json)

## real-era-r3-giant-steps-release-conflict

Decision: **APPROVE**
Relation type: `era`; label: `positive`
Query: Giant Steps 原专辑首次发行的年代归属（1950s / 1960s）有什么来源冲突？保留双方，不作确定裁决。
Source → target: `eval:album:giant_steps` → `era:1960s`

两个来源对同一 Giant Steps 原专辑 release predicate 给出互斥年份：Rhino 明确写 released in early 1960，另一教育文章写 released in 1959。Gold 不应自行裁定真实性，也不能把两个年代作为两个确定事实；保留 1950s/1960s 两个候选、双方 evidence、temporal_conflict=true，并将年份置空和 precision=unknown，符合 conflict case 设计。

Label SHA256: `4af0c20000bbaf8678de3ea4e6840f1ce0519de5c50c0ea6a6d90d2d8bba83fe`
Parent case line SHA256: `7c66b738f94f135e619b25110b08dd5dc5afa4381d915872bbc5f4b0d7cfb3c4`
Replay input line SHA256: `87b8243a07b9e81245954ba458ff4f3b56cb595e04761230d6d0504d65a8051d`
Receipt: [real-era-r3-giant-steps-release-conflict.json](receipts/real-era-r3-giant-steps-release-conflict.json)

## real-scene-r3-blur-britpop-conflict

Decision: **APPROVE**
Relation type: `scene`; label: `positive`
Query: Blur 的历史 Britpop 场景归属是否存在外部归类与成员自述的分歧？保留双方，不作确定裁决。
Source → target: `eval:artist:blur` → `scene:britpop_movement`

BRIT Awards 明确将 Blur 描述为 Britpop frontrunner 并称其 survived the scene；Blur 成员在后来的直接访谈中则表示“We never really did ‘Britpop.’ We’re not part of that.” 两者均针对 Blur 与历史 Britpop 的归属/认同，虽自述带有政治文化品牌语境，但足以形成外部归类与成员自我认同之间的 attribution conflict。应保留双方 evidence，不裁定单一事实。

Label SHA256: `37965125710d230cf198665d316fd46d17aca3a530972df9dbb151ba60c05a50`
Parent case line SHA256: `cc6394ea123778608c608970200cde54e3e2f51823ac49913ab6e7e7840bf183`
Replay input line SHA256: `918f7a5b4d420a1448457fcee454a3c7a8f8f846b11c3e406f192f3de7b045c5`
Receipt: [real-scene-r3-blur-britpop-conflict.json](receipts/real-scene-r3-blur-britpop-conflict.json)

## real-influence-r3-stockhausen-cluster-conflict

Decision: **APPROVE**
Relation type: `influence`; label: `positive`
Query: Karlheinz Stockhausen → Cluster 的早期音乐 influenced 归因是否有争议？保留作者判断与成员否定。
Source → target: `eval:artist:karlheinz_stockhausen` → `eval:artist:cluster`

Guardian 完整句的主语明确为 Cluster，并将 Stockhausen 与其他来源一起描述为其转化进电子音乐的参考/影响来源；Dieter Moebius 在被直接问及 Stockhausen 是否影响他们时回答“direi di no”，构成对同一历史音乐影响归因的限定否定。因此应保留双方 attribution，并标记 attribution_conflict，而不是静默选择其中一方。

Label SHA256: `74550d6859572fa98c1a8949d3cf1bd812e5209a86522e9d5b1feecc00b0afe8`
Parent case line SHA256: `ab63e470b8407ac642884959d015888ba02f6ba1f278325d55fcdbcf1f82d606`
Replay input line SHA256: `f0b8c61027191e1c8887b58509954d8f4717915bd0e75d264f59388ca4933d90`
Receipt: [real-influence-r3-stockhausen-cluster-conflict.json](receipts/real-influence-r3-stockhausen-cluster-conflict.json)

## real-influence-r3-pixies-nirvana-forward

Decision: **APPROVE**
Relation type: `influence`; label: `positive`
Query: Pixies → Nirvana 的 influenced 音乐关系是否被所给证据支持？只返回该实体对。
Source → target: `eval:artist:pixies` → `eval:artist:nirvana`

Rock & Roll Hall of Fame 的 Nirvana Influence 页面将 The Pixies 明确列在 Nirvana 的 Influences 下，因此证据支持规范方向 Pixies → Nirvana influenced。source、target、predicate 和方向均正确。

Label SHA256: `b054981d582ce133f3cacef46ff4ae6e922df3160db10d8fcae0d511415a513a`
Parent case line SHA256: `671dfca7b89f1efaa17f3e2a9c2c5dc9914dcf013c8afe904cb8f212e9144549`
Replay input line SHA256: `ba05383541250679d6d11e6307b0b1c8bfd747772c0c5989cbeb00d87e824387`
Receipt: [real-influence-r3-pixies-nirvana-forward.json](receipts/real-influence-r3-pixies-nirvana-forward.json)

## real-influence-r3-pixies-nirvana-reverse

Decision: **APPROVE**
Relation type: `influence`; label: `negative`
Query: Nirvana → Pixies 的 influenced 音乐关系是否被所给证据支持？只返回该实体对。
Source → target: `eval:artist:nirvana` → `eval:artist:pixies`

与正向 case 使用同一冻结 evidence，但该 evidence 只支持 Pixies → Nirvana，不支持查询中的 Nirvana → Pixies。该 negative 为 evidence-relative，不断言现实中绝无反向影响，因此反方向控制样本设计正确。

Label SHA256: `c68ab1af3715433976e4566509715bae9ebaaab6b81194673cf91f2175a4f487`
Parent case line SHA256: `28651b27dae5d48a9dab27518c99c2d0833ead6a11919a5422463b2edb549949`
Replay input line SHA256: `5751ee4a1373889aebe391e89eda1d59573b31eb7e8b89b842f446e85a5b4334`
Receipt: [real-influence-r3-pixies-nirvana-reverse.json](receipts/real-influence-r3-pixies-nirvana-reverse.json)

## real-influence-r3-pixies-cobain-forward

Decision: **APPROVE**
Relation type: `influence`; label: `positive`
Query: Pixies → Kurt Cobain 的 inspired 音乐关系是否被所给证据支持？只返回该实体对。
Source → target: `eval:artist:pixies` → `eval:artist:kurt_cobain`

Kurt Cobain 明确表示自己创作时“trying to rip off the Pixies”，完整上下文还说明使用了 Pixies 的 dynamics，因此证据明确支持 Pixies → Kurt Cobain 的 inspired/influence 方向。实体、predicate 和方向正确。

Label SHA256: `56722573eff250d241dbe7824f630acd971eb56027f6fd9d120233830618e1a2`
Parent case line SHA256: `b7f1489943985af7708d931404f0855d9ed1a74963077d73de4f957e3fe87455`
Replay input line SHA256: `bc8816a1f6f110393c0efb99f0ab49300fb78585be336c524db010986c6963b3`
Receipt: [real-influence-r3-pixies-cobain-forward.json](receipts/real-influence-r3-pixies-cobain-forward.json)

## real-influence-r3-pixies-cobain-reverse

Decision: **APPROVE**
Relation type: `influence`; label: `negative`
Query: Kurt Cobain → Pixies 的 inspired 音乐关系是否被所给证据支持？只返回该实体对。
Source → target: `eval:artist:kurt_cobain` → `eval:artist:pixies`

同一证据明确表达 Cobain 从 Pixies 获取创作启发，只支持 Pixies → Kurt Cobain，不支持 Kurt Cobain → Pixies。作为与 forward case 配对的 evidence-relative reverse negative 合理，不应输出反向 inspired relation。

Label SHA256: `ff3879dea3a2d196413b6262f800cd69ee5ad6173283d3d3879501acb1e695b0`
Parent case line SHA256: `2fc2c1d15441c12cf3b41c9c0d717b5c5a067c3eafd44680850ca26976f2e11a`
Replay input line SHA256: `cd57d1ab1dd274333e1485f1aa3ea2f37b43c04ab0bf175f4faf23ba3225c7d0`
Receipt: [real-influence-r3-pixies-cobain-reverse.json](receipts/real-influence-r3-pixies-cobain-reverse.json)

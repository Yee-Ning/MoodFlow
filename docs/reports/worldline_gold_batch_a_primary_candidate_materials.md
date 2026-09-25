# WorldLine Gold Dataset V1 Batch A Primary Candidate Materials

## Status

This package contains source and NetEase catalog identity candidates for the 10 Batch A primary artists. Every record remains `pending_review`. It contains no `accepted` or `rejected` label, no resolved catalog mapping, and no Gold Dataset row.

Collection date: 2026-09-25

## Files

- Source candidate ledger: `ai-service/evals/worldline_gold_v1/source_review/batch_a_primary_source_candidates.jsonl`
- Catalog identity candidate ledger: `ai-service/evals/worldline_gold_v1/identity_review/batch_a_primary_catalog_candidates.jsonl`
- Per-candidate review records: `ai-service/evals/worldline_gold_v1/candidate_review/records/`
- Review tracking: `ai-service/evals/worldline_gold_v1/candidate_review/candidate_review_tracking.jsonl`
- Source checklist: `ai-service/evals/worldline_gold_v1/source_review/source_review_checklist.md`
- Identity checklist: `ai-service/evals/worldline_gold_v1/identity_review/identity_review_checklist.md`

## Source Candidates

| Candidate | Relation hypothesis | Publisher | Source type | Evidence candidate status | Review focus |
|---|---|---|---|---|---|
| 周杰伦 | C-pop | Universal Music Japan | Official label biography | Pending | Confirm page snapshot and registry term mapping |
| 蔡依林 | C-pop | Sony Music Taiwan | Official label editorial | Pending | Decide whether “華語樂壇” directly supports `genre:c_pop` |
| G.E.M. 邓紫棋 | C-pop | i-D | Music editorial | Pending | Check whether sentence-level wording is direct enough |
| 陈奕迅 | C-pop | i-D | Music editorial | Pending | Check whether sentence-level wording is direct enough |
| Kendrick Lamar | Hip-Hop | Apple Music | Streaming catalog editorial | Pending | Decide whether artist playlist classification is eligible evidence |
| Nas | Hip-Hop | Apple Music | Streaming catalog editorial | Pending | Check page identity and classification together |
| Beyoncé | R&B | Apple Music | Streaming catalog editorial | Pending | Decide whether artist playlist classification is eligible evidence |
| Nujabes | Jazz Hip-Hop | CDJapan / Neowing | Retailer release description | Pending | Review retailer provenance and original Japanese wording |
| 宇多田光 | R&B | Web Japan / Japan Echo Inc. | Government portal editorial | Pending | Review the 1999 source's temporal scope |
| Epik High | Hip-Hop | YGEX | Official artist profile | Pending | Confirm group identity remains separate from members |

The source ledger stores a bounded excerpt candidate and its SHA-256 value. The hash covers the excerpt candidate only. It does not claim that a full remote page has been frozen. A reviewer must freeze an eligible source before promotion to Gold.

## Catalog Identity Candidates

The catalog candidates were collected through NetEase artist search (`type=100`) and checked against the corresponding artist detail endpoint. Search rank is discovery evidence only and is not an identity decision.

| Candidate | Catalog reference candidate | Catalog display name | Main collision risk | Status |
|---|---|---|---|---|
| 周杰伦 | `netease:artist:6452` | 周杰伦 | Multiple exact or alias-name duplicate entries | Pending |
| 蔡依林 | `netease:artist:7219` | 蔡依林 | `Tsai` / `Cai` Romanization and collaboration page | Pending |
| G.E.M. 邓紫棋 | `netease:artist:7763` | G.E.M.邓紫棋 | Stage-name collisions | Pending |
| 陈奕迅 | `netease:artist:2116` | 陈奕迅 | Multiple exact Romanized-name entries | Pending |
| Kendrick Lamar | `netease:artist:37995` | Kendrick Lamar | Collaboration page | Pending |
| Nas | `netease:artist:40424` | Nas | Short-name collision, including Lil Nas X | Pending |
| Beyoncé | `netease:artist:48436` | Beyoncé | Duplicate-name and tribute pages | Pending |
| Nujabes | `netease:artist:162138` | Nujabes | Exact-name duplicate catalog entry | Pending |
| 宇多田光 | `netease:artist:18122` | 宇多田ヒカル | Cross-script name reconciliation | Pending |
| Epik High | `netease:artist:126682` | EPIK HIGH | Member pages returned beside the group | Pending |

Each catalog row includes the query URL, artist detail URL, aliases or translation, representative tracks, and observed collision candidates. `final_catalog_reference` remains `null` in every row.

## Human Review Procedure

1. Open one JSON record in `candidate_review/records/` and its matching source and catalog ledger rows.
2. Follow the source checklist. Verify the publisher, page identity, exact span, direct relation support, and whether a stable local snapshot can be created later.
3. Follow the identity checklist. Verify the canonical name, aliases, region, representative works, and all listed collisions. Do not choose an ID by search rank alone.
4. Record reviewer progress in `candidate_review_tracking.jsonl`. Keep the status `pending` until the responsible reviewer has completed the corresponding checklist.
5. If a candidate fails review, use the existing exclusion workflow. Do not copy it into the Gold Dataset.

## Current Limits

- Remote pages have not been frozen into fixture files.
- The source excerpt hashes are not full-page content hashes.
- Streaming playlist classifications require an explicit policy decision before they can count as final evidence.
- The Jolin Tsai source uses Chinese-language music-scene wording rather than the exact registry label `C-pop`.
- The Nujabes source is a retailer release description and needs a source-quality decision.
- Catalog identities remain candidates even when names, aliases, and representative tracks align.

## Explicit Non-Outcomes

- No candidate has been accepted or rejected.
- No identity has been marked `resolved`, `ambiguous`, or `unresolved`.
- No relation evidence has been promoted to final evidence.
- No `worldline_gold_v1.jsonl` file has been created.
- No business code, RAG component, knowledge base, database, or Evaluation runner was changed.

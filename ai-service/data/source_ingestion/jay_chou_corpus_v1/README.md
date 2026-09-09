# 周杰伦音乐知识 Corpus V1 — Source Ingestion

This directory is the human-review boundary for the first Music RAG corpus.
`sources_candidates.json` contains traceable public sources discovered from
official artist/label pages, interviews, mainstream or academic publications,
Wikipedia/Wikidata, and MusicBrainz. Every initial candidate is `pending`.

The V1 workflow stops at raw capture:

```text
Source Discovery -> Candidate Manifest -> Human Approval -> Fetch -> Raw Snapshot
```

It does not parse pages, extract article bodies, convert to Markdown, clean,
deduplicate, chunk, link entities, extract claims, embed, or index content.

From `ai-service`, validate and inspect the queue:

```powershell
python scripts/source_ingestion.py validate
python scripts/source_ingestion.py list --status pending
```

Record a human decision, then fetch only an approved source:

```powershell
python scripts/source_ingestion.py review jc-v1-001 approved
$env:MOODFLOW_INGESTION_USER_AGENT = "MoodFlowMusicRAG/1.0 (contact: you@example.com)"
python scripts/source_ingestion.py fetch --source-id jc-v1-001
```

To revoke approval before fetching, set the status back to `pending` or to
`rejected`. Bulk fetch is explicit and still selects only approved entries:

```powershell
python scripts/source_ingestion.py fetch --all-approved
```

The fetch client checks `robots.txt`, identifies itself, paces requests per
origin (1.1 seconds by default), rejects HTTP errors and unexpected media types,
and limits response bodies to 50 MiB by default. Access controls are never
bypassed. Human reviewers remain responsible for checking source-specific terms
and copyright constraints before changing a status to `approved`.

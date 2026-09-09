# Raw snapshots

`html/`, `pdf/`, `json/`, and `txt/` contain byte-for-byte HTTP response bodies
created only after a candidate is marked `approved`.

Files use `<source_id>--<UTC timestamp>--<sha256 prefix>.<type>` names and are
opened with exclusive-create semantics. Existing snapshots are never replaced.
`sources_manifest.jsonl` is an append-only provenance ledger generated at fetch
time. Raw payloads and the generated ledger are intentionally ignored by Git to
avoid accidentally committing copyrighted or large source material.

Do not place parsed text, normalized Markdown, cleaned documents, chunks, or
embeddings in this directory.

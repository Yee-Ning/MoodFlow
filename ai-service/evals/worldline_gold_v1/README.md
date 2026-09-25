# WorldLine Gold V1 Candidate Review Workspace

This directory is an evaluation-only preparation workspace. Nothing here is an approved Gold label or a production knowledge record.

## Layout

- `candidate_review/candidate_pool.jsonl`: 14 discovery candidates, all `pending_review`.
- `candidate_review/candidate_review_workbook.md`: generated human review workbook.
- `candidate_review/candidate_exclusions.jsonl`: initially empty; reviewers append excluded candidates only after a real review.
- `source_review/source_review_checklist.md`: source and evidence review gate.
- `identity_review/identity_review_checklist.md`: knowledge-to-catalog identity gate.
- `templates/worldline_gold_candidate_template.json`: blank pending review record.
- `templates/candidate_exclusion_template.json`: exclusion record shape.
- `candidate_review_tools.py`: validates the workspace and regenerates the workbook.

## Commands

Run from `ai-service`:

```powershell
.\.venv\Scripts\python.exe evals\worldline_gold_v1\candidate_review_tools.py validate
.\.venv\Scripts\python.exe evals\worldline_gold_v1\candidate_review_tools.py generate-workbook
.\.venv\Scripts\python.exe evals\worldline_gold_v1\candidate_review_tools.py validate-record `
  --record evals\worldline_gold_v1\candidate_review\records\<candidate>.json
```

Validation intentionally rejects any candidate pool row or candidate template that is pre-labeled `accepted` or `rejected`. Final Gold conversion is outside this preparation workspace and requires a later explicit instruction after human review.

## Review order

1. Open the generated candidate workbook.
2. Use the source checklist to locate and freeze eligible source material.
3. Copy the candidate template for the candidate being reviewed.
4. Record source, exact span, offsets and content hash without assigning a final outcome.
5. Complete the identity checklist independently.
6. Record exclusions only when a reviewer has established an allowed exclusion reason.
7. Keep every candidate pending until owner review in a later phase.

Do not write this workspace into the production Knowledge Base, Entity Registry, WorldLine graph or database.

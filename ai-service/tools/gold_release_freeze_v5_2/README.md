# West-approved Gold V5.2 Phase 2 freeze

This offline operator applies only the ten approved cases to the hash-pinned
V5.1 frozen-2 source. It copies every other physical JSONL line verbatim,
including its existing release binding and freeze metadata. It does not alter
production bindings, earlier Gold versions, the knowledge release, or Qdrant.

`approved_plan.json` records the approved Parent lists and Codex's implementation
of the approved rewrite directions. Fact-to-anchor bindings distinguish positive
evidence from scope limits. It is not a separate digital signature by West.

Run from `ai-service`:

```powershell
.venv/Scripts/python.exe -B -m pytest --noconftest -p no:cacheprovider -q tools/gold_release_freeze_v5_2/test_freeze_gold_v5_2.py
.venv/Scripts/python.exe -B -X utf8 tools/gold_release_freeze_v5_2/freeze_gold_v5_2.py
.venv/Scripts/python.exe -B -X utf8 tools/gold_release_freeze_v5_2/freeze_gold_v5_2.py --check
```

The first execution refuses existing output, uses the actual execution time in
Asia/Shanghai, validates all changes before publication, writes with exclusive
creation, and sets the new dataset read-only. `--check` replays the same pure
transformation using the recorded timestamp and writes nothing.

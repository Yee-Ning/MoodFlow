# Recommendation Evaluation V1 Report

## 1. Run Metadata

- Dataset kind: `synthetic_contract_fixture`
- Git commit: `646fc1db082fffbdd0daf26a5974d66cb391c0db`
- Evaluation timestamp: `2026-10-11T04:46:54.690695+00:00`
- Configuration digest: `sha256:bb65b57d927a31023a60b8224fd82bbd008ce053fa75832c667811bf3f65c991`
- Holdout: 20%; fixed seed: 20261011
- Conversion window: 7.0 days

## 2. Metrics

| Mode | Experiment | Variant | Configuration | Result | Samples |
|---|---|---|---|---|---:|
| Online | exp | control | sha256:weights | 100.0% (1/1) | 1 |
| Online | exp | treatment | sha256:weights | 0.0% (0/1) | 1 |

## 3. Failure / Exclusion Audit

{"invalid_log_rows": 0, "other_conversation_events": 0, "rejected_receipts": {}}

## 4. Scope and Limits

- Random holdout measures preference recovery, not temporal generalization or niche discovery quality.
- Replay scope: existing_curator_ranking_stage; no LLM selection or external writes. Ranking-only CLI examples do not measure LLM selection or recall quality.
- Online conversion is receipt-based observation, not proof that recommendation caused a like; independent user actions remain possible.
- Join scope is the existing authenticated conversation, not cross-conversation user tracking.
- Repeated exposures are separate units; a receipt can qualify overlapping windows. Duplicate event ingestion is removed.
- Failed/unknown snapshots, previously liked songs, unsuccessful recommendations and unmatured windows are excluded.
- Playback completion: not_connected; no computed value. No significance test or sticky A/B storage.
- Synthetic examples validate contracts only and are not real recommendation quality measurements.

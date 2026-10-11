# Recommendation Evaluation V1 Report

## 1. Run Metadata

- Dataset kind: `synthetic_contract_fixture`
- Git commit: `646fc1db082fffbdd0daf26a5974d66cb391c0db`
- Evaluation timestamp: `2026-10-11T04:46:54.186826+00:00`
- Configuration digest: `sha256:bb65b57d927a31023a60b8224fd82bbd008ce053fa75832c667811bf3f65c991`
- Holdout: 20%; fixed seed: 20261011
- Conversion window: 7.0 days

## 2. Metrics

| Mode | Experiment | Variant | Configuration | Result | Samples |
|---|---|---|---|---|---:|
| Offline | synthetic-exp | control | sha256:82c663f8e9d0f22c219fc4b11b04f079b3a34193eacddbca55f8c22e1f9d98e1 | 100.0% (3/3) | 3 |

## 3. Failure / Exclusion Audit

{}

## 4. Scope and Limits

- Random holdout measures preference recovery, not temporal generalization or niche discovery quality.
- Replay scope: existing_curator_read_select_phase; first business write refused before implementation. Ranking-only CLI examples do not measure LLM selection or recall quality.
- Online conversion is receipt-based observation, not proof that recommendation caused a like; independent user actions remain possible.
- Join scope is the existing authenticated conversation, not cross-conversation user tracking.
- Repeated exposures are separate units; a receipt can qualify overlapping windows. Duplicate event ingestion is removed.
- Failed/unknown snapshots, previously liked songs, unsuccessful recommendations and unmatured windows are excluded.
- Playback completion: not_connected; no computed value. No significance test or sticky A/B storage.
- Synthetic examples validate contracts only and are not real recommendation quality measurements.

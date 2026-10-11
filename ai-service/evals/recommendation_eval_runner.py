"""Read-only evaluation over existing ranking and scoped Journal read interfaces."""

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from hashlib import sha256
from pathlib import Path
import subprocess

from app.agent.foundation.long_horizon.history_writer import digest
from app.agent.handoff.curator import _catalog
from app.agent.handoff.recommendation import RecommendationWeights, rank_recommendations, order_recommendation_selection
from app.schemas.music import MusicSearchResult
from evals.recommendation_metrics import (
    EvaluationConfig, METRIC_DEFINITIONS, PLAY_COMPLETION_STATUS, exposure_from_event,
    historical_like_hit_rate, metric, online_conversion, random_holdout, read_journal_like_receipts,
)
from evals.worldline_eval_report import _format_metric


def replay_existing_ranking(training_ids, *, catalog_response, knowledge_ids=(), tags=(), weights=None, k=15):
    """Replay Curator's existing read-only ranking stage, not playlist writes/LLM selection.

    Candidates must come from an independent saved search response. Never add
    held-out song IDs to recall or pass the validation set into the scorer.
    """
    if type(k) is not int or not 0 <= k <= 20:
        raise ValueError("k must be an integer in [0, 20]")
    response = MusicSearchResult.model_validate(catalog_response)
    candidates = _catalog(response.model_dump_json(), response.keyword)
    weights = weights if weights is not None else RecommendationWeights()
    ranked = rank_recommendations(candidates, training_ids, set(knowledge_ids), tags, weights=weights)[:60]
    selected = order_recommendation_selection(ranked[:k], training_ids, set(knowledge_ids), tags, weights=weights)
    return [song.song_id for song in selected]


def offline_replay(liked_ids, recommend, config, *, experiment_id=None, variant_id=None, weights=None):
    training, validation = random_holdout(liked_ids, config)
    weights = weights if weights is not None else RecommendationWeights()
    # Even empty input is passed honestly; a catalog/knowledge-only ranking may be possible.
    recommended = recommend(training) if validation else []
    score = historical_like_hit_rate(validation, recommended)
    return {"experiment_id": experiment_id, "variant_id": variant_id,
            "configuration_digest": digest(asdict(weights)), "weights": asdict(weights),
            "holdout_ratio": config.holdout_ratio, "seed": config.seed,
            "training_size": len(training), "validation_size": len(validation),
            "status": "measured" if validation else "empty_validation", **score}


async def replay_curator_read_phase(training_ids, *, task, state, runtime, request, model, tools, operations,
                                   weights=None, experiment=None):
    """Run the unchanged Curator through recall/knowledge/budget/model selection.

    Standalone evaluation process only: scoped patches refuse every business
    mutation BEFORE calling its implementation. A fresh disposable Journal may
    record the refused boundary, but no write is sent to the music service.
    Returning the validated pre-write selection does not fabricate write success.
    """
    from unittest.mock import patch
    from app.agent.handoff import curator
    from app.agent.foundation.long_horizon.operations import active_operations
    from app.tools.music import MusicServiceClient, MusicServiceError
    from evals.recommendation_metrics import song_ids

    training = sorted(song_ids(training_ids))
    if operations.write_events():
        raise ValueError("replay requires a fresh disposable evaluation Journal")
    blocked = []
    original_request = MusicServiceClient._request

    async def read_likes(client):
        return training

    async def refuse_write(client, *args, **kwargs):
        blocked.append(True)
        raise MusicServiceError("http_403", "evaluation_write_blocked")

    async def guarded_http(client, method, path, **kwargs):
        if method.upper() != "GET":
            raise MusicServiceError("http_403", "evaluation_write_blocked")
        return await original_request(client, method, path, **kwargs)

    token = active_operations.set(operations)
    try:
        with (patch.object(MusicServiceClient, "get_liked_song_ids", read_likes),
              patch.object(MusicServiceClient, "_request", guarded_http),
              patch.object(MusicServiceClient, "create_playlist", refuse_write),
              patch.object(MusicServiceClient, "add_song_to_playlist", refuse_write),
              patch.object(MusicServiceClient, "remove_song_from_playlist", refuse_write),
              patch.object(MusicServiceClient, "set_song_liked", refuse_write),
              patch.object(MusicServiceClient, "delete_playlist", refuse_write),
              patch.object(curator, "record_recommendation", lambda *args, **kwargs: None)):
            result = await curator.execute_curation(task, state, runtime, request=request,
                model=model, tools=tools, weights=weights, experiment=experiment)
    finally:
        active_operations.reset(token)
    if len(blocked) != 1:
        raise ValueError("Curator did not reach the guarded selection boundary; replay is not a valid measurement")
    data = result.data["curation"]
    if data["accepted_song_ids"]:
        raise ValueError("evaluation unexpectedly accepted a business mutation")
    return [row["song_id"] for row in data["selected_songs"]]


async def offline_replay_async(liked_ids, recommend, config, *, experiment_id=None, variant_id=None, weights=None):
    """Full read-phase adapter: validation labels never reach the callback."""
    training, validation = random_holdout(liked_ids, config)
    recommended = await recommend(training) if validation else []
    weights = weights if weights is not None else RecommendationWeights()
    return {"experiment_id": experiment_id, "variant_id": variant_id,
            "configuration_digest": digest(asdict(weights)), "weights": asdict(weights),
            "holdout_ratio": config.holdout_ratio, "seed": config.seed,
            "training_size": len(training), "validation_size": len(validation),
            "status": "measured" if validation else "empty_validation",
            **historical_like_hit_rate(validation, recommended)}


def aggregate_offline(rows):
    groups = {}
    for row in rows:
        key = row["experiment_id"], row["variant_id"], row["configuration_digest"]
        group = groups.setdefault(key, {"numerator": 0, "denominator": 0, "cases": 0})
        group["numerator"] += row["numerator"]
        group["denominator"] += row["denominator"]
        group["cases"] += 1
    return [{"experiment_id": key[0], "variant_id": key[1], "configuration_digest": key[2],
             "cases": value["cases"], **metric(value["numerator"], value["denominator"])}
            for key, value in groups.items()]


def load_recommendation_events(path):
    """Accept JSONL event exports or existing '<marker> <JSON>' structured logs."""
    events, excluded = [], 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if "curation.recommendation " in line:
            line = line.split("curation.recommendation ", 1)[1]
        elif not line.lstrip().startswith("{"):
            continue
        try:
            events.append(exposure_from_event(json.loads(line)))
        except (ValueError, TypeError, KeyError):
            excluded += 1
    return events, excluded


def write_report(output, *, offline, online, config, dataset_kind, evidence, exclusions=None,
                 replay_scope="existing_curator_ranking_stage; no LLM selection or external writes"):
    """Reuse evals JSON + Markdown conventions and existing metric rendering."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)  # Never overwrite old/frozen reports.
    git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    report = {"schema_version": "recommendation-eval-v1", "evaluation_timestamp": datetime.now(UTC).isoformat(),
              "git_commit": git_commit, "dataset_kind": dataset_kind, "configuration": asdict(config),
              "configuration_digest": digest(asdict(config)), "metric_definitions": METRIC_DEFINITIONS,
              "offline_cases": offline, "offline_groups": aggregate_offline(offline), "online_groups": online,
              "play_completion_status": PLAY_COMPLETION_STATUS, "play_completion_rate": None,
              "significance_test": "not_performed", "evidence": evidence, "exclusions": exclusions or {},
              "replay_scope": replay_scope}
    lines = ["# Recommendation Evaluation V1 Report", "", "## 1. Run Metadata", "",
             f"- Dataset kind: `{dataset_kind}`", f"- Git commit: `{git_commit}`",
             f"- Evaluation timestamp: `{report['evaluation_timestamp']}`",
             f"- Configuration digest: `{report['configuration_digest']}`",
             f"- Holdout: {config.holdout_ratio:.0%}; fixed seed: {config.seed}",
             f"- Conversion window: {config.conversion_window_days} days", "", "## 2. Metrics", "",
             "| Mode | Experiment | Variant | Configuration | Result | Samples |",
             "|---|---|---|---|---|---:|"]
    for mode, rows in (("Offline", report["offline_groups"]), ("Online", online)):
        for row in rows:
            lines.append(f"| {mode} | {row['experiment_id']} | {row['variant_id']} | {row['configuration_digest']} | "
                         f"{_format_metric(row)} | {row['sample_size']} |")
    lines += ["", "## 3. Failure / Exclusion Audit", "", json.dumps(report["exclusions"], ensure_ascii=False),
              "", "## 4. Scope and Limits", "",
              "- Random holdout measures preference recovery, not temporal generalization or niche discovery quality.",
              f"- Replay scope: {replay_scope}. Ranking-only CLI examples do not measure LLM selection or recall quality.",
              "- Online conversion is receipt-based observation, not proof that recommendation caused a like; independent user actions remain possible.",
              "- Join scope is the existing authenticated conversation, not cross-conversation user tracking.",
              "- Repeated exposures are separate units; a receipt can qualify overlapping windows. Duplicate event ingestion is removed.",
              "- Failed/unknown snapshots, previously liked songs, unsuccessful recommendations and unmatured windows are excluded.",
              "- Playback completion: not_connected; no computed value. No significance test or sticky A/B storage.",
              "- Synthetic examples validate contracts only and are not real recommendation quality measurements."]
    (output / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def run_online_job(log_path, store, identity, *, expected_auth_scope, observed_until, output, config=None,
                   dataset_kind="observed_logs_and_journal"):
    """Batch calculation using an existing authorized store; does not initialize PG/DDL.

    The caller supplies the store and already-authorized identity/scope. Never
    derive a user identity from public logs, or export scope/liking history.
    """
    config = config if config is not None else EvaluationConfig()
    exposures, invalid = load_recommendation_events(log_path)
    receipts, rejected = read_journal_like_receipts(store, identity, expected_auth_scope=expected_auth_scope)
    thread = identity.conversation_id.removeprefix("handoff:v1:") if identity.graph_mode == "handoff" else identity.conversation_id
    matching = [row for row in exposures if (row.graph_mode, row.thread_key) == (identity.graph_mode, digest(thread))]
    groups = online_conversion(matching, receipts, config, observed_until=observed_until)
    return write_report(output, offline=[], online=groups, config=config, dataset_kind=dataset_kind,
        evidence={"log_sha256": sha256(Path(log_path).read_bytes()).hexdigest(),
                  "observed_until": observed_until.isoformat(), "verified_receipts": len(receipts)},
        exclusions={"invalid_log_rows": invalid, "other_conversation_events": len(exposures) - len(matching),
                    "rejected_receipts": rejected})


def main():
    parser = argparse.ArgumentParser(description="Read-only current-like holdout ranking replay")
    parser.add_argument("--cases", type=Path, required=True, help="JSON cases from saved like/search read responses")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--holdout-ratio", type=float, default=EvaluationConfig().holdout_ratio)
    parser.add_argument("--seed", type=int, default=EvaluationConfig().seed)
    args = parser.parse_args()
    body = json.loads(args.cases.read_text(encoding="utf-8"))
    config = EvaluationConfig(holdout_ratio=args.holdout_ratio, seed=args.seed)
    rows = []
    for case in body["cases"]:
        weights = RecommendationWeights(**case.get("weights", {}))
        rows.append(offline_replay(case["liked_ids"], lambda training: replay_existing_ranking(
            training, catalog_response=case["catalog_response"], knowledge_ids=case.get("knowledge_ids", ()),
            tags=case.get("tags", ()), weights=weights, k=case.get("k", 15)), config,
            experiment_id=case.get("experiment_id"), variant_id=case.get("variant_id"), weights=weights))
    write_report(args.output, offline=rows, online=[], config=config,
                 dataset_kind=body["dataset_kind"], evidence={"input_digest": digest(body)})


if __name__ == "__main__":
    main()

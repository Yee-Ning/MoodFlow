"""Markdown reporting for WorldLine evaluation runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.worldline_eval_models import WorldLineEvalRun


def _format_metric(value: dict[str, Any]) -> str:
    score = value.get("value")
    if score is None:
        rendered = "N/A"
    else:
        rendered = f"{score:.1%}"
    return f"{rendered} ({value.get('numerator', 0)}/{value.get('denominator', 0)})"


def render_worldline_report(
    run: WorldLineEvalRun,
    metrics: dict[str, Any],
    path: Path,
) -> Path:
    summary = metrics["summary"]
    semantic = metrics["semantic_quality"]
    grounding = metrics["evidence_grounding"]
    exploration = metrics["exploration_quality"]
    identity = metrics["identity_resolution"]
    reliability = metrics["system_reliability"]
    failures = [result for result in run.results if not result.passed]
    is_gold = run.dataset_kind == "real_gold_fixture"
    lines = [
        (
            f"# WorldLine Real Gold {run.dataset_version.upper()} Evaluation Report"
            if is_gold
            else "# WorldLine Evaluation V0 Report"
        ),
        "",
        "## 1. Run Metadata",
        "",
        f"- Dataset version: `{run.dataset_version}`",
        f"- Dataset kind: `{run.dataset_kind}`",
        f"- Dataset SHA256: `{run.dataset_sha256}`",
        *(
            [f"- Source ledger SHA256: `{run.source_ledger_sha256}`"]
            if run.source_ledger_sha256
            else []
        ),
        f"- Git commit: `{run.git_commit}`",
        f"- Evaluation timestamp: `{run.evaluation_timestamp}`",
        (
            "- Dataset provenance: reviewed real-gold fixtures with loader-verified source-ledger integrity."
            if is_gold
            else "- Dataset provenance: synthetic contract fixtures approved for deterministic regression; these labels are not independent real-world music fact annotations."
        ),
        "",
        "## 2. Summary",
        "",
        f"- Cases: {summary['cases']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Skipped: {summary['skipped']}",
        f"- Errors: {summary['errors']}",
        f"- Execution success: {_format_metric(summary['execution_success_rate'])}",
        "",
        "## 3. Quality Metrics",
        "",
        "| Area | Metric | Result |",
        "|---|---|---:|",
        f"| Semantic | Relation precision | {_format_metric(semantic['semantic_relation_precision'])} |",
        f"| Semantic | Relation recall | {_format_metric(semantic['semantic_relation_recall'])} |",
        f"| Semantic | Grounded relation precision | {_format_metric(semantic['grounded_relation_precision'])} |",
        f"| Semantic | Expected rejection accuracy | {_format_metric(semantic['expected_rejection_accuracy'])} |",
        f"| Evidence | Provenance validity | {_format_metric(grounding['semantic_evidence_provenance_validity'])} |",
        f"| Evidence | Citation validity | {_format_metric(grounding['citation_validity'])} |",
        f"| Evidence | Validator classification accuracy | {_format_metric(grounding['grounding_validator_accuracy'])} |",
        f"| Exploration | Success rate | {_format_metric(exploration['genre_expansion_success_rate'])} |",
        f"| Exploration | Artist precision | {_format_metric(exploration['genre_artist_precision'])} |",
        f"| Exploration | Artist recall | {_format_metric(exploration['genre_artist_recall'])} |",
        f"| Exploration | Action gate accuracy | {_format_metric(exploration['action_gate_accuracy'])} |",
        f"| Identity | Resolution success | {_format_metric(identity['knowledge_catalog_resolution_success_rate'])} |",
        f"| Identity | Catalog accuracy | {_format_metric(identity['catalog_identity_accuracy'])} |",
        f"| Identity | Ambiguity detection | {_format_metric(identity['ambiguity_detection_rate'])} |",
        f"| Identity | Unresolved reason accuracy | {_format_metric(identity['unresolved_reason_accuracy'])} |",
        f"| Identity | False executable identity | {_format_metric(identity['false_executable_identity_rate'])} |",
        f"| Reliability | Graph merge success | {_format_metric(reliability['graph_merge_success_rate'])} |",
        f"| Reliability | Invalid merge rejection | {_format_metric(reliability['invalid_merge_rejection_rate'])} |",
        f"| Reliability | Session continuity | {_format_metric(reliability['session_continuity_rate'])} |",
        f"| Reliability | Checkpoint recovery | {_format_metric(reliability['checkpoint_recovery_success_rate'])} |",
        f"| Reliability | Post-recovery exploration | {_format_metric(reliability['post_recovery_exploration_success'])} |",
        f"| Reliability | Failure atomicity | {_format_metric(reliability['failure_atomicity_rate'])} |",
        "",
        "## 4. Yield Diagnostics",
        "",
        f"- Semantic accepted relation rate: {_format_metric(semantic['accepted_relation_rate'])}",
        f"- Semantic rejection rate: {_format_metric(semantic['rejection_rate'])}",
        f"- Genre evidence acceptance: {_format_metric(exploration['evidence_acceptance_rate'])}",
        f"- Genre catalog resolution yield: {_format_metric(exploration['catalog_resolution_yield'])}",
        f"- Genre projection yield: {_format_metric(exploration['projection_yield'])}",
        f"- Valid artist yield mean/P50/P95: {exploration['valid_artist_yield']['mean']} / {exploration['valid_artist_yield']['p50']} / {exploration['valid_artist_yield']['p95']}",
        "",
        "Yield metrics are diagnostic baselines. A lower yield caused by a correct evidence or identity rejection is not treated as a quality failure.",
        "",
        "## 5. Failure Audit",
        "",
    ]
    if failures:
        lines.extend(
            [
                "| Case | Tier | Task | Status | Reasons |",
                "|---|---|---|---|---|",
                *[
                    f"| {item.case_id} | {item.tier.value} | {item.task.value} | {item.status} | {'; '.join(item.failures)} |"
                    for item in failures
                ],
            ]
        )
    else:
        lines.append("No failed, skipped, or errored cases in this run.")
    lines.extend(
        [
            "",
            "## 6. Scope and Limits",
            "",
            "- The current scope covers artist → genre and executable Hip-Hop genre → artist only.",
            (
                "- Real Gold inputs remain evaluation-only and do not modify the production Knowledge Base or Entity Registry."
                if is_gold
                else "- Component and session fixtures are frozen synthetic contracts. They verify implementation behavior but do not establish real-world music knowledge accuracy."
            ),
            "- PostgreSQL metrics are N/A unless the run includes the postgres tier with `RUN_WORLDLINE_EVAL_POSTGRES=1`.",
            "- RAG ranking, Embedding, RRF and Reranker quality are outside this evaluation scope.",
            "- No LLM Judge is used for deterministic relation, identity, graph, citation or checkpoint checks.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

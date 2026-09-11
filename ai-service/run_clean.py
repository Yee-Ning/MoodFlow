"""Operator CLI for the Music RAG Clean v0.1 offline pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Sequence


AI_SERVICE_ROOT = Path(__file__).resolve().parent
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.rag.clean.config import DEFAULT_CLEAN_CONFIG_PATH, load_clean_config
from app.rag.clean.corpus import NormalizedCorpusReader
from app.rag.clean.discovery import (
    ExistingLLMNoiseDiscoveryRuntime,
    NoiseDiscoveryService,
    write_candidate_rules_report,
)
from app.rag.clean.errors import CleanPipelineError
from app.rag.clean.profiling import NormalizedCorpusProfiler, write_profile_report
from app.rag.clean.sampling import (
    StratifiedSampler,
    load_sampling_manifest,
    write_sampling_manifest,
)
from app.rag.clean.service import CleanPipelineService


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile, sample, discover candidates, and clean normalized music documents."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("profile", "sample", "discover-noise", "clean"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--config", type=Path, default=DEFAULT_CLEAN_CONFIG_PATH)
        subparser.add_argument("--normalized-root", type=Path)
        subparser.add_argument("--manifest", type=Path)

    profile = subparsers.choices["profile"]
    profile.add_argument("--output", type=Path)

    sample = subparsers.choices["sample"]
    sample.add_argument("--output", type=Path)
    sample.add_argument("--seed", type=int)
    sample.add_argument("--size", type=int)

    discover = subparsers.choices["discover-noise"]
    discover.add_argument("--sampling-manifest", type=Path)
    discover.add_argument("--output", type=Path)

    clean = subparsers.choices["clean"]
    clean.add_argument("--dry-run", action="store_true")
    clean.add_argument("--run-id")
    clean.add_argument("--cleaned-output-root", type=Path)
    clean.add_argument("--report-root", type=Path)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    project_root: Path = AI_SERVICE_ROOT,
) -> int:
    args = parse_args(argv)
    root = project_root.resolve()
    try:
        config_path = _resolve(root, args.config)
        config, checksum = load_clean_config(config_path)
        normalized_root = _resolve(root, args.normalized_root)
        manifest = _resolve(root, args.manifest)
        reader = NormalizedCorpusReader(
            root,
            normalized_root=normalized_root,
            manifest_path=manifest,
        )
        if args.command == "profile":
            report = NormalizedCorpusProfiler(config.profiling).profile(
                reader.load_all()
            )
            output = _resolve(root, args.output) or (
                root
                / "data"
                / "clean"
                / "reports"
                / config.clean_version
                / "profile"
                / "profile_report.json"
            )
            write_profile_report(report, output)
            payload = {"output": _relative(root, output), **report["text_length"]}
            payload["document_count"] = report["document_count"]
        elif args.command == "sample":
            sampling_config = config.sampling.model_copy(
                update={
                    **({"seed": args.seed} if args.seed is not None else {}),
                    **({"sample_size": args.size} if args.size is not None else {}),
                }
            )
            sampling = StratifiedSampler(sampling_config).sample(reader.load_all())
            output = _resolve(root, args.output) or (
                root
                / "data"
                / "clean"
                / "reports"
                / config.clean_version
                / "sampling"
                / "sampling_manifest.json"
            )
            write_sampling_manifest(sampling, output)
            payload = {
                "output": _relative(root, output),
                "seed": sampling.seed,
                "requested_size": sampling.requested_size,
                "actual_size": sampling.actual_size,
                "dimensions_used": sampling.dimensions_used,
                "degraded_dimensions": sampling.degraded_dimensions,
            }
        elif args.command == "discover-noise":
            sampling_path = _resolve(root, args.sampling_manifest) or (
                root
                / "data"
                / "clean"
                / "reports"
                / config.clean_version
                / "sampling"
                / "sampling_manifest.json"
            )
            service = NoiseDiscoveryService(
                config.noise_discovery,
                clean_version=config.clean_version,
                max_text_chars_per_document=config.sampling.max_text_chars_per_document,
                runtime=ExistingLLMNoiseDiscoveryRuntime(),
            )
            report = asyncio.run(
                service.discover(reader.load_all(), load_sampling_manifest(sampling_path))
            )
            output = _resolve(root, args.output) or (
                root
                / "data"
                / "clean"
                / "reports"
                / config.clean_version
                / "noise_discovery"
                / "candidate_rules.json"
            )
            write_candidate_rules_report(report, output)
            payload = {
                "output": _relative(root, output),
                "candidate_count": report["candidate_count"],
                "human_review_required": True,
                "registry_modified": False,
            }
        else:
            service = CleanPipelineService(
                root,
                config,
                registry_checksum=checksum,
                normalized_root=normalized_root,
                normalized_manifest_path=manifest,
                cleaned_output_root=_resolve(root, args.cleaned_output_root),
                report_root=_resolve(root, args.report_root),
            )
            outcome = service.run(dry_run=args.dry_run, run_id=args.run_id)
            payload = {
                **outcome.summary,
                "report_directory": _relative(root, outcome.report_paths.directory),
            }
    except (CleanPipelineError, OSError, ValueError) as exc:
        _print({"status": "failed", "error": str(exc).splitlines()[0][:300]})
        return 2
    _print(payload)
    return 0


def _resolve(root: Path, value: Path | None) -> Path | None:
    if value is None:
        return None
    return value.resolve() if value.is_absolute() else (root / value).resolve()


def _relative(root: Path, value: Path) -> str:
    resolved = value.resolve()
    return (
        resolved.relative_to(root).as_posix()
        if resolved.is_relative_to(root)
        else str(resolved)
    )


def _print(payload: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())

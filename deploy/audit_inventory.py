"""File-size inventory only: no file contents, no deletion, no Docker mutation."""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def category(relative: str) -> tuple[str, str]:
    parts = relative.replace("\\", "/").split("/")
    if any(p in {".venv-eval", "visual-qa"} for p in parts):
        return "C", "non-runtime Eval/visual QA environment; retain locally"
    if any(p in {".venv", "node_modules", "target", "dist",
                 "__pycache__", ".pytest_cache", ".ruff_cache"} for p in parts):
        return "B", "rebuild/install"
    if parts[0] in {".git", ".idea"} or any(p in {"evals", "tmp", "tmp_gold_check", ".dev",
            ".deepeval", "visual-qa", "reports", "audits"} for p in parts):
        return "C", "archive/exclude; retain locally"
    if parts[0] == "ai-service" and len(parts) > 1 and parts[1] == "data":
        if relative.startswith(("ai-service/data/knowledge/releases/worldline-final-native-release-20260927/v2/",
                                "ai-service/data/indexing/worldline/final-native-20260927/v4/")):
            return "A", "approved artifact; only locked files exported"
        return "C", "unselected corpus/index/staging; retain locally"
    if parts[-1].startswith(".env") and parts[-1] != ".env.example":
        return "C", "local environment; secrets provisioned separately"
    if parts[0] == "ai-service" and len(parts) > 1 and parts[1] == "artifacts":
        if relative.startswith("ai-service/artifacts/intent_model/") and (
                "/encoder/" in relative or parts[-1] in {
                    "classifier_head.pt", "label_mapping.json", "thresholds.json", "training_config.json"}):
            return "A", "foundation intent classifier runtime asset"
        return "C", "training metrics/error analysis; retain locally"
    if any(p in {"tests", "scripts"} for p in parts):
        return "C", "development checks; migration CLI explicitly included in image"
    return "A", "source/config/runtime assets; Docker uses narrower allowlist"


def inventory(root: Path) -> tuple[list[dict], list[str]]:
    totals = defaultdict(lambda: {"bytes": 0, "files": 0})
    skipped = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    relative = Path(entry.path).relative_to(root).as_posix()
                    if entry.is_symlink() or entry.is_junction():
                        skipped.append(relative)
                    elif entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        bucket = "/".join(relative.split("/")[:2])
                        group, recommendation = category(relative)
                        key = (bucket, group, recommendation)
                        totals[key]["bytes"] += entry.stat(follow_symlinks=False).st_size
                        totals[key]["files"] += 1
        except OSError:
            skipped.append(directory.relative_to(root).as_posix())
    rows = [{"path": path, "category": group, **values, "recommendation": recommendation}
            for (path, group, recommendation), values in totals.items()]
    return sorted(rows, key=lambda r: r["bytes"], reverse=True), skipped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, skipped = inventory(args.root.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "method": "logical bytes; no contents read",
               "total_bytes": sum(r["bytes"] for r in rows), "rows": rows, "skipped_links_or_errors": skipped}
    (args.output / "size_inventory.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (args.output / "size_inventory.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["path", "category", "bytes", "files", "recommendation"])
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"total_bytes": payload["total_bytes"], "groups": len(rows), "skipped": len(skipped)}))


if __name__ == "__main__":
    main()

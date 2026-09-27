"""Export/verify the pinned embedding cache; no inference, download or Eval."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from deploy.artifacts import digest, within

LOCK_PATH = Path(__file__).with_name("model-cache-manifest.json")


def verify_model_cache(root: Path) -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    for relative, expected in lock["files"].items():
        path = within(root, relative)
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"model_cache_missing_or_changed:{relative}")
    return lock


def export_model_cache(source: Path, destination: Path) -> dict:
    lock = verify_model_cache(source)
    destination.mkdir(parents=True, exist_ok=False)
    for relative in lock["files"]:
        target = within(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Materialize Windows cache links into ordinary files, portable to Linux.
        shutil.copyfile(within(source, relative), target)
    return verify_model_cache(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.home() / ".cache/huggingface")
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    lock = export_model_cache(args.root, args.export) if args.export else verify_model_cache(args.root)
    print(json.dumps({"verified": True, "model": lock["model"], "revision": lock["revision"]}))


if __name__ == "__main__":
    main()

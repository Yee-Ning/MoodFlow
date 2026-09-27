"""Read-only artifact contract; no index build, retrieval or evaluation."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

LOCK_PATH = Path(__file__).with_name("frozen-artifacts.json")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return "sha256:" + value.hexdigest()


def within(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("artifact_path_outside_root")
    return path


def verify(root: Path, lock: dict | None = None) -> dict:
    lock = lock or json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    for relative, expected in lock["files"].items():
        path = within(root, relative)
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"artifact_missing_or_changed:{relative}")
    manifest = json.loads(within(root, lock["manifest_path"]).read_text(encoding="utf-8"))
    for key in ("collection_name", "source_release_id", "projection_fingerprint"):
        if manifest[key] != lock[key]:
            raise ValueError(f"frozen_identity_mismatch:{key}")
    if manifest["ready_for_retrieval"] is not True or manifest["projected_count"] != 41:
        raise ValueError("frozen_index_not_ready")
    return lock


def export(root: Path, destination: Path) -> dict:
    lock = verify(root)
    # A new directory only: never overwrite/delete user artifacts.
    destination.mkdir(parents=True, exist_ok=False)
    for relative in lock["files"]:
        target = within(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(within(root, relative), target)
    verify(destination, lock)
    return lock


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("ai-service"))
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    lock = export(args.root, args.export) if args.export else verify(args.root)
    print(json.dumps({"verified": True, "release": lock["source_release_id"],
                      "files": len(lock["files"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Prepare a checkout without reading or replacing an existing environment file."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def verify_models(root: Path) -> int:
    bundle = root / "ai-service/artifacts/intent_model"
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = (bundle / relative).resolve()
        if not path.is_relative_to(bundle.resolve()):
            raise ValueError("Model manifest path is outside the model directory")
        if not path.is_file():
            raise ValueError(f"Missing model file: {relative}; run git lfs pull")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            if stream.read(128).startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise ValueError("Model weights are LFS pointers; install Git LFS and run git lfs pull")
            stream.seek(0)
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError(f"Model checksum mismatch: {relative}; run git lfs pull")
    return len(manifest["files"])


def create_environment(root: Path) -> bool:
    if (root / ".env").exists():
        return False
    template = (root / ".env.example").read_text(encoding="utf-8")
    try:
        with (root / ".env").open("x", encoding="utf-8", newline="\n") as output:
            output.write(template)
    except FileExistsError:
        return False
    return True


def run(command: list[str], root: Path) -> None:
    subprocess.run(command, cwd=root, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check prerequisites without creating files")
    args = parser.parse_args(argv)
    try:
        for executable in ("git", "docker"):
            if not shutil.which(executable):
                raise ValueError(f"Install {executable} before continuing")
        run(["docker", "compose", "version"], ROOT)
        run(["docker", "info", "--format", "{{.OSType}}"], ROOT)
        if not args.check:
            run(["git", "submodule", "sync", "--recursive"], ROOT)
            run(["git", "submodule", "update", "--init", "--recursive"], ROOT)
            run(["git", "lfs", "pull"], ROOT)
        if not (ROOT / "netease-music-api/app.js").is_file():
            raise ValueError("Missing NetEase API submodule; run git submodule update --init --recursive")
        print(f"Verified {verify_models(ROOT)} model files.")
        if not args.check:
            print("Created .env from the example." if create_environment(ROOT) else "Kept the existing .env.")
        print("Configure your own LLM settings in .env, then run: docker compose up --build -d")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Setup failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

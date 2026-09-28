"""Read-only ECS deployment input checks. Never opens credential files."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
from pathlib import Path

from deploy.artifacts import verify
from deploy.model_cache import verify_model_cache


ROOT = Path(__file__).resolve().parents[1]
TORCH_WHEEL = "torch-2.14.0+cpu-cp312-cp312-manylinux_2_28_x86_64.whl"
TORCH_SHA256 = "a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260"


def check_sha256(path: Path, expected: str, label: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError(f"invalid_expected_sha256:{label}")
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"missing_or_empty:{label}")
    actual = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            actual.update(block)
    if actual.hexdigest() != expected:
        raise ValueError(f"sha256_mismatch:{label}")


def check_inputs(
    checkout: Path,
    artifacts: Path,
    postgres_dump: Path,
    postgres_sha256: str,
    qdrant_snapshot: Path,
    qdrant_sha256: str,
    env_file: Path,
    secret_files: tuple[Path, Path, Path],
    *,
    check_platform: bool = False,
) -> dict:
    if check_platform and (platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}):
        raise ValueError("production_host_requires_linux_amd64")
    for relative in ("netease-music-api/app.js", "netease-music-api/pnpm-lock.yaml"):
        if not (checkout / relative).is_file():
            raise ValueError("netease_submodule_not_initialized")
    if not env_file.is_file():
        raise ValueError("missing_env_file")
    for label, path in zip(("postgres_password", "llm_api_key", "qdrant_api_key"), secret_files, strict=True):
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"missing_or_empty_secret_file:{label}")
    check_sha256(artifacts / "wheelhouse" / TORCH_WHEEL, TORCH_SHA256, "cpu_torch_wheel")
    lock = verify(artifacts)
    model = verify_model_cache(artifacts / "huggingface")
    check_sha256(postgres_dump, postgres_sha256, "postgres_dump")
    if not qdrant_snapshot.name.startswith(lock["collection_name"] + "-") or qdrant_snapshot.suffix != ".snapshot":
        raise ValueError("qdrant_snapshot_collection_mismatch")
    check_sha256(qdrant_snapshot, qdrant_sha256, "qdrant_snapshot")
    return {"verified": True, "release": lock["source_release_id"],
            "collection": lock["collection_name"], "model_revision": model["revision"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=ROOT)
    parser.add_argument("--artifacts", type=Path, default=ROOT / "deployment-artifacts")
    parser.add_argument("--postgres-dump", type=Path, required=True)
    parser.add_argument("--postgres-sha256", required=True)
    parser.add_argument("--qdrant-snapshot", type=Path, required=True)
    parser.add_argument("--qdrant-sha256", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--postgres-password-file", type=Path, required=True)
    parser.add_argument("--llm-api-key-file", type=Path, required=True)
    parser.add_argument("--qdrant-api-key-file", type=Path, required=True)
    parser.add_argument("--check-platform", action="store_true")
    args = parser.parse_args()
    try:
        result = check_inputs(
            args.checkout, args.artifacts, args.postgres_dump, args.postgres_sha256,
            args.qdrant_snapshot, args.qdrant_sha256, args.env_file,
            (args.postgres_password_file, args.llm_api_key_file, args.qdrant_api_key_file),
            check_platform=args.check_platform,
        )
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"deployment preflight failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

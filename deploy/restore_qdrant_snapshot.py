"""Restore the single frozen Qdrant collection into a fresh production volume."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

from deploy.artifacts import LOCK_PATH
from deploy.ecs_preflight import check_sha256


def restore(snapshot: Path, expected_sha256: str, *, endpoint: str, secret_file: Path) -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    collection = lock["collection_name"]
    if not snapshot.name.startswith(collection + "-") or snapshot.suffix != ".snapshot":
        raise ValueError("qdrant_snapshot_collection_mismatch")
    check_sha256(snapshot, expected_sha256, "qdrant_snapshot")
    key = secret_file.read_text(encoding="utf-8").strip()
    if not key or any(char in key for char in '\r\n"\\'):
        raise ValueError("invalid_qdrant_secret_file")
    url = endpoint.rstrip("/") + "/collections/" + collection
    with httpx.Client(headers={"api-key": key}, timeout=300, trust_env=False) as client:
        existing = client.get(url)
        if existing.status_code != 404:
            existing.raise_for_status()
            raise ValueError("qdrant_collection_already_exists")
        with snapshot.open("rb") as stream:
            response = client.post(url + "/snapshots/upload", params={"wait": "true", "priority": "snapshot",
                                                                       "checksum": expected_sha256},
                                   files={"snapshot": (snapshot.name, stream, "application/octet-stream")})
        response.raise_for_status()
        if response.json().get("result") is not True:
            raise ValueError("qdrant_snapshot_restore_failed")
        info = client.get(url)
        info.raise_for_status()
        result = info.json()["result"]
        if result["status"] != "green" or result["points_count"] != 41:
            raise ValueError("qdrant_restored_collection_not_ready")
    return {"restored": True, "collection": collection, "points": 41}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    try:
        result = restore(args.snapshot, args.sha256, endpoint=os.environ["QDRANT_URL"],
                         secret_file=Path("/run/secrets/qdrant_api_key"))
    except (ValueError, OSError, KeyError, httpx.HTTPError):
        # Authentication or URL details must not be printed by the CLI.
        parser.exit(1, "Qdrant restore failed; inspect snapshot, secret and collection readiness\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()

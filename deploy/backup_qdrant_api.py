"""Run by stdin inside ai-service; emit only non-secret snapshot metadata."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx


def schema(info: dict) -> dict:
    result = info["result"]
    params = result["config"]["params"]
    vectors = params["vectors"]
    sparse = params["sparse_vectors"]
    return {"status": result["status"], "points_count": result["points_count"],
            "dense": {name: {"size": value["size"], "distance": value["distance"]}
                      for name, value in vectors.items()},
            "sparse": {name: {"modifier": value.get("modifier")}
                       for name, value in sparse.items()}}


def main() -> None:
    collection = sys.argv[1]
    try:
        key = Path("/run/secrets/qdrant_api_key").read_text(encoding="utf-8").strip()
        if not key:
            raise ValueError("empty_key")
        base = "http://qdrant:6333/collections/" + collection
        with httpx.Client(timeout=120, trust_env=False, headers={"api-key": key}) as client:
            before = client.get(base)
            before.raise_for_status()
            current = schema(before.json())
            if current["status"] != "green" or current["points_count"] < 1:
                raise ValueError("collection_not_ready")
            created = client.post(base + "/snapshots", params={"wait": "true"})
            created.raise_for_status()
            payload = created.json()
            if payload.get("status") != "ok":
                raise ValueError("snapshot_create_failed")
            snapshot = payload["result"]
        print(json.dumps({"collection": collection, "schema": current,
                          "snapshot": {"name": snapshot["name"], "size": snapshot["size"],
                                       "checksum": snapshot.get("checksum")}}, sort_keys=True))
    except (OSError, KeyError, ValueError, httpx.HTTPError):
        print("qdrant_snapshot_api_failed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

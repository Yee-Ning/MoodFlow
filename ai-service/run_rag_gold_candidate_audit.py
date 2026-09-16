"""Audit a Gold amendment proposal against the immutable source release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.rag.evaluation.gold_candidate_audit import audit_proposals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(
        audit_proposals(args.gold, args.proposals, args.release_manifest),
        ensure_ascii=False,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()

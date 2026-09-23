"""Summarize existing evaluation cases annotated with Jev decision outcomes.

Input is JSONL. Each row may be an existing case extended with a decision_type,
baseline/Jev outcomes and optional gold labels; no new dataset format is required.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.agent.decision.jev.evaluation import summarize_decisions


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(json.dumps(summarize_decisions(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

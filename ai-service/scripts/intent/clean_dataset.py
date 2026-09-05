import argparse
import json
import sys
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.agent.foundation.intent_dataset.cleaning import clean_dataset


def parse_args() -> argparse.Namespace:
    data_root = AI_SERVICE_ROOT / "data" / "intent"
    parser = argparse.ArgumentParser(
        description="Clean, validate, deduplicate, and isolate intent conflicts."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=data_root / "raw" / "generated.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root / "processed" / "cleaned.jsonl",
    )
    parser.add_argument(
        "--conflicts-output",
        type=Path,
        default=data_root / "processed" / "conflicts.jsonl",
    )
    parser.add_argument(
        "--rejected-output",
        type=Path,
        default=data_root / "processed" / "rejected.jsonl",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = clean_dataset(
        args.input,
        args.output,
        args.conflicts_output,
        args.rejected_output,
    )
    print(json.dumps(summary.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

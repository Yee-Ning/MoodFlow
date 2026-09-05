import argparse
import json
import sys
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.agent.foundation.intent_dataset.validation import validate_dataset_artifacts


def parse_args() -> argparse.Namespace:
    data_root = AI_SERVICE_ROOT / "data" / "intent"
    parser = argparse.ArgumentParser(
        description="Validate intent data, distributions, conflicts, and leakage."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=data_root / "processed" / "cleaned.jsonl",
    )
    parser.add_argument(
        "--splits-dir",
        type=Path,
        default=data_root / "splits",
    )
    parser.add_argument(
        "--conflicts",
        type=Path,
        default=data_root / "processed" / "conflicts.jsonl",
    )
    parser.add_argument(
        "--rejected",
        type=Path,
        default=data_root / "processed" / "rejected.jsonl",
    )
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=data_root / "taxonomy.json",
    )
    parser.add_argument(
        "--label-mapping",
        type=Path,
        default=data_root / "label_mapping.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON summary path.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = validate_dataset_artifacts(
        args.input,
        args.splits_dir,
        args.conflicts,
        args.taxonomy,
        args.label_mapping,
        args.rejected,
    )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    if not summary["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

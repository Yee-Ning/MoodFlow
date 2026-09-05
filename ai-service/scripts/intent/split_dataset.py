import argparse
import json
import sys
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.agent.foundation.intent_dataset.splitting import split_dataset


def parse_args() -> argparse.Namespace:
    data_root = AI_SERVICE_ROOT / "data" / "intent"
    parser = argparse.ArgumentParser(
        description="Create reproducible label-aware multi-label dataset splits."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=data_root / "processed" / "cleaned.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root / "splits",
    )
    parser.add_argument(
        "--manual-test",
        type=Path,
        default=data_root / "splits" / "manual_test.jsonl",
    )
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--test-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = split_dataset(
        args.input,
        args.output,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
        random_seed=args.seed,
        manual_test_path=args.manual_test,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

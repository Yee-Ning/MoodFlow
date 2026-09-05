import argparse
import json
import sys
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.agent.foundation.intent_dataset.generation import generate_dataset


def parse_args() -> argparse.Namespace:
    data_root = AI_SERVICE_ROOT / "data" / "intent"
    parser = argparse.ArgumentParser(
        description="Build a raw multi-label intent dataset from manual seeds."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=data_root / "raw" / "manual_seeds.jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_root / "raw" / "generated.jsonl",
    )
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=data_root / "taxonomy.json",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--variants-per-seed", type=int, default=1)
    parser.add_argument(
        "--synthetic-input",
        action="append",
        type=Path,
        default=[],
        help="Optional provider-generated JSONL already marked source=synthetic.",
    )
    parser.add_argument(
        "--rule-bootstrap-input",
        action="append",
        type=Path,
        default=[],
        help="Optional Rule Router bootstrap JSONL marked source=rule_bootstrap.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = generate_dataset(
        args.input,
        args.output,
        args.taxonomy,
        random_seed=args.seed,
        variants_per_seed=args.variants_per_seed,
        synthetic_input_paths=args.synthetic_input,
        rule_bootstrap_input_paths=args.rule_bootstrap_input,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

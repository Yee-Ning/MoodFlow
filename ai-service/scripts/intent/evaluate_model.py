import argparse
import json
import sys
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.agent.foundation.intent_model.metrics import write_error_analysis
from app.agent.foundation.intent_model.training import evaluate_saved_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a saved multi-label intent model.")
    parser.add_argument(
        "--model-dir", type=Path, default=AI_SERVICE_ROOT / "artifacts" / "intent_model"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=AI_SERVICE_ROOT / "data" / "intent" / "splits" / "test.jsonl",
    )
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--errors-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary, errors = evaluate_saved_model(
        model_dir=args.model_dir,
        dataset_path=args.input,
        batch_size=args.batch_size,
        device=args.device,
    )
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    if args.errors_output is not None:
        write_error_analysis(args.errors_output, errors)


if __name__ == "__main__":
    main()

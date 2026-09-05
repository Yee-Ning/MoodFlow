import argparse
import json
import sys
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.agent.foundation.intent_model.artifacts import load_model_bundle
from app.agent.foundation.intent_model.inference import predict_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run standalone intent prediction.")
    parser.add_argument("--text", required=True)
    parser.add_argument(
        "--model-dir", type=Path, default=AI_SERVICE_ROOT / "artifacts" / "intent_model"
    )
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bundle = load_model_bundle(args.model_dir, device=args.device)
    prediction = predict_text(
        bundle.model,
        bundle.tokenizer,
        args.text,
        bundle.label_mapping,
        bundle.thresholds,
        max_length=bundle.training_config.max_length,
        device=bundle.device,
    )
    print(json.dumps(prediction, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

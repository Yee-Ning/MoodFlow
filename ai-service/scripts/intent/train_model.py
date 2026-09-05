import argparse
import sys
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.agent.foundation.intent_model.config import TrainingConfig
from app.agent.foundation.intent_model.training import train_model


def parse_args() -> argparse.Namespace:
    data_root = AI_SERVICE_ROOT / "data" / "intent"
    parser = argparse.ArgumentParser(
        description="Fine-tune BGE as a supervised multi-label intent classifier."
    )
    parser.add_argument("--train", type=Path, default=data_root / "splits" / "train.jsonl")
    parser.add_argument("--val", type=Path, default=data_root / "splits" / "val.jsonl")
    parser.add_argument("--test", type=Path, default=data_root / "splits" / "test.jsonl")
    parser.add_argument(
        "--label-mapping", type=Path, default=data_root / "label_mapping.json"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=AI_SERVICE_ROOT / "artifacts" / "intent_model"
    )
    parser.add_argument("--model-name", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--threshold-step", type=float, default=0.05)
    parser.add_argument("--pooling", choices=("cls", "mean"), default="cls")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = TrainingConfig(
        model_name=args.model_name,
        max_length=args.max_length,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        epochs=args.epochs,
        weight_decay=args.weight_decay,
        dropout=args.dropout,
        random_seed=args.seed,
        threshold=args.threshold,
        threshold_step=args.threshold_step,
        pooling=args.pooling,
        device=args.device,
    )
    train_model(
        train_path=args.train,
        val_path=args.val,
        test_path=args.test,
        label_mapping_path=args.label_mapping,
        output_dir=args.output_dir,
        config=config,
    )


if __name__ == "__main__":
    main()

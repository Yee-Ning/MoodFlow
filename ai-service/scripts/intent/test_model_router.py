import argparse
import json
import logging
import sys
from pathlib import Path

AI_SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(AI_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_SERVICE_ROOT))

from app.agent.foundation.model_routing import ModelRouter

DEFAULT_TEXTS = (
    "分析一下这首歌",
    "帮我暂停",
    "如果周杰伦没有发行范特西会怎样",
    "什么叫概念专辑",
    "分析一下这首歌然后帮我收藏",
    "当前电影可以吗",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test the long-lived ModelRouter.")
    parser.add_argument(
        "--model-dir", type=Path, default=AI_SERVICE_ROOT / "artifacts" / "intent_model"
    )
    parser.add_argument("--device", choices=("auto", "cuda", "mps", "cpu"), default="auto")
    parser.add_argument(
        "--text",
        action="append",
        help="Optional input; repeat the flag to test multiple texts.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    router = ModelRouter(args.model_dir, device=args.device)
    for text in args.text or DEFAULT_TEXTS:
        result = router.route(text)
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

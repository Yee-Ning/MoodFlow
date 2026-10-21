"""Run the independent stdlib replay against report-53, without v1 writes."""

from unittest.mock import patch

from tools.v52_offline_runtime import verify_dimension_034 as replay


OUTPUT = replay.OUTPUT.parent / "h1-dimension-prompt-034-v2-20261019"


def main():
    with patch.object(replay, "OUTPUT", OUTPUT):
        replay.main()


if __name__ == "__main__":
    main()

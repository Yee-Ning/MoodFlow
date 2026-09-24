"""Run the offline WorldLine evaluation harness."""

from __future__ import annotations

import asyncio

from evals.worldline_eval_runner import _main


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

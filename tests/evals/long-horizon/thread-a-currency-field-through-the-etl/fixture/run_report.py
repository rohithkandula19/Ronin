#!/usr/bin/env python3
"""Print a per-channel summary of an existing feed.

    python3 run_report.py --feed out/orders.jsonl
"""

from __future__ import annotations

import argparse
import sys

from etl.config import Config
from etl.errors import EtlError
from etl.readers import JsonlFeedReader
from etl.writers import SummaryWriter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--feed", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.load(args.config)
        feed = args.feed or config.output_path()
        summary = SummaryWriter()
        summary.extend(record for _, record in JsonlFeedReader(feed).read())
    except EtlError as exc:
        print(f"report failed: {exc}", file=sys.stderr)
        return 2
    print(summary.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

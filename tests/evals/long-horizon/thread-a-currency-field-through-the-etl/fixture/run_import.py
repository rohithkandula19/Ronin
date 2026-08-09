#!/usr/bin/env python3
"""Import a source file of orders into the canonical feed.

    python3 run_import.py --source data/orders_sample.csv --out out/orders.jsonl
"""

from __future__ import annotations

import argparse
import sys

from etl.config import Config
from etl.errors import EtlError
from etl.pipeline import Pipeline
from etl.readers import CsvOrderReader, JsonlFeedReader
from etl.transforms import DedupeOrders, EnrichRegion, Normalize
from etl.validate import Validator
from etl.writers import JsonlWriter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--source", required=True)
    parser.add_argument("--format", choices=("csv", "jsonl"), default="csv")
    parser.add_argument("--out", default=None, help="defaults to the configured output path")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when any row was rejected",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.load(args.config)
        if args.format == "csv":
            reader = CsvOrderReader(
                args.source,
                delimiter=config.input_delimiter(),
                encoding=config.input_encoding(),
            )
        else:
            reader = JsonlFeedReader(args.source, encoding=config.input_encoding())
        destination = args.out or config.output_path()
        with JsonlWriter(destination) as writer:
            pipeline = Pipeline(
                reader=reader,
                validator=Validator(config),
                writer=writer,
                transforms=(Normalize(), EnrichRegion()),
                filters=(DedupeOrders(),),
            )
            result = pipeline.run()
    except EtlError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 2
    print(result.render())
    print(f"feed written to {destination}")
    for row_error in result.errors:
        print(row_error.render(), file=sys.stderr)
    if args.strict and result.rejected:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

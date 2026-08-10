"""Show how the pending order export will be split across workers.

Usage: python3 shard_orders.py [orders.csv]
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from shard.batching import iter_chunks
from shard.sizing import DEFAULT_CHUNK_SIZE, describe_split


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else Path("orders.csv")
    rows = read_rows(source)
    print(describe_split(len(rows), DEFAULT_CHUNK_SIZE))
    for chunk in iter_chunks(rows, DEFAULT_CHUNK_SIZE):
        print(chunk.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

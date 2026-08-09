"""Print the throughput report for the last batch of runs.

Usage: python3 throughput.py [runs.csv]
"""

from __future__ import annotations

import sys
from pathlib import Path

from thru.metrics import report
from thru.records import load_samples


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else Path("runs.csv")
    result = load_samples(source)
    for line in report(result.samples):
        print(line)
    print(f"skipped: {', '.join(result.skipped) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

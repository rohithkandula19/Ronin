"""Print the dispatch plan for the queued jobs.

Usage: python3 spool_plan.py [jobs.json]
"""

from __future__ import annotations

import sys
from pathlib import Path

from spool.runner import load_jobs, plan_all


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else Path("jobs.json")
    for line in plan_all(load_jobs(source)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

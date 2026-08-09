"""`python -m feedkit` style entry point."""

from __future__ import annotations

import json
import sys
from typing import Any

from feedkit import check_feed_payload


def main(argv: list[str] | None = None) -> int:
    """Read a JSON payload from a file (or stdin) and report on it."""
    args = list(sys.argv[1:] if argv is None else argv)
    raw = open(args[0], encoding="utf-8").read() if args else sys.stdin.read()
    payload: dict[str, Any] = json.loads(raw)
    problems = check_feed_payload(payload)
    for problem in problems:
        print(f"error: {problem}")
    return 1 if problems else 0

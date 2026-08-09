"""Print the on-call status board.

Usage: python3 status_board.py [services.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from opsboard.settings import load_settings
from opsboard.table import render


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else Path("services.json")
    records = json.loads(source.read_text(encoding="utf-8"))
    settings = load_settings("opsboard.ini")
    print(render(records, settings))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

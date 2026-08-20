"""Print one normalised name per record."""

from __future__ import annotations

from intake.normalise import full_name

RECORDS = [
    {"first": "Ada ", "middle": "Byron", "last": " Lovelace"},
    {"first": "Alan", "middle": None, "last": "Turing"},
]


def main() -> None:
    for record in RECORDS:
        print(full_name(record))

"""Print the fleet total."""

from __future__ import annotations

from quota.usage import total_bytes

RECORDS = [
    {"account": "a", "bytes": 100},
    {"account": "b"},
    {"account": "c", "bytes": 250},
]


def main() -> None:
    print(total_bytes(RECORDS))
